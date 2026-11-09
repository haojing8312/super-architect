from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
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


def test_import_refuses_a_target_inside_the_legacy_source(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    before = sorted(path.name for path in root.iterdir())
    with pytest.raises(ValueError, match="旧数据目录内部"):
        import_legacy(root, root / "imports")
    with pytest.raises(ValueError, match="旧数据目录内部"):
        import_legacy(root, root)
    assert sorted(path.name for path in root.iterdir()) == before
    assert not (root / "backups").exists()


def test_dry_run_does_not_create_sidecars_in_a_wal_source(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    assert not (root / "workbench.sqlite3-wal").exists()
    assert not (root / "workbench.sqlite3-shm").exists()
    result = import_legacy(root, tmp_path / "target" / "imports", dry_run=True)
    assert [item["sessions"] for item in result["workspaces"]] == [1, 1]
    assert not (root / "workbench.sqlite3-wal").exists()
    assert not (root / "workbench.sqlite3-shm").exists()


def test_failed_import_removes_partial_destination_but_keeps_backup(tmp_path, monkeypatch):
    root, _, _ = legacy_tree(tmp_path)
    target = tmp_path / "target" / "imports"

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr("architecture_workbench.migration._import_scope", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        import_legacy(root, target)
    assert target.is_dir() and list(target.iterdir()) == []
    backups = list((tmp_path / "target" / "backups").glob("legacy-*"))
    assert backups and (backups[0] / "local" / "workbench.sqlite3").is_file()


def test_imported_sessions_use_a_workspace_inside_the_new_data_dir(tmp_path):
    root, (local_session, _), _ = legacy_tree(tmp_path)
    outside = tmp_path / "outside-workspace"
    outside.mkdir()
    with Store(root).connection() as db:
        db.execute("UPDATE sessions SET workspace=? WHERE id=?", (str(outside), local_session["id"]))
    result = import_legacy(root, tmp_path / "target" / "imports")
    for item in result["workspaces"]:
        destination = Path(item["path"]).resolve()
        store = Store(destination)
        for session in store.sessions():
            workspace = Path(session["workspace"]).resolve()
            assert workspace.is_dir()
            assert workspace.is_relative_to(destination)
            assert not workspace.is_relative_to(root.resolve())


def test_import_sanitizes_report_ids_instead_of_creating_paths_outside_reports(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    report_file = root / "reports" / "r.html"
    report_file.parent.mkdir(exist_ok=True)
    report_file.write_text("<html>r</html>", encoding="utf-8")
    with Store(root).connection() as db:
        db.execute("INSERT INTO reports(id,title,path,asset_count,size,created_at) VALUES (?,?,?,?,?,?)",
                   ("../../escape", "r", str(report_file), 0, report_file.stat().st_size,
                    "2026-01-01T00:00:00+00:00"))
    result = import_legacy(root, tmp_path / "target" / "imports")
    destination = Path(result["workspaces"][0]["path"])
    assert not (destination.parent / "escape").exists()
    children = [path for path in (destination / "reports").iterdir() if path.is_dir()]
    assert len(children) == 1
    assert all("\n" not in child.name and "/" not in child.name and "\\" not in child.name
               for child in children)
    assert (children[0] / "r.html").read_text(encoding="utf-8") == "<html>r</html>"


def test_discover_does_not_follow_windows_junction_members(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    root, _, _ = legacy_tree(tmp_path)
    outside = tmp_path / "outside-member"
    outside.mkdir()
    Store(outside).create_session("外部会话")
    junction = root / "members" / "junction"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                             capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip("cannot create junction: " + created.stderr.strip())
    assert [scope.key for scope in discover(root)] == ["local", "member-1"]
    assert [scope["key"] for scope in preview(root)["scopes"]] == ["local", "member-1"]


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


def test_preview_and_import_preserve_committed_wal_without_touching_source(tmp_path):
    import hashlib
    root, _, _ = legacy_tree(tmp_path)
    database = root / 'workbench.sqlite3'
    with sqlite3.connect(database) as writer:
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE sessions SET title='最新WAL标题'")
        writer.commit()
        def snapshot():
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('workbench.sqlite3*')}
        before = snapshot()
        assert '-wal' in ' '.join(before)
        assert preview(root)['scopes'][0]['sessions'] == 1
        result = import_legacy(root, tmp_path / 'destination' / 'imports')
        imported = Store(Path(result['workspaces'][0]['path']))
        assert imported.sessions()[0]['title'] == '最新WAL标题'
        assert snapshot() == before
        writer.execute("UPDATE sessions SET title='再次提交'")
        writer.commit()
        second = import_legacy(root, tmp_path / 'destination' / 'imports')
        assert second['workspaces'][0]['path'] != result['workspaces'][0]['path']
        assert Store(Path(second['workspaces'][0]['path'])).sessions()[0]['title'] == '再次提交'


def test_member_only_source_still_rejects_nested_destination(tmp_path):
    source = tmp_path / 'old'
    Store(source / 'members' / 'one').create_session('旧工作区')
    with pytest.raises(ValueError, match='目标数据目录'):
        import_legacy(source, source / 'new')
    assert not (source / 'new').exists()


def test_import_rejects_path_like_session_identifiers(tmp_path):
    source = tmp_path / 'old'
    store = Store(source)
    session = store.create_session('不可信标识')
    with store.connection() as db:
        db.execute('UPDATE sessions SET id=? WHERE id=?', ('../../escape', session['id']))
    with pytest.raises(ValueError, match='会话标识'):
        import_legacy(source, tmp_path / 'target' / 'imports')


def test_report_assets_and_workspace_outputs_survive_import(tmp_path):
    root, _, _ = legacy_tree(tmp_path)
    original = Store(root)
    workspace = root / 'workspaces' / 'default'
    (workspace / 'drawing.txt').write_text('设计成果', encoding='utf-8')
    report = root / 'reports' / 'sample'
    report.mkdir(parents=True)
    (report / 'assets').mkdir()
    (report / 'assets' / 'image.png').write_bytes(b'local-image')
    html = report / 'index.html'
    html.write_text('<img src="assets/image.png">', encoding='utf-8')
    with original.connection() as db:
        db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?)', ('sample', '报告', str(html), 1, html.stat().st_size, '2026-01-01'))
    result = import_legacy(root, tmp_path / 'target' / 'imports')
    target = Path(result['workspaces'][0]['path'])
    assert (target / 'workspaces/default/drawing.txt').read_text(encoding='utf-8') == '设计成果'
    assert (target / 'reports/sample/assets/image.png').read_bytes() == b'local-image'
