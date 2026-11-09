from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from architecture_workbench.app import create_app
from architecture_workbench.local import optional_assets
from architecture_workbench.store import Store, default_data_dir


@pytest.fixture
def client(tmp_path):
    application = create_app(tmp_path / "data")
    with TestClient(application) as test_client:
        test_client.headers["X-Workbench-Token"] = test_client.get("/api/bootstrap").json()["token"]
        yield test_client


def test_no_account_startup_and_retired_endpoints_are_gone(client):
    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["mode"] == "local" and bootstrap["product"] == "architecture-workbench"
    assert client.get("/api/health").json()["mode"] == "local"
    for path in ("/api/remote/me", "/api/remote/login", "/api/cloud/status", "/api/update/status"):
        assert client.get(path).status_code == 404, path


def test_settings_are_saved_directly_and_account_keys_are_rejected(client):
    saved = client.put("/api/settings", json={"theme": "dark", "provider": "codebuddy"})
    assert saved.status_code == 200
    assert saved.json()["theme"] == "dark" and saved.json()["provider"] == "codebuddy"
    assert "X-Workbench-Account-Scope" not in saved.headers
    for key in ("admin_url", "admin_tenant_id", "release_trust"):
        rejected = client.put("/api/settings", json={key: "retired"})
        assert rejected.status_code == 422 and "退役" in rejected.json()["detail"]


def test_local_info_reports_data_workspace_and_optional_components(client, tmp_path):
    info = client.get("/api/local/info").json()
    assert info["mode"] == "local" and info["data_dir"] == str((tmp_path / "data").resolve())
    assert Path(info["workspace"]).is_dir()
    assert info["optional"]["cad_viewer"]["available"] is False
    assert info["optional"]["report_editor"]["available"] is False
    assert set(optional_assets()) == {"cad_viewer", "report_editor"}
    assert client.get("/api/local/imports").json() == {"items": []}


def test_core_local_workflow_survives_restart(tmp_path):
    data = tmp_path / "data"
    with TestClient(create_app(data)) as client:
        client.headers["X-Workbench-Token"] = client.get("/api/bootstrap").json()["token"]
        session = client.post("/api/sessions", json={"title": "本地项目"}).json()
        attachment = client.post("/api/attachments", files={"file": ("图纸.txt", b"local", "text/plain")}).json()
        assert client.get(attachment["url"]).content == b"local"
        assert client.post(f"/api/sessions/{session['id']}/cancel", json={}).json()["cancelled"] is False
    reopened = Store(data)
    assert reopened.get_session(session["id"])["title"] == "本地项目"
    assert reopened.get_file(attachment["id"])["name"] == "图纸.txt"


def test_data_directory_can_be_selected_by_environment(monkeypatch, tmp_path):
    target = tmp_path / "selected"
    monkeypatch.setenv("ARCHITECTURE_DATA_DIR", str(target))
    assert default_data_dir() == target.resolve()
    with TestClient(create_app()) as client:
        token = client.get("/api/bootstrap").json()["token"]
        info = client.get("/api/local/info", headers={"X-Workbench-Token": token}).json()
        assert info["data_dir"] == str(target.resolve())
        assert target.is_dir()


def test_retired_account_settings_are_removed_from_existing_local_data(tmp_path):
    store = Store(tmp_path / "data")
    with store.connection() as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('admin_url','\"https://old.invalid\"')")
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('release_trust','{}')")
    reopened = Store(tmp_path / "data")
    settings = reopened.get_settings()
    assert "admin_url" not in settings and "release_trust" not in settings
    with pytest.raises(ValueError, match="退役"):
        reopened.save_settings({"admin_url": "https://old.invalid"})


def test_local_info_requires_the_process_capability(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as client:
        assert client.get("/api/local/info").status_code == 401
