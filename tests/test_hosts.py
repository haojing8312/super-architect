"""Host API and ownership checks. Never stop the live 9877 validation host."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from architecture_workbench import hosts
from architecture_workbench.store import Store


def controlled_app(tmp_path, monkeypatch):
    root = tmp_path / "product"
    folder = root / "integrations" / "blender"
    folder.mkdir(parents=True)
    executable = root / "blender.exe"; executable.write_bytes(b"owned-test-fixture-not-executed")
    bootstrap = folder / "bootstrap.py"; bootstrap.write_text("# isolated fixture", encoding="utf-8")
    (folder / "installed.json").write_text(json.dumps({"executable": str(executable), "version": "4.5.14"}), encoding="utf-8")
    monkeypatch.setattr(hosts, "project_root", lambda: root)
    monkeypatch.setattr(hosts, "load_blender_helper", lambda _root: None)
    app = FastAPI(); app.state.store = Store(tmp_path / "data")
    hosts.register_hosts(app)
    return app, root, executable, bootstrap


def fake_helper(ready=False, occupied=False, pid=22):
    state = {"ready": ready, "pid": pid, "background": True, "factory_startup": True, "version": "4.5.14", "addon_protocol": 5}
    helper = SimpleNamespace(state=state, occupied=occupied, start_calls=0, stop_calls=0)
    helper.status = lambda: dict(state)
    helper.listening = lambda: helper.occupied
    def stop():
        helper.stop_calls += 1; helper.state["ready"] = False; helper.occupied = False
        return {"stopped": True}
    helper.stop = stop
    return helper


def record(root, executable, bootstrap, pid=22, factory=True):
    arguments = [str(executable), "--background", "--factory-startup" if factory else "--ordinary-user-session", "--python", str(bootstrap)]
    folder = root / ".local" / "blender-host"; folder.mkdir(parents=True, exist_ok=True)
    (folder / "process.json").write_text(json.dumps({"pid": pid, "command": arguments}), encoding="utf-8")
    return arguments


def test_inventory_does_not_promote_prepared_plugins_to_live_hosts(tmp_path, monkeypatch):
    app, root, _exe, _bootstrap = controlled_app(tmp_path, monkeypatch)
    source = root / "integrations" / "host-plugins"; source.mkdir()
    digest = "a" * 64
    (source / "catalog.json").write_text(json.dumps({"packages": [{"id": "rhino", "sha256": digest, "license": "MIT", "tag": "0.2.2"}]}))
    prepared = root / ".local" / "toolchains" / "plugins" / "prepared" / "rhino"
    (prepared / "net8.0").mkdir(parents=True)
    (prepared / "net8.0" / "rhinomcp.rhp").write_bytes(b"prepared fixture")
    (prepared / "architecture-prepared.json").write_text(json.dumps({"artifact_sha256": digest}))
    with TestClient(app) as client:
        response = client.get("/api/hosts")
    assert response.status_code == 200
    value = response.json()
    assert value["local"] and value["project_root"] == str(root)
    assert "ready" in value["asr"] and "sdk_found" in value["sdk"]
    rhino = next(host for host in value["commercial"] if host["id"] == "rhino")
    assert rhino["plugin"]["prepared"] and not rhino["ready"] and not rhino["plugin"]["verified_in_host"]


def test_stop_refuses_same_executable_pid_without_factory_scope(tmp_path, monkeypatch):
    app, root, executable, bootstrap = controlled_app(tmp_path, monkeypatch)
    arguments = record(root, executable, bootstrap, factory=False)
    helper = fake_helper(ready=True, occupied=True)
    app.state.host_manager._helper = helper
    monkeypatch.setattr(hosts, "_actual_process_arguments", lambda _pid: arguments)
    with TestClient(app) as client:
        response = client.post("/api/hosts/blender/stop")
    assert response.status_code == 409 and "不会" in response.json()["detail"]
    assert helper.stop_calls == 0 and not (root / ".local" / "blender-host" / "stop").exists()


def test_recorded_factory_argv_is_insufficient_when_actual_process_differs(tmp_path, monkeypatch):
    app, root, executable, bootstrap = controlled_app(tmp_path, monkeypatch)
    record(root, executable, bootstrap)
    helper = fake_helper(ready=True, occupied=True)
    app.state.host_manager._helper = helper
    monkeypatch.setattr(hosts, "_actual_process_arguments", lambda _pid: [str(executable), "user-project.blend"])
    with TestClient(app) as client:
        assert not client.get("/api/hosts").json()["blender"]["owns_process"]
        assert client.post("/api/hosts/blender/start").status_code == 409
        assert client.post("/api/hosts/blender/stop").status_code == 409
    assert helper.start_calls == helper.stop_calls == 0


def test_stop_when_no_active_host_is_idempotent_and_writes_nothing(tmp_path, monkeypatch):
    app, root, _exe, _bootstrap = controlled_app(tmp_path, monkeypatch)
    helper = fake_helper(ready=False, occupied=False)
    app.state.host_manager._helper = helper
    with TestClient(app) as client:
        response = client.post("/api/hosts/blender/stop")
    assert response.status_code == 200 and response.json()["already_stopped"]
    assert helper.stop_calls == 0 and not (root / ".local" / "blender-host").exists()


def test_start_override_uses_helper_contract_and_verifies_ready(tmp_path, monkeypatch):
    app, root, executable, bootstrap = controlled_app(tmp_path, monkeypatch)
    helper = fake_helper()
    app.state.host_manager._helper = helper
    arguments = [str(executable), "--background", "--factory-startup", "--python", str(bootstrap)]
    monkeypatch.setattr(hosts, "_actual_process_arguments", lambda _pid: arguments)
    def start(executable=None):
        assert executable == str(arguments[0])
        helper.start_calls += 1
        record(root, Path(executable), bootstrap)
        helper.state["ready"] = True; helper.occupied = True
        return dict(helper.state)
    helper.start = start
    with TestClient(app) as client:
        response = client.post("/api/hosts/blender/start")
    assert response.status_code == 200 and response.json()["ready"] and response.json()["owns_process"]
    assert helper.start_calls == 1 and helper.stop_calls == 0


def test_frozen_root_uses_executable_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(hosts.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hosts.sys, "executable", str(tmp_path / "ArchitectureWorkbench.exe"))
    assert hosts.project_root() == tmp_path


def test_live_factory_host_status_and_start_are_read_only_idempotent(tmp_path):
    app = FastAPI(); app.state.store = Store(tmp_path / "data")
    hosts.register_hosts(app)
    before = app.state.host_manager.blender()
    if not before["ready"]: pytest.skip("No current project-owned factory host; test does not launch or stop a real host")
    record_path = Path(before["state_path"]) / "process.json"
    original_record = record_path.read_bytes()
    with TestClient(app) as client:
        inventory = client.get("/api/hosts")
        assert inventory.status_code == 200 and inventory.json()["blender"]["ready"]
        response = client.post("/api/hosts/blender/start")
        assert response.status_code == 200 and response.json()["already_running"]
        assert response.json()["pid"] == before["pid"]
    assert record_path.read_bytes() == original_record
    # Deliberately no stop request: root is using this host for live validation.
