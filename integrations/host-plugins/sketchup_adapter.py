#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""MCP stdio adapter for Shattenjagger's MIT SketchUp bridge (cmd/id/args).

This is NOT the vendor's JSON-RPC-over-TCP extension. It adapts the licensed
bridge's one-request-per-socket protocol and exposes the vendor skill's eight
tool names. Real geometry remains inside a licensed, running desktop SketchUp.
"""
from __future__ import annotations

import base64
import itertools
import json
import math
import os
from pathlib import Path
import socket
import sys
import threading

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Architecture SketchUp Adapter", instructions="Operate the current desktop SketchUp via its local MIT extension. Coordinates and lengths are inches unless explicit Ruby units are used. Check actual geometry and viewport before declaring a task complete.")
_ids = itertools.count(1)
_lock = threading.Lock()


def call_bridge(command: str, arguments: dict | None = None):
    host = os.environ.get("SU_MCP_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SketchUp adapter requires a loopback host")
    port = int(os.environ.get("SU_MCP_PORT", "9876"))
    timeout = min(1800, max(1, float(os.environ.get("SU_MCP_RUBY_TIMEOUT", "180"))))
    with _lock:
        identifier = next(_ids)
        packet = {"id": identifier, "cmd": command, "args": arguments or {}}
        with socket.create_connection((host, port), timeout=5) as channel:
            channel.settimeout(timeout)
            channel.sendall(json.dumps(packet, ensure_ascii=False).encode("utf-8") + b"\n")
            payload = bytearray()
            while b"\n" not in payload:
                chunk = channel.recv(65536)
                if not chunk:
                    raise ConnectionError("SketchUp closed the socket before a response")
                payload.extend(chunk)
                if len(payload) > 16 * 1024 * 1024:
                    raise ValueError("SketchUp response exceeds 16 MB")
        response = json.loads(payload.split(b"\n", 1)[0])
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error", "SketchUp operation failed")))
        if response.get("id") != identifier:
            raise ValueError("SketchUp response id does not match the request")
        return response.get("result")


RUBY_ENTITIES = """
model = Sketchup.active_model
found = {}; pending = model.entities.to_a
until pending.empty?
  entity = pending.pop
  next unless entity.valid?
  next if found.key?(entity.entityID)
  found[entity.entityID] = entity
  if entity.is_a?(Sketchup::Group)
    pending.concat(entity.entities.to_a)
  elsif entity.is_a?(Sketchup::ComponentInstance)
    pending.concat(entity.definition.entities.to_a)
  end
end
items = (d['entity_ids'] || []).map { |id| found.fetch(id) { raise "Unknown entity id: #{id}" } }
"""


def structured(tool: str, data: dict, ruby: str, *, entities=False):
    # All structured data travels as base64 JSON, never interpolated Ruby text.
    encoded = base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode("ascii")
    code = f"# workbench-tool:{tool}\nrequire 'json'\nrequire 'base64'\nd=JSON.parse(Base64.strict_decode64('{encoded}'))\n"
    code += RUBY_ENTITIES if entities else "model=Sketchup.active_model\n"
    result = call_bridge("eval", {"code": code + ruby})
    try:
        return json.loads(result)
    except (ValueError, TypeError) as error:
        raise ValueError("SketchUp returned invalid structured output") from error


def validate_ids(values: list[int]):
    if not values or len(values) > 5000 or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
        raise ValueError("entity_ids must contain 1–5000 positive integer ids")


def vector3(value):
    if not isinstance(value, list) or len(value) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError("Expected three finite coordinates")


@mcp.tool()
def get_model_info() -> dict:
    """Read model name/path/entity count/layers/component definitions/bounds; lengths are inches."""
    return structured("get_model_info", {}, "JSON.generate({'title'=>model.title,'path'=>model.path,'entity_count'=>model.entities.length,'layers'=>model.layers.map(&:name),'component_definitions'=>model.definitions.map(&:name),'bounds'=>{'min'=>model.bounds.min.to_a,'max'=>model.bounds.max.to_a}})")


@mcp.tool()
def get_selection(detail: bool = False) -> dict:
    """Read selected entity ids and types; optionally include bounds."""
    return structured("get_selection", {"detail": detail}, "JSON.generate({'entities'=>model.selection.map{|e| r={'id'=>e.entityID,'type'=>e.typename}; r['bounds']={'min'=>e.bounds.min.to_a,'max'=>e.bounds.max.to_a} if d['detail']; r}})")


@mcp.tool()
def get_entity_info(entity_ids: list[int]) -> dict:
    """Read existing entities by the entity ids returned by this adapter."""
    validate_ids(entity_ids)
    return structured("get_entity_info", {"entity_ids": entity_ids}, "JSON.generate({'entities'=>items.map{|e| r={'id'=>e.entityID,'type'=>e.typename,'bounds'=>{'min'=>e.bounds.min.to_a,'max'=>e.bounds.max.to_a}}; r['material']=e.material.name if e.respond_to?(:material) && e.material; r['transformation']=e.transformation.to_a if e.respond_to?(:transformation); r}})", entities=True)


@mcp.tool()
def create_geometry(type: str, points: list[list[float]] | None = None, width: float | None = None,
                    depth: float | None = None, height: float | None = None,
                    origin: list[float] | None = None, name: str = "") -> dict:
    """Create face/edge/group_with_faces/box. Inches. For box, origin is TOP and extrusion is -height, matching the original skill contract."""
    if type not in {"face", "edge", "group_with_faces", "box"}:
        raise ValueError("Unsupported geometry type")
    if type == "box":
        if any(value is None or not math.isfinite(value) or value <= 0 for value in (width, depth, height)):
            raise ValueError("Box width/depth/height must be positive finite inch values")
        origin = origin or [0, 0, 0]; vector3(origin)
    else:
        if not points or len(points) > 10000 or (type == "edge" and len(points) != 2) or (type != "edge" and len(points) < 3):
            raise ValueError("Invalid point count for geometry")
        for point in points: vector3(point)
    data = {"type": type, "points": points, "width": width, "depth": depth, "height": height, "origin": origin, "name": name}
    return structured("create_geometry", data, """
case d['type']
when 'edge'
  e=model.active_entities.add_line(d['points'][0],d['points'][1]); raise 'Edge creation failed' unless e
  result={'id'=>e.entityID,'type'=>'edge','length'=>e.length.to_f}
when 'face'
  e=model.active_entities.add_face(d['points']); raise 'Face creation failed' unless e
  result={'id'=>e.entityID,'type'=>'face','area'=>e.area.to_f}
when 'group_with_faces'
  e=model.active_entities.add_group; e.name=d['name']; face=e.entities.add_face(d['points']); raise 'Face creation failed' unless face
  result={'id'=>e.entityID,'type'=>'group','area'=>face.area.to_f}
when 'box'
  x,y,z=d['origin']; w=d['width']; h=d['height']; depth=d['depth']
  e=model.active_entities.add_group; e.name=d['name']
  face=e.entities.add_face([[x,y,z],[x+w,y,z],[x+w,y+depth,z],[x,y+depth,z]])
  raise 'Box face creation failed' unless face
  face.reverse! if face.normal.z < 0; face.pushpull(-h)
  result={'id'=>e.entityID,'type'=>'box'}
end
JSON.generate(result)
""")


@mcp.tool()
def transform_entities(entity_ids: list[int], transform_type: str, vector: list[float] | None = None,
                       angle: float = 0, axis: list[float] | None = None,
                       center: list[float] | None = None, factor: float = 1) -> dict:
    """Move/rotate/scale existing entities. Lengths inches, rotation degrees, uniform scaling factor."""
    validate_ids(entity_ids)
    if transform_type not in {"move", "rotate", "scale"}: raise ValueError("Invalid transform_type")
    scale_factors = vector if transform_type == "scale" and vector is not None else [factor, factor, factor]
    vector = vector or [0, 0, 0]; axis = axis or [0, 0, 1]; center = center or [0, 0, 0]
    for value in (vector, axis, center): vector3(value)
    if not math.isfinite(angle) or not math.isfinite(factor) or factor <= 0 or (transform_type == "rotate" and all(x == 0 for x in axis)):
        raise ValueError("Invalid rotation or scaling values")
    vector3(scale_factors)
    if any(value == 0 for value in scale_factors): raise ValueError("Scaling values cannot be zero")
    return structured("transform_entities", {"entity_ids": entity_ids, "transform_type": transform_type, "vector": vector, "angle": angle, "axis": axis, "center": center, "scale_factors": scale_factors}, """
t=case d['transform_type']
when 'move' then Geom::Transformation.translation(d['vector'])
when 'rotate' then Geom::Transformation.rotation(d['center'],d['axis'],d['angle']*Math::PI/180.0)
when 'scale' then Geom::Transformation.scaling(d['center'],*d['scale_factors'])
end
items.group_by(&:parent).each{|parent,group| parent.entities.transform_entities(t,group)}
JSON.generate({'transformed'=>items.length,'entity_ids'=>d['entity_ids']})
""", entities=True)


@mcp.tool()
def set_material(entity_ids: list[int], color: str | list[int], alpha: float = 1, back_face: bool = False) -> dict:
    """Apply a named/HEX or RGB material to existing entities; alpha 0–1."""
    validate_ids(entity_ids)
    if not math.isfinite(alpha) or not 0 <= alpha <= 1: raise ValueError("alpha must be 0–1")
    if isinstance(color, list) and (len(color) != 3 or any(isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 255 for x in color)):
        raise ValueError("RGB color must contain three integers from 0 to 255")
    return structured("set_material", {"entity_ids": entity_ids, "color": color, "alpha": alpha, "back_face": back_face}, """
material=model.materials.add("Workbench-MCP-#{Time.now.to_f}")
material.color=d['color'].is_a?(Array) ? Sketchup::Color.new(*d['color']) : Sketchup::Color.new(d['color'])
material.alpha=d['alpha']
items.each{|e| raise 'Entity does not support material' unless e.respond_to?(:material=); e.material=material; e.back_material=material if d['back_face'] && e.is_a?(Sketchup::Face)}
JSON.generate({'updated'=>items.length,'entity_ids'=>d['entity_ids']})
""", entities=True)


@mcp.tool()
def delete_entities(entity_ids: list[int]) -> dict:
    """Erase only the explicitly supplied existing entity ids."""
    validate_ids(entity_ids)
    return structured("delete_entities", {"entity_ids": entity_ids}, "items.each{|e| e.erase! if e.valid?}; JSON.generate({'deleted'=>items.length,'entity_ids'=>d['entity_ids']})", entities=True)


@mcp.tool()
def execute_ruby(code: str) -> dict:
    """Execute explicit Ruby in the live SketchUp model. Full host privileges; save via model.save(path)."""
    if not code.strip() or len(code) > 2 * 1024 * 1024: raise ValueError("Ruby code is empty or exceeds 2 MB")
    return {"output": call_bridge("eval", {"code": code})}


if __name__ == "__main__":
    mcp.run(transport="stdio")
