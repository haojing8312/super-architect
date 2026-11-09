from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from architecture_workbench.app import create_app
from architecture_workbench.migration import discover, import_legacy, preview
from architecture_workbench.store import Store


def legacy_tree(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    anonymous = Store(root)
    local_session = anonymous.create_session("匿名区项目")
    anonymous.add_message(local_session["id"], "user", "匿名区需求")
    local_file = anonymous.save_attachment("anonymous.txt", io.BytesIO(b"anonymous"))
    member = Store(root / "members" / ("a" * 32))
    member_session = member.create_session("会员区项目")
    member.add_message(member_session["id"], "user", "会员区需求")
    member_file = member.save_attachment("member.txt", io.BytesIO(b"member"))
    return root, (local_session, local_file), (member_session, member_file)


def test_preview_lists_each_legacy_scope_without_writing(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    before = sorted(path.name for path in root.iterdir() if not path.name.endswith(("-wal", "-shm")))
    result = preview(root)
    assert [scope["key"] for scope in result["scopes"]] == ["local", "member-1"]
    assert result["scopes"][0]["sessions"] == 1 and result["scopes"][1]["messages"] == 1
    after = sorted(path.name for path in root.iterdir() if not path.name.endswith(("-wal", "-shm")))
    assert after == before
    assert [scope.key for scope in discover(root)] == ["local", "member-1"]


def test_import_creates_independent_workspaces_backup_and_preserves_source(tmp_path):
    root, (local_session, local_file), (member_session, member_file) = legacy_tree(tmp_path)
    before = (root / "workbench.sqlite3").read_bytes()
    result = import_legacy(root, tmp_path / "target" / "imports")
    assert len(result["workspaces"]) == 2
    assert (root / "workbench.sqlite3").read_bytes() == before
    assert (root / "attachments" / Path(local_file["path"]).name).is_file()
    assert Path(result["backup"]).is_dir()

    local_workspace, member_workspace = (Path(item["path"]) for item in result["workspaces"])
    assert local_workspace != member_workspace
    local = Store(local_workspace)
    member = Store(member_workspace)
    assert [item["title"] for item in local.sessions()] == ["匿名区项目"]
    assert [item["title"] for item in member.sessions()] == ["会员区项目"]
    assert local.messages(local_session["id"])[0]["content"] == "匿名区需求"
    assert member.messages(member_session["id"])[0]["content"] == "会员区需求"

    local_copy = local.get_file(local_file["id"])
    member_copy = member.get_file(member_file["id"])
    assert Path(local_copy["path"]).read_bytes() == b"anonymous"
    assert Path(member_copy["path"]).read_bytes() == b"member"
    assert Path(local_copy["path"]).is_relative_to(local.data_dir)
    assert Path(member_copy["path"]).is_relative_to(member.data_dir)
    manifest = json.loads((local_workspace / "legacy-import.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "imported" and manifest["source"] == str(root.resolve())
    assert manifest["sessions"] == 1


def test_import_is_idempotent_and_dry_run_writes_nothing(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    target = tmp_path / "target" / "imports"
    dry = import_legacy(root, target, dry_run=True)
    assert dry["workspaces"] and all(item.get("dry_run") for item in dry["workspaces"])
    assert not list(target.rglob("workbench.sqlite3"))
    first = import_legacy(root, target)
    backup_root = target.parent / "backups"
    backups_after_first = len(list(backup_root.glob("legacy-*")))
    second = import_legacy(root, target)
    assert len(first["workspaces"]) == 2
    assert all(item.get("already_imported") for item in second["workspaces"])
    assert len(list(backup_root.glob("legacy-*"))) == backups_after_first == 1


def test_import_refuses_file_rows_pointing_outside_the_legacy_scope(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be imported", encoding="utf-8")
    anonymous = Store(root)
    with anonymous.connection() as db:
        db.execute("INSERT INTO files(id,name,path,type,size,created_at) VALUES (?,?,?,?,?,?)",
                   ("c" * 32, "outside-secret.txt", str(outside), "text/plain", outside.stat().st_size, "2026-01-01T00:00:00+00:00"))
    result = import_legacy(root, tmp_path / "target" / "imports")
    imported = Path(result["workspaces"][0]["path"])
    copied = {path.read_bytes() for path in (imported / "attachments").iterdir()}
    assert copied == {b"anonymous"}
    assert result["workspaces"][0]["skipped"]["files"] == 1


def test_legacy_import_api_requires_confirmation_and_scan(client_env, tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    client = client_env
    scan = client.post("/api/local/legacy/scan", json={"path": str(root)})
    assert scan.status_code == 200 and len(scan.json()["scopes"]) == 2
    without = client.post("/api/local/legacy/import", json={"path": str(root)})
    assert without.status_code == 422 and "确认" in without.json()["detail"]
    imported = client.post("/api/local/legacy/import", json={"path": str(root), "confirm": True})
    assert imported.status_code == 200
    workspaces = imported.json()["workspaces"]
    assert len(workspaces) == 2
    history = client.get("/api/local/imports").json()["items"]
    assert len(history) == 2
    assert all(Path(item["path"]).is_dir() for item in history)
    assert client.post("/api/local/legacy/scan", json={"path": str(tmp_path / "missing")}).status_code == 422


@pytest.fixture
def client_env(tmp_path):
    application = create_app(tmp_path / "current")
    with TestClient(application) as client:
        client.headers["X-Workbench-Token"] = client.get("/api/bootstrap").json()["token"]
        yield client
