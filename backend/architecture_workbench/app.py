"""The local business API used by the desktop, browser and agent tools."""
from __future__ import annotations

import io
import logging
import mimetypes
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .documents import IMAGE_MIMES, local_path, preview_dxf
from .workspaces import WorkspaceStore

log = logging.getLogger(__name__)


class SessionInput(BaseModel):
    title: str = "新会话"
    workspace: str = ""
    provider: str | None = None
    skills: list[str] | None = None
    profile: str = ""
    pinned: bool = False


class PathInput(BaseModel):
    path: str


class MemoryInput(BaseModel):
    content: str
    category: str = "general"


class QueryInput(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=30)
    source_id: str | None = Field(default=None, min_length=1, max_length=200)


class ReportInput(BaseModel):
    title: str
    sections: list[dict]
    theme: str = "minimal"


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    application = FastAPI(title="超级建筑师本地服务", version=__version__)
    store = WorkspaceStore(data_dir)
    application.state.store = store
    from .security import register_security
    register_security(application)

    @application.exception_handler(KeyError)
    async def missing_item(_request, exception):
        return JSONResponse(status_code=404, content={"detail": str(exception.args[0]) if exception.args else "项目不存在"})

    @application.exception_handler(ValueError)
    async def invalid_input(_request, exception):
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    @application.exception_handler(OSError)
    async def filesystem_error(_request, exception):
        log.warning("Filesystem request failed: %s", exception)
        return JSONResponse(status_code=422, content={"detail": "无法读取或写入本地文件，请检查路径及权限"})

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request, exception):
        errors = [f'{".".join(str(part) for part in error["loc"] if part != "body")}: {error["msg"]}' for error in exception.errors()]
        return JSONResponse(status_code=422, content={"detail": "；".join(errors)})

    @application.get("/api/health")
    def health():
        configured = bool(store.get_settings().get("embedding_model_dir"))
        return {"status": "ok", "product": "architecture-workbench", "version": __version__, "database": "sqlite",
                "knowledge_search": "semantic+keyword" if configured else "fts5+substring" if store.fts_available else "substring",
                "semantic_configured": configured, "semantic_model_verified": None}

    @application.get("/api/sessions")
    def sessions():
        return {"items": store.sessions()}

    @application.post("/api/sessions", status_code=201)
    def create_session(body: SessionInput):
        options = {"profile": body.profile, "pinned": body.pinned}
        if body.skills is not None:
            options["skills"] = body.skills
        if body.provider is not None:
            options["provider"] = body.provider
        return store.create_session(body.title, body.workspace, **options)

    @application.get("/api/sessions/{identifier}")
    def get_session(identifier: str):
        return store.get_session(identifier)

    @application.put("/api/sessions/{identifier}")
    def update_session(identifier: str, body: dict = Body(...)):
        return store.update_session(identifier, **body)

    @application.delete("/api/sessions/{identifier}")
    async def delete_session(identifier: str):
        if identifier in application.state.runtime_manager.active:
            raise HTTPException(409, "请先停止该会话的任务，再删除对话")
        store.delete_session(identifier)
        return {"deleted": True, "id": identifier}

    @application.get("/api/sessions/{identifier}/messages")
    def messages(identifier: str):
        return {"items": store.messages(identifier)}

    @application.get("/api/sessions/{identifier}/draft")
    def draft(identifier: str):
        store.get_session(identifier)
        return store.get_canvas("draft-"+identifier).get("draft", {"text": "", "attachments": []})

    @application.put("/api/sessions/{identifier}/draft")
    def save_draft(identifier: str, body: dict = Body(...)):
        store.get_session(identifier)
        if not isinstance(body.get("text", ""), str) or len(body.get("text", ""))>100000 or not isinstance(body.get("attachments", []),list):
            raise HTTPException(422, "输入草稿格式无效")
        store.save_canvas("draft-"+identifier,{"elements":[],"viewport":{},"draft":body})
        return body

    @application.get("/api/settings")
    def settings():
        return store.get_settings()

    @application.put("/api/settings")
    async def save_settings(body: dict = Body(...)):
        return JSONResponse(await application.state.remote.update_settings(body), headers={"X-Workbench-Account-Scope": store.account_scope})

    @application.get("/api/memories")
    def memories():
        return {"items": store.memories()}

    @application.post("/api/memories", status_code=201)
    def save_memory(body: MemoryInput):
        return store.save_memory(body.content, body.category)

    @application.delete("/api/memories/{identifier}")
    def delete_memory(identifier: str):
        store.delete_memory(identifier)
        return {"deleted": True, "id": identifier}

    @application.get("/api/knowledge")
    def knowledge():
        return {"items": store.knowledge_items()}

    @application.post("/api/knowledge/import", status_code=201)
    def import_knowledge(body: PathInput):
        return store.knowledge_import(body.path)

    @application.post("/api/knowledge/query")
    def query_knowledge(body: QueryInput):
        return {"items": store.knowledge_query(body.query, body.limit, source_id=body.source_id)}

    @application.delete("/api/knowledge/{identifier}")
    def delete_knowledge(identifier: str):
        store.delete_knowledge(identifier)
        return {"deleted": True, "id": identifier}

    @application.get("/api/resources")
    def resources():
        return {"items": store.resources()}

    @application.post("/api/resources/import", status_code=201)
    def import_resource(body: PathInput):
        return store.import_resource(body.path)

    @application.delete("/api/resources/{identifier}")
    def delete_resource(identifier: str):
        store.delete_resource(identifier)
        return {"deleted": True, "id": identifier}

    def file_response(item: dict, *, report: bool = False):
        file_path = local_path(store._owned_path(item["path"]))
        mime = item.get("type") or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if report:
            # Reports are escaped, self-contained, and carry their own inert CSP.
            headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; sandbox"
            return FileResponse(file_path, media_type="text/html", filename=item.get("title", "作品汇报") + ".html", headers=headers, content_disposition_type="attachment")
        if mime in IMAGE_MIMES:
            return FileResponse(file_path, media_type=mime, headers=headers)
        if mime.startswith("text/") and file_path.suffix.lower() not in {".html", ".htm", ".svg"}:
            return FileResponse(file_path, media_type="text/plain", headers=headers)
        # SVG/HTML imports may contain scripts. Serve them as downloads only.
        return FileResponse(file_path, media_type="application/octet-stream", filename=item["name"], headers=headers)

    @application.get("/api/resources/{identifier}/file")
    def get_resource_file(identifier: str):
        return file_response(store.get_resource(identifier))

    @application.post("/api/attachments", status_code=201)
    def upload_attachment(file: UploadFile = File(...)):
        try:
            return store.save_attachment(file.filename or "attachment.bin", file.file)
        finally:
            file.file.close()

    @application.get("/api/files/{identifier}")
    def get_file(identifier: str):
        return file_response(store.get_file(identifier))

    @application.get("/api/canvas/{identifier}")
    def canvas(identifier: str):
        return store.get_canvas(identifier)

    @application.put("/api/canvas/{identifier}")
    def save_canvas(identifier: str, body: dict = Body(...)):
        return store.save_canvas(identifier, body)

    @application.get("/api/sessions/{identifier}/todos")
    def todos(identifier: str):
        return {"items": store.get_todos(identifier)}

    @application.put("/api/sessions/{identifier}/todos")
    def save_todos(identifier: str, body: list[dict] | dict = Body(...)):
        items = body if isinstance(body, list) else body.get("items", body.get("todos"))
        return {"items": store.save_todos(identifier, items)}

    @application.get("/api/search")
    def search(q: str = ""):
        return {"items": store.search(q)}

    @application.post("/api/workspace/files")
    def workspace_files(body: PathInput):
        return store.workspace_files(body.path)

    @application.post("/api/drawing/preview")
    def drawing_preview(body: PathInput):
        source = local_path(body.path)
        try:
            rendered = preview_dxf(source)
        except ValueError:
            raise
        except Exception as exception:
            log.warning("DXF preview failed: %s", exception)
            raise HTTPException(422, "DXF 解析或渲染失败，请检查文件格式与实体内容") from exception
        file = store.save_attachment(source.stem + ".svg", io.BytesIO(rendered["svg"].encode("utf-8")))
        return {**rendered, "file_id": file["id"], "url": file["url"]}

    @application.post("/api/report", status_code=201)
    def report(body: ReportInput):
        return store.create_report(body.title, body.sections, theme=body.theme)

    @application.get("/api/reports")
    def reports():
        return {"items": store.reports()}

    @application.get("/api/reports/{identifier}/file")
    def report_file(identifier: str):
        return file_response(store.get_report(identifier), report=True)

    from .runtime import register_runtime
    from .skills import register_skills
    register_skills(application)
    register_runtime(application)
    from .interactions import register_interactions
    register_interactions(application)
    from .artifacts import register_artifacts
    register_artifacts(application)
    from .cad import register_cad
    register_cad(application)
    from .canvas_projects import register_canvas_projects
    register_canvas_projects(application)
    from .canvas_collaboration import register_canvas_collaboration
    register_canvas_collaboration(application)
    from .creative import register_creative
    from .hosts import register_hosts
    register_creative(application)
    register_hosts(application)
    from .reminders import register_reminders
    from .usage import register_usage
    register_reminders(application)
    register_usage(application)
    from .embeddings import close_all_workers
    application.router.add_event_handler("shutdown", close_all_workers)
    from .remote import register_remote
    register_remote(application)
    from .report_builder import register_report_builder
    register_report_builder(application)
    from .geometry import register_geometry
    from .speech import register_speech
    register_geometry(application)
    register_speech(application)
    from .design_workflow import register_design_workflow
    register_design_workflow(application)
    from .deliverables import register_deliverables
    from .interior import register_interior
    register_deliverables(application)
    register_interior(application)
    from .cloud import register_cloud
    register_cloud(application)
    from .updates import register_updates
    register_updates(application)

    # Frozen builds put the pre-built frontend alongside the package.
    candidates = [Path(__file__).resolve().parent / "frontend", Path(__file__).resolve().parents[2] / "frontend" / "dist"]
    frontend = next((candidate for candidate in candidates if (candidate / "index.html").is_file()), None)
    if frontend is not None:
        application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return application
