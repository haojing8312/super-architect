"""SketchUp C API preview, always isolated from the HTTP process.

The external SketchUp SDK is deliberately not redistributed. C API coordinates
are inches; the original SUViewer applies nested transforms before converting
to metres. This reader uses the same transform order and exports metre OBJ.
"""
from __future__ import annotations

import ctypes as C
import base64
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .documents import local_path

MAX_TRIANGLES = 250_000
MAX_TEXTURE_PIXELS = 4_194_304
MAX_TEXTURE_BYTES = 64 * 1024 * 1024
IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


class Ref(C.Structure):
    _fields_ = [("ptr", C.c_void_p)]


class Point(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double), ("z", C.c_double)]


class Transform(C.Structure):
    _fields_ = [("values", C.c_double * 16)]


class Color(C.Structure):
    _fields_ = [("red", C.c_ubyte), ("green", C.c_ubyte), ("blue", C.c_ubyte), ("alpha", C.c_ubyte)]


def normalize_matrix(values) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if len(result) != 16 or not all(math.isfinite(value) for value in result):
        raise ValueError("模型变换矩阵无效")
    denominator = result[15]
    if denominator and abs(denominator - 1.) > 1e-6:
        result = tuple(value / denominator for value in result)
    return result


def multiply_matrix(left, right) -> tuple[float, ...]:
    # SU column-major storage is equivalent to System.Numerics row-vector
    # storage in the reference reader: world = local * parent.
    return tuple(sum(left[row * 4 + index] * right[index * 4 + column] for index in range(4)) for row in range(4) for column in range(4))


def transform_point(point, matrix) -> tuple[float, float, float]:
    x, y, z = point
    return tuple((x * matrix[column] + y * matrix[4 + column] + z * matrix[8 + column] + matrix[12 + column]) * .0254 for column in range(3))


def mirrored(matrix) -> bool:
    a, b, c = matrix[0:3]
    d, e, f = matrix[4:7]
    g, h, i = matrix[8:11]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g) < 0


class SketchUpNative:
    """Only construct this class inside the isolated native worker."""
    def __init__(self, dll: Path):
        self.directory_handle = os.add_dll_directory(str(dll.parent)) if os.name == "nt" else None
        self.library = C.CDLL(str(dll))
        reference = C.POINTER(Ref)
        count = C.POINTER(C.c_size_t)
        self.bind("SUInitialize", [], None)
        self.bind("SUTerminate", [], None)
        self.bind("SUModelCreateFromFile", [reference, C.c_char_p])
        self.bind("SUModelRelease", [reference])
        self.bind("SUModelGetEntities", [Ref, reference])
        for kind in ("Faces", "Instances", "Groups"):
            self.bind("SUEntitiesGetNum" + kind, [Ref, count])
            self.bind("SUEntitiesGet" + kind, [Ref, C.c_size_t, reference, count])
        self.bind("SUMeshHelperCreate", [reference, Ref])
        self.bind("SUMeshHelperRelease", [reference])
        self.bind("SUMeshHelperGetNumVertices", [Ref, count])
        self.bind("SUMeshHelperGetNumTriangles", [Ref, count])
        self.bind("SUMeshHelperGetVertices", [Ref, C.c_size_t, C.POINTER(Point), count])
        self.bind("SUMeshHelperGetVertexIndices", [Ref, C.c_size_t, C.POINTER(C.c_size_t), count])
        self.bind("SUGroupGetEntities", [Ref, reference])
        self.bind("SUGroupGetTransform", [Ref, C.POINTER(Transform)])
        self.bind("SUComponentInstanceGetDefinition", [Ref, reference])
        self.bind("SUComponentInstanceGetTransform", [Ref, C.POINTER(Transform)])
        self.bind("SUComponentDefinitionGetEntities", [Ref, reference])
        self.bind("SUFaceGetFrontMaterial", [Ref, reference])
        self.bind("SUFaceGetBackMaterial", [Ref, reference])
        self.bind("SUDrawingElementGetMaterial", [Ref, reference])
        self.bind("SUMaterialGetColor", [Ref, C.POINTER(Color)])
        # Older DLLs may lack image/camera exports. Geometry still works and the
        # result identifies unavailable capabilities instead of inventing data.
        self.missing_exports = []
        self.warning_messages = []
        self._materials = {}
        self._texture_bytes = 0
        additional = {
            "SUMaterialGetName": [Ref, reference],
            "SUMaterialGetType": [Ref, C.POINTER(C.c_int)],
            "SUMaterialGetOpacity": [Ref, C.POINTER(C.c_double)],
            "SUMaterialGetUseOpacity": [Ref, C.POINTER(C.c_bool)],
            "SUMaterialGetTexture": [Ref, reference],
            "SUTextureGetImageRep": [Ref, reference],
            "SUImageRepCreate": [reference],
            "SUImageRepRelease": [reference],
            "SUImageRepConvertTo32BitsPerPixel": [Ref],
            "SUImageRepGetPixelDimensions": [Ref, count, count],
            "SUImageRepGetRowPadding": [Ref, count],
            "SUImageRepGetDataSize": [Ref, count, count],
            "SUImageRepGetData": [Ref, C.c_size_t, C.POINTER(C.c_ubyte)],
            "SUMeshHelperGetFrontSTQCoords": [Ref, C.c_size_t, C.POINTER(Point), count],
            "SUMeshHelperGetBackSTQCoords": [Ref, C.c_size_t, C.POINTER(Point), count],
            "SUStringCreate": [reference],
            "SUStringRelease": [reference],
            "SUStringGetUTF8Length": [Ref, count],
            "SUStringGetUTF8": [Ref, C.c_size_t, C.POINTER(C.c_char), count],
            "SUModelGetCamera": [Ref, reference],
            "SUModelGetNumScenes": [Ref, count],
            "SUModelGetScenes": [Ref, C.c_size_t, reference, count],
            "SUSceneGetName": [Ref, reference],
            "SUSceneGetUseCamera": [Ref, C.POINTER(C.c_bool)],
            "SUSceneGetCamera": [Ref, reference],
            "SUCameraGetOrientation": [Ref, C.POINTER(Point), C.POINTER(Point), C.POINTER(Point)],
            "SUCameraGetPerspective": [Ref, C.POINTER(C.c_bool)],
            "SUCameraGetPerspectiveFrustumFOV": [Ref, C.POINTER(C.c_double)],
            "SUCameraGetOrthographicFrustumHeight": [Ref, C.POINTER(C.c_double)],
            "SUCameraGetAspectRatio": [Ref, C.POINTER(C.c_double)],
        }
        for name, arguments in additional.items():
            try: self.bind(name, arguments)
            except AttributeError: self.missing_exports.append(name)

    def bind(self, name, arguments, result=C.c_int):
        function = getattr(self.library, name)
        function.argtypes = arguments
        function.restype = result
        setattr(self, name, function)
        return function

    @staticmethod
    def check(code, operation):
        if code:
            raise ValueError(f"SketchUp SDK {operation} 失败（错误码 {code}）；请检查模型版本及完整性")

    def references(self, entities, kind):
        count = C.c_size_t()
        self.check(getattr(self, "SUEntitiesGetNum" + kind)(entities, C.byref(count)), "GetNum" + kind)
        if count.value > 500_000:
            raise ValueError("模型实体数量超过预览上限，请简化模型")
        if not count.value:
            return []
        references = (Ref * count.value)()
        copied = C.c_size_t()
        self.check(getattr(self, "SUEntitiesGet" + kind)(entities, count.value, references, C.byref(copied)), "Get" + kind)
        if copied.value > count.value:
            raise ValueError("SDK 返回的实体数量无效")
        return list(references[:copied.value])

    def warn(self, message):
        if message not in self.warning_messages and len(self.warning_messages) < 50:
            self.warning_messages.append(message)

    def string_property(self, reference, getter, fallback):
        required = ("SUStringCreate", "SUStringRelease", "SUStringGetUTF8Length", "SUStringGetUTF8", getter)
        if any(not hasattr(self, name) for name in required): return fallback
        value = Ref()
        self.check(self.SUStringCreate(C.byref(value)), "StringCreate")
        try:
            if getattr(self, getter)(reference, C.byref(value)) != 0: return fallback
            length = C.c_size_t()
            self.check(self.SUStringGetUTF8Length(value, C.byref(length)), "StringLength")
            if length.value > 16384: raise ValueError("模型名称超过预览上限")
            buffer = C.create_string_buffer(length.value + 1)
            copied = C.c_size_t()
            self.check(self.SUStringGetUTF8(value, len(buffer), buffer, C.byref(copied)), "StringUTF8")
            return buffer.value.decode("utf-8", errors="replace") or fallback
        finally:
            if value.ptr: self.SUStringRelease(C.byref(value))

    def texture(self, material):
        required = ("SUMaterialGetTexture", "SUTextureGetImageRep", "SUImageRepCreate", "SUImageRepRelease", "SUImageRepConvertTo32BitsPerPixel", "SUImageRepGetPixelDimensions", "SUImageRepGetDataSize", "SUImageRepGetData")
        if any(not hasattr(self, name) for name in required):
            raise ValueError("本机 SDK 缺少纹理图像接口")
        from PIL import Image
        texture, image = Ref(), Ref()
        self.check(self.SUMaterialGetTexture(material, C.byref(texture)), "GetTexture")
        self.check(self.SUImageRepCreate(C.byref(image)), "ImageRepCreate")
        try:
            self.check(self.SUTextureGetImageRep(texture, C.byref(image)), "GetTextureImage")
            self.check(self.SUImageRepConvertTo32BitsPerPixel(image), "Texture32Bits")
            width, height, size, bits, padding = (C.c_size_t() for _ in range(5))
            self.check(self.SUImageRepGetPixelDimensions(image, C.byref(width), C.byref(height)), "TextureDimensions")
            self.check(self.SUImageRepGetDataSize(image, C.byref(size), C.byref(bits)), "TextureSize")
            if hasattr(self, "SUImageRepGetRowPadding"):
                self.check(self.SUImageRepGetRowPadding(image, C.byref(padding)), "TextureRowPadding")
            if not width.value or not height.value or width.value * height.value > MAX_TEXTURE_PIXELS:
                raise ValueError("贴图超过 4194304 像素预览上限")
            stride = width.value * 4 + padding.value
            if bits.value != 32 or size.value < stride * height.value or size.value > MAX_TEXTURE_BYTES:
                raise ValueError("SDK 返回无效或过大的贴图数据")
            if self._texture_bytes + size.value > MAX_TEXTURE_BYTES:
                raise ValueError("全部贴图超过 64 MB 原始数据预览上限")
            pixels = (C.c_ubyte * size.value)()
            self.check(self.SUImageRepGetData(image, size.value, pixels), "TexturePixels")
            # SDK Windows buffers are BGRA, top row first. This is verified
            # against an SDK-loaded four-color PNG fixture; the reference's
            # positive-height BMP shortcut would vertically invert those bytes.
            decoded = Image.frombytes("RGBA", (width.value, height.value), bytes(pixels), "raw", "BGRA" if os.name == "nt" else "RGBA", stride, 1)
            output = io.BytesIO(); decoded.save(output, format="PNG")
            png = output.getvalue()
            self._texture_bytes += size.value
            return {"width": width.value, "height": height.value, "mime_type": "image/png",
                    "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                    "sha256": hashlib.sha256(png).hexdigest(), "wrap": "repeat", "flip_y": True,
                    "has_alpha": decoded.getextrema()[3][0] < 255}
        finally:
            if image.ptr: self.SUImageRepRelease(C.byref(image))

    def resolve_material(self, reference):
        if reference.ptr in self._materials: return self._materials[reference.ptr]
        if len(self._materials) >= 1024: raise ValueError("模型材质数量超过 1024 个预览上限")
        color = Color(214, 217, 224, 255)
        self.SUMaterialGetColor(reference, C.byref(color))
        opacity = color.alpha / 255
        if hasattr(self, "SUMaterialGetOpacity"):
            value, used = C.c_double(), C.c_bool(True)
            if hasattr(self, "SUMaterialGetUseOpacity"): self.SUMaterialGetUseOpacity(reference, C.byref(used))
            if self.SUMaterialGetOpacity(reference, C.byref(value)) == 0 and used.value:
                opacity = min(1., max(0., value.value))
        identifier = "mat_" + str(len(self._materials) + 1)
        material_type = C.c_int()
        if hasattr(self, "SUMaterialGetType"): self.SUMaterialGetType(reference, C.byref(material_type))
        result = {"id": identifier, "name": self.string_property(reference, "SUMaterialGetName", identifier),
                  "color": [color.red / 255, color.green / 255, color.blue / 255, opacity],
                  "opacity": opacity, "texture": None, "source_type": ["colored", "textured", "colorized_texture"][material_type.value] if material_type.value in (0, 1, 2) else "unknown"}
        if material_type.value in (1, 2):
            try: result["texture"] = self.texture(reference)
            except (ValueError, OSError) as error:
                result["texture_error"] = str(error); self.warn(f"材质 {result['name']} 贴图未导出：{error}")
        self._materials[reference.ptr] = result
        return result

    def material(self, entity, parent, *, face=False):
        for name in ("SUFaceGetFrontMaterial", "SUFaceGetBackMaterial") if face else ("SUDrawingElementGetMaterial",):
            material = Ref()
            if getattr(self, name)(entity, C.byref(material)) == 0 and material.ptr:
                return self.resolve_material(material), name == "SUFaceGetBackMaterial"
        return parent, False

    def camera(self, reference, identifier, name, source):
        if not hasattr(self, "SUCameraGetOrientation") or not hasattr(self, "SUCameraGetPerspective"):
            self.warn("本机 SDK 缺少相机方向/投影接口"); return None
        position, target, up, perspective = Point(), Point(), Point(), C.c_bool()
        self.check(self.SUCameraGetOrientation(reference, C.byref(position), C.byref(target), C.byref(up)), "CameraOrientation")
        self.check(self.SUCameraGetPerspective(reference, C.byref(perspective)), "CameraPerspective")
        values = [position.x, position.y, position.z, target.x, target.y, target.z, up.x, up.y, up.z]
        if not all(math.isfinite(value) for value in values) or sum(value * value for value in values[6:]) < 1e-12:
            self.warn(f"相机 {name} 方向无效，未导出"); return None
        result = {"id": identifier, "name": name, "source": source,
                  "position": [value * .0254 for value in values[:3]], "target": [value * .0254 for value in values[3:6]],
                  "up": values[6:], "perspective": perspective.value, "fov_degrees": None,
                  "orthographic_height": None, "aspect_ratio": None, "units": "metres", "up_axis": "Z"}
        for api_name, key, scale in (("SUCameraGetPerspectiveFrustumFOV", "fov_degrees", 1.), ("SUCameraGetOrthographicFrustumHeight", "orthographic_height", .0254), ("SUCameraGetAspectRatio", "aspect_ratio", 1.)):
            if hasattr(self, api_name):
                value = C.c_double()
                if getattr(self, api_name)(reference, C.byref(value)) == 0 and math.isfinite(value.value) and value.value > 0:
                    result[key] = value.value * scale
        return result

    def cameras(self, model):
        current, scenes = None, []
        if hasattr(self, "SUModelGetCamera"):
            reference = Ref()
            if self.SUModelGetCamera(model, C.byref(reference)) == 0 and reference.ptr:
                current = self.camera(reference, "current", "当前相机", "current")
        required = ("SUModelGetNumScenes", "SUModelGetScenes", "SUSceneGetCamera", "SUSceneGetUseCamera")
        if any(not hasattr(self, name) for name in required):
            self.warn("本机 SDK 缺少场景相机接口"); return current, scenes
        count = C.c_size_t()
        self.check(self.SUModelGetNumScenes(model, C.byref(count)), "GetNumScenes")
        if count.value > 2048: raise ValueError("模型场景数量超过 2048 个预览上限")
        if not count.value: return current, scenes
        references = (Ref * count.value)(); copied = C.c_size_t()
        self.check(self.SUModelGetScenes(model, count.value, references, C.byref(copied)), "GetScenes")
        if copied.value > count.value: raise ValueError("SDK 返回的场景数量无效")
        for index, scene in enumerate(references[:copied.value]):
            enabled, reference = C.c_bool(), Ref()
            if self.SUSceneGetUseCamera(scene, C.byref(enabled)) != 0 or not enabled.value: continue
            if self.SUSceneGetCamera(scene, C.byref(reference)) != 0 or not reference.ptr: continue
            name = self.string_property(scene, "SUSceneGetName", f"Scene {index + 1}")
            value = self.camera(reference, f"scene_{index + 1}", name, "scene")
            if value: scenes.append(value)
        # Model/scene camera references are borrowed and must not be released.
        return current, scenes

    def read(self, path: Path) -> dict:
        model = Ref()
        self._materials.clear(); self.warning_messages.clear(); self._texture_bytes = 0
        self.SUInitialize()
        try:
            self.check(self.SUModelCreateFromFile(C.byref(model), str(path).encode("utf-8")), "Load")
            entities = Ref()
            self.check(self.SUModelGetEntities(model, C.byref(entities)), "GetEntities")
            output = ["# SketchUp preview; coordinates in metres; Z-up", "o SketchUpModel"]
            stats = {"triangles": 0, "vertices": 0, "faces": 0, "groups": 0, "instances": 0,
                     "uv_coordinates": 0, "textured_faces": 0}
            bounds = [[math.inf] * 3, [-math.inf] * 3]
            cache = {}
            used_materials = {}
            default_material = {"id": "mat_default", "name": "默认材质", "color": [.84, .85, .88, 1.], "opacity": 1., "texture": None, "source_type": "default"}

            def mesh(face, uv_side=None):
                key = (face.ptr, uv_side)
                if key in cache:
                    return cache[key]
                helper = Ref()
                self.check(self.SUMeshHelperCreate(C.byref(helper), face), "Triangulate")
                try:
                    vertices, triangles = C.c_size_t(), C.c_size_t()
                    self.check(self.SUMeshHelperGetNumVertices(helper, C.byref(vertices)), "GetNumVertices")
                    self.check(self.SUMeshHelperGetNumTriangles(helper, C.byref(triangles)), "GetNumTriangles")
                    if triangles.value > MAX_TRIANGLES or vertices.value > MAX_TRIANGLES * 3:
                        raise ValueError("模型三角面数量超过预览上限，请简化模型")
                    if not vertices.value or not triangles.value:
                        return [], [], None
                    points = (Point * vertices.value)()
                    indices = (C.c_size_t * (triangles.value * 3))()
                    copied = C.c_size_t()
                    self.check(self.SUMeshHelperGetVertices(helper, vertices.value, points, C.byref(copied)), "GetVertices")
                    if copied.value != vertices.value:
                        raise ValueError("SDK 返回不完整的顶点数据")
                    self.check(self.SUMeshHelperGetVertexIndices(helper, triangles.value * 3, indices, C.byref(copied)), "GetIndices")
                    if copied.value != triangles.value * 3 or any(index >= vertices.value for index in indices):
                        raise ValueError("SDK 返回无效的三角面索引")
                    uv = None
                    if uv_side is not None:
                        name = "SUMeshHelperGetBackSTQCoords" if uv_side else "SUMeshHelperGetFrontSTQCoords"
                        stq = (Point * vertices.value)()
                        if hasattr(self, name) and getattr(self, name)(helper, vertices.value, stq, C.byref(copied)) == 0 and copied.value == vertices.value:
                            if all(math.isfinite(p.x) and math.isfinite(p.y) and math.isfinite(p.z) and abs(p.z) > 1e-12 for p in stq):
                                uv = [(point.x / point.z, point.y / point.z) for point in stq]
                            else: self.warn("部分贴图 STQ 坐标无效，保留几何与材质颜色")
                        else: self.warn("部分贴图面缺少原生 UV 数据，保留几何与材质颜色")
                    value = ([(point.x, point.y, point.z) for point in points], list(indices), uv)
                    cache[key] = value
                    return value
                finally:
                    self.SUMeshHelperRelease(C.byref(helper))

            def walk(container, matrix, parent_material, ancestors, depth=0):
                if depth > 64 or container.ptr in ancestors:
                    raise ValueError("模型组/组件存在循环或层级超过 64 层")
                chain = ancestors | {container.ptr}
                for face in self.references(container, "Faces"):
                    material, back = self.material(face, parent_material, face=True)
                    points, indices, uv = mesh(face, back if material["texture"] else None)
                    if stats["triangles"] + len(indices) // 3 > MAX_TRIANGLES:
                        raise ValueError("模型三角面数量超过 250000 预览上限，请简化模型")
                    used_materials[material["id"]] = material
                    color = (1., 1., 1.) if uv is not None and material["texture"] else material["color"][:3]
                    offset = stats["vertices"] + 1
                    uv_offset = stats["uv_coordinates"] + 1
                    output.append("usemtl " + material["id"])
                    for point in points:
                        transformed = transform_point(point, matrix)
                        if not all(math.isfinite(coordinate) for coordinate in transformed):
                            raise ValueError("模型包含无效坐标")
                        for axis in range(3):
                            bounds[0][axis] = min(bounds[0][axis], transformed[axis])
                            bounds[1][axis] = max(bounds[1][axis], transformed[axis])
                        output.append("v " + " ".join(format(value, ".10g") for value in (*transformed, *color)))
                    if uv is not None:
                        for point in uv: output.append("vt " + " ".join(format(value, ".10g") for value in point))
                        stats["uv_coordinates"] += len(uv)
                        stats["textured_faces"] += 1
                    reverse = mirrored(matrix)
                    for index in range(0, len(indices), 3):
                        triangle = indices[index:index + 3]
                        if reverse:
                            triangle = [triangle[0], triangle[2], triangle[1]]
                        output.append("f " + " ".join(f"{offset + vertex}/{uv_offset + vertex}" if uv is not None else str(offset + vertex) for vertex in triangle))
                    stats["triangles"] += len(indices) // 3
                    stats["vertices"] += len(points)
                    stats["faces"] += 1
                for kind, transform_name in (("Groups", "SUGroupGetTransform"), ("Instances", "SUComponentInstanceGetTransform")):
                    for instance in self.references(container, kind):
                        transform = Transform()
                        self.check(getattr(self, transform_name)(instance, C.byref(transform)), transform_name)
                        world = multiply_matrix(normalize_matrix(transform.values), matrix)
                        nested = Ref()
                        if kind == "Groups":
                            self.check(self.SUGroupGetEntities(instance, C.byref(nested)), "GroupGetEntities")
                            stats["groups"] += 1
                        else:
                            definition = Ref()
                            self.check(self.SUComponentInstanceGetDefinition(instance, C.byref(definition)), "GetDefinition")
                            self.check(self.SUComponentDefinitionGetEntities(definition, C.byref(nested)), "DefinitionGetEntities")
                            stats["instances"] += 1
                        output.append("g " + kind.lower() + str(stats["groups"] + stats["instances"]))
                        inherited, _back = self.material(instance, parent_material)
                        walk(nested, world, inherited, chain, depth + 1)

            walk(entities, IDENTITY, default_material, set())
            if not stats["triangles"]:
                raise ValueError("SKP 中没有可预览的三角面（纯线稿或空模型）")
            current_camera, cameras = self.cameras(model)
            material_list = list(used_materials.values())
            mtl = "\n".join("newmtl " + material["id"] + "\nKd " + " ".join(format(value, ".8g") for value in material["color"][:3]) + "\nd " + format(material["opacity"], ".8g") for material in material_list)
            return {"obj": "\n".join(output) + "\n", "mtl": mtl + "\n", "name": path.name, **stats,
                    "units": "metres", "source_units": "inches", "up_axis": "Z",
                    "bounds": {"min": bounds[0], "max": bounds[1]},
                    "dimensions_mm": [round((bounds[1][axis] - bounds[0][axis]) * 1000, 5) for axis in range(3)],
                    "vertex_colors": True, "textures": bool(stats["textured_faces"]),
                    "materials": material_list, "texture_count": sum(bool(material["texture"]) for material in material_list),
                    "cameras": cameras, "current_camera": current_camera,
                    "missing_sdk_exports": self.missing_exports, "warnings": self.warning_messages}
        finally:
            if model.ptr:
                self.SUModelRelease(C.byref(model))
            self.SUTerminate()
            if self.directory_handle:
                self.directory_handle.close()


def find_sdk(settings: dict) -> Path | None:
    configured = settings.get("sketchup_sdk") or os.environ.get("ARCHITECTURE_SKETCHUP_SDK", "")
    if configured:
        if not isinstance(configured, (str, Path)):
            return None
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            candidate = candidate / "SketchUpAPI.dll"
        return candidate.resolve() if candidate.is_file() else None
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    candidates = sorted((program_files / "SketchUp").glob("SketchUp */SketchUpAPI.dll"), reverse=True)
    # Development-machine fallback to the installed reference product. Never copied.
    candidates.append(Path("E:/worksoft/jzxz/SketchUpAPI.dll"))
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def approved_model_path(store, value: str) -> Path:
    source = local_path(value)
    if source.suffix.lower() != ".skp":
        raise ValueError("此接口用于 SKP 原生预览；OBJ、STL、GLB 使用客户端本地查看器")
    # Imported source paths map to immutable workspace-managed copies.
    for item in store.resources() + store.knowledge_items():
        if source == Path(item["path"]).resolve() or source == Path(item.get("source_path", item["path"])).resolve():
            return local_path(store._owned_path(item["path"]))
    with store.connection() as db:
        files = db.execute("SELECT path FROM files").fetchall()
    if any(source == Path(row["path"]).resolve() for row in files):
        return local_path(store._owned_path(str(source)))
    roots = [Path(store.get_settings()["workspace"]).resolve()]
    roots.extend(Path(session["workspace"]).resolve() for session in store.sessions())
    if not any(source.is_relative_to(root) for root in roots):
        raise ValueError("模型须先导入素材库/上传附件，或位于已选工作区内")
    return source


def isolated_preview(source: Path, sdk: Path, data_dir: Path, timeout: float = 60) -> dict:
    jobs = data_dir / "native-jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skp-", dir=jobs) as directory:
        output = Path(directory) / "result.json"
        prefix = [sys.executable, "native-geometry-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "architecture_workbench.geometry", "--worker"]
        command = prefix + [str(source), str(sdk), str(output)]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parent.parent), environment.get("PYTHONPATH", "")]))
        try:
            process = subprocess.run(command, cwd=str(sdk.parent), env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exception:
            raise ValueError("SKP 原生解析超过 60 秒，隔离进程已停止，请简化模型") from exception
        if process.returncode:
            if output.is_file():
                payload = json.loads(output.read_text(encoding="utf-8"))
                raise ValueError(payload.get("error", "SKP 原生解析失败"))
            code = f"0x{process.returncode & 0xffffffff:08X}"
            raise ValueError(f"SketchUp 原生 SDK 隔离进程异常退出（{code}）；主服务仍可用，请检查 SDK 位数及运行依赖")
        if not output.is_file() or output.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("SKP 解析未返回有效预览，或预览数据过大")
        return json.loads(output.read_text(encoding="utf-8"))


class ModelInput(BaseModel):
    path: str


def register_geometry(app):
    router = APIRouter(prefix="/api")
    store = app.state.store

    @router.get("/model/status")
    def status():
        sdk = find_sdk(store.get_settings())
        return {"sdk_found": bool(sdk), "sdk_path": str(sdk) if sdk else "", "isolated": True,
                "error": "" if sdk else "未找到 SketchUpAPI.dll；请设置 sketchup_sdk 为本机 SketchUp SDK 路径"}

    @router.post("/model/preview")
    def preview(body: ModelInput):
        try:
            source = approved_model_path(store, body.path)
            sdk = find_sdk(store.get_settings())
            if sdk is None:
                raise HTTPException(503, "未找到 SketchUp 原生 SDK；在设置 sketchup_sdk 中指定 SketchUpAPI.dll（SDK 不随工作台分发）")
            result = isolated_preview(source, sdk, store.data_dir)
            attachment = store.save_attachment(source.stem + ".obj", io.BytesIO(result["obj"].encode("utf-8")))
            return {**result, "name": Path(body.path).name, "file_id": attachment["id"], "url": attachment["url"], "path": attachment["path"]}
        except (ValueError, OSError) as exception:
            raise HTTPException(422, str(exception)) from exception

    app.include_router(router)


def _worker(arguments):
    source, sdk, output = map(Path, arguments)
    if os.name == "nt":
        # Prevent a native access violation from opening a blocking Windows dialog.
        C.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    try:
        payload = SketchUpNative(sdk).read(source)
        output.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    except Exception as exception:
        output.write_text(json.dumps({"error": str(exception)}, ensure_ascii=False), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        raise SystemExit(_worker(sys.argv[2:]))
    raise SystemExit("This module is an isolated native worker; use the workbench API.")
