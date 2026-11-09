"""Optional independent management service, member OAuth and signed downloads."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import urlsplit, quote
import zipfile

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


def admin_base(settings):
    url = str(settings.get("admin_url", "")).strip().rstrip("/")
    parsed = urlsplit(url)
    if not url:
        raise ValueError("请先设置此产品的独立管理端地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("管理端地址不能带凭据、查询或片段")
    if not parsed.hostname or any(ord(char) <= 32 for char in url):
        raise ValueError("管理端地址需要有效主机名")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("管理端使用 HTTPS，本机调试可使用回环 HTTP")
    try:
        port = parsed.port
        if port == 0:
            raise ValueError()
    except ValueError:
        raise ValueError("管理端端口无效") from None
    host = parsed.hostname.lower()
    host = "[" + host + "]" if ":" in host else host.encode("idna").decode("ascii")
    authority = host + (":" + str(port) if port and port != (443 if parsed.scheme == "https" else 80) else "")
    return parsed.scheme + "://" + authority + parsed.path.rstrip("/")


def protect(value: dict) -> bytes:
    if os.name != "nt":
        raise ValueError("当前版本的持久会员登录仅支持 Windows 凭据保护")
    import win32crypt
    return win32crypt.CryptProtectData(json.dumps(value).encode(), "ArchitectureWorkbench", None, None, None, 0)


def unprotect(value: bytes) -> dict:
    import win32crypt
    return json.loads(win32crypt.CryptUnprotectData(value, None, None, None, 0)[1])


def member_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"[0-9]{1,19}", str(value)):
        raise ValueError("会员服务返回的身份编号无效")
    identifier = int(value)
    if not 0 < identifier <= 9223372036854775807:
        raise ValueError("会员服务返回的身份编号无效")
    return str(identifier)


def safe_profile(value):
    if not isinstance(value, dict) or not isinstance(value.get("user"), dict):
        raise ValueError("会员服务返回的会话资料无效")
    # The original NativeMemberSessions projection contains exactly these
    # public fields. Never forward unknown properties, tokens or raw errors.
    user = {key: value["user"][key] for key in ("id", "username", "phone", "display_name")
            if isinstance(value["user"].get(key), str)}
    if not user.get("id"):
        raise ValueError("会员服务返回的会话资料无效")
    return {"user": user, "expires_at": value.get("expires_at") if isinstance(value.get("expires_at"), str) else None}


class MemberLogin(BaseModel):
    account: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    tenant_id: int | None = Field(default=None, ge=0, le=2147483647, strict=True)


class RemoteClient:
    def __init__(self, store, can_change=None, scope_lock=None, on_expired=None):
        self.store = store
        self.root_dir = Path(getattr(store, "root_dir", store.data_dir))
        self.session_file = self.root_dir / "member-session.bin"
        self.cached_manifest = self.root_dir / "admin-manifest.json"
        self._lock = asyncio.Lock()
        self._scope_lock = scope_lock or asyncio.Lock()
        self.can_change = can_change
        self.on_expired = on_expired
        try:
            saved = self.session()
            if saved and saved.get("user_id"):
                self._check_binding(saved.get("base"), saved.get("tenant_id", 0))
                self._activate(saved)
        except (ValueError, KeyError, OSError):
            # A corrupt credential file is reported by /me. Original local
            # documents remain available and are never silently reassigned.
            pass

    def _activate(self, value=None, *, renew=False):
        if hasattr(self.store, "activate"):
            if value:
                return self.store.activate(value["base"], value.get("tenant_id", 0), member_id(value["user_id"]), renew=renew)
            return self.store.activate(renew=renew)
        return "anonymous"

    def _idle(self):
        if self.can_change:
            self.can_change()

    def _assert_scope_current(self):
        if getattr(self.store, "closing", False) or (hasattr(self.store, "bound_epoch") and self.store.bound_epoch != self.store.epoch):
            raise RemoteError("会员工作区已改变，请重新打开客户端", 409)

    def _check_binding(self, base, tenant):
        settings = self.store.get_settings()
        if base != admin_base(settings):
            raise ValueError("管理端地址已改变，请重新登录")
        if tenant != settings.get("admin_tenant_id", 0):
            raise ValueError("会员租户已改变，请重新登录")

    async def request(self, method, path, *, payload=None, session=None, expected_base=None, tenant_id=None, binding_tenant=None):
        base = admin_base(self.store.get_settings())
        if expected_base and base != expected_base:
            raise ValueError("管理端地址已改变，请重新登录")
        tenant = session.get("tenant_id", 0) if session else (self.store.get_settings().get("admin_tenant_id", 0) if tenant_id is None else tenant_id)
        guard_tenant = tenant if binding_tenant is None else binding_tenant
        self._check_binding(base, guard_tenant)
        headers = {"tenant-id": str(tenant)}
        if session:
            if session.get("base") != base:
                raise ValueError("管理端地址已改变，请重新登录")
            headers["Authorization"] = "Bearer " + session["access_token"]
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
                reply = await client.request(method, base + path, json=payload, headers=headers)
        except httpx.HTTPError:
            # Refresh URLs contain credentials; exception strings are private.
            raise RemoteError("无法连接管理端，请检查地址或网络", 502) from None
        self._check_binding(base, guard_tenant)
        if reply.status_code >= 300:
            raise RemoteError(f"管理端请求失败（HTTP {reply.status_code}）", reply.status_code)
        try:
            value = reply.json()
        except ValueError:
            raise RemoteError("管理端返回了无效的响应", 502) from None
        if not isinstance(value, dict):
            raise RemoteError("管理端返回了无效的响应", 502)
        if value.get("code", 0) != 0:
            status = value.get("code")
            message = "会员会话已失效，请重新登录" if status == 401 else "会员账号无权限或已停用" if status == 403 else "管理端请求失败，请检查账号、密码或服务配置"
            raise RemoteError(message, status)
        return value.get("data", value)

    def session(self):
        if not self.session_file.is_file():
            return None
        try:
            return unprotect(self.session_file.read_bytes())
        except Exception:
            raise ValueError("本机会员凭据无法解密，请重新登录") from None

    def save(self, value):
        temporary = self.session_file.with_suffix(".tmp")
        temporary.write_bytes(protect(value))
        os.replace(temporary, self.session_file)

    async def manifest(self):
        result = await self.request("GET", "/superip/architecture/client/manifest?platform=windows-x64")
        if result.get("schemaVersion") != 1 or result.get("product") != "architecture-workbench":
            raise ValueError("管理端返回的产品清单不匹配")
        self.cached_manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _tokens(self, tokens, expected_id=None):
        if not isinstance(tokens, dict) or not all(isinstance(tokens.get(key), str) and tokens[key] for key in ("accessToken", "refreshToken")):
            raise ValueError("会员登录未返回有效 OAuth 凭据")
        identifier = member_id(tokens.get("userId"))
        if expected_id and identifier != member_id(expected_id):
            raise ValueError("刷新返回的会员身份不匹配，请重新登录")
        return {"access_token": tokens["accessToken"], "refresh_token": tokens["refreshToken"], "user_id": identifier}

    async def login(self, account, password, tenant_id=None):
        async with self._lock, self._scope_lock:
            self._assert_scope_current()
            self._idle()
            settings = self.store.get_settings()
            base = admin_base(settings)
            tenant = settings.get("admin_tenant_id", 0) if tenant_id is None else tenant_id
            previous_tenant = settings.get("admin_tenant_id", 0)
            if isinstance(tenant, bool) or not isinstance(tenant, int) or not 0 <= tenant <= 2147483647:
                raise ValueError("会员租户编号必须为非负整数")
            tokens = await self.request("POST", "/app-api/member/auth/login", payload={"username": account, "password": password}, expected_base=base, tenant_id=tenant, binding_tenant=previous_tenant)
            value = {"base": base, "tenant_id": tenant, "protocol": "member", "local_session_id": uuid.uuid4().hex, **self._tokens(tokens)}
            profile = safe_profile(await self.request("GET", "/app-api/member/auth/session", session=value, binding_tenant=previous_tenant))
            self._check_binding(base, previous_tenant)
            self._idle()
            if hasattr(self.store, "prepare"):
                self.store.prepare(value["base"], value["tenant_id"], value["user_id"])
            self.save({**value, "profile": profile})
            if tenant != previous_tenant:
                self.store.save_settings({"admin_tenant_id": tenant})
            self._activate(value, renew=True)
            return {**profile, "account_scope": getattr(self.store, "account_scope", "anonymous")}

    async def me(self):
        async with self._lock:
            self._assert_scope_current()
            value = self.session()
            if not value:
                return {"logged_in": False, "account_scope": "anonymous"}
            self._check_binding(value.get("base"), value.get("tenant_id", 0))
            try:
                try:
                    profile = safe_profile(await self.request("GET", "/app-api/member/auth/session", session=value))
                except RemoteError as error:
                    if error.status != 401:
                        raise
                    tokens = await self.request("POST", "/app-api/member/auth/refresh-token?refreshToken=" + quote(value["refresh_token"], safe=""), expected_base=value["base"], tenant_id=value.get("tenant_id", 0))
                    value.update(self._tokens(tokens, value.get("user_id")))
                    profile = safe_profile(await self.request("GET", "/app-api/member/auth/session", session=value))
                self._check_binding(value["base"], value.get("tenant_id", 0))
                self.save({**value, "profile": profile})
                if value.get("user_id"):
                    desired = self.store.member_scope(value["base"], value.get("tenant_id", 0), value["user_id"]) if hasattr(self.store, "member_scope") else "anonymous"
                    if getattr(self.store, "account_scope", "anonymous") != desired:
                        async with self._scope_lock:
                            self._idle()
                            self._activate(value)
                return {"logged_in": True, "profile": profile, "account_scope": getattr(self.store, "account_scope", "anonymous")}
            except RemoteError as error:
                if error.status in {401, 403}:
                    self.store.closing = True
                    try:
                        if self.on_expired:
                            await self.on_expired()
                        self.session_file.unlink(missing_ok=True)
                        self._activate(renew=True)
                    finally:
                        self.store.closing = False
                raise

    async def logout(self):
        async with self._lock, self._scope_lock:
            self._assert_scope_current()
            self._idle()
            try:
                value = self.session()
            except ValueError:
                value = None
            revoked = False
            try:
                if value:
                    await self.request("POST", "/app-api/member/auth/logout", session=value)
                    revoked = True
            except (ValueError, httpx.HTTPError):
                pass
            self.session_file.unlink(missing_ok=True)
            self._activate(renew=True)
            return {"logged_in": False, "account_scope": "anonymous", "server_logout": revoked}

    async def update_settings(self, body):
        async with self._lock, self._scope_lock:
            self._assert_scope_current()
            current = self.store.get_settings()
            changed = any(key in body and body[key] != current.get(key, 0 if key == "admin_tenant_id" else "")
                          for key in ("admin_url", "admin_tenant_id"))
            if changed:
                self._idle()
                if body.get("admin_url"):
                    admin_base({"admin_url": body["admin_url"]})
            result = self.store.save_settings(body)
            if changed:
                self.session_file.unlink(missing_ok=True)
                self._activate(renew=True)
                self.cached_manifest.unlink(missing_ok=True)
            return result

    def release_policy(self):
        from .signed_release import ReleasePolicy
        trust = self.store.get_settings().get("release_trust") or {}
        if not isinstance(trust, dict) or not trust.get("public_keys"):
            raise ValueError("此产品尚未配置可信的版本更新，请联系管理员")
        return ReleasePolicy(origin=trust.get("origin", ""), installer_prefix=trust.get("installer_prefix", "ArchitectureWorkbench"),
                             public_keys=trust["public_keys"], tls_ca=trust.get("tls_ca"))

    async def latest_release(self):
        from . import __version__
        from .signed_release import latest
        base = admin_base(self.store.get_settings())
        release = await latest(base, __version__, policy=self.release_policy())
        if base != admin_base(self.store.get_settings()):
            raise ValueError("管理端地址已改变，请重新检查版本")
        return release

    async def download_update(self):
        from . import __version__
        from .signed_release import download_verified
        # Device realm/trust changes use this same lock. A verified package may
        # not be committed under a different product's configuration.
        async with self._lock:
            self._assert_scope_current()
            release = await self.latest_release()
            if release is None:
                raise ValueError("当前客户端已是最新版本")
            path = await download_verified(release, self.root_dir / "downloads", policy=self.release_policy(), current_version=__version__)
            return {"path": str(path), "version": release.projection()["version"], "verified": True, "signature_verified": True,
                    "installed": False}

    async def download(self, url, sha256, filename, limit=300 * 1024 * 1024):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("下载地址需要无凭据的 HTTPS 地址")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256 or ""):
            raise ValueError("下载清单缺少有效 SHA-256 校验值")
        folder = self.store.data_dir / "downloads"
        folder.mkdir(exist_ok=True)
        target = folder / Path(filename).name
        temporary = target.with_suffix(target.suffix + ".part")
        digest, length = hashlib.sha256(), 0
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                async with client.stream("GET", url) as reply:
                    if reply.status_code != 200:
                        raise ValueError(f"下载失败（HTTP {reply.status_code}）")
                    with temporary.open("wb") as stream:
                        async for chunk in reply.aiter_bytes():
                            length += len(chunk)
                            if length > limit:
                                raise ValueError("下载文件超过大小限制")
                            digest.update(chunk)
                            stream.write(chunk)
            if digest.hexdigest().lower() != sha256.lower():
                raise ValueError("下载文件校验不符，已丢弃")
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)


def extract_skill(archive: Path, destination: Path, key: str):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", key):
        raise ValueError("技能标识无效")
    root = destination / key
    root.mkdir()
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            normalized = entry.filename.replace("\\", "/")
            if normalized.startswith(key + "/"):
                normalized = normalized[len(key) + 1:]
            if not normalized:
                continue
            path = (root / normalized).resolve()
            if not path.is_relative_to(root.resolve()) or ":" in normalized or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("技能压缩包含不安全路径")
            total += entry.file_size
            if total > 600 * 1024 * 1024 or len(bundle.infolist()) > 30000:
                raise ValueError("技能解压大小超过限制")
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, path.open("wb") as sink:
                    import shutil
                    shutil.copyfileobj(source, sink)
    if not (root / "SKILL.md").is_file():
        raise ValueError("技能包缺少 SKILL.md")
    return root


def register_remote(app):
    router = APIRouter(prefix="/api/remote")
    def can_change():
        manager = getattr(app.state, "runtime_manager", None)
        if manager and (manager.active or manager.probing):
            raise HTTPException(409, "请先完成或停止正在执行的 Agent 任务，再切换会员账号")
        collaboration = getattr(app.state, "canvas_collaboration", None)
        if collaboration and collaboration.role != "none":
            raise HTTPException(409, "请先结束画布协作，再切换会员账号")

    async def on_expired():
        # Only owned runtime jobs and the selected-canvas share are closed.
        # Their old request contexts retain the previous member database.
        await app.state.canvas_collaboration.stop(force=True)
        await app.state.runtime_manager.shutdown()

    service = RemoteClient(app.state.store, can_change, app.state.canvas_collaboration.operation_lock, on_expired)
    app.state.remote = service

    async def invoke(coroutine):
        try:
            return await coroutine
        except RemoteError as error:
            status = error.status if error.status in {401, 403, 409} else 502
            raise HTTPException(status, str(error), headers={"X-Workbench-Account-Scope": app.state.store.account_scope}) from None
        except (ValueError, httpx.HTTPError, KeyError) as error:
            message = "无法连接管理端，请检查地址或网络" if isinstance(error, httpx.HTTPError) else "管理端响应字段不完整" if isinstance(error, KeyError) else str(error)
            raise HTTPException(502, message) from None

    @router.get("/manifest")
    async def manifest():
        return await invoke(service.manifest())

    @router.post("/login")
    async def login(body: MemberLogin):
        return JSONResponse(await invoke(service.login(body.account, body.password, body.tenant_id)), headers={"X-Workbench-Account-Scope": app.state.store.account_scope})

    @router.get("/me")
    async def me():
        return JSONResponse(await invoke(service.me()), headers={"X-Workbench-Account-Scope": app.state.store.account_scope})

    @router.post("/logout")
    async def logout():
        return JSONResponse(await invoke(service.logout()), headers={"X-Workbench-Account-Scope": app.state.store.account_scope})

    @router.post("/skills/{key}/install")
    async def install(key: str):
        manifest = await invoke(service.manifest())
        item = next((s for s in manifest.get("skills", []) if s.get("skillKey") == key), None)
        if not item:
            raise HTTPException(404, "清单中没有此技能")
        archive = await invoke(service.download(item["downloadUrl"], item["sha256"], key + ".zip"))
        try:
            with tempfile.TemporaryDirectory(prefix="architecture-skill-") as temp:
                folder = extract_skill(archive, Path(temp), key)
                return app.state.skill_catalog.import_skill(str(folder))
        except (ValueError, zipfile.BadZipFile) as error:
            raise HTTPException(422, str(error)) from None

    @router.post("/download-update")
    async def download_update():
        return await invoke(service.download_update())

    @router.get("/releases/latest")
    async def latest_release():
        release = await invoke(service.latest_release())
        return {"update_available": release is not None, "release": release.projection() if release else None}

    app.include_router(router)


class RemoteError(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status
