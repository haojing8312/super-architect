import pytest
from fastapi.testclient import TestClient

from architecture_workbench.app import create_app
from architecture_workbench.design_workflow import KnowledgeCollections, canvas_deliverable_draft
from architecture_workbench.store import Store
from architecture_workbench.workspaces import WorkspaceStore


def fixture(store, directory, name, content):
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return store.knowledge_import(path)


def test_collections_filter_before_global_ranking_and_share_document(tmp_path):
    store = Store(tmp_path / "data")
    service = KnowledgeCollections(store)
    a, b = service.create("项目A"), service.create("项目B")
    one = fixture(store, tmp_path, "甲.txt", "合成设计要求：蓝色入口，采光采用天窗。")
    other = fixture(store, tmp_path, "乙.txt", "合成设计要求：红色入口，天窗天窗天窗天窗。")
    service.link(a["id"], one["id"])
    service.link(b["id"], other["id"])
    result = service.query("天窗", collection_id=a["id"])
    assert result and {row["source_id"] for row in result} == {one["id"]}
    assert "蓝色" in service.prompt("天窗", a["id"])["prompt"]
    assert "红色" not in service.prompt("天窗", a["id"])["prompt"]
    with pytest.raises(KeyError):
        service.query("天窗", collection_id=a["id"], source_id=other["id"])
    service.link(b["id"], one["id"])
    service.delete(a["id"])
    assert {row["id"] for row in service.documents(b["id"])} == {one["id"], other["id"]}
    assert len(store.knowledge_items()) == 2


def test_folder_import_recurses_records_partial_errors_and_deduplicates(tmp_path):
    store = Store(tmp_path / "data")
    service = KnowledgeCollections(store)
    collection = service.create("合成资料")
    source = tmp_path / "selected"
    (source / "nested").mkdir(parents=True)
    (source / "one.md").write_text("采光设计采用南侧窗。", encoding="utf-8")
    (source / "nested/two.txt").write_text("院落采用回廊。", encoding="utf-8")
    (source / "bad.pdf").write_bytes(b"broken pdf")
    result = service.import_path(str(source), collection["id"])
    assert result["imported"] == 2 and len(result["errors"]) == 1
    service.import_path(str(source), collection["id"])
    assert len(service.documents(collection["id"])) == 2


def test_selected_real_citation_to_canvas_and_editable_draft(tmp_path):
    store = Store(tmp_path / "data")
    service = KnowledgeCollections(store)
    item = fixture(store, tmp_path, "说明.txt", "合成方案：入口采用蓝色；院落保留天窗。")
    result = service.query("入口")
    board = service.cards("入口", citation_ids=[result[0]["citation_id"]])
    doc = store.get_canvas(board["canvas_id"])
    assert board["added_cards"] == 1
    card = doc["elements"][0]
    assert card["source"]["source_id"] == item["id"] and card["source"]["citation_id"] in card["text"]
    draft = canvas_deliverable_draft(store, board["canvas_id"], [card["id"]])
    assert draft["sections"][0]["sources"][0]["source_id"] == item["id"]
    assert "path" not in draft["sections"][0]["sources"][0]
    assert draft["origin"]["canvas_id"] == board["canvas_id"]
    from architecture_workbench.deliverables import normalize_spec
    assert "collection_id" not in card["source"]
    assert normalize_spec(draft)["sections"][0]["sources"][0]["source_id"] == item["id"]
    # Existing cards saved before this fix can still contain an unset group.
    card["source"]["collection_id"] = None
    store.save_canvas(board["canvas_id"], doc)
    assert normalize_spec(canvas_deliverable_draft(store, board["canvas_id"]))["sections"]
    with pytest.raises(ValueError):
        service.cards("入口", citation_ids=["RAG-FORGED"])
    store.save_canvas("default", doc)
    assert canvas_deliverable_draft(store, "default")["sections"]


def test_member_realm_collection_and_board_are_isolated(tmp_path):
    store = WorkspaceStore(tmp_path / "data")
    service = KnowledgeCollections(store)
    a = service.create("匿名库")
    item = fixture(store, tmp_path, "private.txt", "匿名私有词：青鹤。")
    service.link(a["id"], item["id"])
    board = service.cards("青鹤", a["id"], citation_ids=[service.query("青鹤", collection_id=a["id"])[0]["citation_id"]])
    store.activate("https://one.example", 0, 10)
    assert service.list() == []
    with pytest.raises(KeyError):
        service.documents(a["id"])
    with pytest.raises(ValueError):
        canvas_deliverable_draft(store, board["canvas_id"])
    store.activate()
    assert service.get(a["id"])["name"] == "匿名库"


def test_large_canvas_rejected_without_silently_dropping_sections(tmp_path):
    store = Store(tmp_path / "data")
    elements = [{"id": f"text-{i}", "type": "text", "text": f"完整章节 {i}"} for i in range(101)]
    store.save_canvas("large", {"elements": elements})
    with pytest.raises(ValueError, match="60"):
        canvas_deliverable_draft(store, "large")
    draft = canvas_deliverable_draft(store, "large", [row["id"] for row in elements[:60]])
    assert len(draft["sections"]) == 60
    assert draft["sections"][-1]["content"] == "完整章节 59"
    assert len(store.get_canvas("large")["elements"]) == 101


def test_actual_router_order_scoped_endpoints_and_bad_inputs(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as client:
        headers = {"X-Workbench-Token": client.get("/api/bootstrap").json()["token"]}
        result = client.post("/api/knowledge/collections", json={"name": "HTTP资料库"}, headers=headers)
        assert result.status_code == 201
        identifier = result.json()["id"]
        assert client.get(f"/api/knowledge/collections/{identifier}/documents", headers=headers).json() == {"items": []}
        for body in [{"query": "X", "limit": None}, {"query": "X", "limit": True}, {"query": None}]:
            assert client.post("/api/knowledge/scoped-query", json=body, headers=headers).status_code == 422
        assert client.post("/api/knowledge/folder-import", json={"path": None}, headers=headers).status_code == 422
        assert client.post("/api/knowledge/to-canvas", json={"query": "X", "citation_ids": "bad"}, headers=headers).status_code == 422
