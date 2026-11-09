"""Real fixed-editor integration and source/page/isolation failure cases."""
import base64
import copy
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from fastapi import FastAPI
from fastapi.testclient import TestClient

from architecture_workbench.interior import InteriorService, PRIMARY_SOURCE_REFERENCE, register_interior
from architecture_workbench.security import register_security
from architecture_workbench.store import Store

PROJECT = Path(__file__).resolve().parents[1]
EDITOR_ROOT = PROJECT / "resources" / "skills" / "interior-3d-web" / "固定编辑器"
pytestmark = pytest.mark.skipif(
    not EDITOR_ROOT.is_dir(),
    reason="请先执行 npm run build:interior 构建本仓库公开室内编辑器",
)


def plan_bytes():
    image = Image.new("RGB", (800, 600), "white")
    pen = ImageDraw.Draw(image)
    pen.rectangle((100, 100, 700, 500), outline="black", width=8)
    pen.line((100, 550, 700, 550), fill="black", width=2)
    pen.text((360, 552), "6000 mm", fill="black")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "data")
    source = store.save_attachment("6000mm-floorplan.png", io.BytesIO(plan_bytes()))
    interior = InteriorService(store)
    task = interior.create({"file_id": source["id"], "name": "明确尺寸合成户型", "styles": ["scandi", "cream"],
                            "scale_policy": "calibrated", "points_px": [[100, 550], [700, 550]], "reference_length_m": 6})
    return interior, task, source


def traced(service, task):
    service.trace(task["id"], {"rooms": [{"name": "合成客厅", "bounds_px": [100, 100, 600, 400]}]})
    return service.scene(task["id"])


def test_manual_floorplan_to_real_standalone_editor(service):
    interior, task, source = service
    scene = traced(interior, task)
    assert scene["rooms"][0]["size"] == [6., 4.]
    assert scene["drawing"]["scaleX"] == .01
    assert scene["drawing"]["status"] == "user-calibrated"
    assert "未进行 AI" in scene["recognition"]["summary"]
    assert base64.b64decode(scene["source"]["imageData"].split(",")[1]) == plan_bytes()
    html = interior.output_file(task["id"], "html").read_text(encoding="utf-8")
    assert "window.FixedInterior" in html and "__FROZEN_SCENE_MODULE__" not in html
    module = re.search(r'"editor-model":"data:text/javascript;base64,([^" ]+)"', html)
    assert module and task["source"]["sha256"] in base64.b64decode(module[1]).decode()
    assert len(html) > 3_000_000  # Real embedded offline renderer, not a menu mock.
    assert "http://www.w3.org/2000/svg" in html
    assert "https://cdn" not in html


@pytest.mark.parametrize("mutation", ["task", "source", "bitmap", "page", "evidence", "scale", "style"])
def test_rejects_cross_task_and_untruthful_source_binding(service, mutation):
    interior, task, _ = service
    scene = traced(interior, task)
    if mutation == "task": scene["generation"]["taskId"] = "b" * 32
    if mutation == "source": scene["generation"]["sourceSha256"] = "b" * 64
    if mutation == "bitmap": scene["source"]["imageData"] = scene["source"]["imageData"][:-10] + "AAAAAA=="
    if mutation == "page": scene["generation"]["page"] = 2
    if mutation == "evidence": scene["architecture"][0]["sourceEvidence"]["sourceSha256"] = "b" * 64
    if mutation == "scale": scene["drawing"]["scaleX"] = .02
    if mutation == "style": scene["schemes"]["oriental"] = {"name": "未选风格", "objects": []}
    with pytest.raises(ValueError): interior.save_scene(task["id"], scene)
    assert interior.get(task["id"])["revision"] == 1


def test_rejects_changed_source_and_screenshot_outside_task(service, tmp_path):
    interior, task, source = service
    scene = traced(interior, task)
    other = tmp_path / "unowned-proof.png"
    other.write_bytes(plan_bytes())
    scene["schemes"]["scandi"]["styleAudit"]["screenshots"] = [str(other)]
    with pytest.raises(ValueError, match="越出"): interior.save_scene(task["id"], scene)
    Path(source["path"]).write_bytes(plan_bytes() + b"changed")
    scene["schemes"]["scandi"]["styleAudit"]["screenshots"] = []
    with pytest.raises(ValueError, match="已经变化"): interior.save_scene(task["id"], scene)


def test_saved_editor_user_addition_and_immutable_revisions(service):
    interior, task, _ = service
    scene = traced(interior, task)
    before = interior.output_file(task["id"], "scene").read_bytes()
    furniture = {"id": "human-added-sofa", "name": "用户设计沙发", "layer": "design", "type": "sofa", "room": "room-1",
                 "pos": [3, .45, 2], "size": [2, .9, .85], "rotation": 0, "color": "#d1bcaa", "visible": True,
                 "material": {"preset": "fabric"}, "furnitureDetail": {"version": "furniture-detail/1.0", "styleId": "scandi"}}
    scene["schemes"]["scandi"]["objects"].append(furniture)
    result = interior.save_edited_scene(task["id"], scene)
    assert result["revision"] == 2
    saved = interior.scene(task["id"])
    evidence = saved["schemes"]["scandi"]["objects"][0]["sourceEvidence"]
    assert "not-observed-in-plan" in evidence["inferred"]
    assert evidence["sourceSha256"] == task["source"]["sha256"]
    assert (Path(task["output_dir"]) / "scene-0001.json").read_bytes() == before
    with pytest.raises(ValueError, match="尚未|缺少"): interior.assemble(task["id"], final=True)


def test_pdf_selected_page_is_enforced_and_bitmap_is_verified(tmp_path):
    pytest.importorskip("pypdfium2")
    store = Store(tmp_path / "pdf")
    red, blue = Image.new("RGB", (100, 80), "red"), Image.new("RGB", (100, 80), "blue")
    data = io.BytesIO();red.save(data, "PDF", save_all=True, append_images=[blue])
    source = store.save_attachment("two-pages.pdf", io.BytesIO(data.getvalue()))
    interior = InteriorService(store)
    task = interior.create({"file_id": source["id"], "page": 2})
    selected = Image.open(interior.source_file(task["id"]))
    r, g, b = selected.convert("RGB").getpixel((30, 30))
    assert b > 240 and r < 10
    interior.trace(task["id"], {"estimated_width_m": 4, "rooms": [{"name": "用户描绘", "bounds_px": [10, 10, 100, 80]}]})
    scene = interior.scene(task["id"])
    assert scene["source"]["page"] == 2 and scene["source"]["derivedFromSha256"] == task["source"]["sha256"]
    scene["source"]["page"] = 1
    with pytest.raises(ValueError, match="页码"): interior.save_scene(task["id"], scene)
    with pytest.raises(ValueError, match="页码"): interior.create({"file_id": source["id"], "page": 3})


def test_http_preview_is_opaque_and_uses_existing_nonce(service):
    interior, task, _ = service
    traced(interior, task)
    app = FastAPI();app.state.store = interior.store
    register_interior(app);register_security(app)
    with TestClient(app) as client:
        token = client.get("/api/bootstrap").json()["token"]
        route = f"/api/interior/tasks/{task['id']}/preview/file"
        assert client.get(route).status_code == 401
        response = client.get(route, params={"token": token, "channel": "a" * 32})
        assert response.status_code == 200
        csp = response.headers["content-security-policy"]
        assert "sandbox allow-scripts allow-downloads allow-forms;" in csp and "allow-same-origin" not in csp
        assert "connect-src 'none'" in csp and "form-action 'none'" in csp
        assert token not in response.text
        assert "interior-snapshot-request" in response.text
        download = client.get(f"/api/interior/tasks/{task['id']}/files/html/file", params={"token": token})
        assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        assert "interior-snapshot-request" not in download.text


def test_custom_style_requires_actual_current_references_and_profile(service):
    interior, task, source = service
    with pytest.raises(ValueError, match="至少一张"): interior.create({"file_id": source["id"], "styles": ["custom"]})
    with pytest.raises(ValueError, match="单独选择"): interior.create({"file_id": source["id"], "styles": ["custom", "scandi"]})
    reference = interior.store.save_attachment("human-style-reference.png", io.BytesIO(plan_bytes()))
    custom = interior.create({"file_id": source["id"], "styles": ["custom"], "reference_ids": [reference["id"]],
                             "scale_policy": "calibrated", "points_px": [[100, 550], [700, 550]], "reference_length_m": 6})
    manifest = interior.manifest(custom["id"])
    assert manifest["primarySource"]["id"] == source["id"]
    assert manifest["config"]["styleMode"] == "custom"
    assert manifest["config"]["customStyleReferenceIds"] == [reference["id"]]
    assert [x["role"] for x in manifest["attachments"]] == ["plan", "style-reference"]
    assert custom["reference_files"][0]["id"] == reference["id"]
    scene = traced(interior, task)
    scene["generation"]["taskId"] = custom["id"]
    scene["schemes"]["custom"] = scene["schemes"].pop("scandi")
    scene["schemes"].pop("cream")
    scene["style"] = "custom"
    with pytest.raises(ValueError, match="customProfile"): interior.save_scene(custom["id"], scene)
    scene["schemes"]["custom"]["customProfile"] = {"referenceImageIds": ["historical-reference"]}
    with pytest.raises(ValueError, match="customProfile"): interior.save_scene(custom["id"], scene)
    # Explicit human-selected material profile, no claim the synthetic raster
    # was automatically interpreted as a real room style.
    furniture = {"sofa": "wood-cloud", "chair": "woven", "table": "travertine", "cabinet": "circle-panel", "bed": "wood-frame", "lamp": "lantern",
                 "wood": "#a47755", "fabric": "#ddd0ba", "accent": "#333333", "metal": "#555555", "stone": "#bbbbbb", "rug": "#eeeeee"}
    material = {"pattern": "plaster", "preset": "paint", "color": "#eeeeee", "accent": "#cccccc", "textureSize": 1, "roughness": .8}
    scene["schemes"]["custom"]["customProfile"] = {"version": "custom-style/1.0", "furniture": furniture,
         "decor": {"art": "botanical", "rug": "woven", "vase": "round", "plant": "leafy", "frame": "#888888", "pot": "#aaaaaa", "leaf": "#335533", "accent": "#666666"},
         "surfaces": {x: copy.deepcopy(material) for x in ["floor", "wall", "featureWall", "ceiling"]},
         "referenceImageIds": [reference["id"]], "features": ["人工选型验收，参考合成图片；不声称 AI 视觉提取"]}
    interior.save_scene(custom["id"], scene)
    assert interior.assemble(custom["id"])["status"] == "draft-preview"


def write_authoring_candidate(interior, task, scene):
    authoring = copy.deepcopy(scene)
    authoring["source"]["imageData"] = PRIMARY_SOURCE_REFERENCE
    raw = json.dumps(authoring, ensure_ascii=False, indent=2).encode("utf-8")
    candidate = Path(task["candidate_scene_path"])
    with candidate.open("xb") as handle:
        handle.write(raw)
    return candidate, raw, authoring


def test_collect_expands_source_reference_only_and_keeps_authoring_bytes(service):
    interior, task, _ = service
    expected = traced(interior, task)
    candidate, raw, _ = write_authoring_candidate(interior, task, expected)
    result = interior.collect(task["id"])
    accepted = interior.scene(task["id"])
    assert result["revision"] == 2 and result["status"] == "draft-preview"
    assert accepted["source"]["imageData"] == expected["source"]["imageData"]
    assert accepted["source"]["imageData"].startswith("data:image/png;base64,")
    assert base64.b64decode(accepted["source"]["imageData"].split(",")[1]) == plan_bytes()
    assert accepted == {**expected, "revision": 2}
    assert candidate.read_bytes() == raw
    assert PRIMARY_SOURCE_REFERENCE.encode() in candidate.read_bytes()


@pytest.mark.parametrize("mutation", ["task", "file", "sha", "page", "boolean-page", "name", "sample", "original-bytes"])
def test_collect_source_reference_cannot_repair_false_binding(service, mutation):
    interior, task, source = service
    scene = traced(interior, task)
    if mutation == "task": scene["generation"]["taskId"] = "f" * 32
    if mutation == "file": scene["generation"]["sourceFileId"] = "f" * 32
    if mutation == "sha": scene["generation"]["sourceSha256"] = "f" * 64
    if mutation == "page": scene["generation"]["page"] = 2
    if mutation == "boolean-page": scene["generation"]["page"] = True
    if mutation == "name": scene["source"]["name"] = "foreign-plan.png"
    if mutation == "sample": scene["isSample"] = True
    candidate, raw, _ = write_authoring_candidate(interior, task, scene)
    if mutation == "original-bytes": Path(source["path"]).write_bytes(plan_bytes() + b"changed")
    with pytest.raises(ValueError): interior.collect(task["id"])
    assert interior.get(task["id"])["revision"] == 1
    assert candidate.read_bytes() == raw


def test_direct_scene_save_and_editor_import_reject_authoring_reference(service):
    interior, task, _ = service
    scene = traced(interior, task)
    scene["source"]["imageData"] = PRIMARY_SOURCE_REFERENCE
    with pytest.raises(ValueError, match="内嵌本次底图"): interior.save_scene(task["id"], scene)
    with pytest.raises(ValueError, match="内嵌本次底图"): interior.save_edited_scene(task["id"], scene)
    assert interior.get(task["id"])["revision"] == 1


def test_collect_never_substitutes_a_foreign_bitmap_without_exact_reference(service):
    interior, task, _ = service
    scene = traced(interior, task)
    other = io.BytesIO();Image.new("RGB", (800, 600), "red").save(other, "PNG")
    scene["source"]["imageData"] = "data:image/png;base64," + base64.b64encode(other.getvalue()).decode()
    candidate = Path(task["candidate_scene_path"])
    raw = json.dumps(scene, ensure_ascii=False).encode()
    candidate.write_bytes(raw)
    with pytest.raises(ValueError, match="原图"): interior.collect(task["id"])
    assert candidate.read_bytes() == raw
    assert interior.get(task["id"])["revision"] == 1


@pytest.mark.parametrize("mutation", [None, "derived-sha", "source-page", "selected-bitmap", "original-pdf"])
def test_pdf_reference_expands_only_verified_selected_page(tmp_path, mutation):
    pytest.importorskip("pypdfium2")
    store = Store(tmp_path / "pdf-reference")
    red, blue = Image.new("RGB", (100, 80), "red"), Image.new("RGB", (100, 80), "blue")
    pdf = io.BytesIO();red.save(pdf, "PDF", save_all=True, append_images=[blue])
    file = store.save_attachment("pages.pdf", io.BytesIO(pdf.getvalue()))
    interior = InteriorService(store)
    task = interior.create({"file_id": file["id"], "page": 2})
    interior.trace(task["id"], {"estimated_width_m": 4, "rooms": [{"name": "第二页人工描绘", "bounds_px": [10, 10, 100, 80]}]})
    expected = interior.scene(task["id"])
    scene = copy.deepcopy(expected)
    if mutation == "derived-sha": scene["source"]["derivedFromSha256"] = "e" * 64
    if mutation == "source-page": scene["source"]["page"] = 1
    candidate, raw, _ = write_authoring_candidate(interior, task, scene)
    if mutation == "selected-bitmap":
        altered = io.BytesIO();red.save(altered, "PNG")
        interior.source_file(task["id"]).write_bytes(altered.getvalue())
    if mutation == "original-pdf": Path(file["path"]).write_bytes(pdf.getvalue() + b"changed")
    if mutation:
        with pytest.raises(ValueError): interior.collect(task["id"])
        assert interior.get(task["id"])["revision"] == 1
    else:
        result = interior.collect(task["id"])
        accepted = interior.scene(task["id"])
        assert result["revision"] == 2
        assert accepted == {**expected, "revision": 2}
        pixel = Image.open(io.BytesIO(base64.b64decode(accepted["source"]["imageData"].split(",")[1]))).convert("RGB").getpixel((30, 30))
        assert pixel[2] > 240 and pixel[0] < 10
        assert accepted["source"]["derivedFromSha256"] == task["source"]["sha256"] and accepted["source"]["page"] == 2
    assert candidate.read_bytes() == raw


def test_prompt_uses_existing_mcp_under_read_only_shell_with_bounded_draft(service):
    interior, task, _ = service
    prompt = interior.prompt(task["id"])
    assert "shell 保持只读" in prompt and "architecture.session_get" in prompt
    assert task["session_id"] in prompt and task["output_dir"] in prompt
    assert 'file_write(path="本次方案.scene.json"' in prompt and 'file_write(path="识别核对记录.md"' in prompt
    assert "overwrite=false" in prompt and "2 MiB" in prompt and PRIMARY_SOURCE_REFERENCE in prompt
    assert "禁止改 sandbox" in prompt and "不运行组装、截图或正式交付脚本" in prompt
