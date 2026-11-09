"""OAuth adapter lifecycle and real local data ownership, no native AI calls."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from architecture_workbench.app import create_app
from architecture_workbench.remote import RemoteClient, RemoteError, member_id
from architecture_workbench.workspaces import WorkspaceStore


BASE = "http://127.0.0.1:48082"


def adapter(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))


def auth_reply(request):
    path = request.url.path
    if path.endswith("/login"):
        account = json.loads(request.content)["username"]
        identifier = "17" if account == "alpha" else "29"
        return httpx.Response(200, json={"code": 0, "data": {"userId": identifier, "accessToken": "access-" + identifier, "refreshToken": "refresh-" + identifier}})
    if path.endswith("/session"):
        identifier = request.headers["authorization"].rsplit("-", 1)[1]
        return httpx.Response(200, json={"code": 0, "data": {"user": {"id": "member:0:" + identifier, "username": "alpha" if identifier == "17" else "beta", "display_name": "测试会员", "phone": "", "accessToken": "MUST_NOT_LEAK"}, "expires_at": "2026-10-05T00:00:00Z"}})
    if path.endswith("/logout"):
        return httpx.Response(200, json={"code": 0, "data": True})
    raise AssertionError(path)


def test_full_api_member_namespaces_preserve_anonymous_and_restore_each_member(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.store.save_settings({"admin_url": BASE})
    adapter(monkeypatch, auth_reply)
    with TestClient(app) as client:
        def token():
            return client.get("/api/bootstrap").json()
        def call(method, path, body=None, capability=None):
            return client.request(method, "/api" + path, json=body, headers={"X-Workbench-Token": capability or token()["token"]})

        anonymous = token()
        legacy = call("POST", "/sessions", {"title": "原来的本机项目"}).json()
        assert call("POST", "/remote/login", {"account": "alpha", "password": "test-password"}).status_code == 200
        alpha = token()
        assert alpha["account_scope"].startswith("member-") and alpha["token"] != anonymous["token"]
        assert call("GET", "/sessions", capability=anonymous["token"]).status_code == 401
        assert call("GET", "/sessions").json()["items"] == []
        a = call("POST", "/sessions", {"title": "A独有的会话"}).json()
        memory = call("POST", "/memories", {"content": "A独有的长期记忆"}).json()
        canvas = call("POST", "/canvas-projects", {"name": "A独有画布"}).json()
        source = tmp_path / "alpha.txt"
        source.write_text("A的私有知识资料", encoding="utf-8")
        knowledge = call("POST", "/knowledge/import", {"path": str(source)}).json()
        attachment = client.post("/api/attachments", headers={"X-Workbench-Token": token()["token"]},
                                 files={"file": ("alpha-private.txt", b"ALPHA_PRIVATE_ATTACHMENT", "text/plain")}).json()
        assert "MUST_NOT_LEAK" not in str(call("GET", "/remote/me").json())
        encrypted = (tmp_path / "member-session.bin").read_bytes()
        assert b"access-17" not in encrypted and b"refresh-17" not in encrypted

        assert call("POST", "/remote/login", {"account": "beta", "password": "test-password"}).status_code == 200
        beta = token()
        assert beta["account_scope"] != alpha["account_scope"]
        for path in ("/sessions/" + a["id"], "/sessions/" + legacy["id"]):
            assert call("GET", path).status_code == 404
        assert call("GET", "/canvas/" + canvas["id"]).json().get("name") != "A独有画布"
        assert call("GET", "/memories").json()["items"] == []
        assert call("GET", "/knowledge").json()["items"] == []
        assert call("GET", "/files/" + attachment["id"]).status_code == 404
        assert client.get(attachment["url"], params={"token": alpha["token"]}).status_code == 401
        b = call("POST", "/sessions", {"title": "B独有的会话"}).json()
        assert call("POST", "/remote/logout", {}).json()["logged_in"] is False
        assert token()["account_scope"] == "anonymous"
        assert [s["id"] for s in call("GET", "/sessions").json()["items"]] == [legacy["id"]]
        assert call("POST", "/remote/login", {"account": "alpha", "password": "test-password"}).status_code == 200
        assert token()["account_scope"] == alpha["account_scope"]
        assert call("GET", "/sessions/" + a["id"]).status_code == 200
        assert call("GET", "/sessions/" + b["id"]).status_code == 404
        assert call("GET", "/memories").json()["items"][0]["id"] == memory["id"]
        assert call("GET", "/knowledge").json()["items"][0]["id"] == knowledge["id"]
        assert call("GET", "/files/" + attachment["id"]).content == b"ALPHA_PRIVATE_ATTACHMENT"
    restarted = create_app(tmp_path)
    assert restarted.state.store.account_scope == alpha["account_scope"]
    assert restarted.state.store.get_session(a["id"])["title"] == "A独有的会话"


def test_request_context_late_results_stay_in_their_original_database(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.activate(BASE, 0, "17")
    with store.bind():
        old_dir = store.data_dir
        store.activate(BASE, 0, "29")
        assert store.data_dir == old_dir
        saved = store.create_session("A请求的迟到结果")
    assert store.sessions() == []
    store.activate(BASE, 0, "17")
    assert store.get_session(saved["id"])["title"] == "A请求的迟到结果"


def test_tenant_and_server_are_part_of_local_ownership(tmp_path):
    store = WorkspaceStore(tmp_path)
    scopes = [store.activate(base, tenant, "17") for base, tenant in ((BASE, 0), (BASE, 1), ("https://independent.example", 0))]
    assert len(set(scopes)) == 3
    assert store.root_store.sessions() == []


def test_concurrent_me_only_refreshes_once_and_preserves_identity(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE, "admin_tenant_id": 7})
    service = RemoteClient(store)
    saved = {"base": BASE, "tenant_id": 7, "user_id": "17", "access_token": "expired", "refresh_token": "test-refresh", "local_session_id": "stable-local-session"}
    service.save(saved)
    scope = store.activate(BASE, 7, "17")
    refreshed = []
    calls = []
    def respond(request):
        calls.append((request.url.path, request.headers["tenant-id"]))
        if request.url.path.endswith("/refresh-token"):
            refreshed.append(True)
            return httpx.Response(200, json={"code": 0, "data": {"userId": 17, "accessToken": "access-17", "refreshToken": "rotated-refresh"}})
        if request.headers.get("authorization") == "Bearer expired":
            return httpx.Response(200, json={"code": 401, "msg": "internal values must remain private"})
        return auth_reply(request)
    adapter(monkeypatch, respond)
    async def concurrent():
        return await asyncio.gather(*(service.me() for _ in range(6)))
    results = asyncio.run(concurrent())
    assert all(result["logged_in"] for result in results)
    assert len(refreshed) == 1 and {tenant for _, tenant in calls} == {"7"}
    assert service.session()["local_session_id"] == "stable-local-session"
    assert store.account_scope == scope


@pytest.mark.parametrize("invalid", [True, 1.0, "1.0", "-1", "", 0, "9223372036854775808"])
def test_invalid_member_identity_is_never_used_for_scope(invalid):
    with pytest.raises(ValueError):
        member_id(invalid)


def test_refresh_identity_mismatch_cannot_overwrite_session(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    service = RemoteClient(store)
    original = {"base": BASE, "tenant_id": 0, "user_id": "17", "access_token": "expired", "refresh_token": "original-refresh"}
    service.save(original)
    def respond(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(401)
        return httpx.Response(200, json={"code": 0, "data": {"userId": 29, "accessToken": "wrong", "refreshToken": "wrong"}})
    adapter(monkeypatch, respond)
    with pytest.raises(ValueError, match="身份不匹配"):
        asyncio.run(service.me())
    assert service.session() == original


def test_expired_session_is_removed_but_member_files_remain(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    service = RemoteClient(store)
    service.save({"base": BASE, "user_id": "17", "access_token": "revoked", "refresh_token": "revoked"})
    store.activate(BASE, 0, "17")
    saved = store.create_session("保留这个项目")
    adapter(monkeypatch, lambda _: httpx.Response(200, json={"code": 401, "msg": "stack trace PRIVATE"}))
    with pytest.raises(RemoteError, match="失效"):
        asyncio.run(service.me())
    assert service.session() is None and store.account_scope == "anonymous"
    store.activate(BASE, 0, "17")
    assert store.get_session(saved["id"])


def test_transport_errors_never_expose_refresh_url(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    service = RemoteClient(store)
    service.save({"base": BASE, "user_id": "17", "access_token": "old", "refresh_token": "PRIVATE-REFRESH"})
    def respond(request):
        if request.url.path.endswith("/session"):
            return httpx.Response(401)
        raise httpx.ConnectError("PRIVATE-REFRESH in query " + str(request.url), request=request)
    adapter(monkeypatch, respond)
    with pytest.raises(RemoteError) as error:
        asyncio.run(service.me())
    assert "PRIVATE-REFRESH" not in str(error.value)
    assert service.session() is not None


def test_busy_runtime_rejects_account_switch_without_contacting_server(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.store.save_settings({"admin_url": BASE})
    app.state.runtime_manager.reserve("owned-active-test")
    adapter(monkeypatch, lambda _: pytest.fail("busy account change contacted the server"))
    with TestClient(app) as client:
        token = client.get("/api/bootstrap").json()["token"]
        response = client.post("/api/remote/login", headers={"X-Workbench-Token": token}, json={"account": "alpha", "password": "test"})
        assert response.status_code == 409
    assert app.state.store.account_scope == "anonymous"


def test_management_address_change_clears_local_auth_without_sending_old_tokens(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.store.save_settings({"admin_url": BASE})
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return auth_reply(request)
    adapter(monkeypatch, respond)
    with TestClient(app) as client:
        def headers():
            return {"X-Workbench-Token": client.get("/api/bootstrap").json()["token"]}
        assert client.post("/api/remote/login", headers=headers(), json={"account": "alpha", "password": "test"}).status_code == 200
        calls.clear()
        previous = headers()
        changed = client.put("/api/settings", headers=previous, json={"admin_url": "https://other-independent.example"})
        assert changed.status_code == 200 and changed.headers["X-Workbench-Account-Scope"] == "anonymous"
        assert app.state.remote.session() is None
        assert not calls
        assert client.get("/api/sessions", headers=previous).status_code == 401
        assert client.get("/api/remote/me", headers=headers()).json()["logged_in"] is False


def test_invalid_settings_never_change_management_binding(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    store.activate(BASE, 0, "17")
    with pytest.raises(ValueError):
        store.save_settings({"admin_url": "https://other.example", "theme": "INVALID"})
    assert store.get_settings()["admin_url"] == BASE


def test_stale_queued_logout_cannot_revoke_even_a_new_login_of_the_same_member(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    store.activate(BASE, 0, "17")
    service = RemoteClient(store)
    original = {"base": BASE, "user_id": "17", "access_token": "NEW_SESSION_ACCESS", "refresh_token": "NEW_SESSION_REFRESH"}
    adapter(monkeypatch, lambda _: pytest.fail("stale logout contacted native member service"))
    async def queued():
        await service._lock.acquire()
        with store.bind():
            task = asyncio.create_task(service.logout())
        await asyncio.sleep(0)
        service.save(original)
        store.activate(BASE, 0, "17", renew=True)
        service._lock.release()
        with pytest.raises(RemoteError, match="工作区已改变"):
            await task
    asyncio.run(queued())
    assert service.session() == original


def test_failed_new_workspace_preparation_preserves_previous_login(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    service = RemoteClient(store)
    original = {"base": BASE, "user_id": "17", "access_token": "access-17", "refresh_token": "refresh-17"}
    service.save(original)
    scope = store.activate(BASE, 0, "17")
    adapter(monkeypatch, auth_reply)
    prepare = store.prepare
    def fail_new(base=None, tenant_id=0, member_id=None):
        if member_id == "29":
            raise ValueError("测试模拟新目录不可写")
        return prepare(base, tenant_id, member_id)
    monkeypatch.setattr(store, "prepare", fail_new)
    with pytest.raises(ValueError, match="不可写"):
        asyncio.run(service.login("beta", "test"))
    assert service.session() == original and store.account_scope == scope


def test_private_skill_and_connection_files_are_member_scoped(tmp_path):
    from architecture_workbench.skills import SkillCatalog
    store = WorkspaceStore(tmp_path)
    catalog = SkillCatalog(store)
    anonymous = catalog.mcp_path
    store.activate(BASE, 0, "17")
    catalog.mcp_path.write_text('{"private_a":{"url":"http://127.0.0.1:1"}}', encoding="utf-8")
    catalog.override_root.mkdir(parents=True)
    private_skill = catalog.override_root / "only-alpha"
    private_skill.mkdir()
    (private_skill / "SKILL.md").write_text("ALPHA_PRIVATE_SKILL", encoding="utf-8")
    first = catalog.mcp_path
    store.activate(BASE, 0, "29")
    assert catalog.mcp_path != first and catalog.mcp_path != anonymous
    assert not catalog.mcp_path.exists()
    assert not (catalog.override_root / "only-alpha" / "SKILL.md").exists()
    store.activate(BASE, 0, "17")
    assert "ALPHA_PRIVATE_SKILL" in (catalog.override_root / "only-alpha" / "SKILL.md").read_text()


def test_member_artifact_snapshot_uses_initialized_schema(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    store.activate(BASE, 0, "17")
    session = store.create_session("真实会员产物")
    path = __import__("pathlib").Path(session["workspace"]) / "output.txt"
    path.write_text("ACTUAL_MEMBER_ARTIFACT", encoding="utf-8")
    value = app.state.artifacts.resolve(session["id"], str(path))
    assert value["id"] and store.get_file(value["id"])


def test_same_member_relogin_and_anonymous_logout_renew_local_capability(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.store.save_settings({"admin_url": BASE})
    adapter(monkeypatch, auth_reply)
    with TestClient(app) as client:
        def login():
            capability = client.get("/api/bootstrap").json()["token"]
            result = client.post("/api/remote/login", headers={"X-Workbench-Token": capability}, json={"account": "alpha", "password": "test"})
            assert result.status_code == 200
            return client.get("/api/bootstrap").json()["token"]
        first, second = login(), login()
        assert first != second
        assert client.post("/api/remote/logout", headers={"X-Workbench-Token": second}).status_code == 200
        anonymous = client.get("/api/bootstrap").json()["token"]
        assert client.post("/api/remote/logout", headers={"X-Workbench-Token": anonymous}).status_code == 200
        assert client.get("/api/bootstrap").json()["token"] != anonymous


def test_failed_candidate_tenant_does_not_lock_previous_member_out(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE, "admin_tenant_id": 0})
    service = RemoteClient(store)
    original = {"base": BASE, "tenant_id": 0, "user_id": "17", "access_token": "access-17", "refresh_token": "refresh-17"}
    service.save(original)
    scope = store.activate(BASE, 0, "17")
    def respond(request):
        assert request.headers["tenant-id"] == "1"
        return httpx.Response(403)
    adapter(monkeypatch, respond)
    with pytest.raises(RemoteError):
        asyncio.run(service.login("beta", "test", 1))
    assert service.session() == original and store.account_scope == scope
    assert store.get_settings()["admin_tenant_id"] == 0


def test_stopped_collaboration_snapshot_does_not_cross_member_switch(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    store = app.state.store
    store.save_settings({"admin_url": BASE})
    adapter(monkeypatch, auth_reply)
    with TestClient(app) as client:
        def headers():
            return {"X-Workbench-Token": client.get("/api/bootstrap").json()["token"]}
        assert client.post("/api/remote/login", headers=headers(), json={"account": "alpha", "password": "test"}).status_code == 200
        canvas = client.post("/api/canvas-projects", headers=headers(), json={"name": "A私有分享", "data": {"elements": [{"id": "private", "type": "text", "text": "ALPHA_SECRET_TEXT", "x": 0, "y": 0, "width": 100, "height": 50}], "viewport": {}}}).json()
        assert client.post("/api/canvas-collaboration/host", headers=headers(), json={"canvas_id": canvas["id"], "bind_host": "127.0.0.1", "port": 0}).status_code == 200
        assert client.post("/api/canvas-collaboration/stop", headers=headers()).status_code == 200
        assert "ALPHA_SECRET_TEXT" in str(client.get("/api/canvas-collaboration/snapshot", headers=headers()).json())
        assert client.post("/api/remote/login", headers=headers(), json={"account": "beta", "password": "test"}).status_code == 200
        snapshot = client.get("/api/canvas-collaboration/snapshot", headers=headers()).json()
        assert snapshot["document"] is None and snapshot["canvas_id"] is None
        assert "ALPHA_SECRET_TEXT" not in str(snapshot)


def test_queued_old_canvas_host_cannot_start_under_next_account(tmp_path):
    from fastapi import HTTPException
    app = create_app(tmp_path)
    store = app.state.store
    store.activate(BASE, 0, "17")
    saved = app.state.canvas_projects.create("A queued private canvas")
    manager = app.state.canvas_collaboration
    async def queue():
        await manager.operation_lock.acquire()
        with store.bind():
            task = asyncio.create_task(manager.start_host(saved["id"], "127.0.0.1", 0))
        await asyncio.sleep(0)
        store.activate(BASE, 0, "29")
        manager.operation_lock.release()
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 409
        assert manager.role == "none" and manager.listener is None
    asyncio.run(queue())


def test_expiry_cleanup_blocks_new_work_until_nonce_renewal(tmp_path, monkeypatch):
    from architecture_workbench.canvas_collaboration import CanvasCollaboration
    from fastapi import HTTPException
    store = WorkspaceStore(tmp_path)
    store.save_settings({"admin_url": BASE})
    store.activate(BASE, 0, "17")
    manager = CanvasCollaboration(store)
    async def lifecycle():
        entered, finish = asyncio.Event(), asyncio.Event()
        async def cleanup():
            entered.set()
            await finish.wait()
        service = RemoteClient(store, on_expired=cleanup)
        service.save({"base": BASE, "user_id": "17", "access_token": "expired", "refresh_token": "expired"})
        task = asyncio.create_task(service.me())
        await entered.wait()
        assert store.closing is True
        with pytest.raises(RemoteError):
            service._assert_scope_current()
        with pytest.raises(HTTPException):
            await manager.start_host("never-bind", "127.0.0.1", 0)
        finish.set()
        with pytest.raises(RemoteError):
            await task
        assert store.closing is False and store.account_scope == "anonymous"
    adapter(monkeypatch, lambda _: httpx.Response(401))
    asyncio.run(lifecycle())
