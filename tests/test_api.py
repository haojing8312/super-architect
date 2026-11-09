from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from architecture_workbench.app import create_app


@pytest.fixture
def client(tmp_path):
    application = create_app(tmp_path / "data")
    with TestClient(application) as test_client:
        test_client.headers["X-Workbench-Token"] = test_client.get("/api/bootstrap").json()["token"]
        yield test_client


def new_session(client):
    response = client.post("/api/sessions", json={"title": "测试项目"})
    assert response.status_code == 201
    return response.json()["id"]


def test_knowledge_http_source_filter_uses_real_imported_documents(client,tmp_path):
    first=tmp_path/'资料甲.txt';first.write_text('资料甲采用蓝图线条。',encoding='utf-8')
    second=tmp_path/'资料乙.txt';second.write_text('资料乙采用蓝图说明。',encoding='utf-8')
    left=client.post('/api/knowledge/import',json={'path':str(first)}).json()
    right=client.post('/api/knowledge/import',json={'path':str(second)}).json()
    result=client.post('/api/knowledge/query',json={'query':'蓝图','source_id':left['id']})
    assert result.status_code==200 and result.json()['items']
    assert {item['source_id'] for item in result.json()['items']}=={left['id']}
    assert right['id'] not in {item['source_id'] for item in result.json()['items']}
    assert client.post('/api/knowledge/query',json={'query':'蓝图','source_id':''}).status_code==422


def test_api_session_lifecycle_and_error_contract(client):
    assert client.get("/api/health").json()["status"] == "ok"
    identifier = new_session(client)
    assert client.get("/api/sessions").json()["items"][0]["id"] == identifier
    assert client.put(f"/api/sessions/{identifier}", json={"title": "修改后的项目", "provider": "codebuddy"}).json()["title"] == "修改后的项目"
    assert client.get(f"/api/sessions/{identifier}/messages").json() == {"items": []}
    assert client.delete(f"/api/sessions/{identifier}").json()["deleted"]
    missing = client.get(f"/api/sessions/{identifier}")
    assert missing.status_code == 404 and isinstance(missing.json()["detail"], str)
    invalid = client.post("/api/knowledge/query", json={"query": ""})
    assert invalid.status_code == 422 and isinstance(invalid.json()["detail"], str)
    validation = client.post("/api/memories", json={})
    assert validation.status_code == 422 and isinstance(validation.json()["detail"], str)


def test_api_attachment_download_and_html_inertness(client):
    response = client.post("/api/attachments", files={"file": ("../notes.txt", b"workspace file", "text/plain")})
    assert response.status_code == 201
    uploaded = response.json()
    assert {"id", "name", "path", "type", "size", "url"}.issubset(uploaded)
    assert client.get(uploaded["url"]).content == b"workspace file"
    html = client.post("/api/attachments", files={"file": ("active.html", b"<script>alert(1)</script>", "text/html")}).json()
    download = client.get(html["url"])
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/files/not-found").status_code == 404


def test_api_knowledge_resource_and_report_paths(client, tmp_path):
    source = tmp_path / "规范.md"
    source.write_text("框架结构设计：抗震设计验算层间位移。", encoding="utf-8")
    item = client.post("/api/knowledge/import", json={"path": str(source)}).json()
    results = client.post("/api/knowledge/query", json={"query": "抗震"}).json()["items"]
    assert results[0]["source_id"] == item["id"]
    resource = client.post("/api/resources/import", json={"path": str(source)}).json()
    assert "框架" in client.get(resource["url"]).text
    report = client.post("/api/report", json={"title": "建筑作品集", "sections": [{"title": "设计分析", "content": "<script>alert(1)</script>\n这是设计结果。"}]}).json()
    assert report["portable"]
    file = client.get(report["url"])
    assert "<script>" not in file.text
    assert "&lt;script&gt;" in file.text
    assert file.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in file.headers["content-security-policy"]
    assert client.get("/api/reports").json()["items"][0]["id"] == report["id"]
    assert client.delete(f"/api/knowledge/{item['id']}").json()["deleted"]
    assert client.delete(f"/api/resources/{resource['id']}").json()["deleted"]
    assert source.exists()


def test_api_memory_settings_canvas_todos_workspace(client, tmp_path):
    identifier = new_session(client)
    settings = client.get("/api/settings").json()
    assert settings["provider"] == "codex"
    assert client.put("/api/settings", json={"theme": "dark"}).json()["theme"] == "dark"
    memory = client.post("/api/memories", json={"content": "项目采用轻钢结构", "category": "project"}).json()
    assert client.get("/api/memories").json()["items"][0]["id"] == memory["id"]
    assert client.get("/api/search", params={"q": "轻钢"}).json()["items"]
    assert client.delete(f"/api/memories/{memory['id']}").json()["deleted"]
    assert client.put(f"/api/canvas/{identifier}", json={"elements": [{"id": "rect"}], "viewport": {"zoom": 2}}).json()["elements"]
    assert client.get(f"/api/canvas/{identifier}").json()["viewport"]["zoom"] == 2
    assert client.put(f"/api/sessions/{identifier}/todos", json={"items": [{"title": "完成方案"}]}).json()["items"][0]["id"]
    assert len(client.get(f"/api/sessions/{identifier}/todos").json()["items"]) == 1
    files = client.post("/api/workspace/files", json={"path": settings["workspace"]})
    assert files.status_code == 200 and "items" in files.json()
    rejected = client.post("/api/workspace/files", json={"path": str(tmp_path)})
    assert rejected.status_code == 422
    invalid = client.put("/api/settings", json={"provider": "remote-api"})
    assert invalid.status_code == 422


def test_api_real_dxf_preview_and_unsupported_dwg(client, tmp_path):
    import ezdxf
    drawing = ezdxf.new("R2010")
    drawing.modelspace().add_line((0, 0), (20, 10))
    drawing.modelspace().add_circle((10, 5), radius=3)
    drawing.modelspace().add_text("Building A", dxfattribs={"height": 1})
    source = tmp_path / "drawing.dxf"
    drawing.saveas(source)
    response = client.post("/api/drawing/preview", json={"path": str(source)})
    assert response.status_code == 200, response.text
    result = response.json()
    assert "<svg" in result["svg"]
    assert result["entities"] == 3
    assert result["entity_types"] == {"LINE": 1, "CIRCLE": 1, "TEXT": 1}
    assert client.get(result["url"]).status_code == 200
    bad = tmp_path / "drawing.dwg"
    bad.write_bytes(b"AC1024")
    unsupported = client.post("/api/drawing/preview", json={"path": str(bad)})
    assert unsupported.status_code == 422 and "DXF" in unsupported.json()["detail"]


def test_api_bad_file_paths_and_corrupt_documents_return_clear_errors(client, tmp_path):
    assert client.post("/api/knowledge/import", json={"path": str(tmp_path / "missing.pdf")}).status_code == 422
    assert client.post("/api/resources/import", json={"path": str(tmp_path)}).status_code == 422
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"bad excel")
    response = client.post("/api/knowledge/import", json={"path": str(broken)})
    assert response.status_code == 422 and "解析失败" in response.json()["detail"]
    assert not client.get("/api/knowledge").json()["items"]
    assert client.put("/api/settings", json={"workspace": 123}).status_code == 422
    assert client.put("/api/settings", json={"provider": {"name": "codex"}}).status_code == 422
    assert client.post("/api/report", json={"title": "无效章节", "sections": [{"title": "x", "assets": None}]}).status_code == 422
