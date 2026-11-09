"""Validate the actual bundled MCP against a real isolated Blender scene."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT / ".local" / "blender-host" / "outputs"
PROMPT = "在独立本机 Blender 环境中读取场景、创建建筑测试立方体、渲染 PNG 并导出 GLB，验证本工程 BlenderMCP 真实建模链路。"

def strings(result):
    if result.isError: raise RuntimeError(str(result))
    return "\n".join(block.text for block in result.content if block.type == "text")

async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    OUTPUT.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": "9877",
        "DISABLE_TELEMETRY": "true", "BLENDER_MCP_DISABLE_TELEMETRY": "true", "MCP_DISABLE_TELEMETRY": "true",
        "TEMP": str(PROJECT / ".local" / "temp"), "TMP": str(PROJECT / ".local" / "temp")}
    profile = PROJECT / ".local" / "blender-host" / "mcp-profile"; profile.mkdir(exist_ok=True)
    environment["APPDATA"] = str(profile)
    launcher = PROJECT / "resources" / "skills" / "BlenderMCP" / "blender_mcp_launcher.py"
    python = PROJECT / "resources" / "python" / "python.exe"
    configuration = StdioServerParameters(command=str(python), args=[str(launcher)], env=environment, cwd=str(PROJECT))
    async with stdio_client(configuration) as streams:
        async with ClientSession(*streams) as client:
            initialized = await client.initialize()
            before = strings(await client.call_tool("get_scene_info", {"user_prompt": PROMPT}))
            code = f'''
import bpy, math
from mathutils import Vector
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = 1
bpy.ops.mesh.primitive_cube_add(size=2, location=(0,0,1.5))
cube = bpy.context.object
cube.name = 'WorkbenchValidationCube'
cube.dimensions = (4,3,3)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bevel = cube.modifiers.new('CornerChamfer', 'BEVEL'); bevel.width=.06; bevel.segments=3
material = bpy.data.materials.new('ArchitecturalSlate'); material.diffuse_color=(.16,.34,.48,1)
cube.data.materials.append(material)
bpy.ops.mesh.primitive_plane_add(size=200, location=(0,0,0))
ground = bpy.context.object; ground.name='ValidationGround'
floor = bpy.data.materials.new('WarmFloor'); floor.diffuse_color=(.8,.76,.68,1); ground.data.materials.append(floor)
bpy.ops.object.camera_add(location=(8,-10,7))
camera=bpy.context.object; camera.name='ValidationCamera'
camera.rotation_euler=(Vector((0,0,1.1))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO'; camera.data.ortho_scale=9
scene.camera=camera
bpy.ops.object.light_add(type='AREA', location=(2,-4,8))
light=bpy.context.object; light.name='ValidationKeyLight'; light.data.energy=1800; light.data.shape='DISK'; light.data.size=5
scene.render.engine='CYCLES'; scene.cycles.device='CPU'; scene.cycles.samples=16
scene.render.resolution_x=640; scene.render.resolution_y=480; scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'; scene.render.filepath={str(OUTPUT / 'cube.png')!r}
bpy.ops.wm.save_as_mainfile(filepath={str(OUTPUT / 'cube.blend')!r})
bpy.ops.render.render(write_still=True)
bpy.ops.object.select_all(action='DESELECT'); cube.select_set(True); bpy.context.view_layer.objects.active=cube
bpy.ops.export_scene.gltf(filepath={str(OUTPUT / 'cube.glb')!r}, export_format='GLB', use_selection=True)
print('WorkbenchValidationCube saved, rendered and exported')
'''
            execution = strings(await client.call_tool("execute_blender_code", {"code": code, "user_prompt": PROMPT}))
            if "Error" in execution or "error" in execution or "Rejected" in execution: raise RuntimeError(execution)
            after = strings(await client.call_tool("get_scene_info", {"user_prompt": PROMPT}))
            details = strings(await client.call_tool("get_object_info", {"object_name": "WorkbenchValidationCube", "user_prompt": PROMPT}))
            if "WorkbenchValidationCube" not in after or "WorkbenchValidationCube" not in details: raise RuntimeError("New object was not found in the live Blender scene")
    artifacts = []
    for name in ("cube.blend", "cube.png", "cube.glb"):
        path=OUTPUT/name
        if not path.is_file() or path.stat().st_size < 100: raise RuntimeError(f"Missing real modeling artifact: {name}")
        artifacts.append({"name": name, "path": str(path), "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    png=(OUTPUT/'cube.png').read_bytes(); glb=(OUTPUT/'cube.glb').read_bytes()
    if png[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',png[16:24]) != (640,480): raise RuntimeError("Invalid rendered PNG")
    if glb[:4] != b'glTF' or struct.unpack('<II',glb[4:12]) != (2,len(glb)): raise RuntimeError("Invalid exported GLB")
    evidence={"verified_at": datetime.now(timezone.utc).isoformat(), "sdk": initialized.serverInfo.model_dump(),
        "host": "127.0.0.1:9877", "scene_before": before, "scene_after": after, "object_info": details,
        "execution": execution, "artifacts": artifacts, "render_size": [640,480], "real_modeling_verified": True,
        "viewport_screenshot_verified": False, "rendered_image_visually_inspected": False, "cloud_services_called": False}
    path=PROJECT/'docs'/'evidence'/'blender-host.json'; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({"real_modeling_verified": True,"artifacts":artifacts},ensure_ascii=False,indent=2))

if __name__ == '__main__': asyncio.run(main())
