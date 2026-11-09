"""Paired plugin defaults must not silently select the incompatible vendor transport."""
import asyncio
import json
from pathlib import Path
import socket
import sys

import pytest

from architecture_workbench import skills
from architecture_workbench.skills import SkillCatalog, professional_presets
from architecture_workbench.store import Store


def product_fixture(tmp_path, monkeypatch, *, prepared=True):
    project = tmp_path / "product"
    root = project / "resources" / "skills"
    for name, server, entry in (("SketchUpMCP", "sketchup", "stdio_bridge.py"),
                                ("RevitMCP", "revit", "server/build/index.js")):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {name}\nrequiresMcp: {server}\n---\nHost required", encoding="utf-8")
        old_entry = directory / entry
        old_entry.parent.mkdir(parents=True, exist_ok=True)
        old_entry.write_text("incompatible vendor transport", encoding="utf-8")
    integration = project / "integrations" / "host-plugins"
    integration.mkdir(parents=True)
    (integration / "sketchup_adapter.py").write_text("fixture adapter", encoding="utf-8")
    sha = "a" * 64
    (integration / "catalog.json").write_text(json.dumps({"packages": [{"id": "revit-2026", "sha256": sha}]}), encoding="utf-8")
    staged = project / ".local" / "toolchains" / "plugins" / "prepared" / "revit-2026"
    entry = staged / "revit_mcp_plugin" / "Commands" / "RevitMCPCommandSet" / "server" / "build" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("fixture upstream paired transport", encoding="utf-8")
    if prepared:
        (staged / "architecture-prepared.json").write_text(json.dumps({"package": "revit-2026", "artifact_sha256": sha,
            "status": "staged-not-host-tested"}), encoding="utf-8")
    monkeypatch.setattr(skills, "host_plugin_project_root", lambda: project)
    monkeypatch.setattr(skills, "resource_skill_root", lambda: root)
    store = Store(tmp_path / "data")
    store.save_settings({"skill_source": str(root), "python_executable": sys.executable, "node_executable": "custom-node"})
    return project, root, staged, SkillCatalog(store)


def test_selected_skills_inject_paired_adapters_and_current_interpreters(tmp_path, monkeypatch):
    project, _, _, catalog = product_fixture(tmp_path, monkeypatch)
    _, definitions, missing = catalog.for_session(["SketchUpMCP", "RevitMCP"])
    assert missing == []
    assert set(definitions) == {"sketchup", "revit"}
    assert Path(definitions["sketchup"]["args"][0]) == project / "integrations" / "host-plugins" / "sketchup_adapter.py"
    assert definitions["sketchup"]["command"] == sys.executable
    assert definitions["sketchup"]["env"]["SU_MCP_HOST"] == "127.0.0.1"
    assert definitions["sketchup"]["env"]["SU_MCP_PORT"] == "9876"
    assert definitions["revit"]["command"] == "custom-node"
    assert "prepared" in Path(definitions["revit"]["args"][0]).parts
    assert Path(definitions["revit"]["cwd"]).is_dir()
    # Catalog/stdio readiness is not evidence of a licensed running host.
    assert all("ready" not in item["status"].lower() for item in catalog.list())


@pytest.mark.parametrize("marker", [None, "invalid-json", {"package": "revit-2026", "artifact_sha256": "b" * 64,
                                                            "status": "staged-not-host-tested"}])
def test_missing_or_untrusted_stage_does_not_fall_back_to_vendor_transport(tmp_path, monkeypatch, marker):
    project, _, staged, catalog = product_fixture(tmp_path, monkeypatch, prepared=False)
    if marker is not None:
        (staged / "architecture-prepared.json").write_text(marker if isinstance(marker, str) else json.dumps(marker), encoding="utf-8")
    (project / "integrations" / "host-plugins" / "sketchup_adapter.py").unlink()
    _, definitions, missing = catalog.for_session(["SketchUpMCP", "RevitMCP"])
    assert definitions == {}
    assert missing == ["revit", "sketchup"]


def test_explicit_user_mcp_choices_override_defaults_without_writing_configuration(tmp_path, monkeypatch):
    _, _, _, catalog = product_fixture(tmp_path, monkeypatch)
    custom = {"sketchup": {"command": sys.executable, "args": ["user-adapter.py"], "enabled": True},
              "revit": {"command": "user-node", "args": ["user-revit.js"], "enabled": False}}
    catalog.save_servers(custom)
    original = catalog.mcp_path.read_bytes()
    _, definitions, missing = catalog.for_session(["SketchUpMCP", "RevitMCP"])
    assert definitions["sketchup"]["args"] == ["user-adapter.py"]
    assert "revit" not in definitions and missing == ["revit"]
    assert catalog.mcp_path.read_bytes() == original


def test_frozen_paths_use_executable_and_explicit_original_resource_cache(tmp_path, monkeypatch):
    original_project, original_skills, _, _ = product_fixture(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    (bundle / "integrations" / "host-plugins").mkdir(parents=True)
    (bundle / "integrations" / "host-plugins" / "sketchup_adapter.py").write_text("bundled adapter", encoding="utf-8")
    # Use the actual path calculation, independent of PyInstaller _MEIPASS.
    monkeypatch.undo()
    monkeypatch.setattr(skills.sys, "frozen", True, raising=False)
    monkeypatch.setattr(skills.sys, "executable", str(bundle / "ArchitectureWorkbench.exe"))
    assert skills.host_plugin_project_root() == bundle
    settings = {"skill_source": str(original_skills), "python_executable": sys._base_executable, "node_executable": "custom-node"}
    definitions = professional_presets(settings, original_skills)
    assert Path(definitions["sketchup"]["args"][0]).is_relative_to(bundle)
    assert Path(definitions["revit"]["args"][0]).is_relative_to(original_project)
    # Generated absolute CLI paths may refer to a different machine: ignore them.
    generated = original_project / ".local" / "toolchains" / "plugins" / "mcp-paired.json"
    generated.write_text('{"servers":{"revit":{"command":"stale-node","args":["missing.js"]}}}', encoding="utf-8")
    assert professional_presets(settings, original_skills)["revit"]["command"] == "custom-node"


def test_actual_sketchup_preset_reports_missing_host_as_tool_error(tmp_path, monkeypatch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(skills, "host_plugin_project_root", lambda: project)
    definition = professional_presets({"python_executable": sys.executable})["sketchup"]
    # Bind an isolated port without listening; no user host or model is touched.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        env = {**definition["env"], "SU_MCP_PORT": str(unavailable.getsockname()[1])}

        async def exercise():
            parameters = StdioServerParameters(command=definition["command"], args=definition["args"], env=env, cwd=definition["cwd"])
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert len(tools.tools) == 8
                    result = await client.call_tool("get_model_info", {})
                    assert result.isError
        asyncio.run(exercise())
