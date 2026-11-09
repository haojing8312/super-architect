from __future__ import annotations

import io
from pathlib import Path

import pytest

from architecture_workbench.documents import extract_text, local_path
from architecture_workbench.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


def test_sessions_messages_settings_survive_restart(store):
    session = store.create_session("结构设计")
    store.update_session(session["id"], provider="codebuddy", skills=["structural-analysis"])
    message = store.add_message(session["id"], "user", "请验算柱承载力", attachments=["file-1"])
    store.save_settings({"model": "my-model", "theme": "dark"})
    reopened = Store(store.data_dir)
    assert reopened.get_session(session["id"])["skills"] == ["structural-analysis"]
    assert reopened.get_session(session["id"])["provider"] == "codebuddy"
    assert reopened.messages(session["id"])[0]["metadata"]["attachments"] == ["file-1"]
    assert reopened.messages(session["id"])[0]["id"] == message["id"]
    assert reopened.get_settings()["model"] == "my-model"
    assert Path(reopened.get_settings()["workspace"]).is_dir()
    assert Path(reopened.get_settings()["skill_source"]).name == "skills"


def test_session_inherits_default_provider_skills_and_pinned_survives(store):
    store.save_settings({"provider": "codebuddy", "skills": ["design"]})
    session = store.create_session("默认配置", profile="architect", pinned=True)
    assert session["provider"] == "codebuddy"
    assert session["skills"] == ["design"]
    store.update_session(session["id"], pinned=False, profile="engineer")
    reopened = Store(store.data_dir)
    assert not reopened.get_session(session["id"])["pinned"]
    assert reopened.get_session(session["id"])["profile"] == "engineer"


def test_session_delete_cascades_only_its_data(store):
    first = store.create_session("第一项目")
    second = store.create_session("第二项目")
    store.add_message(first["id"], "user", "项目需求")
    store.add_message(second["id"], "user", "保留需求")
    store.save_todos(first["id"], [{"id": "one", "title": "收集规范"}])
    store.save_todos(second["id"], [{"id": "one", "title": "独立待办"}])
    store.save_canvas(first["id"], {"elements": [{"id": "box", "type": "rect"}]})
    store.delete_session(first["id"])
    with pytest.raises(KeyError):
        store.get_session(first["id"])
    assert len(store.messages(second["id"])) == 1
    assert store.get_todos(second["id"])[0]["title"] == "独立待办"
    assert store.get_canvas(first["id"])["elements"] == []


def test_knowledge_chinese_retrieval_has_real_sources_and_deletes(store, tmp_path):
    structural = tmp_path / "结构规范.md"
    structural.write_text("# 混凝土结构\n抗震设计要求控制层间位移角。钢筋混凝土框架梁柱需要验算承载力。", encoding="utf-8")
    unrelated = tmp_path / "园林方案.txt"
    unrelated.write_text("景观设计采用乡土植物，植物布置应考虑灌溉需求。", encoding="utf-8")
    first = store.knowledge_import(structural)
    store.knowledge_import(unrelated)
    results = store.knowledge_query("钢筋混凝土承载力")
    assert results
    assert results[0]["source_id"] == first["id"]
    assert "钢筋混凝土" in results[0]["content"]
    assert results[0]["source_path"] == str(structural.resolve())
    assert Path(first["path"]).read_text(encoding="utf-8") == structural.read_text(encoding="utf-8")
    assert store.knowledge_import(structural)["deduplicated"]
    assert len(store.knowledge_items()) == 2
    assert not store.knowledge_query("totally_unrelated_word")
    store.delete_knowledge(first["id"])
    assert not store.knowledge_query("混凝土")
    assert structural.exists()
    assert not Path(first["path"]).exists()


def test_text_docx_and_xlsx_import(store, tmp_path):
    from docx import Document
    from openpyxl import Workbook
    document = Document()
    document.add_paragraph("公共建筑消防疏散")
    document.add_table(rows=1, cols=2).rows[0].cells[0].text = "疏散楼梯"
    word = tmp_path / "消防.docx"
    document.save(word)
    workbook = Workbook()
    workbook.active.title = "工程量"
    workbook.active.append(["项目", "数量"])
    workbook.active.append(["混凝土体积", 48.5])
    excel = tmp_path / "工程量.xlsx"
    workbook.save(excel)
    text = tmp_path / "说明.txt"
    text.write_text("建筑轴网设计", encoding="gb18030")
    store.knowledge_import(word)
    store.knowledge_import(excel)
    store.knowledge_import(text)
    assert store.knowledge_query("疏散楼梯")
    assert "48.5" in store.knowledge_query("混凝土体积")[0]["content"]
    assert store.knowledge_query("建筑轴网")


def make_text_pdf(path: Path):
    """A minimal genuine PDF fixture with a selectable text content stream."""
    stream = b"BT /F1 12 Tf 30 100 Td (reinforced concrete foundations) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    pdf += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(pdf)


def test_real_pdf_import_and_scanned_pdf_rejected(store, tmp_path):
    from pypdf import PdfWriter
    pdf = tmp_path / "foundation.pdf"
    make_text_pdf(pdf)
    item = store.knowledge_import(pdf)
    assert store.knowledge_query("concrete foundations")[0]["source_id"] == item["id"]
    scanned = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with scanned.open("wb") as stream:
        writer.write(stream)
    with pytest.raises(ValueError, match="OCR"):
        store.knowledge_import(scanned)


def test_invalid_document_leaves_no_index_or_copy(store, tmp_path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a ZIP or Word document")
    with pytest.raises(ValueError, match="解析失败"):
        store.knowledge_import(broken)
    assert store.knowledge_items() == []
    assert list((store.data_dir / "knowledge").iterdir()) == []


def test_resources_and_attachments_are_copied_and_safe(store, tmp_path):
    source = tmp_path / "sample.html"
    source.write_text("<script>alert('x')</script>", encoding="utf-8")
    resource = store.import_resource(source)
    assert Path(resource["path"]).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    attachment = store.save_attachment("../../a.txt", io.BytesIO(b"architectural notes"))
    assert attachment["name"] == "a.txt"
    assert Path(attachment["path"]).parent == store.data_dir / "attachments"
    assert store.get_file(attachment["id"])["size"] == 19
    store.delete_resource(resource["id"])
    assert source.exists()
    assert not Path(resource["path"]).exists()
    with pytest.raises(ValueError):
        store._owned_path(str(tmp_path / "outside.txt"))


def test_canvas_and_todos_are_persistent_and_validate_duplicates(store):
    session = store.create_session("建筑方案")
    store.save_canvas(session["id"], {"elements": [{"id": "image", "type": "image", "x": 42}], "viewport": {"zoom": 1.5}})
    store.save_todos(session["id"], [{"title": "方案比较", "completed": False}])
    reopened = Store(store.data_dir)
    assert reopened.get_canvas(session["id"])["elements"][0]["x"] == 42
    todo = reopened.get_todos(session["id"])[0]
    assert todo["id"]
    assert not todo["completed"]
    with pytest.raises(ValueError, match="重复"):
        reopened.save_todos(session["id"], [{"id": "a"}, {"id": "a"}])
    assert reopened.get_todos(session["id"])[0]["id"] == todo["id"]


def test_workspace_cannot_escape_selected_folder(store, tmp_path):
    workspace = Path(store.get_settings()["workspace"])
    (workspace / "方案.txt").write_text("floor plans", encoding="utf-8")
    assert store.workspace_files()["items"][0]["name"] == "方案.txt"
    with pytest.raises(ValueError, match="工作区"):
        store.workspace_files(str(tmp_path))
    with pytest.raises(ValueError, match="工作区"):
        local_path(tmp_path, directory=True, root=workspace)
    alternate = tmp_path / "另一个项目"
    alternate.mkdir()
    store.create_session("另一个项目", str(alternate))
    assert store.workspace_files(str(alternate))["path"] == str(alternate.resolve())


def test_portable_report_escapes_html_and_embeds_copied_assets(store, tmp_path):
    from PIL import Image
    image = tmp_path / "效果图.png"
    Image.new("RGB", (4, 4), "blue").save(image)
    resource = store.import_resource(image)
    result = store.create_report("方案 <一>", [{"title": "<script>alert(1)</script>", "content": f"<img src=x onerror=alert(1)>\n![效果图]({resource['url']})"}])
    report = Path(result["path"])
    text = report.read_text(encoding="utf-8")
    assert "data:image/png;base64," in text
    assert "<script>" not in text
    assert "&lt;img src=x" in text
    assert str(image) not in text
    assert "/api/resources/" not in text
    assert result["portable"] and result["asset_count"] == 1
    assert len(list((report.parent / "assets").iterdir())) == 1
    image.unlink()
    assert "data:image/png;base64," in report.read_text(encoding="utf-8")


def test_report_rejects_active_assets_and_cleans_only_new_report(store, tmp_path):
    bad = tmp_path / "active.svg"
    bad.write_text('<svg><script>alert(1)</script></svg>', encoding="utf-8")
    with pytest.raises(ValueError, match="SVG"):
        store.create_report("错误素材", [{"title": "第一章", "content": f"![x]({bad})"}])
    assert list((store.data_dir / "reports").iterdir()) == []
    assert bad.exists()


def test_global_search_matches_persisted_messages_and_memory(store, tmp_path):
    session = store.create_session("抗震项目")
    store.add_message(session["id"], "assistant", "抗震设计应验算构件承载力")
    store.save_memory("本项目按抗震设防要求设计", "project")
    matches = store.search("抗震")
    assert {item["kind"] for item in matches} == {"session", "message", "memory"}
    assert store.search("") == []
