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
