"""Start/stop only the isolated project-owned BlenderMCP host."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import time

PROJECT = Path(__file__).resolve().parents[2]
STATE = PROJECT / ".local" / "blender-host"

def listening():
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(("127.0.0.1", 9877)) == 0

def owned_process(pid):
    installed = PROJECT / "integrations" / "blender" / "installed.json"
    recorded = STATE / "process.json"
    if not installed.is_file() or not recorded.is_file(): return False
    process = json.loads(recorded.read_text(encoding="utf-8"))
    if process.get("pid") != pid: return False
    command = process.get("command", [])
    if len(command) != 5 or command[1:4] != ["--background", "--factory-startup", "--python"] or Path(command[4]).resolve() != (PROJECT / "integrations" / "blender" / "bootstrap.py").resolve():
        return False
    expected = command[0]
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle: return False
        try:
            buffer = ctypes.create_unicode_buffer(32768); length = ctypes.c_ulong(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)): return False
            return Path(buffer.value).resolve() == Path(expected).resolve()
        finally: kernel.CloseHandle(handle)
    commandline = Path(f"/proc/{pid}/cmdline")
    return commandline.is_file() and str(PROJECT / "integrations" / "blender" / "bootstrap.py").encode() in commandline.read_bytes()

def status():
    ready = STATE / "ready.json"
    result = json.loads(ready.read_text()) if ready.is_file() else {"ready": False}
    result["ready"] = ready.is_file() and owned_process(result.get("pid", 0)) and listening()
    return result

def start(executable=None):
    STATE.mkdir(parents=True, exist_ok=True)
    current = status()
    if current["ready"]: return current
    if listening(): raise RuntimeError("Port 9877 is already occupied by a host not owned by this project; nothing was stopped")
    (STATE / "stop").unlink(missing_ok=True)
    (STATE / "ready.json").unlink(missing_ok=True)
    install = json.loads((PROJECT / "integrations" / "blender" / "installed.json").read_text(encoding="utf-8"))
    environment = dict(os.environ)
    for key, folder in (("BLENDER_USER_CONFIG", "config"), ("BLENDER_USER_SCRIPTS", "scripts"), ("BLENDER_USER_DATAFILES", "datafiles")):
        path = STATE / folder; path.mkdir(exist_ok=True); environment[key] = str(path)
    temporary = PROJECT / ".local" / "temp"; temporary.mkdir(exist_ok=True)
    environment.update({"TEMP": str(temporary), "TMP": str(temporary), "DISABLE_TELEMETRY": "true",
        "BLENDER_MCP_DISABLE_TELEMETRY": "true", "MCP_DISABLE_TELEMETRY": "true"})
    program = str(Path(executable or install["executable"]).resolve(strict=True))
    command = [program, "--background", "--factory-startup", "--python", str(PROJECT / "integrations" / "blender" / "bootstrap.py")]
    with (STATE / "host.log").open("ab") as log:
        child = subprocess.Popen(command, cwd=PROJECT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    (STATE / "process.json").write_text(json.dumps({"pid": child.pid, "command": command}, ensure_ascii=False, indent=2), encoding="utf-8")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if child.poll() is not None: raise RuntimeError("Isolated Blender exited; inspect .local/blender-host/host.log")
        current = status()
        if current["ready"]: return current
        time.sleep(.2)
    raise RuntimeError("Isolated Blender did not start within 30 seconds; inspect the host log")

def stop():
    if not status()["ready"]: return {"stopped": True, "already_stopped": True}
    (STATE / "stop").touch()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not listening(): return {"stopped": True}
        time.sleep(.2)
    raise RuntimeError("Blender is still executing a command; stop marker remains queued, no unrelated process was killed")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["start", "stop", "status"])
    arguments = parser.parse_args()
    print(json.dumps({"start": start, "stop": stop, "status": status}[arguments.command](), ensure_ascii=False, indent=2))
