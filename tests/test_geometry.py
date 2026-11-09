"""Actual SDK-written SKP fixtures; SDK and models are not redistributed.

python tests/test_geometry.py --create-fixture SDK_PATH .local/native-fixtures/test.skp
"""
from __future__ import annotations

import os
import base64
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from architecture_workbench.geometry import IDENTITY, approved_model_path, find_sdk, isolated_preview, multiply_matrix, normalize_matrix, register_geometry, transform_point
from architecture_workbench.store import Store

FIXTURE_SCRIPT = r'''
import ctypes as C
import sys
from pathlib import Path
from architecture_workbench.geometry import SketchUpNative, Ref, Point, Transform
api = SketchUpNative(Path(sys.argv[1]))
target = Path(sys.argv[2]); target.parent.mkdir(parents=True, exist_ok=True)
R = C.POINTER(Ref)
api.bind('SUModelCreate', [R])
api.bind('SUModelSaveToFile', [Ref, C.c_char_p])
api.bind('SUGeometryInputCreate', [R])
api.bind('SUGeometryInputRelease', [R])
api.bind('SUGeometryInputSetVertices', [Ref, C.c_size_t, C.POINTER(Point)])
api.bind('SULoopInputCreate', [R])
api.bind('SULoopInputAddVertexIndex', [Ref, C.c_size_t])
api.bind('SUGeometryInputAddFace', [Ref, R, C.POINTER(C.c_size_t)])
api.bind('SUEntitiesFill', [Ref, Ref, C.c_bool])
api.bind('SUGroupCreate', [R])
api.bind('SUEntitiesAddGroup', [Ref, Ref])
api.bind('SUGroupSetTransform', [Ref, C.POINTER(Transform)])
api.bind('SUComponentDefinitionCreate', [R])
api.bind('SUModelAddComponentDefinitions', [Ref, C.c_size_t, R])
api.bind('SUComponentDefinitionCreateInstance', [Ref, R])
api.bind('SUComponentInstanceSetTransform', [Ref, C.POINTER(Transform)])
api.bind('SUEntitiesAddInstance', [Ref, Ref, R])
def check(code): api.check(code, 'native_fixture_creation')
def matrix(tx=0, ty=0, sx=1, sy=1, sz=1):
    return Transform((C.c_double * 16)(sx,0,0,0,0,sy,0,0,0,0,sz,0,tx,ty,0,1))
def face(entities):
    geometry, loop = Ref(), Ref()
    check(api.SUGeometryInputCreate(C.byref(geometry)))
    try:
        points = (Point * 3)(Point(0,0,0), Point(10,0,0), Point(0,10,10))
        check(api.SUGeometryInputSetVertices(geometry, 3, points))
        check(api.SULoopInputCreate(C.byref(loop)))
        for index in range(3): check(api.SULoopInputAddVertexIndex(loop, index))
        index = C.c_size_t()
        check(api.SUGeometryInputAddFace(geometry, C.byref(loop), C.byref(index)))
        check(api.SUEntitiesFill(entities, geometry, True))
    finally: api.SUGeometryInputRelease(C.byref(geometry))
def group(parent, transform):
    value, entities = Ref(), Ref()
    check(api.SUGroupCreate(C.byref(value)))
    check(api.SUEntitiesAddGroup(parent, value))
    check(api.SUGroupSetTransform(value, C.byref(transform)))
    check(api.SUGroupGetEntities(value, C.byref(entities)))
    return entities
def instance(parent, definition, transform):
    value = Ref()
    check(api.SUComponentDefinitionCreateInstance(definition, C.byref(value)))
    check(api.SUEntitiesAddInstance(parent, value, None))
    check(api.SUComponentInstanceSetTransform(value, C.byref(transform)))
model, root = Ref(), Ref()
api.SUInitialize()
try:
    check(api.SUModelCreate(C.byref(model)))
    check(api.SUModelGetEntities(model, C.byref(root)))
    face(root)
    first = group(root, matrix(tx=100, sx=2, sy=2, sz=2)); face(first)
    nested = group(first, matrix(ty=20)); face(nested)
    definition, content = Ref(), Ref()
    check(api.SUComponentDefinitionCreate(C.byref(definition)))
    check(api.SUModelAddComponentDefinitions(model, 1, (Ref * 1)(definition)))
    check(api.SUComponentDefinitionGetEntities(definition, C.byref(content))); face(content)
    instance(root, definition, matrix(ty=100))
    instance(nested, definition, matrix(tx=5, ty=5))
    instance(root, definition, matrix(tx=200, sx=-1))
    check(api.SUModelSaveToFile(model, str(target).encode('utf-8')))
    print(str(target))
finally:
    if model.ptr: api.SUModelRelease(C.byref(model))
    api.SUTerminate()
'''


def create_native_fixture(sdk: Path, target: Path) -> Path:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1] / "backend"), environment.get("PYTHONPATH", "")]))
    process = subprocess.run([sys.executable, "-c", FIXTURE_SCRIPT, str(sdk), str(target.resolve())], cwd=str(sdk.parent), env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if process.returncode:
        raise AssertionError(f"Native SDK fixture failed {process.returncode}: {process.stderr.decode('utf-8', errors='replace')}")
    assert target.is_file() and target.stat().st_size > 100
    return target.resolve()


MATERIAL_FIXTURE_SCRIPT = r'''
import ctypes as C
import sys
from pathlib import Path
from PIL import Image, ImageDraw
from architecture_workbench.geometry import SketchUpNative, Ref, Point, Color
api=SketchUpNative(Path(sys.argv[1])); source=Path(sys.argv[2])
R=C.POINTER(Ref); P=C.POINTER(Point)
api.bind('SUModelSaveToFile',[Ref,C.c_char_p])
api.bind('SUMaterialCreate',[R])
api.bind('SUMaterialSetName',[Ref,C.c_char_p])
api.bind('SUMaterialSetColor',[Ref,C.POINTER(Color)])
api.bind('SUMaterialSetOpacity',[Ref,C.c_double])
api.bind('SUModelAddMaterials',[Ref,C.c_size_t,R])
api.bind('SUTextureCreateFromFile',[R,C.c_char_p,C.c_double,C.c_double])
api.bind('SUMaterialSetTexture',[Ref,Ref])
api.bind('SUFaceSetFrontMaterial',[Ref,Ref])
api.bind('SUFaceSetBackMaterial',[Ref,Ref])
api.bind('SUDrawingElementSetMaterial',[Ref,Ref])
api.bind('SUCameraCreate',[R])
api.bind('SUCameraRelease',[R])
api.bind('SUCameraSetOrientation',[Ref,P,P,P])
api.bind('SUCameraSetPerspectiveFrustumFOV',[Ref,C.c_double])
api.bind('SUCameraSetOrthographicFrustumHeight',[Ref,C.c_double])
api.bind('SUCameraSetAspectRatio',[Ref,C.c_double])
api.bind('SUSceneCreate',[R])
api.bind('SUSceneSetName',[Ref,C.c_char_p])
api.bind('SUModelAddScenes',[Ref,C.c_size_t,R])
api.bind('SUSceneSetUseCamera',[Ref,C.c_bool])
api.bind('SUSceneSetCamera',[Ref,Ref])
def check(code): api.check(code,'material_camera_fixture_creation')
image_path=source.with_suffix('.checker.png')
image=Image.new('RGBA',(32,32)); draw=ImageDraw.Draw(image)
for box,color in [((0,0,15,15),(255,0,0,255)),((16,0,31,15),(0,255,0,255)),((0,16,15,31),(0,0,255,128)),((16,16,31,31),(255,255,0,255))]: draw.rectangle(box,fill=color)
image.save(image_path)
model,entities=Ref(),Ref()
api.SUInitialize()
try:
 check(api.SUModelCreateFromFile(C.byref(model),str(source).encode('utf-8')))
 check(api.SUModelGetEntities(model,C.byref(entities)))
 texture_material,texture,green,red=Ref(),Ref(),Ref(),Ref()
 for material,name,color in [(texture_material,'棋盘纹理',(255,255,255,255)),(green,'继承绿色',(20,180,70,255)),(red,'半透明红色',(220,35,70,255))]:
  check(api.SUMaterialCreate(C.byref(material)))
  check(api.SUMaterialSetName(material,name.encode('utf-8')))
  check(api.SUMaterialSetColor(material,C.byref(Color(*color))))
  check(api.SUModelAddMaterials(model,1,(Ref*1)(material)))
 check(api.SUTextureCreateFromFile(C.byref(texture),str(image_path).encode('utf-8'),1.,1.))
 check(api.SUMaterialSetTexture(texture_material,texture))
 check(api.SUMaterialSetOpacity(red,.4))
 first_face=api.references(entities,'Faces')[0]
 check(api.SUFaceSetFrontMaterial(first_face,texture_material))
 first_group=api.references(entities,'Groups')[0]; group_entities=Ref()
 check(api.SUDrawingElementSetMaterial(first_group,texture_material if len(sys.argv)>3 and sys.argv[3]=='1' else green))
 check(api.SUGroupGetEntities(first_group,C.byref(group_entities)))
 check(api.SUFaceSetBackMaterial(api.references(group_entities,'Faces')[0],texture_material))
 check(api.SUDrawingElementSetMaterial(api.references(entities,'Instances')[-1],red))
 def orientation(camera,position,target,up):
  eye,look,vector=Point(*position),Point(*target),Point(*up)
  check(api.SUCameraSetOrientation(camera,C.byref(eye),C.byref(look),C.byref(vector)))
 current=Ref(); check(api.SUModelGetCamera(model,C.byref(current)))
 orientation(current,(50,-70,40),(0,0,0),(0,0,1))
 check(api.SUCameraSetPerspectiveFrustumFOV(current,40.))
 check(api.SUCameraSetAspectRatio(current,1.6))
 for name,position,up,perspective in [('外观透视',(80,-60,50),(0,0,1),True),('平面正交',(0,0,120),(0,1,0),False)]:
  scene,camera=Ref(),Ref()
  check(api.SUSceneCreate(C.byref(scene)))
  check(api.SUSceneSetName(scene,name.encode('utf-8')))
  check(api.SUModelAddScenes(model,1,(Ref*1)(scene)))
  check(api.SUCameraCreate(C.byref(camera)))
  try:
   orientation(camera,position,(0,0,0),up)
   check(api.SUCameraSetPerspectiveFrustumFOV(camera,55.)) if perspective else check(api.SUCameraSetOrthographicFrustumHeight(camera,80.))
   check(api.SUCameraSetAspectRatio(camera,1.5))
   check(api.SUSceneSetCamera(scene,camera)); check(api.SUSceneSetUseCamera(scene,True))
  finally: api.SUCameraRelease(C.byref(camera))
 check(api.SUModelSaveToFile(model,str(source).encode('utf-8')))
 print(str(source))
finally:
 if model.ptr: api.SUModelRelease(C.byref(model))
 api.SUTerminate()
'''


def create_native_material_fixture(sdk: Path, target: Path, *, inherited_texture=False) -> Path:
    source = create_native_fixture(sdk, target)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1] / "backend"), environment.get("PYTHONPATH", "")]))
    process = subprocess.run([sys.executable, "-c", MATERIAL_FIXTURE_SCRIPT, str(sdk), str(source), "1" if inherited_texture else "0"], cwd=str(sdk.parent), env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if process.returncode:
        raise AssertionError(f"Material SDK fixture failed {process.returncode}: {process.stderr.decode('utf-8', errors='replace')}")
    return source


def test_transform_matches_reference_inches_to_metres():
    local = list(IDENTITY); local[13] = 20
    parent = [2.,0,0,0,0,2.,0,0,0,0,2.,0,100.,0,0,1.]
    world = multiply_matrix(local, parent)
    assert transform_point((0, 0, 0), world) == pytest.approx((2.54, 1.016, 0))
    assert transform_point((10, 10, 0), world) == pytest.approx((3.048, 1.524, 0))
    assert normalize_matrix([2 * value for value in parent]) == tuple(parent)


def test_model_path_requires_import_or_selected_workspace(tmp_path):
    store = Store(tmp_path / "data")
    outside = tmp_path / "outside.skp"; outside.write_bytes(b"test")
    with pytest.raises(ValueError, match="导入"): approved_model_path(store, str(outside))
    imported = store.import_resource(outside)
    assert approved_model_path(store, str(outside)) == Path(imported["path"])
    workspace = Path(store.get_settings()["workspace"]) / "inside.skp"; workspace.write_bytes(b"test")
    assert approved_model_path(store, str(workspace)) == workspace


def test_missing_sdk_returns_diagnostic_without_fake_preview(tmp_path):
    store = Store(tmp_path / "data"); store.save_settings({"sketchup_sdk": str(tmp_path / "missing.dll")})
    model = Path(store.get_settings()["workspace"]) / "model.skp"; model.write_bytes(b"test")
    application = FastAPI(); application.state.store = store; register_geometry(application)
    with TestClient(application) as client:
        assert not client.get("/api/model/status").json()["sdk_found"]
        response = client.post("/api/model/preview", json={"path": str(model)})
        assert response.status_code == 503 and "SDK" in response.json()["detail"]


def test_native_sdk_roundtrip_groups_instances_reflection_and_units(tmp_path):
    sdk = find_sdk({})
    if os.name != "nt" or sdk is None: pytest.skip("External Windows SketchUp SDK is not installed")
    source = create_native_fixture(sdk, tmp_path / "真实模型.skp")
    result = isolated_preview(source, sdk, tmp_path / "data")
    assert result["triangles"] == 6 and result["groups"] == 2 and result["instances"] == 3
    assert result["vertices"] == 18
    assert result["dimensions_mm"] == pytest.approx([5080, 2794, 508])
    vertices = [[float(value) for value in line.split()[1:4]] for line in result["obj"].splitlines() if line.startswith("v ")]
    assert any(vertex == pytest.approx([2.54, 1.016, 0]) for vertex in vertices)
    assert any(vertex == pytest.approx([2.794, 1.27, 0]) for vertex in vertices)
    assert any(vertex == pytest.approx([5.08, 0, 0]) for vertex in vertices)
    assert max(vertex[2] for vertex in vertices) == pytest.approx(.508)
    assert len([line for line in result["obj"].splitlines() if line.startswith("f ")]) == 6
    assert result["units"] == "metres" and result["source_units"] == "inches"


def test_bad_native_model_is_contained_and_next_read_still_works(tmp_path):
    sdk = find_sdk({})
    if os.name != "nt" or sdk is None: pytest.skip("External Windows SketchUp SDK is not installed")
    bad = tmp_path / "corrupt.skp"; bad.write_bytes(b"not a SketchUp model")
    with pytest.raises(ValueError, match="SDK|SketchUp"): isolated_preview(bad, sdk, tmp_path / "data")
    source = create_native_fixture(sdk, tmp_path / "valid.skp")
    assert isolated_preview(source, sdk, tmp_path / "data")["triangles"] == 6


def test_native_sdk_material_texture_uv_opacity_and_cameras(tmp_path):
    sdk = find_sdk({})
    if os.name != "nt" or sdk is None: pytest.skip("External Windows SketchUp SDK is not installed")
    source = create_native_material_fixture(sdk, tmp_path / "纹理场景.skp")
    result = isolated_preview(source, sdk, tmp_path / "data")
    assert result["triangles"] == 6 and result["groups"] == 2 and result["instances"] == 3
    assert result["dimensions_mm"] == pytest.approx([5080, 2794, 508])
    by_name = {material["name"]: material for material in result["materials"]}
    assert by_name["继承绿色"]["color"][:3] == pytest.approx([20 / 255, 180 / 255, 70 / 255])
    assert by_name["半透明红色"]["opacity"] == pytest.approx(.4)
    texture = by_name["棋盘纹理"]["texture"]
    assert result["textures"] and result["texture_count"] == 1 and result["textured_faces"] == 2
    assert texture["width"] == 32 and texture["height"] == 32 and texture["has_alpha"]
    png = base64.b64decode(texture["data_url"].split(",", 1)[1])
    assert hashlib.sha256(png).hexdigest() == texture["sha256"]
    from PIL import Image
    decoded = Image.open(io.BytesIO(png)).convert("RGBA")
    expected = Image.open(source.with_suffix(".checker.png")).convert("RGBA")
    assert decoded.tobytes() == expected.tobytes()  # verifies channels, alpha and vertical orientation
    uv = [list(map(float, line.split()[1:])) for line in result["obj"].splitlines() if line.startswith("vt ")]
    assert len(uv) == result["uv_coordinates"] == 6
    assert any(point != [0., 0.] for point in uv)
    assert any("/" in line for line in result["obj"].splitlines() if line.startswith("f "))
    assert {line[7:] for line in result["obj"].splitlines() if line.startswith("usemtl ")} == {material["id"] for material in result["materials"]}
    current = result["current_camera"]
    assert current["position"] == pytest.approx([1.27, -1.778, 1.016])
    assert current["fov_degrees"] == pytest.approx(40.) and current["perspective"]
    scenes = {camera["name"]: camera for camera in result["cameras"]}
    assert scenes["外观透视"]["position"] == pytest.approx([2.032, -1.524, 1.27])
    assert scenes["外观透视"]["fov_degrees"] == pytest.approx(55.)
    assert scenes["平面正交"]["position"] == pytest.approx([0, 0, 3.048])
    assert not scenes["平面正交"]["perspective"]
    assert scenes["平面正交"]["orthographic_height"] == pytest.approx(2.032)
    assert not result["warnings"]


def test_native_sdk_group_and_nested_component_inherit_texture_uv(tmp_path):
    sdk = find_sdk({})
    if os.name != "nt" or sdk is None: pytest.skip("External Windows SketchUp SDK is not installed")
    source = create_native_material_fixture(sdk, tmp_path / "继承贴图.skp", inherited_texture=True)
    result = isolated_preview(source, sdk, tmp_path / "data")
    # Two explicitly painted faces plus a nested group face and a component
    # face inherit the outer group's texture. The other two instances do not.
    assert result["triangles"] == 6 and result["textured_faces"] == 4
    assert result["texture_count"] == 1 and result["uv_coordinates"] == 12
    material = next(value for value in result["materials"] if value["name"] == "棋盘纹理")
    assert result["obj"].count("usemtl " + material["id"] + "\n") == 4
    coordinates = [line for line in result["obj"].splitlines() if line.startswith("vt ")]
    assert coordinates[:3] == coordinates[6:9] == coordinates[9:12]
    assert not result["warnings"]


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--create-fixture":
        print(create_native_fixture(Path(sys.argv[2]).resolve(), Path(sys.argv[3])))
    elif len(sys.argv) == 4 and sys.argv[1] == "--create-material-fixture":
        print(create_native_material_fixture(Path(sys.argv[2]).resolve(), Path(sys.argv[3])))
    else: raise SystemExit("Use --create-fixture SDK_PATH OUTPUT_SKP")
