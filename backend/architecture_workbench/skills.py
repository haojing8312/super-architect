"""Local skill copies and per-turn MCP configuration; never alters CLI globals."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

SERVER_NAMES = {"AutoCADMCP": "autocad", "RhinoMCP": "rhino", "SketchUpMCP": "sketchup",
                "BlenderMCP": "blender", "3dsmaxMCP": "3dsmaxMCP", "GrasshopperMCP": "GrasshopperMCP",
                "PhotoshopMCP": "photoshop", "IllustratorMCP": "illustrator", "RevitMCP": "revit",
                "InDesignMCP": "indesign", "WindowsMCP": "windows"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
RUNTIME_ENV = {"DISABLE_TELEMETRY": "true", "ANONYMIZED_TELEMETRY": "false", "POSTHOG_DISABLED": "1",
               "OFFICECLI_NO_AUTO_INSTALL": "1", "OFFICECLI_SKIP_UPDATE": "1", "OFFICECLI_NO_AUTO_RESIDENT": "1",
               "FASTMCP_CHECK_FOR_UPDATES": "off", "FASTMCP_SHOW_SERVER_BANNER": "false",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}


def resource_skill_root() -> Path:
    return Path(__file__).resolve().parents[2] / "resources" / "skills"


def seed_reference_skills(source: str | Path, target: str | Path | None = None) -> dict:
    """Copy owned reference resources without running installers or replacing edits."""
    source = Path(source).resolve(strict=True)
    target = Path(target or resource_skill_root()).resolve()
    if source == target or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("资源来源与目标目录必须独立")
    copied, skipped, total = 0, 0, 0
    for directory, names, files in os.walk(source, followlinks=False):
        names[:] = [name for name in names if name not in SKIP_DIRS and not (Path(directory) / name).is_symlink()]
        for name in files:
            origin = Path(directory) / name
            if origin.is_symlink() or name.startswith(".env") or origin.suffix.lower() in {".pyc", ".pyo"}:
                skipped += 1
                continue
            destination = target / origin.relative_to(source)
            if destination.exists():
                skipped += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
            copied += 1
            total += origin.stat().st_size
    return {"copied_files": copied, "skipped_files": skipped, "bytes": total, "target": str(target),
            "note": "保留 LICENSE/NOTICE 和宿主运行二进制；未运行依赖安装、更新器或遥测。"}


def professional_python(settings: dict) -> str:
    configured = settings.get("python_executable", "")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ValueError("专业 MCP 的 Python 可执行文件不存在")
        return str(path)
    bundled = resource_skill_root().parent / "python" / "python.exe"
    if bundled.is_file():
        return str(bundled)
    if not getattr(sys, "frozen", False):
        return sys.executable
    detected = shutil.which("python") or shutil.which("python3")
    if not detected or "windowsapps" in detected.lower():
        raise ValueError("请在设置中填写 python_executable，专业 MCP 需要独立 Python 环境")
    return detected


def host_plugin_project_root() -> Path:
    """External integrations sit beside the packaged executable, not in _MEIPASS."""
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def paired_host_presets(settings: dict, python: str, node: str) -> dict:
    """Match the staged MIT plugins; never fall back to incompatible vendor bridges.

    Reconstruct the paired template from current paths/interpreters, rather than
    replaying machine-specific commands in the generated mcp-paired.json.
    Presence here describes a connector, not a licensed/running desktop host.
    """
    project = host_plugin_project_root()
    roots = [project]
    configured = Path(settings.get("skill_source") or "")
    # A packaged client may deliberately use its original product resource copy.
    # Only that explicit resources/skills layout provides an alternate cache;
    # do not scan disks or infer a cache from an arbitrary skill directory.
    if configured.name == "skills" and configured.parent.name == "resources":
        candidate = configured.expanduser().resolve().parent.parent
        if candidate not in roots:
            roots.append(candidate)
    presets = {}
    for root in roots:
        adapter = root / "integrations" / "host-plugins" / "sketchup_adapter.py"
        if "sketchup" not in presets and adapter.is_file():
            presets["sketchup"] = {"command": python, "args": [str(adapter)], "cwd": str(adapter.parent),
                "env": {**RUNTIME_ENV, "SU_MCP_HOST": "127.0.0.1", "SU_MCP_PORT": "9876", "SU_MCP_RUBY_TIMEOUT": "180"},
                "enabled": True}
        directory = root / ".local" / "toolchains" / "plugins" / "prepared" / "revit-2026"
        script = directory / "revit_mcp_plugin" / "Commands" / "RevitMCPCommandSet" / "server" / "build" / "index.js"
        if "revit" in presets or not script.is_file():
            continue
        try:
            catalog = json.loads((root / "integrations" / "host-plugins" / "catalog.json").read_text(encoding="utf-8-sig"))
            package = next(item for item in catalog.get("packages", []) if item.get("id") == "revit-2026")
            marker = json.loads((directory / "architecture-prepared.json").read_text(encoding="utf-8-sig"))
            valid = (re.fullmatch(r"[a-fA-F0-9]{64}", str(package.get("sha256", "")))
                     and marker.get("package") == "revit-2026"
                     and marker.get("artifact_sha256") == package["sha256"]
                     and marker.get("status") == "staged-not-host-tested")
        except (OSError, ValueError, TypeError, AttributeError, StopIteration):
            valid = False
        if valid:
            presets["revit"] = {"command": node, "args": [str(script)], "cwd": str(script.parent.parent),
                                 "env": dict(RUNTIME_ENV), "enabled": True}
    return presets


def professional_presets(settings: dict, root: Path | None = None) -> dict:
    root = root or resource_skill_root()
    try:
        python = professional_python(settings)
    except ValueError:
        python = settings.get("python_executable", "python")
    presets = {}
    launchers = {"autocad": ("AutoCADMCP", "cad_mcp_launcher.py"), "rhino": ("RhinoMCP", "rhinomcp_launcher.py"),
                 "blender": ("BlenderMCP", "blender_mcp_launcher.py"), "3dsmaxMCP": ("3dsmaxMCP", "3dsmaxMCP_launcher.py"),
                 "indesign": ("InDesignMCP", "indesign_mcp_launcher.py")}
    for name, (folder, entry) in launchers.items():
        script = root / folder / entry
        if script.is_file():
            presets[name] = {"command": python, "args": [str(script)], "cwd": str(script.parent), "env": dict(RUNTIME_ENV), "enabled": True}
    windows = root / "WindowsMCP" / "src"
    if windows.is_dir():
        presets["windows"] = {"command": python, "args": [str(windows.parent / "architecture_launcher.py"), "serve"],
            "env": {**RUNTIME_ENV, "PYTHONPATH": str(windows)}, "cwd": str(windows.parent), "enabled": True}
    bundled_node = root.parent / "node" / "node.exe"
    node = settings.get("node_executable") or (str(bundled_node) if bundled_node.is_file() else shutil.which("node")) or "node"
    for name, folder, entry in (("photoshop", "PhotoshopMCP", "dist/index.js"),
                                ("illustrator", "IllustratorMCP", "dist/index.js")):
        script = root / folder / "server" / entry
        if script.is_file():
            presets[name] = {"command": node, "args": [str(script)], "cwd": str(script.parent.parent), "env": dict(RUNTIME_ENV), "enabled": True}
    presets.update(paired_host_presets(settings, python, node))
    if (root / "GrasshopperMCP" / "SKILL.md").is_file():
        presets["GrasshopperMCP"] = {"url": "http://127.0.0.1:26929/mcp", "transport": "streamable_http", "enabled": True}
    office = root / "_runtime" / "office" / "officecli-reference.exe"
    if office.is_file():
        presets["officecli"] = {"command": python, "args": [str(office.parent / "architecture_office_mcp.py")], "env": dict(RUNTIME_ENV), "enabled": True,
                                "skills": ["docx", "xlsx", "pdf", "ppt-master"]}
    return presets


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + os.urandom(5).hex() + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def metadata(content: str) -> dict:
    """Read the supported scalar/list frontmatter fields without executing YAML tags."""
    result = {}
    if not content.startswith("---"):
        return result
    header = content.split("---", 2)
    if len(header) < 3:
        return result
    for line in header[1].splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip().strip("\"'")
        if value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [part.strip().strip("\"'") for part in value[1:-1].split(",") if part.strip()]
        elif value:
            result[key] = value
    return result


def dependencies(meta: dict, folder: str) -> list[str]:
    required = meta.get("requiresMcp", SERVER_NAMES.get(folder, ""))
    if isinstance(required, list):
        return [str(item) for item in required]
    return [part.strip() for part in str(required).split(",") if part.strip()]


def validate_servers(value: dict) -> dict:
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("MCP servers 必须是最多 64 项的对象")
    result = {}
    for name, definition in value.items():
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name == "architecture":
            raise ValueError("MCP 名称无效，architecture 为工作台保留名称")
        if not isinstance(definition, dict):
            raise ValueError("MCP 配置必须是对象")
        item = {key: definition[key] for key in ("command", "args", "env", "url", "cwd", "transport", "enabled", "skills") if key in definition}
        if bool(item.get("command")) == bool(item.get("url")):
            raise ValueError(f"{name} 必须填写 command 或 url 中的一项")
        if "command" in item and (not isinstance(item["command"], str) or "\x00" in item["command"]):
            raise ValueError("MCP command 无效")
        if "url" in item and (not isinstance(item["url"], str) or not item["url"].startswith(("http://", "https://"))):
            raise ValueError("MCP url 仅支持 http/https")
        if not isinstance(item.get("args", []), list) or any(not isinstance(arg, str) or "\x00" in arg for arg in item.get("args", [])):
            raise ValueError("MCP args 必须是字符串列表")
        if not isinstance(item.get("env", {}), dict) or any(not ENV_NAME.fullmatch(str(key)) or not isinstance(val, str) for key, val in item.get("env", {}).items()):
            raise ValueError("MCP env 必须是合法变量名与字符串值")
        if item.get("cwd") and not Path(item["cwd"]).is_dir():
            raise ValueError("MCP cwd 目录不存在")
        item.setdefault("enabled", False)
        item.setdefault("args", [])
        result[name] = item
    return result


class SkillCatalog:
    def __init__(self, store):
        self.store = store

    @property
    def data_dir(self):
        return Path(self.store.data_dir)

    @property
    def import_root(self):
        return self.data_dir / "skills"

    @property
    def override_root(self):
        return self.data_dir / "skill-overrides"

    @property
    def state_path(self):
        return self.data_dir / "skills-state.json"

    @property
    def mcp_path(self):
        return self.data_dir / "mcp.json"

    def _state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}

    def _roots(self):
        configured = self.store.get_settings().get("skill_source", "")
        packaged = resource_skill_root()
        return [self.import_root, Path(configured).expanduser() if configured else packaged, packaged]

    def list(self):
        state, items, seen = self._state(), [], set()
        for root in self._roots():
            if not root.is_dir():
                continue
            for directory in sorted(root.iterdir()):
                if not directory.is_dir() or directory.is_symlink():
                    continue
                key = directory.name
                if not SAFE_NAME.fullmatch(key) or key in seen:
                    continue
                path = directory / "SKILL.md"
                if not path.is_file() or path.is_symlink():
                    continue
                seen.add(key)
                override = self.override_root / key / "SKILL.md"
                active_path = override if override.is_file() else path
                content = active_path.read_text(encoding="utf-8-sig")
                info = metadata(content)
                required = dependencies(info, key)
                items.append({"id": key, "name": info.get("name", key),
                              "description": info.get("description_zh", info.get("description", "")),
                              "path": str(directory.resolve()), "content_path": str(active_path.resolve()),
                              "enabled": bool(state.get(key, {}).get("enabled", True)),
                              "kind": "imported" if root == self.import_root else "bundled",
                              "dependencies": required, "requires_setup": bool(info.get("requiresUserSetup", False)),
                              "exclusive_resources": info.get("exclusiveResources", ""),
                              "status": "需要配置 MCP：" + ", ".join(required) if required else "可读取技能"})
        return items

    def get(self, key):
        for item in self.list():
            if item["id"] == key:
                return {**item, "content": Path(item["content_path"]).read_text(encoding="utf-8-sig")}
        raise KeyError("技能不存在")

    def update(self, key, *, enabled=None, content=None):
        item = self.get(key)
        if content is not None:
            if not content.strip() or len(content) > 500_000:
                raise ValueError("技能内容不能为空或超过 500000 字符")
            path = self.override_root / item["id"] / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if enabled is not None:
            state = self._state()
            state.setdefault(key, {})["enabled"] = bool(enabled)
            atomic_json(self.state_path, state)
        return self.get(key)

    def import_skill(self, supplied: str):
        source = Path(supplied).expanduser().resolve(strict=True)
        source = source.parent if source.is_file() and source.name == "SKILL.md" else source
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            raise ValueError("请选择包含 SKILL.md 的技能目录")
        key = source.name
        if not SAFE_NAME.fullmatch(key):
            raise ValueError("技能目录名仅支持英文、数字、点、横线和下划线")
        self.import_root.mkdir(parents=True, exist_ok=True)
        destination = self.import_root / key
        if destination.exists():
            raise ValueError("同名技能已导入，请编辑现有技能或更改源目录名")
        temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=self.import_root))
        copied, skipped, size = 0, 0, 0
        try:
            for path in source.rglob("*"):
                relative = path.relative_to(source)
                if any(part in SKIP_DIRS for part in relative.parts) or path.is_symlink():
                    skipped += 1
                    continue
                if not path.is_file():
                    continue
                if path.suffix.lower() in {".pyc", ".pyo", ".env"} or path.name.startswith(".env"):
                    skipped += 1
                    continue
                size += path.stat().st_size
                if copied >= 30_000 or size > 300 * 1024 * 1024:
                    raise ValueError("技能超过导入上限（300 MB / 30000 文件），请拆分依赖")
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1
            # Preserve accompanying upstream license when a package only has a repo license.
            if not list(temporary.glob("LICENSE*")):
                for license_file in source.parent.glob("LICENSE*"):
                    if license_file.is_file() and not license_file.is_symlink():
                        shutil.copy2(license_file, temporary / license_file.name)
            atomic_json(temporary / "IMPORT.json", {"source": str(source), "copied_files": copied,
                        "skipped_files": skipped, "bytes": size,
                        "note": "仅复制文件；未运行安装器。环境、宿主插件和二进制依赖需独立配置。"})
            os.replace(temporary, destination)
            return {**self.get(key), "copied_files": copied, "skipped_files": skipped}
        finally:
            if temporary.exists():
                temporary.resolve().relative_to(self.import_root.resolve())
                shutil.rmtree(temporary)

    def servers(self):
        defaults = professional_presets(self.store.get_settings())
        overrides = json.loads(self.mcp_path.read_text(encoding="utf-8")) if self.mcp_path.exists() else {}
        return {**defaults, **overrides}

    def save_servers(self, servers):
        value = validate_servers(servers)
        atomic_json(self.mcp_path, value)
        return value

    def for_session(self, selected: list[str]):
        chosen = set(selected)
        skills = [item for item in self.list() if item["enabled"] and (item["id"] in chosen or item["name"] in chosen)]
        required = {name for item in skills for name in item["dependencies"]}
        servers = {}
        for name, definition in self.servers().items():
            matching = chosen.intersection(definition.get("skills", []))
            if definition.get("enabled") and (name in required or matching):
                servers[name] = {key: value for key, value in definition.items() if key not in {"enabled", "skills"}}
        missing = sorted(required - set(servers))
        return [{**item, "content": self.get(item["id"])["content"]} for item in skills], servers, missing


async def diagnose_server(definition: dict, timeout: float = 20):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    async with asyncio.timeout(timeout):
        async with AsyncExitStack() as stack:
            if definition.get("command"):
                streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=definition["command"], args=definition.get("args", []),
                    env={**os.environ, **definition.get("env", {}), **RUNTIME_ENV},
                    cwd=definition.get("cwd"))))
            elif definition.get("transport") == "sse":
                streams = await stack.enter_async_context(sse_client(definition["url"]))
            else:
                streams = await stack.enter_async_context(streamable_http_client(definition["url"]))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            initialized = await session.initialize()
            tools = await session.list_tools()
            return {"ok": True, "check_scope": "initialize/list_tools", "host_task_validation": False,
                    "server": initialized.serverInfo.model_dump(),
                    "protocol_version": initialized.protocolVersion,
                    "tools": [{"name": tool.name, "description": tool.description or "", "input_schema": tool.inputSchema} for tool in tools.tools]}


class SkillChange(BaseModel):
    enabled: bool | None = None
    content: str | None = None


class ImportSkill(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


class McpConfig(BaseModel):
    servers: dict = Field(default_factory=dict)


def register_skills(app):
    router = APIRouter(prefix="/api")
    catalog = SkillCatalog(app.state.store)
    app.state.skill_catalog = catalog

    @router.get("/skills")
    def list_skills():
        return {"items": catalog.list()}

    @router.post("/skills/import")
    def import_skill(body: ImportSkill):
        try:
            return catalog.import_skill(body.path)
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/skills/{key}")
    def get_skill(key: str):
        try:
            return catalog.get(key)
        except KeyError as error:
            raise HTTPException(404, "技能不存在") from error

    @router.put("/skills/{key}")
    def update_skill(key: str, body: SkillChange):
        try:
            return catalog.update(key, enabled=body.enabled, content=body.content)
        except KeyError as error:
            raise HTTPException(404, "技能不存在") from error
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/mcp")
    def get_mcp():
        return {"servers": catalog.servers()}

    @router.put("/mcp")
    def put_mcp(body: McpConfig):
        try:
            return {"servers": catalog.save_servers(body.servers)}
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.post("/mcp/{name}/test")
    async def test_mcp(name: str):
        definition = catalog.servers().get(name)
        if not definition:
            raise HTTPException(404, "MCP 服务未配置")
        try:
            result = await diagnose_server(definition)
            return {"name": name, **result}
        except Exception as error:
            return {"name": name, "ok": False, "error": "连接超时" if isinstance(error, TimeoutError) else str(error)}

    app.include_router(router)
