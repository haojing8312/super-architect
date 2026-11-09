"""Local-only workspace information, optional component detection and legacy import."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import __version__, migration


class LegacyPath(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


class LegacyImport(LegacyPath):
    confirm: bool = False


def _project_file(*relative: str) -> Path:
    package = Path(__file__).resolve().parent
    project = package.parents[1]
    for candidate in (project.joinpath("frontend", "public", *relative), package.joinpath("frontend", *relative)):
        if candidate.is_file():
            return candidate
    return project.joinpath("frontend", "public", *relative)


def optional_assets() -> dict:
    """Reference bundles intentionally absent from the public source tree."""
    return {
        "cad_viewer": {"available": _project_file("cad-viewer", "index.html").is_file(),
                       "capability": "DWG 图层、测量与完整 DXF 浏览器",
                       "fallback": "DXF 快速预览仍可用；DWG 需自备兼容查看器或可选组件"},
        "report_editor": {"available": _project_file("report-builder", "index.html").is_file(),
                          "capability": "完整作品汇报可视化编辑器",
                          "fallback": "快速文档汇报与本地 HTML/Office 导出仍可用"},
    }


def register_local(app):
    router = APIRouter(prefix="/api/local")
    store = app.state.store

    @router.get("/dashboard")
    def dashboard():
        """Persisted task outcomes plus the runtime's actual active sessions."""
        from .deliverables import get_deliverable
        active = set(app.state.runtime_manager.active)
        with store.connection() as db:
            questions = db.execute("SELECT session_id,question FROM interaction_questions WHERE status='pending' ORDER BY created_at DESC").fetchall()
            waiting = {}
            for question in questions:
                waiting.setdefault(question["session_id"], question["question"])
            sessions = db.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 50").fetchall()
            tasks, media = [], []
            for session in sessions:
                latest = db.execute("SELECT * FROM messages WHERE session_id=? AND role IN ('assistant','system') ORDER BY created_at DESC,rowid DESC LIMIT 1", (session["id"],)).fetchone()
                metadata = {}
                if latest:
                    try:
                        metadata = json.loads(latest["metadata"])
                    except (TypeError, ValueError):
                        pass
                if not isinstance(metadata, dict):
                    metadata = {}
                context = {}
                context_root = store.data_dir / "runtime-context"
                context_file = context_root / (session["id"] + ".json")
                if context_file.resolve().is_relative_to(context_root.resolve()) and not context_file.is_symlink():
                    try:
                        if context_file.stat().st_size < 16_384:
                            value = json.loads(context_file.read_text(encoding="utf-8"))
                            if isinstance(value, dict):
                                context = value
                    except (OSError, ValueError):
                        pass
                status = "running" if session["id"] in active else None
                if session["id"] in waiting:
                    status = "awaiting_input"
                elif not status and context.get("status") in {"completed", "cancelled", "failed", "running"}:
                    status = "failed" if context["status"] == "running" else context["status"]
                elif not status and latest and "completed" in metadata:
                    status = "completed" if metadata["completed"] else "cancelled" if "取消" in latest["content"] else "failed"
                if status:
                    profile = session["profile"] or "assistant"
                    department = "review" if profile in {"webreport", "ppt"} else "bim" if profile in {"blender", "rhino", "revit", "sketchup", "grasshopper"} else "architecture" if profile in {"autocad", "cad"} else None
                    tasks.append({"id": session["id"], "sessionId": session["id"], "title": session["title"], "status": status,
                                  "department": department, "updatedAt": session["updated_at"],
                                  "detail": waiting.get(session["id"], "") or (latest["content"][:100] if status == "failed" else "")})
                if latest:
                    for index, artifact in enumerate(metadata.get("artifacts", []) if isinstance(metadata.get("artifacts", []), list) else []):
                        if isinstance(artifact, dict) and artifact.get("path"):
                            media.append({"id": latest["id"] + ":" + str(index), "sessionId": session["id"],
                                          "title": Path(artifact["path"]).name, "kind": artifact.get("kind", "文件"), "updatedAt": latest["created_at"]})
            rows = db.execute("SELECT id FROM canvases WHERE id LIKE 'deliverable-%' ORDER BY updated_at DESC LIMIT 8").fetchall()
        artifacts = []
        for row in rows:
            try:
                item = get_deliverable(store, row["id"][len("deliverable-"):])
            except (KeyError, ValueError):
                continue
            artifacts.append({"id": item["id"], "title": item["title"], "kind": " / ".join(item["formats"]), "updatedAt": item["created_at"]})
        tasks.sort(key=lambda task: task["status"] not in {"running", "awaiting_input"})
        return {"tasks": tasks[:8], "artifacts": sorted(artifacts + media, key=lambda item: item.get("updatedAt", ""), reverse=True)[:8]}

    @router.get("/info")
    def info():
        settings = store.get_settings()
        return {
            "mode": "local", "version": __version__, "data_dir": str(store.data_dir),
            "workspace": settings.get("workspace", ""),
            "path_policy": "数据目录在启动时通过 --data-dir 或 ARCHITECTURE_DATA_DIR 选择；工作区必须是已存在的本机文件夹。",
            "optional": optional_assets(),
        }

    @router.get("/imports")
    def imports():
        items = []
        root = store.data_dir / "imports"
        for manifest in sorted(root.rglob("legacy-import.json"), reverse=True):
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({**value, "path": str(manifest.parent)})
        return {"items": items}

    @router.post("/legacy/scan")
    def scan(body: LegacyPath):
        try:
            return migration.preview(body.path)
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/legacy/import")
    def legacy_import(body: LegacyImport):
        if body.confirm is not True:
            raise HTTPException(422, "请确认已了解：导入会先备份旧目录，并为每个旧账号建立独立本地工作区")
        try:
            return migration.import_legacy(body.path, store.data_dir / "imports")
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    app.include_router(router)
