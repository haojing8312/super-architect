"""Run the supplied BlenderMCP addon in an isolated Blender background process.

The upstream addon uses GUI timers and refuses -b. This host keeps its protocol,
queue, command handlers and Blender thread rules, draining that queue explicitly
on Blender's main thread. No installed addon or existing preferences are edited.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parents[2]
STATE = PROJECT / ".local" / "blender-host"
ADDON = PROJECT / "resources" / "skills" / "BlenderMCP" / "blender_mcp_server" / "src" / "blender_mcp" / "bundled" / "addon.py"
# Requests and dependencies are pure-Python fallbacks; Blender's own packages
# take precedence, so foreign-ABI numpy/native extensions are never selected.
sys.path.append(str(PROJECT / "resources" / "python" / "Lib" / "site-packages"))
specification = importlib.util.spec_from_file_location("architecture_blender_addon", ADDON)
addon = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = addon
specification.loader.exec_module(addon)
import bpy


class BackgroundServer(addon.BlenderMCPServer):
    def start(self):
        if self.running: return
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 9877)); self.socket.listen(5)
        self.running = True
        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.server_thread.start()
        print("Architecture isolated BlenderMCP main-thread host: 127.0.0.1:9877", flush=True)


addon.register()
server = BackgroundServer(host="127.0.0.1", port=9877)
bpy.types.blendermcp_server = server
scene = bpy.context.scene
scene.blendermcp_auto_start_server = False
for name in ("polyhaven", "hyper3d", "hunyuan3d", "sketchfab", "polypizza"):
    setattr(scene, "blendermcp_use_" + name, False)
server.start()
scene.blendermcp_server_running = True
STATE.mkdir(parents=True, exist_ok=True)
(STATE / "ready.json").write_text(json.dumps({"pid": os.getpid(), "port": 9877, "version": bpy.app.version_string,
    "addon_protocol": addon.ADDON_PROTOCOL_VERSION, "background": bpy.app.background,
    "factory_startup": True, "cloud_assets_enabled": False, "telemetry_consent": False}), encoding="utf-8")
try:
    while server.running and not (STATE / "stop").exists():
        server._drain_command_queue()
        time.sleep(.02)
finally:
    server.stop()
    (STATE / "ready.json").unlink(missing_ok=True)
    (STATE / "stop").unlink(missing_ok=True)
