"""Local host inventory and safe control of the project's factory Blender host.

Commercial exe/plugin presence is not a live MCP connection claim. Blender
ownership requires the recorded isolated argv AND the actual process argv;
the controller never terminates a PID or writes a stop marker for another host.
"""
from __future__ import annotations

import base64
import ctypes
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, HTTPException

from .geometry import find_sdk
from .speech import asr_status
from .skills import professional_python


def project_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def read_json(path: Path) -> dict:
    try:
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024: return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError): return {}


def executable_path(value, expected: str) -> Path | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value: return None
    try:
        path = Path(os.path.expandvars(value.strip().strip('"'))).expanduser()
        if path.is_file() and path.name.casefold() == expected.casefold(): return path.resolve()
    except (OSError, ValueError): pass
    return None


def previous_project(metadata: dict) -> Path | None:
    """Infer only the previously recorded portable tree; never search a drive."""
    recorded = metadata.get("executable")
    try: value = Path(recorded).expanduser().resolve() if isinstance(recorded, str) and recorded else None
    except (OSError, ValueError): value = None
    if value and value.name.casefold() != "blender.exe": value = None
    if value and len(value.parents) > 4:
        root = value.parents[4]
        if value.is_relative_to(root / ".local" / "toolchains" / "blender") and (root / "integrations" / "blender" / "installed.json").is_file():
            return root
    return None


def _actual_process_arguments(pid: int) -> list[str] | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2 ** 32: return None
    if os.name != "nt":
        try: return [value.decode() for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if value]
        except (OSError, UnicodeError): return None
    powershell = shutil.which("powershell.exe")
    if not powershell: return None
    script = f"[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; if($p){{[Console]::Write($p.CommandLine)}}"
    try:
        process = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode("ascii")],
                                 capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        command = process.stdout.decode("utf-8-sig").strip()
        if process.returncode or not command or len(command) > 32768: return None
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        count = ctypes.c_int(); values = shell.CommandLineToArgvW(command, ctypes.byref(count))
        if not values: return None
        try: return [values[index] for index in range(count.value)] if 1 <= count.value <= 128 else None
        finally: kernel.LocalFree(ctypes.cast(values, ctypes.c_void_p))
    except (OSError, ValueError, subprocess.TimeoutExpired): return None


def load_blender_helper(root: Path):
    path = root / "integrations" / "blender" / "host.py"
    if not path.is_file(): return None
    spec = importlib.util.spec_from_file_location("architecture_factory_blender_host", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROJECT = root
    module.STATE = root / ".local" / "blender-host"
    return module


class HostManager:
    def __init__(self, store, root: Path | None = None):
        self.store = store
        self.root = (root or project_root()).resolve()
        self._helper = load_blender_helper(self.root)
        self._lock = threading.RLock()
        self._argv_cache = {}
        self._version_cache = {}

    def blender_metadata(self):
        path = self.root / "integrations" / "blender" / "installed.json"
        metadata = read_json(path)
        settings = self.store.get_settings()
        configured = executable_path(settings.get("blender_executable"), "blender.exe")
        recorded = executable_path(metadata.get("executable"), "blender.exe")
        old_root = previous_project(metadata)
        if not recorded and old_root:
            recorded = executable_path(read_json(old_root / "integrations" / "blender" / "installed.json").get("executable"), "blender.exe")
        return metadata, configured or recorded, path, old_root

    def version(self, path: Path | None, metadata: dict):
        if path is None: return ""
        if path == executable_path(metadata.get("executable"), "blender.exe") and metadata.get("version"):
            return str(metadata["version"])
        try:
            stat = path.stat(); key = (str(path), stat.st_mtime_ns, stat.st_size)
            if key in self._version_cache: return self._version_cache[key]
            result = subprocess.run([str(path), "--version"], capture_output=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            text = result.stdout.decode("utf-8", errors="replace")[:4096]
            match = re.search(r"^Blender\s+(\d+\.\d+(?:\.\d+)?)", text, re.MULTILINE)
            version = match.group(1) if result.returncode == 0 and match else ""
            self._version_cache[key] = version
            return version
        except (OSError, subprocess.TimeoutExpired): return ""

    def owns_process(self, pid: int) -> bool:
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2 ** 32: return False
        record = read_json(self.root / ".local" / "blender-host" / "process.json")
        arguments = record.get("command")
        if record.get("pid") != pid or not isinstance(arguments, list) or len(arguments) != 5: return False
        expected = ["--background", "--factory-startup", "--python"]
        if arguments[1:4] != expected: return False
        executable = executable_path(arguments[0], "blender.exe")
        if executable is None: return False
        bootstrap = self.root / "integrations" / "blender" / "bootstrap.py"
        try:
            if Path(arguments[4]).resolve() != bootstrap.resolve() or not bootstrap.is_file(): return False
        except (TypeError, OSError, ValueError): return False
        key = (pid, tuple(arguments)); cached = self._argv_cache.get(key)
        if cached and time.monotonic() - cached[0] < 2: actual = cached[1]
        else:
            actual = _actual_process_arguments(pid)
            self._argv_cache = {key: (time.monotonic(), actual)}
        if not actual or len(actual) != 5 or actual[1:4] != expected: return False
        try:
            return Path(actual[0]).resolve() == executable and Path(actual[4]).resolve() == bootstrap.resolve()
        except (OSError, ValueError): return False

    def blender(self):
        with self._lock:
            metadata, executable, installed_file, _old_root = self.blender_metadata()
            current, error, occupied = {"ready": False}, "", False
            if self._helper:
                try:
                    current = self._helper.status()
                    occupied = bool(self._helper.listening())
                except (OSError, ValueError, KeyError) as exception: error = str(exception)
            else: error = "未找到本产品的隔离 Blender 宿主脚本"
            pid = current.get("pid", 0)
            owned = self.owns_process(pid) if pid else False
            ready = bool(current.get("ready") and owned and current.get("background") and current.get("factory_startup"))
            record = read_json(self.root / ".local" / "blender-host" / "process.json")
            recorded_arguments = record.get("command", [])
            running = executable_path(recorded_arguments[0], "blender.exe") if owned and isinstance(recorded_arguments, list) and recorded_arguments else None
            effective_executable = running if ready and running else executable
            if occupied and not ready:
                error = "9877 端口被未验证为本项目隔离进程的宿主占用；不会退出或替换它"
            elif not executable: error = "未检测到 Blender 可执行文件；可填写 blender_executable 或运行本产品安装工具"
            version = current.get("version") if ready else self.version(executable, metadata)
            return {"installed": effective_executable is not None, "executable": str(effective_executable) if effective_executable else "", "version": version or "",
                    "configured_executable": str(executable) if executable else "", "running_executable": str(running) if running else "",
                    "selection_change_pending": bool(ready and running and executable and running != executable),
                    "ready": ready, "owns_process": owned, "pid": pid if owned else None, "port": 9877,
                    "port_occupied": occupied, "mode": "project-owned-background-factory", "background": bool(current.get("background")),
                    "factory_startup": bool(current.get("factory_startup")), "addon_protocol": current.get("addon_protocol") if ready else None,
                    "helper_available": self._helper is not None, "installed_config": str(installed_file),
                    "bootstrap_path": str(self.root / "integrations" / "blender" / "bootstrap.py"),
                    "state_path": str(self.root / ".local" / "blender-host"), "official_url": "https://www.blender.org/download/",
                    "error": error, "connection_check": "owned process + factory argv + loopback listener; no geometry or LLM task executed"}

    def start_blender(self):
        with self._lock:
            current = self.blender()
            if current["ready"]: return {**current, "already_running": True}
            if current["port_occupied"]: raise HTTPException(409, current["error"])
            if not self._helper or not current["installed"]: raise HTTPException(503, current["error"])
            if not current["version"]: raise HTTPException(422, "无法确认该程序为 Blender；请检查可执行文件路径")
            try:
                selected = current["executable"]
                if "executable" in inspect.signature(self._helper.start).parameters:
                    self._helper.start(executable=selected)
                else:
                    metadata = read_json(Path(current["installed_config"]))
                    if executable_path(metadata.get("executable"), "blender.exe") != Path(selected):
                        raise ValueError("隔离宿主脚本不支持此路径覆盖，请更新本产品脚本")
                    self._helper.start()
            except (RuntimeError, OSError, ValueError, KeyError) as exception:
                raise HTTPException(503, str(exception)) from None
            self._argv_cache.clear()
            result = self.blender()
            if not result["ready"]: raise HTTPException(503, result["error"] or "隔离进程启动后尚未完成身份/连接验证")
            return {**result, "already_running": False}

    def stop_blender(self):
        with self._lock:
            current = self.blender()
            if self._helper is None: raise HTTPException(503, "缺少本产品宿主脚本，无法确认停止状态；没有操作任何进程")
            if not current["ready"]:
                if current["port_occupied"]: raise HTTPException(409, current["error"])
                return {"stopped": True, "already_stopped": True, "owns_process": False}
            if not current["owns_process"] or not self._helper: raise HTTPException(409, "无法确认宿主归本项目所有；未停止任何进程")
            try: result = self._helper.stop()
            except (RuntimeError, OSError, ValueError) as exception: raise HTTPException(503, str(exception)) from None
            self._argv_cache.clear()
            if self.blender()["ready"]: raise HTTPException(503, "本项目宿主仍在执行任务；停止请求已排队")
            return {**result, "owns_process": True}

    def plugin(self, key: str, required: str):
        metadata = read_json(self.root / "integrations" / "host-plugins" / "catalog.json")
        package = next((value for value in metadata.get("packages", []) if value.get("id") == key), {})
        _metadata, _executable, _installed, old_root = self.blender_metadata()
        for root in dict.fromkeys([self.root, old_root] if old_root else [self.root]):
            directory = root / ".local" / "toolchains" / "plugins" / "prepared" / key
            marker = read_json(directory / "architecture-prepared.json")
            if package.get("sha256") and marker.get("artifact_sha256") == package["sha256"] and (directory / required).is_file():
                return {"prepared": True, "path": str(directory), "source": "project" if root == self.root else "previous-project",
                        "license": package.get("license", ""), "version": package.get("tag", ""), "verified_in_host": False}
        return {"prepared": False, "path": str(self.root / ".local" / "toolchains" / "plugins" / "prepared" / key),
                "license": package.get("license", ""), "version": package.get("tag", ""), "verified_in_host": False}

    def commercial_hosts(self):
        settings = self.store.get_settings()
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        definitions = [
            ("rhino", "Rhino 8", "Rhino.exe", [program_files / "Rhino 8" / "System" / "Rhino.exe"], "rhino", "net8.0/rhinomcp.rhp", "https://www.rhino3d.com/download/rhino-for-windows/evaluation/"),
            ("sketchup", "SketchUp Desktop", "SketchUp.exe", [program_files / "SketchUp" / f"SketchUp {year}" / "SketchUp.exe" for year in range(2027, 2021, -1)], "sketchup", "su_mcp_bridge.rb", "https://www.sketchup.com/en/try-sketchup?sketchup=pro"),
            ("revit", "Autodesk Revit", "Revit.exe", [program_files / "Autodesk" / f"Revit {year}" / "Revit.exe" for year in range(2027, 2022, -1)], "revit-2026", "revit_mcp_plugin/RevitMCPPlugin.dll", "https://www.autodesk.com/products/revit/free-trial"),
        ]
        result = []
        for key, label, filename, locations, plugin, required, official in definitions:
            detected = executable_path(settings.get(key + "_executable"), filename)
            if detected is None:
                detected = next((path.resolve() for path in locations if path.is_file()), None)
            if detected is None and os.name == "nt":
                import winreg
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, f"Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{filename}") as entry:
                            detected = executable_path(winreg.QueryValueEx(entry, "")[0], filename)
                    except OSError: continue
                    if detected: break
            result.append({"id": key, "name": label, "installed": detected is not None, "executable": str(detected) if detected else "",
                           "ready": False, "plugin": self.plugin(plugin, required), "official_url": official,
                           "error": "程序文件已检测到；插件加载/真实 MCP 连接仍需在宿主内验证" if detected else "未检测到商业桌面宿主；需合法账户/许可安装后再加载插件"})
        result.append({"id": "grasshopper", "name": "Grasshopper / Cordyceps", "installed": result[0]["installed"], "executable": result[0]["executable"],
                       "ready": False, "plugin": self.plugin("grasshopper", "Cordyceps.gha"), "official_url": "https://github.com/brookstalley/cordyceps",
                       "error": "需在 Rhino 8 的 Grasshopper 画布放置 Cordyceps 组件；文件准备不代表连接已就绪"})
        return result

    def inventory(self):
        settings = self.store.get_settings(); sdk = find_sdk(settings)
        try:
            python = professional_python(settings)
            python_status = {"found": True, "executable": python, "error": ""}
        except (ValueError, OSError, TypeError) as error: python_status = {"found": False, "executable": "", "error": str(error)}
        return {"project_root": str(self.root), "blender": self.blender(), "asr": asr_status(settings),
                "sdk": {"sdk_found": sdk is not None, "sdk_path": str(sdk) if sdk else "", "isolated": True},
                "python": python_status, "commercial": self.commercial_hosts(),
                "plugin_setup": {"script_path": str(self.root / "scripts" / "setup-host-plugins.py"),
                                 "available": (self.root / "scripts" / "setup-host-plugins.py").is_file()}, "local": True}


def register_hosts(app):
    router = APIRouter(prefix="/api/hosts")
    manager = HostManager(app.state.store)
    app.state.host_manager = manager

    @router.get("")
    def inventory(): return manager.inventory()

    @router.post("/blender/start")
    def start(): return manager.start_blender()

    @router.post("/blender/stop")
    def stop(): return manager.stop_blender()

    app.include_router(router)
