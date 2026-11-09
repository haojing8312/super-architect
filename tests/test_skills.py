import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from architecture_workbench.skills import SkillCatalog, diagnose_server, metadata, validate_servers, professional_presets, RUNTIME_ENV
from architecture_workbench.stdio import workspace_path
from architecture_workbench.store import Store
from architecture_workbench.runtime import native_mcp


def test_import_preserves_license_and_edit_does_not_modify_source(tmp_path):
    source = tmp_path / "vendor" / "AutoCADMCP"
    source.mkdir(parents=True)
    original = "---\nname: AutoCADMCP\ndescription: CAD\nmetadata:\n  requiresMcp: autocad\n---\n# Vendor skill"
    (source / "SKILL.md").write_text(original, encoding="utf-8")
    (source.parent / "LICENSE").write_text("upstream license", encoding="utf-8")
    (source / ".env").write_text("secret", encoding="utf-8")
    (source / "runtime.exe").write_bytes(b"not executable")
    scripts = source / "scripts"
    scripts.mkdir()
    (scripts / "tool.py").write_text("raise RuntimeError('must never run while importing')", encoding="utf-8")
    store = Store(tmp_path / "data")
    catalog = SkillCatalog(store)
    result = catalog.import_skill(str(source))
    destination = Path(result["path"])
    assert (destination / "LICENSE").read_text() == "upstream license"
    assert not (destination / ".env").exists()
    assert (destination / "runtime.exe").read_bytes() == b"not executable"
    assert (destination / "scripts" / "tool.py").exists()
    catalog.update("AutoCADMCP", enabled=False, content="---\nname: Updated\n---\nupdated")
    assert catalog.get("AutoCADMCP")["enabled"] is False
    assert catalog.get("AutoCADMCP")["name"] == "Updated"
    assert (source / "SKILL.md").read_text(encoding="utf-8") == original
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == original
    with pytest.raises(ValueError, match="同名"):
        catalog.import_skill(str(source))


def test_mcp_injection_is_lazy_and_disabled_skills_are_respected(tmp_path):
    store = Store(tmp_path / "data")
    catalog = SkillCatalog(store)
    for name, server in (("AutoCADMCP", "autocad"), ("RhinoMCP", "rhino")):
        source = tmp_path / name
        source.mkdir()
        (source / "SKILL.md").write_text(f"---\nname: {name}\nmetadata:\n  requiresMcp: {server}\n---\nUse MCP", encoding="utf-8")
        catalog.import_skill(str(source))
    catalog.save_servers({"autocad": {"command": sys.executable, "args": [], "enabled": True},
                          "rhino": {"command": sys.executable, "args": [], "enabled": True}})
    skills, servers, missing = catalog.for_session(["AutoCADMCP"])
    assert [skill["id"] for skill in skills] == ["AutoCADMCP"]
    assert list(servers) == ["autocad"]
    assert missing == []
    assert catalog.for_session([])[1] == {}
    catalog.update("AutoCADMCP", enabled=False)
    assert catalog.for_session(["AutoCADMCP"])[1] == {}
    config = catalog.servers()
    config["rhino"]["enabled"] = False
    catalog.save_servers(config)
    assert catalog.for_session(["RhinoMCP"])[2] == ["rhino"]


def test_mcp_config_validation_and_supported_metadata():
    value = metadata("---\nname: CAD\nmetadata:\n  requiresMcp: [autocad, rhino]\n  requiresUserSetup: true\n---\ntext")
    assert value["requiresMcp"] == ["autocad", "rhino"]
    assert value["requiresUserSetup"] is True
    with pytest.raises(ValueError):
        validate_servers({"architecture": {"command": "python"}})
    with pytest.raises(ValueError):
        validate_servers({"external": {"command": "python", "url": "https://example.org"}})
    with pytest.raises(ValueError):
        validate_servers({"external": {"url": "file:///etc/passwd"}})


def test_office_preset_avoids_unguarded_vendor_mcp_and_windows_auto_installer(tmp_path):
    office = tmp_path / "_runtime" / "office"
    office.mkdir(parents=True)
    (office / "officecli-reference.exe").write_bytes(b"fixture")
    (tmp_path / "WindowsMCP" / "src").mkdir(parents=True)
    presets = professional_presets({}, tmp_path)
    assert presets["officecli"]["args"][0].endswith("architecture_office_mcp.py")
    assert "mcp" not in presets["officecli"]["args"]
    assert presets["windows"]["args"][0].endswith("architecture_launcher.py")
    assert all(RUNTIME_ENV[key] == "1" for key in ("OFFICECLI_SKIP_UPDATE", "OFFICECLI_NO_AUTO_INSTALL", "OFFICECLI_NO_AUTO_RESIDENT"))


def test_workspace_escape_and_symlink_are_rejected(tmp_path):
    store = Store(tmp_path / "data")
    session = store.create_session("Scoped")
    assert workspace_path(store, session["id"], "output/result.txt").is_relative_to(Path(session["workspace"]))
    with pytest.raises(ValueError):
        workspace_path(store, session["id"], "../../outside.txt")
    with pytest.raises(ValueError):
        workspace_path(store, session["id"], str(tmp_path / "outside.txt"))
    with pytest.raises(ValueError):
        workspace_path(store, session["id"], ".")
    root = Path(session["workspace"])
    try:
        (root / "link").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        return  # Windows developer mode may not grant symlink creation.
    with pytest.raises(ValueError):
        workspace_path(store, session["id"], "link/outside.txt")


def test_real_workbench_mcp_initialize_list_tools_and_scoped_writes(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    store = Store(tmp_path / "data")
    session = store.create_session("MCP protocol")
    definition = native_mcp(store.data_dir, session["id"])
    result = asyncio.run(diagnose_server(definition))
    assert result["ok"]
    names = {tool["name"] for tool in result["tools"]}
    assert {"memory_save", "knowledge_query", "todo_save", "canvas_save", "file_read", "file_write"} <= names

    async def exercise():
        parameters = StdioServerParameters(command=definition["command"], args=definition["args"], env={**os.environ, **definition["env"]})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                write = await client.call_tool("file_write", {"path": "protocol.txt", "content": "真实协议"})
                assert not write.isError
                rejected = await client.call_tool("file_write", {"path": str(tmp_path / "outside.txt"), "content": "blocked"})
                assert rejected.isError
                saved = await client.call_tool("memory_save", {"content": "Project units are mm"})
                assert not saved.isError
                todos = await client.call_tool("todo_save", {"items": [{"title": "Model", "status": "pending"}]})
                assert not todos.isError
                canvas = await client.call_tool("canvas_save", {"elements": [{"id": "rect", "type": "rect", "x": 0, "y": 0}]})
                assert not canvas.isError
                script = await client.call_tool("file_write", {"path": "build.py", "content": "from pathlib import Path\nPath('artifact.txt').write_text('verified',encoding='utf-8')\nprint('SCRIPT_OK')\n"})
                assert not script.isError
                executed = await client.call_tool("run_python_script", {"path": "build.py", "args": []})
                assert not executed.isError
                assert "SCRIPT_OK" in str(executed)
                outside = await client.call_tool("run_python_script", {"path": str(tmp_path / "not-selected.py")})
                assert outside.isError
                relative_escape = await client.call_tool("run_python_script", {"path": "build.py", "args": ["--output=../../outside.txt"]})
                assert relative_escape.isError
                presented = await client.call_tool("present_options", {"question": "What units?", "options": ["mm", "m"]})
                assert not presented.isError
    asyncio.run(exercise())
    assert (Path(session["workspace"]) / "protocol.txt").read_text(encoding="utf-8") == "真实协议"
    assert not (tmp_path / "outside.txt").exists()
    assert store.memories()[0]["content"] == "Project units are mm"
    assert store.get_todos(session["id"])[0]["title"] == "Model"
    assert store.get_canvas(session["id"])["elements"][0]["id"] == "rect"
    assert (Path(session["workspace"]) / "artifact.txt").read_text() == "verified"
    from architecture_workbench.interactions import InteractionStore
    assert InteractionStore(Store(store.data_dir)).questions(session["id"])[0]["status"] == "pending"
