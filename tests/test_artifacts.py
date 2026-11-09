import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from docx import Document
import pytest
from starlette.routing import Mount

from architecture_workbench.app import create_app
from architecture_workbench.artifacts import ArtifactResolver, register_artifacts
from architecture_workbench.store import Store


def count_files(store):
    with store.connection() as db:
        return db.execute("SELECT count(*) FROM files").fetchone()[0]


def test_real_raster_and_docx_are_cached_without_deleting_source(tmp_path):
    store = Store(tmp_path / "data")
    session = store.create_session("Artifacts")
    workspace = Path(session["workspace"])
    png = workspace / "建筑渲染.png"
    Image.new("RGB", (48, 32), (120, 80, 40)).save(png)
    document = Document()
    document.add_paragraph("工程方案产物")
    document.save(workspace / "方案.docx")
    resolver = ArtifactResolver(store)
    image = resolver.resolve(session["id"], png.name)
    assert image["image"] and image["type"] == "image/png"
    assert image["size"] == png.stat().st_size
    assert resolver.resolve(session["id"], str(png))["id"] == image["id"]
    assert ArtifactResolver(Store(store.data_dir)).resolve(session["id"], str(png))["id"] == image["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(lambda _: resolver.resolve(session["id"], str(png))["id"], range(8))) == {image["id"]}
    assert count_files(store) == 1
    doc = resolver.resolve(session["id"], "方案.docx")
    assert not doc["image"] and doc["url"].startswith("/api/files/")
    assert Document(store.get_file(doc["id"])["path"]).paragraphs[0].text == "工程方案产物"
    assert png.exists() and (workspace / "方案.docx").exists()
    Image.new("RGB", (49, 32), "red").save(png)
    replacement = resolver.resolve(session["id"], str(png))
    assert replacement["id"] != image["id"]
    assert Path(store.get_file(image["id"])["path"]).exists()


def test_artifact_scope_does_not_expose_other_workspace_or_database(tmp_path):
    store = Store(tmp_path / "data")
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    own = store.create_session("Own", str(tmp_path / "one"))
    other = store.create_session("Other", str(tmp_path / "two"))
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    other_file = Path(other["workspace"]) / "other.txt"
    other_file.write_text("other", encoding="utf-8")
    resolver = ArtifactResolver(store)
    for value in (str(outside), str(other_file), "../private.txt", str(store.db_path),
                  r"\\server\share\private.txt", "//server/share/private.txt", r"\\?\C:\secret.txt", "notes.txt:stream"):
        with pytest.raises(HTTPException) as error:
            resolver.resolve(own["id"], value)
        assert error.value.status_code == 403
    assert count_files(store) == 0


def test_existing_attachments_resources_and_reports_only(tmp_path):
    store = Store(tmp_path / "data")
    session = store.create_session("Owned data")
    resolver = ArtifactResolver(store)
    uploaded = store.save_attachment("notes.txt", io.BytesIO(b"uploaded"))
    resolved = resolver.resolve(session["id"], uploaded["path"])
    assert resolved["id"] == uploaded["id"] and count_files(store) == 1
    source = Path(session["workspace"]) / "material.txt"
    source.write_text("material", encoding="utf-8")
    resource = store.import_resource(source)
    report = store.create_report("Report", [{"title": "Design", "content": "Real report"}])
    assert not resolver.resolve(session["id"], resource["path"])["image"]
    html = resolver.resolve(session["id"], report["path"])
    assert not html["image"] and html["type"] == "text/html"
    assert Path(report["path"]).exists() and source.exists()


def test_invalid_images_shortcuts_and_symlinks_are_rejected(tmp_path):
    store = Store(tmp_path / "data")
    session = store.create_session("Unsafe inputs")
    root = Path(session["workspace"])
    resolver = ArtifactResolver(store)
    (root / "fake.png").write_text("<svg><script>alert(1)</script></svg>", encoding="utf-8")
    (root / "jump.url").write_text("[InternetShortcut]\nURL=file:///private", encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        resolver.resolve(session["id"], "fake.png")
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        resolver.resolve(session["id"], "jump.url")
    assert error.value.status_code == 403
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink creation needs developer mode")
    with pytest.raises(HTTPException) as error:
        resolver.resolve(session["id"], "link.txt")
    assert error.value.status_code == 403


def test_real_artifact_api_download_requires_existing_local_token(tmp_path):
    app = create_app(tmp_path / "data")
    register_artifacts(app)
    # In production root registers this router before the frontend's catch-all mount.
    route = app.router.routes.pop()
    insertion = next((index for index, item in enumerate(app.router.routes) if isinstance(item, Mount)), len(app.router.routes))
    app.router.routes.insert(insertion, route)
    session = app.state.store.create_session("HTTP artifacts")
    root = Path(session["workspace"])
    Image.new("RGB", (20, 20), "blue").save(root / "api.png")
    (root / "inert.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
    with TestClient(app) as client:
        assert client.post("/api/artifacts/resolve", json={"session_id": session["id"], "path": "api.png"}).status_code in {401, 403}
        token = client.get("/api/bootstrap").json()["token"]
        client.headers["X-Workbench-Token"] = token
        image = client.post("/api/artifacts/resolve", json={"session_id": session["id"], "path": "api.png"})
        assert image.status_code == 200, image.text
        downloaded = client.get(image.json()["url"])
        assert downloaded.status_code == 200 and downloaded.content == (root / "api.png").read_bytes()
        assert downloaded.headers["content-type"] == "image/png"
        svg = client.post("/api/artifacts/resolve", json={"session_id": session["id"], "path": "inert.svg"}).json()
        assert not svg["image"]
        inert = client.get(svg["url"])
        assert inert.headers["content-type"] == "application/octet-stream"
        assert inert.headers["content-disposition"].startswith("attachment")
        assert inert.headers["x-content-type-options"] == "nosniff"
        assert client.post("/api/artifacts/resolve", json={"session_id": "missing", "path": "api.png"}).status_code == 404
