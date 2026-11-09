import asyncio
import base64
import copy
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import json
from pathlib import Path
import types
import uuid
import sys
import warnings

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from PIL import Image

from architecture_workbench.cloud import PREFIX, CloudService, checked_options, register_cloud, safe_metadata
from architecture_workbench.cloud_projects import CloudProjects
from architecture_workbench.cloud_worker import CloudWorker, validated_output
from architecture_workbench.design_workflow import KnowledgeCollections
from architecture_workbench.interior import InteriorService
from architecture_workbench.runtime import RuntimeManager
from architecture_workbench.remote import RemoteError
import architecture_workbench.runtime as runtime
from architecture_workbench.skills import SkillCatalog
from architecture_workbench.workspaces import WorkspaceStore
from architecture_workbench.app import create_app


class FakeServer:
    def __init__(self):
        self.calls, self.projects, self.assets, self.blobs = [], {}, {}, {}
        self.heartbeat_error = None

    async def request(self, method, path, *, payload=None, session=None, expected_base=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        path = path[len(PREFIX):]
        route = path.split("?")[0]
        if route == "/catalog":
            return {"items": [{"id": "tools", "enabled": True}]}
        if route == "/assets":
            rows = list(self.assets.values())
            return {"items": copy.deepcopy(rows)}
        if route == "/projects" and method == "POST":
            project_id = uuid.uuid4().hex
            item = {"id": project_id, "revision": 1, **copy.deepcopy(payload)}
            self.projects[project_id] = item
            return copy.deepcopy(item)
        if route.startswith("/projects/"):
            project_id = route.split("/")[2]
            project = self.projects.setdefault(project_id, {"id": project_id, "type": "creative", "revision": 1, "document": {}, "asset_ids": []})
            if method == "PUT":
                if payload["base_revision"] != project["revision"]:
                    raise HTTPException(409, "CAS conflict")
                project.update(copy.deepcopy(payload))
                project["revision"] += 1
            return copy.deepcopy(project)
        if route == "/devices" and method == "POST":
            return {"id": uuid.uuid4().hex, **payload}
        if route.startswith("/devices/"):
            return {"id": route.split("/")[2], "status": "active"}
        if route == "/worker/claim":
            return {"job": None, "lease_token": None}
        if route.endswith("/heartbeat"):
            if self.heartbeat_error:
                raise self.heartbeat_error
            return {"job": {"state": "running"}, "expires_at_ms": 1}
        if route.endswith("/events"):
            assert set(payload) == {"device_id", "lease_token", "events"}
            return {"accepted": len(payload["events"])}
        if route.endswith("/complete"):
            assert payload["asset_ids"] and payload["verification"]["verified"]
            return {"state": "completed", "output_asset_ids": payload["asset_ids"]}
        if route.endswith("/fail"):
            return {"state": "failed"}
        raise AssertionError((method, path, payload))

    def http(self, request):
        assert request.headers["authorization"] == "Bearer private-member-token"
        assert request.headers["tenant-id"] == "0"
        if request.method == "POST":
            message = BytesParser(policy=default).parsebytes(("Content-Type: " + request.headers["content-type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + request.content)
            form, content, filename = {}, None, None
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    content, filename = part.get_payload(decode=True), part.get_filename()
                else:
                    form[name] = part.get_payload(decode=True).decode()
            assert form["sha256"] == hashlib.sha256(content).hexdigest()
            asset_id = uuid.uuid4().hex
            item = {"id": asset_id, "filename": filename, "role": form["role"], "project_id": form.get("project_id"),
                    "sha256": form["sha256"], "size_bytes": len(content), "content_url": PREFIX + "/assets/" + asset_id + "/content"}
            self.assets[asset_id], self.blobs[asset_id] = item, content
            return httpx.Response(200, json=item)
        asset_id = request.url.path.split("/")[-2]
        return httpx.Response(200, content=self.blobs[asset_id])


@pytest.fixture
def cloud(tmp_path):
    app = FastAPI()
    facade = WorkspaceStore(tmp_path / "data")
    facade.save_settings({"admin_url": "https://cloud.example", "admin_tenant_id": 0})
    facade.activate("https://cloud.example", 0, "1")
    session = {"base": "https://cloud.example", "tenant_id": 0, "user_id": "1", "access_token": "private-member-token", "refresh_token": "private-refresh-token"}
    server = FakeServer()
    async def me():
        return {"logged_in": bool(remote.saved), "profile": {"user": {"id": "1", "username": "fixture"}}}
    remote = types.SimpleNamespace(saved=session, me=me, session=lambda: remote.saved,
                                   request=server.request, _scope_lock=asyncio.Lock(), _assert_scope_current=lambda: None)
    app.state.store, app.state.remote = facade, remote
    app.state.runtime_manager = RuntimeManager(facade, SkillCatalog(facade))
    register_cloud(app)
    service = app.state.cloud
    service.http_factory = lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(server.http), **kwargs)
    return service, server, app


def test_worker_default_disabled_and_anonymous_cannot_enable(cloud):
    service, server, app = cloud
    with TestClient(app) as client:
        assert client.get("/api/cloud/status").json()["worker"]["enabled"] is False
        assert not any(path.endswith("/claim") for _, path, _ in server.calls)
        service.remote.saved = None
        assert client.put("/api/cloud/worker", json={"enabled": True}).status_code == 401
    assert not any("/devices" in path for _, path, _ in server.calls)


def test_authenticated_private_download_hash_and_no_browser_credentials(cloud):
    service, server, app = cloud
    async def check():
        context = await service.context()
        uploaded = await service.upload(context, "资料.md", b"verified document\n")
        assert uploaded["content_url"].startswith("/api/cloud/assets/")
        raw = server.assets[uploaded["id"]]
        assert await service.download(context, raw) == b"verified document\n"
        raw["sha256"] = "0" * 64
        with pytest.raises(HTTPException, match="哈希"):
            await service.download(context, raw)
        raw["content_url"] = "https://attacker.invalid/private"
        with pytest.raises(HTTPException, match="受控"):
            await service.download(context, raw)
    asyncio.run(check())
    with TestClient(app) as client:
        body = client.get("/api/cloud/assets").text
        assert "private-member-token" not in body and "private-refresh-token" not in body


def test_late_origin_or_member_response_cannot_touch_new_store(cloud):
    service, server, _ = cloud
    async def check():
        context = await service.context()
        original = context.store
        async def delayed(*args, **kwargs):
            service.facade.activate("https://cloud.example", 0, "2")
            return {"items": []}
        service.remote.request = delayed
        with pytest.raises(HTTPException, match="切换"):
            await service.request(context, "GET", "/catalog")
        assert original is not service.facade.current
        with pytest.raises(HTTPException):
            service.save_worker_config(context, enabled=True)
        assert not (service.facade.current.data_dir / "cloud-worker.json").exists()
    asyncio.run(check())


def test_metadata_and_cloud_options_never_transport_local_configuration():
    source = {"password": "secret", "access_token": "secret", "config_path": "C:/private/config.toml", "nested": {"url": "http://localhost:1?token=secret", "text": "Bearer top-secret"},
              "href": "cloud-asset:" + "a" * 32, "ok": "wall dimensions"}
    portable = safe_metadata(source)
    assert "secret" not in json.dumps(portable)
    assert portable["href"] == source["href"] and portable["ok"] == "wall dimensions"
    for item in ({"executable": "cmd.exe"}, {"shell": "anything"}, {"nested": {"url": "https://other.invalid"}}, {"model": "C:/config"}):
        with pytest.raises(HTTPException):
            checked_options(item)


def test_canvas_publish_restore_uploads_owned_images_and_cas(cloud):
    service, server, _ = cloud
    store = service.current_store()
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    attached = store.save_attachment("reference.png", io.BytesIO(image.getvalue()))
    canvas_id = uuid.uuid4().hex
    store.save_canvas(canvas_id, {"name": "可移植方案", "elements": [{"id": "shape", "type": "image", "href": "/api/files/" + attached["id"]}], "viewport": {},
                                  "meta": {"workspace": "C:/private", "authorization": "Bearer private-member-token"}})
    async def check():
        context = await service.context()
        projects = CloudProjects(service)
        result = await projects.publish(context, {"kind": "canvas", "id": canvas_id})
        saved = result["project"]
        assert result["uploaded"] == 1 and saved["asset_ids"]
        text = json.dumps(saved["document"])
        assert "/api/files/" not in text and "private-member-token" not in text and "C:/" not in text
        restored = await projects.restore(context, saved["id"])
        assert restored["id"] != canvas_id
        new = store.get_canvas(restored["id"])
        assert new["elements"][0]["href"].startswith("/api/files/")
        assert new["elements"][0]["href"] != "/api/files/" + attached["id"]
        updated = await projects.publish(context, {"kind": "canvas", "id": canvas_id, "project_id": saved["id"], "base_revision": 1})
        assert updated["project"]["revision"] == 2
        with pytest.raises(HTTPException) as conflict:
            await projects.publish(context, {"kind": "canvas", "id": canvas_id, "project_id": saved["id"], "base_revision": 1})
        assert conflict.value.status_code == 409
    asyncio.run(check())


def test_knowledge_restore_creates_new_collection_and_keeps_existing(cloud, tmp_path):
    service, _, _ = cloud
    store = service.current_store()
    source = tmp_path / "source.md"
    source.write_text("建筑高度为24米。材料采用混凝土。", encoding="utf-8")
    collection = KnowledgeCollections(store).create("原资料库")
    doc = store.knowledge_import(str(source))
    KnowledgeCollections(store).link(collection["id"], doc["id"])
    async def check():
        context = await service.context()
        projects = CloudProjects(service)
        result = await projects.publish(context, {"kind": "knowledge", "id": collection["id"]})
        restored = await projects.restore(context, result["project"]["id"])
        assert restored["id"] != collection["id"]
        assert len(KnowledgeCollections(store).documents(restored["id"])) == 1
        assert len(KnowledgeCollections(store).documents(collection["id"])) == 1
    asyncio.run(check())


@pytest.mark.parametrize("suffix,content,capability", [
    ("png", b"fake image", "quick"), ("obj", b"v 0 0 0\nf 1 2 3\n", "image_to_model"),
    ("glb", b"glTF" + b"\0" * 20, "image_to_model"), ("ply", b"ply\nformat ascii 1.0\nend_header\n", "image_to_model"),
    ("mp4", b"\0\0\0\x0cftypisom", "video"), ("pptx", b"not a zip", "documents"),
])
def test_fake_outputs_never_validate(tmp_path, suffix, content, capability):
    path = tmp_path / ("fake." + suffix)
    path.write_bytes(content)
    with pytest.raises(HTTPException):
        validated_output(path, capability)


def test_real_numeric_obj_and_png_validate(tmp_path):
    path = tmp_path / "mesh.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    assert validated_output(path, "image_to_model")["format"] == "obj"
    image = tmp_path / "image.png"
    Image.new("RGB", (4, 4), "white").save(image)
    assert validated_output(image, "quick")["size"] > 0


def _runtime(app, behavior):
    @app.post("/api/sessions/{session_id}/turn")
    async def local_turn(session_id, body, request=None):
        store = app.state.store.current
        session = store.get_session(session_id)
        async def stream():
            yield json.dumps({"type": "status", "message": "private thought C:/secret private-member-token"}) + "\n"
            if behavior == "wait":
                await asyncio.sleep(30)
            if behavior == "error":
                yield json.dumps({"type": "error", "message": "native error"}) + "\n"
                return
            if behavior == "fake_image":
                path = Path(session["workspace"]) / "fake.png"
                path.write_bytes(b"not a picture")
                artifacts = [{"path": str(path), "source": "native"}]
            else:
                artifacts = []
            store.add_message(session_id, "assistant", "真实本机文稿", completed=True, run_id="native-run-id")
            yield json.dumps({"type": "done", "content": "真实本机文稿", "artifacts": artifacts}) + "\n"
        return StreamingResponse(stream(), media_type="application/x-ndjson")


def _lease(capability="dialogue"):
    return {"job": {"id": uuid.uuid4().hex, "project_id": uuid.uuid4().hex, "capability": capability, "prompt": "生成方案", "options": {}, "input_asset_ids": []},
            "lease_token": "private-lease-token"}


def test_worker_reuses_local_runtime_and_uploads_real_md(cloud):
    service, server, app = cloud
    _runtime(app, "success")
    async def check():
        context = await service.context()
        worker = service.worker
        service.save_worker_config(context, device_id="d" * 32)
        result = await worker.execute(context, "d" * 32, _lease())
        assert result["state"] == "completed"
        complete = next(payload for _, path, payload in server.calls if path.endswith("/complete"))
        assert complete["run_id"] == "native-run-id"
        assert complete["usage"]["input_tokens"] is None
        assert server.blobs[complete["asset_ids"][0]].decode() == "真实本机文稿"
        events = [payload["events"] for _, path, payload in server.calls if path.endswith("/events")]
        event_text = json.dumps(events)
        assert "private thought" not in event_text and "C:/" not in event_text and "private-member-token" not in event_text
    asyncio.run(check())


@pytest.mark.parametrize("behavior,capability", [("error", "dialogue"), ("fake_image", "quick")])
def test_native_error_or_fake_artifact_fails_without_complete(cloud, behavior, capability):
    service, server, app = cloud
    _runtime(app, behavior)
    async def check():
        context = await service.context()
        service.save_worker_config(context, device_id="d" * 32)
        await service.worker.execute(context, "d" * 32, _lease(capability))
        assert any(path.endswith("/fail") for _, path, _ in server.calls)
        assert not any(path.endswith("/complete") for _, path, _ in server.calls)
    asyncio.run(check())


def test_revoked_lease_cancels_runtime_and_never_completes(cloud):
    service, server, app = cloud
    _runtime(app, "wait")
    async def check():
        context = await service.context()
        service.save_worker_config(context, device_id="d" * 32)
        server.heartbeat_error = HTTPException(409, "lease expired")
        service.worker.heartbeat_seconds = .01
        await service.worker.execute(context, "d" * 32, _lease())
        assert not any(path.endswith("/complete") or path.endswith("/fail") for _, path, _ in server.calls)
        assert service.worker.active_session is None
    asyncio.run(check())


def test_disabled_worker_does_not_claim_and_enable_registers_real_device(cloud):
    service, server, _ = cloud
    async def check():
        context = await service.context()
        async def capabilities(_):
            return ["tools"]
        service.worker.capabilities = capabilities
        config = await service.worker.enable(context)
        assert config["enabled"] and len(config["device_id"]) == 32
        await asyncio.sleep(.01)
        await service.worker.stop()
        assert any(path == PREFIX + "/devices" for _, path, _ in server.calls)
        claims = [payload for _, path, payload in server.calls if path.endswith("/claim")]
        assert claims and claims[0]["capabilities"] == ["tools"]
    asyncio.run(check())


def test_concurrent_enable_registers_only_one_loop(cloud):
    service, server, _ = cloud
    async def check():
        context = await service.context()
        async def capabilities(_):
            await asyncio.sleep(.01)
            return ["tools"]
        service.worker.capabilities = capabilities
        one, two = await asyncio.gather(service.worker.enable(context), service.worker.enable(context))
        assert one["device_id"] == two["device_id"]
        assert sum(path == PREFIX + "/devices" for _, path, _ in server.calls) == 1
        await service.worker.stop()
        assert service.worker.task is None
    asyncio.run(check())


def test_original_runtime_subprocess_exports_document_and_releases_reservation(cloud, tmp_path, monkeypatch):
    service, server, app = cloud
    script = tmp_path / "local_stub_runtime.py"
    script.write_text("import sys,json\nfrom pathlib import Path\nprompt=sys.stdin.read()\nassert 'project-evidence-8000mm' in prompt\n"
                      "Path('云端文档.spec.json').write_text(json.dumps({'title':'真实本机交付','sections':[{'title':'布局','content':'建筑设计内容'}],'formats':['json']}),encoding='utf-8')\n"
                      "print(json.dumps({'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'本机已经写出spec'}}))\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "build_command", lambda *a, **kw: [sys.executable, str(script)])
    runtime.register_runtime(app)
    async def check():
        context = await service.context()
        service.save_worker_config(context, device_id="d" * 32)
        lease = _lease("documents")
        lease["job"]["options"] = {"format": "json"}
        server.projects[lease["job"]["project_id"]] = {"id": lease["job"]["project_id"], "type": "documents", "revision": 1, "document": {"design": "project-evidence-8000mm"}, "asset_ids": []}
        result = await service.worker.execute(context, "d" * 32, lease)
        assert result is not None, service.current_store().messages(service.current_store().sessions()[0]["id"])
        assert result["state"] == "completed"
        assert not app.state.runtime_manager.active and not app.state.runtime_manager.resources
        completed = next(payload for _, path, payload in server.calls if path.endswith("/complete"))
        assert completed["run_id"] != "native-run-id" and len(completed["run_id"]) == 32
        document = json.loads(server.blobs[completed["asset_ids"][0]])
        assert document["title"] == "真实本机交付"
    asyncio.run(check())


def test_invalid_server_execution_options_fail_before_local_run(cloud):
    service, server, _ = cloud
    async def check():
        context = await service.context()
        lease = _lease()
        lease["job"]["options"] = {"executable": "external.exe"}
        await service.worker.execute(context, "d" * 32, lease)
        assert not service.current_store().sessions()
        failed = next(payload for _, path, payload in server.calls if path.endswith("/fail"))
        assert failed["error_code"] == "invalid_local_options"
    asyncio.run(check())


def test_normal_embedded_canvas_image_survives_publish_restore(cloud):
    service, server, _ = cloud
    store = service.current_store()
    encoded = io.BytesIO()
    Image.new("RGB", (3, 3), "blue").save(encoded, "BMP")
    canvas_id = uuid.uuid4().hex
    store.save_canvas(canvas_id, {"name": "正常导入图片", "elements": [{"id": "image", "type": "image", "href": "data:image/bmp;base64," + base64.b64encode(encoded.getvalue()).decode()}], "viewport": {}})
    async def check():
        context = await service.context()
        projects = CloudProjects(service)
        result = await projects.publish(context, {"kind": "canvas", "id": canvas_id})
        assert result["uploaded"] == 1
        restored = await projects.restore(context, result["project"]["id"])
        reference = store.get_canvas(restored["id"])["elements"][0]["href"]
        file = store.get_file(reference.split("/")[-1])
        with Image.open(file["path"]) as image:
            assert image.format == "PNG" and image.size == (3, 3)
    asyncio.run(check())


def test_real_interior_scene_restores_with_new_task_source_evidence(cloud):
    service, _, _ = cloud
    store = service.current_store()
    image = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(image, "PNG")
    source = store.save_attachment("controlled-plan.png", io.BytesIO(image.getvalue()))
    interior = InteriorService(store)
    task = interior.create({"file_id": source["id"], "name": "合成室内", "styles": ["scandi"],
                            "scale_policy": "calibrated", "points_px": [[100, 550], [700, 550]], "reference_length_m": 6})
    interior.trace(task["id"], {"rooms": [{"name": "合成房间", "bounds_px": [100, 100, 600, 400]}]})
    async def check():
        context = await service.context()
        projects = CloudProjects(service)
        result = await projects.publish(context, {"kind": "interior", "id": task["id"]})
        restored = await projects.restore(context, result["project"]["id"])
        assert restored["id"] != task["id"]
        new = InteriorService(store)
        manifest, scene = new.manifest(restored["id"]), new.scene(restored["id"])
        assert scene["generation"] == {"taskId": restored["id"], "sourceFileId": manifest["primarySource"]["id"],
                                        "sourceSha256": manifest["primarySource"]["sha256"], "page": 1}
        assert scene["architecture"][0]["sourceEvidence"]["sourceFileId"] == manifest["primarySource"]["id"]
        assert scene["schemes"]["scandi"]["styleAudit"]["screenshots"] == []
        assert new.output_file(restored["id"], "html").is_file()
        assert interior.scene(task["id"])["generation"]["sourceFileId"] == source["id"]
    asyncio.run(check())


def test_401_reuses_original_member_refresh_and_preserves_binding(cloud):
    service, _, _ = cloud
    async def check():
        context = await service.context()
        seen = []
        async def request(method, path, **kwargs):
            seen.append(kwargs["session"]["access_token"])
            if len(seen) == 1:
                raise RemoteError("expired", 401)
            return {"items": []}
        async def me():
            service.remote.saved = {**service.remote.saved, "access_token": "refreshed-member-token"}
            return {"logged_in": True}
        service.remote.request, service.remote.me = request, me
        assert await service.request(context, "GET", "/catalog") == {"items": []}
        assert seen == ["private-member-token", "refreshed-member-token"]
        assert context.member == "1" and context.store is service.current_store()
    asyncio.run(check())


def test_real_app_openapi_has_resolved_request_and_distinct_cloud_operations(tmp_path):
    app = create_app(tmp_path / "actual-app")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        schema = app.openapi()
    assert not any("Duplicate Operation ID" in str(item.message) for item in captured)
    assert "/api/deliverables" in schema["paths"] and "/api/cloud/status" in schema["paths"]
    proxy = schema["paths"]["/api/cloud/{path}"]
    identifiers = [proxy[method]["operationId"] for method in ("get", "post", "put", "delete")]
    assert identifiers == ["architecture_cloud_proxy_" + method for method in ("get", "post", "put", "delete")]
    assert len(set(identifiers)) == 4
    with TestClient(app) as client:
        bootstrap = client.get("/api/bootstrap").json()
        headers = {"X-Workbench-Token": bootstrap["token"]}
        assert client.get("/api/deliverables", headers=headers).status_code == 200
        response = client.get("/api/cloud/catalog", headers=headers)
        assert response.status_code == 401 and "会员" in response.json()["detail"]
        assert client.post("/api/cloud/projects", headers=headers, json={"type": "creative", "name": "test", "document": {}, "asset_ids": []}).status_code == 401
