"""Authenticated cloud bridge; the existing member session remains authoritative.

Cloud metadata and private blobs cross this boundary. Provider credentials and
local execution configuration never do. Every operation is bound to one member,
management origin and immutable local Store for its entire lifetime.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import socket
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .remote import RemoteError, admin_base, member_id
from .skills import atomic_json

PREFIX = "/api/v1/architecture/cloud"
MAX_BLOB = 80 * 1024 * 1024  # Existing Store/ArtifactResolver download ceiling.
ID = re.compile(r"[a-f0-9]{32}")
CAPABILITIES = {"tools", "dialogue", "quick", "canvas", "render", "video",
                "image_to_model", "plugin", "knowledge", "documents", "interior"}
PRIVATE_KEYS = {"password", "secret", "client_secret", "access_token", "refreshtoken",
                "refresh_token", "accesstoken", "authorization", "api_key", "apikey",
                "token", "local_token", "executable", "command", "shell", "env", "mcp",
                "servers", "global_config", "config_path", "data_dir", "workspace",
                "model_provider", "model_providers", "codex_home", "codebuddy_home", "cli_config"}
USAGE_COUNTERS = {"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"}


def identifier(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise HTTPException(422, "云端记录编号无效")
    return value


def owned_blob(store, supplied):
    """An editable business snapshot cannot confer arbitrary file ownership."""
    from .artifacts import _reparse
    raw = Path(supplied)
    root = Path(store.data_dir).resolve()
    path = store._owned_path(str(raw))
    if any(parent.exists() and _reparse(parent) for parent in [raw, *raw.parents]):
        raise HTTPException(422, "云端同步不支持链接文件")
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_nlink != 1 or not 0 < path.stat().st_size <= MAX_BLOB:
        raise HTTPException(422, "云端同步须使用当前会员的真实普通文件")
    return path


def safe_metadata(value):
    """Drop local addressing and secrets from portable recipes and receipts."""
    if isinstance(value, dict):
        return {key: safe_metadata(item) for key, item in value.items()
                if isinstance(key, str) and key.lower() not in PRIVATE_KEYS
                and (not any(part in key.lower() for part in ("password", "secret", "token"))
                     or (key.lower() in USAGE_COUNTERS and (item is None or isinstance(item, (int, float)))))
                and not key.lower().endswith(("_path", "_dir", "_url"))
                and (key.lower() not in {"path", "url", "href", "outputdir", "output_dir"}
                     or (key == "href" and isinstance(item, str) and re.fullmatch(r"cloud-asset:[a-f0-9]{32}", item)))}
    if isinstance(value, list):
        return [safe_metadata(item) for item in value]
    if isinstance(value, str):
        # A recipe must not contain a Windows absolute path, file URL, local
        # capability URL, UNC address, or machine config embedded in prose.
        if re.search(r"(?i)(?:[a-z]:[\\/]|\\\\|file://|https?://(?:localhost|127\.0\.0\.1|\[::1\])|[?&](?:token|access_token)=|Bearer\s+\S+)", value):
            return "[本机引用已移除]"
        if value.startswith("/") or re.search(r"(?:^|\s)/(?:tmp|var|opt|srv|etc|private|run|root|data|home|Users|mnt)/", value):
            return "[本机引用已移除]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise HTTPException(422, "云端配置含不支持的数据")


def checked_options(value):
    """Server options cannot select executables, files, shells or external APIs."""
    if not isinstance(value, dict):
        raise HTTPException(422, "任务参数须为对象")
    def check(item):
        if isinstance(item, dict):
            for key, part in item.items():
                lower = str(key).lower()
                if lower in PRIVATE_KEYS or lower in {"path", "url", "endpoint", "base_url", "cwd"} or lower.endswith(("_path", "_url")):
                    raise HTTPException(422, "云任务不能指定本机路径、程序、凭据或外部API")
                check(part)
        elif isinstance(item, list):
            for part in item:
                check(part)
        elif isinstance(item, str) and ("://" in item or re.search(r"[a-zA-Z]:[\\/]", item) or item.startswith("\\\\")):
            raise HTTPException(422, "云任务参数不能包含外部地址或本机路径")
    check(value)
    return value


@dataclass
class CloudContext:
    base: str
    tenant: int
    member: str
    scope: str
    epoch: int
    store: object = field(repr=False)
    credentials: dict = field(repr=False)


class CloudService:
    def __init__(self, app):
        self.app, self.facade, self.remote = app, app.state.store, app.state.remote
        self.http_factory = httpx.AsyncClient
        self.worker = None

    def current_store(self):
        return self.facade.current if hasattr(self.facade, "current") else self.facade

    async def context(self) -> CloudContext:
        # me() validates native membership and performs the original refresh.
        try:
            result = await self.remote.me()
        except RemoteError as error:
            status = error.status if error.status in {401, 403} else 502
            raise HTTPException(status, "会员会话无效或管理端暂时不可用") from None
        except ValueError:
            raise HTTPException(401, "本机会员凭据无效，请重新登录") from None
        if not result.get("logged_in"):
            raise HTTPException(401, "请先登录会员账号")
        credentials = self.remote.session()
        if not credentials:
            raise HTTPException(401, "会员会话已失效，请重新登录")
        settings = self.facade.get_settings()
        context = CloudContext(admin_base(settings), settings.get("admin_tenant_id", 0),
                               member_id(credentials.get("user_id")),
                               getattr(self.facade, "account_scope", "anonymous"),
                               getattr(self.facade, "epoch", 0), self.current_store(), dict(credentials))
        self.check(context)
        return context

    def check(self, context):
        settings = self.facade.get_settings()
        if (admin_base(settings) != context.base or settings.get("admin_tenant_id", 0) != context.tenant
                or getattr(self.facade, "account_scope", "anonymous") != context.scope
                or getattr(self.facade, "epoch", 0) != context.epoch
                or self.current_store() is not context.store):
            raise HTTPException(409, "会员或管理服务已切换，请重新加载")
        session = self.remote.session()
        if not session or member_id(session.get("user_id")) != context.member or session.get("base") != context.base or session.get("tenant_id", 0) != context.tenant:
            raise HTTPException(401, "会员会话已失效，请重新登录")

    async def refresh(self, context):
        self.check(context)
        result = await self.remote.me()
        if not result.get("logged_in"):
            raise HTTPException(401, "会员会话已失效，请重新登录")
        self.check(context)
        context.credentials = dict(self.remote.session())

    async def request(self, context, method, path, payload=None):
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise HTTPException(422, "云端请求路径无效")
        self.check(context)
        for attempt in range(2):
            try:
                value = await self.remote.request(method, PREFIX + path, payload=payload,
                                                  session=context.credentials, expected_base=context.base)
                self.check(context)
                return value
            except RemoteError as error:
                if error.status == 401 and attempt == 0:
                    await self.refresh(context)
                    continue
                status = error.status if isinstance(error.status, int) and 400 <= error.status <= 599 else 502
                message = "云项目已被更新，请重新读取后保存" if status == 409 else "云端服务请求失败，请检查会员权限与连接"
                raise HTTPException(status, message) from None
        raise HTTPException(401, "会员会话已失效，请重新登录")

    async def bytes_request(self, context, method, path, *, files=None, data=None):
        # The binary/multipart extension uses precisely the original realm and
        # member binding; token only appears in headers in process memory.
        if not re.fullmatch(r"/assets(?:/[a-f0-9]{32}/content)?", path):
            raise HTTPException(422, "私有资产路径无效")
        for attempt in range(2):
            self.check(context)
            headers = {"tenant-id": str(context.tenant), "Authorization": "Bearer " + context.credentials["access_token"]}
            try:
                async with self.http_factory(timeout=120, follow_redirects=False, trust_env=False) as client:
                    async with client.stream(method, context.base + PREFIX + path, headers=headers, files=files, data=data) as reply:
                        status = reply.status_code
                        if status == 401 and attempt == 0:
                            content = b""
                        elif status >= 300:
                            raise HTTPException(status if 400 <= status <= 599 else 502, "私有资产请求失败")
                        else:
                            chunks, total = [], 0
                            async for chunk in reply.aiter_bytes():
                                total += len(chunk)
                                if total > MAX_BLOB:
                                    raise HTTPException(413, "私有资产超过本机80 MB上限")
                                chunks.append(chunk)
                            content = b"".join(chunks)
            except httpx.HTTPError:
                raise HTTPException(502, "无法连接私有资产服务") from None
            if status == 401 and attempt == 0:
                await self.refresh(context)
                continue
            self.check(context)
            return content
        raise HTTPException(401, "会员会话已失效，请重新登录")

    async def upload(self, context, filename, content, *, project_id=None, role="source"):
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BLOB:
            raise HTTPException(413, "上传文件须非空且不超过80 MB")
        if role not in {"source", "reference", "result"}:
            raise HTTPException(422, "素材角色无效")
        filename = Path(filename.replace("\\", "/")).name
        if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
            raise HTTPException(422, "文件名无效")
        digest = hashlib.sha256(content).hexdigest()
        form = {"role": role, "sha256": digest}
        if project_id:
            form["project_id"] = identifier(project_id)
        raw = await self.bytes_request(context, "POST", "/assets", files={"file": (filename, content, "application/octet-stream")}, data=form)
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(502, "上传服务返回无效记录") from None
        if not isinstance(item, dict) or item.get("sha256") != digest or item.get("size_bytes") != len(content):
            raise HTTPException(502, "上传记录哈希或字节数不符")
        identifier(item.get("id"))
        return self.public(item)

    async def asset(self, context, asset_id, *, project_id=None):
        identifier(asset_id)
        # The cloud API intentionally has no unscoped GET asset-by-id endpoint.
        for offset in range(0, 100001, 100):
            query = {"limit": 100, "offset": offset}
            if project_id:
                query["project_id"] = identifier(project_id)
            rows = (await self.request(context, "GET", "/assets?" + urlencode(query))).get("items", [])
            for row in rows:
                if row.get("id") == asset_id:
                    return row
            if len(rows) < 100:
                break
        raise HTTPException(404, "私有素材不存在或不属于当前会员")

    async def download(self, context, asset):
        asset_id = identifier(asset.get("id"))
        expected = PREFIX + "/assets/" + asset_id + "/content"
        if asset.get("content_url") not in {None, expected}:
            raise HTTPException(422, "拒绝非受控的私有素材地址")
        content = await self.bytes_request(context, "GET", "/assets/" + asset_id + "/content")
        digest = asset.get("sha256", "")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) or hashlib.sha256(content).hexdigest() != digest or len(content) != asset.get("size_bytes"):
            raise HTTPException(502, "私有素材哈希或字节数校验失败")
        self.check(context)
        return content

    @staticmethod
    def public(value):
        if isinstance(value, list):
            return [CloudService.public(item) for item in value]
        if isinstance(value, dict):
            item = {key: CloudService.public(part) for key, part in value.items()
                    if key.lower() not in PRIVATE_KEYS and key.lower() not in {"lease_token", "lease_hash", "storage_key", "owner", "member_user_id", "tenant_id"}}
            if isinstance(item.get("content_url"), str) and item["content_url"].startswith(PREFIX + "/assets/"):
                item["content_url"] = "/api/cloud" + item["content_url"][len(PREFIX):]
            return item
        return value

    def worker_config(self, store=None):
        store = store or self.current_store()
        path = Path(store.data_dir) / "cloud-worker.json"
        value = {"enabled": False, "device_id": "", "name": socket.gethostname()[:100], "last_error": ""}
        if path.is_file() and not path.is_symlink():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                value.update({key: saved[key] for key in value if key in saved})
            except (ValueError, OSError):
                pass
        return value

    def save_worker_config(self, context, **fields):
        self.check(context)
        value = {**self.worker_config(context.store), **fields}
        path = Path(context.store.data_dir) / "cloud-worker.json"
        if path.is_symlink():
            raise HTTPException(422, "执行设备配置目录无效")
        atomic_json(path, value)
        return value

    async def status(self):
        worker = self.worker_config()
        worker["enabled"] = bool(self.worker and self.worker.task and not self.worker.task.done()
                                 and self.worker.context and self.worker.context.scope == getattr(self.facade, "account_scope", "anonymous"))
        try:
            context = await self.context()
            await self.request(context, "GET", "/catalog")
            profile = await self.remote.me()
            return {"logged_in": True, "connected": True, "worker": worker, "member": self.public(profile.get("profile", {}))}
        except (HTTPException, RemoteError, ValueError):
            logged_in = False
            with contextlib.suppress(ValueError):
                logged_in = bool(self.remote.session())
            return {"logged_in": logged_in, "connected": False, "worker": worker}


def register_cloud(app):
    from .cloud_projects import CloudProjects
    from .cloud_worker import CloudWorker
    service = CloudService(app)
    projects = CloudProjects(service)
    worker = CloudWorker(service)
    service.worker = worker
    app.state.cloud, app.state.cloud_worker = service, worker
    router = APIRouter(prefix="/api/cloud")

    @router.get("/status")
    async def status():
        return await service.status()

    @router.put("/worker")
    async def configure_worker(body: dict = Body(...)):
        if set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
            raise HTTPException(422, "请指定执行设备启用状态")
        if not body["enabled"]:
            await worker.stop()
            config = service.worker_config()
            config["enabled"] = False
            path = Path(service.current_store().data_dir) / "cloud-worker.json"
            if path.is_symlink():
                raise HTTPException(422, "执行设备配置目录无效")
            atomic_json(path, config)
            return config
        context = await service.context()
        return await worker.enable(context)

    @router.get("/local-objects")
    def local_objects():
        return {"items": projects.local_objects()}

    @router.post("/publish-local")
    async def publish_local(body: dict = Body(...)):
        return await projects.publish(await service.context(), body)

    @router.post("/projects/{project_id}/restore")
    async def restore(project_id: str):
        return await projects.restore(await service.context(), identifier(project_id))

    @router.post("/assets")
    async def upload(file: UploadFile = File(...), project_id: str | None = Form(None), role: str = Form("source")):
        context = await service.context()
        content = await file.read(MAX_BLOB + 1)
        return await service.upload(context, file.filename or "asset.bin", content, project_id=project_id, role=role)

    @router.get("/assets/{asset_id}/content")
    async def download(asset_id: str):
        context = await service.context()
        asset = await service.asset(context, identifier(asset_id))
        content = await service.download(context, asset)
        saved = context.store.save_attachment(asset["filename"], io.BytesIO(content))
        service.check(context)
        return FileResponse(saved["path"], filename=asset["filename"], media_type="application/octet-stream",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                                     "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'"})

    # Deliberate allow-list: this cannot proxy arbitrary server paths/methods.
    allowed = [
        (r"catalog", {"GET"}), (r"projects", {"GET", "POST"}),
        (r"projects/[a-f0-9]{32}", {"GET", "PUT", "DELETE"}),
        (r"projects/[a-f0-9]{32}/(?:versions|shares)", {"GET", "POST"}),
        (r"projects/[a-f0-9]{32}/shares/[a-f0-9]{32}", {"DELETE"}),
        (r"shares/[a-f0-9]{32}", {"DELETE"}), (r"assets", {"GET"}),
        (r"jobs", {"GET", "POST"}), (r"jobs/[a-f0-9]{32}", {"GET"}),
        (r"jobs/[a-f0-9]{32}/events", {"GET"}), (r"jobs/[a-f0-9]{32}/cancel", {"POST"}),
        (r"devices", {"GET", "POST"}), (r"devices/[a-f0-9]{32}/(?:heartbeat|revoke)", {"POST"}),
    ]
    async def proxy(path: str, request: Request):
        if not any(re.fullmatch(pattern, path) and request.method in methods for pattern, methods in allowed):
            raise HTTPException(404, "云端接口不存在")
        context = await service.context()
        body = await request.json() if request.method in {"POST", "PUT"} and await request.body() else None
        if body is not None and not isinstance(body, dict):
            raise HTTPException(422, "云端请求须为对象")
        if path == "jobs" and request.method == "POST":
            if body.get("capability") not in CAPABILITIES:
                raise HTTPException(422, "云端创作能力无效")
            checked_options(body.get("options", {}))
        if request.method in {"POST", "PUT"} and path.startswith("projects") and body is not None and "document" in body:
            body = {**body, "document": safe_metadata(body["document"])}
        query = dict(request.query_params)
        if set(query) - {"limit", "offset", "project_id", "state", "after"}:
            raise HTTPException(422, "云端查询参数无效")
        target = "/" + path
        if re.fullmatch(r"projects/[a-f0-9]{32}/shares/[a-f0-9]{32}", path):
            # Verify the requested share is part of the selected owned project.
            pid, sid = path.split("/")[1], path.split("/")[3]
            shares = await service.request(context, "GET", "/projects/" + pid + "/shares")
            if not any(row.get("id") == sid for row in shares.get("items", [])):
                raise HTTPException(404, "分享不存在")
            target = "/shares/" + sid
        if query:
            target += "?" + urlencode(query)
        result = await service.request(context, request.method, target, body)
        if re.fullmatch(r"projects/[a-f0-9]{32}/shares", path) and request.method == "POST" and isinstance(result, dict):
            result = {**result}
            relative = result.get("url")
            if isinstance(relative, str) and relative.startswith(PREFIX + "/shared/"):
                result["url"] = context.base + relative
        return service.public(result)

    # A distinct APIRoute per method gives stable, unique OpenAPI operation
    # identifiers while preserving the same allow-listed HTTP handler.
    for method in ("GET", "POST", "PUT", "DELETE"):
        router.add_api_route("/{path:path}", proxy, methods=[method],
                             name="cloud_proxy_" + method.lower(),
                             operation_id="architecture_cloud_proxy_" + method.lower())

    app.include_router(router)
    app.router.add_event_handler("shutdown", worker.stop)
