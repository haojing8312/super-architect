"""Actual stdio MCP/socket roundtrip tests against a protocol sandbox.

The fixture acknowledges packets; it DOES NOT execute Ruby or stand in for
SketchUp geometry/viewport acceptance. The JSON evidence labels that boundary.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import socketserver
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"get_model_info", "get_selection", "get_entity_info", "create_geometry", "transform_entities", "set_material", "delete_entities", "execute_ruby"}
PACKETS = []


class Sandbox(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        PACKETS.append(request)
        assert request["cmd"] == "eval" and isinstance(request["args"]["code"], str)
        code = request["args"]["code"]
        marker = re.search(r"# workbench-tool:([a-z_]+)", code)
        tool = marker.group(1) if marker else "execute_ruby"
        response = {"id": request["id"], "ok": True, "result": json.dumps({"sandbox": True, "tool": tool}) if marker else "sandbox-write-acknowledged"}
        if code == "SANDBOX_ERROR": response = {"id": request["id"], "ok": False, "error": "sandbox expected failure"}
        if code == "SANDBOX_WRONG_ID": response["id"] += 1
        self.wfile.write(json.dumps(response).encode() + b"\n")


async def check():
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Sandbox) as host:
        thread = threading.Thread(target=host.serve_forever, daemon=True); thread.start()
        parameters = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("sketchup_adapter.py"))],
            env={**os.environ, "SU_MCP_HOST": "127.0.0.1", "SU_MCP_PORT": str(host.server_address[1])})
        try:
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    assert names == EXPECTED, names
                    cases = [
                        ("get_model_info", {}), ("get_selection", {"detail": True}),
                        ("get_entity_info", {"entity_ids": [1]}),
                        ("create_geometry", {"type": "box", "width": 4, "depth": 3, "height": 2, "origin": [0, 0, 2]}),
                        ("transform_entities", {"entity_ids": [1], "transform_type": "scale", "vector": [2, 1, 1]}),
                        ("set_material", {"entity_ids": [1], "color": [255, 0, 0], "alpha": 0.7}),
                        ("delete_entities", {"entity_ids": [1]}),
                        ("execute_ruby", {"code": "SANDBOX_WRITE_PACKET"})]
                    for name, arguments in cases:
                        response = await client.call_tool(name, arguments)
                        assert not response.isError, response
                    failed = await client.call_tool("execute_ruby", {"code": "SANDBOX_ERROR"})
                    assert failed.isError
                    mismatched = await client.call_tool("execute_ruby", {"code": "SANDBOX_WRONG_ID"})
                    assert mismatched.isError
                    before = len(PACKETS)
                    invalid = await client.call_tool("delete_entities", {"entity_ids": []})
                    assert invalid.isError and len(PACKETS) == before
            result = {"ok": True, "boundary": "real MCP stdio + socket protocol sandbox; no commercial host execution", "actual_host_executed": False,
                      "tools": sorted(names), "successful_roundtrips": len(cases), "error_checks": ["host error", "response id mismatch", "invalid ids rejected before socket"],
                      "wire_commands": sorted({packet["cmd"] for packet in PACKETS})}
            output = ROOT / ".local" / "toolchains" / "plugins" / "sketchup-protocol-sandbox.json"
            output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
        finally:
            host.shutdown(); thread.join(timeout=5)


if __name__ == "__main__": asyncio.run(check())
