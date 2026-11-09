"""Resolve authorized local artifacts into inert, cached file-download snapshots."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import threading
import warnings

from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from .documents import MAX_FILE_BYTES, file_type
from .store import now

RASTER_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF", ".webp": "WEBP", ".bmp": "BMP"}
SHORTCUT_EXTENSIONS = {".lnk", ".url", ".website", ".webloc", ".scf", ".desktop", ".appref-ms"}


def _reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def authorized_path(store, session_id: str, supplied: str) -> Path:
    session = store.get_session(session_id)
    value = supplied.strip()
    if not value or any(ord(character) < 32 for character in value):
        raise HTTPException(422, "请提供有效本机文件路径")
    if value.startswith(("\\\\", "//", "\\??\\")) or "://" in value:
        raise HTTPException(403, "仅支持授权工作区内的普通本机路径")
    # Reject Windows alternate data streams and drive-relative paths on every host.
    colons = [index for index, character in enumerate(value) if character == ":"]
    if colons and (colons != [1] or not value[0].isalpha() or len(value) < 3 or value[2] not in "/\\"):
        raise HTTPException(403, "不支持设备路径、数据流或盘符相对路径")
    workspace = Path(session["workspace"]).resolve()
    data = Path(store.data_dir).resolve()
    allowed = [workspace]
    for name in ("attachments", "resources", "reports"):
        directory = data / name
        resolved = directory.resolve()
        if not resolved.is_relative_to(data) or (directory.exists() and _reparse(directory)):
            raise HTTPException(403, "工作台资料目录包含越界链接")
        allowed.append(resolved)
    raw = Path(value).expanduser()
    raw = raw if raw.is_absolute() else workspace / raw
    target = raw.resolve()
    if not any(target.is_relative_to(directory) and target != directory for directory in allowed):
        raise HTTPException(403, "产物必须位于当前会话工作区或工作台附件、素材、报告目录")
    if target.suffix.lower() in SHORTCUT_EXTENSIONS:
        raise HTTPException(403, "不支持快捷方式产物")
    # Inspect the original chain too: resolving first must not hide a symlink/junction.
    for component in [raw, *raw.parents]:
        if component.exists() and _reparse(component):
            raise HTTPException(403, "不支持符号链接或目录连接产物")
    try:
        info = target.stat()
    except FileNotFoundError as error:
        raise HTTPException(404, "产物文件不存在") from error
    if not stat.S_ISREG(info.st_mode):
        raise HTTPException(422, "产物必须是普通文件")
    if info.st_nlink > 1:
        raise HTTPException(403, "不支持硬链接产物")
    if info.st_size > MAX_FILE_BYTES:
        raise HTTPException(413, "产物超过 80 MB 下载上限")
    return target


def is_raster(path: Path) -> bool:
    expected = RASTER_FORMATS.get(path.suffix.lower())
    if not expected:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != expected:
                    raise ValueError("文件后缀与实际图片格式不一致")
                image.verify()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise HTTPException(422, "产物不是有效的普通 PNG/JPEG/GIF/WebP/BMP 图片") from error
    return True


class ArtifactResolver:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        with store.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS artifact_snapshots (
                cache_key TEXT PRIMARY KEY, source_path TEXT NOT NULL,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL)""")

    def resolve(self, session_id: str, supplied: str) -> dict:
        with self.lock:
            source = authorized_path(self.store, session_id, supplied)
            signature = source.stat()
            raster = is_raster(source)
            verified = source.stat()
            if (verified.st_ino, verified.st_size, verified.st_mtime_ns) != (signature.st_ino, signature.st_size, signature.st_mtime_ns):
                raise HTTPException(409, "产物正在变化，请稍后重试")
            key = hashlib.sha256((os.path.normcase(str(source)) + ":" + str(signature.st_mtime_ns) + ":" + str(signature.st_size)).encode("utf-8")).hexdigest()
            with self.store.connection() as db:
                cache = db.execute("SELECT file_id FROM artifact_snapshots WHERE cache_key=?", (key,)).fetchone()
                existing = db.execute("SELECT id FROM files WHERE path=?", (str(source),)).fetchone()
            item = None
            if cache or existing:
                try:
                    found = self.store.get_file((cache or existing)[0])
                    cached_path = self.store._owned_path(found["path"])
                    if cached_path.is_file() and not _reparse(cached_path) and cached_path.stat().st_size == signature.st_size:
                        item = found
                except (KeyError, OSError, ValueError):
                    pass
            if item is None:
                # Source is never removed. Store owns the immutable copied snapshot.
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(source, flags)
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_ino, opened.st_dev, opened.st_size, opened.st_mtime_ns) != (signature.st_ino, signature.st_dev, signature.st_size, signature.st_mtime_ns):
                        raise HTTPException(409, "产物正在变化，请稍后重试")
                    item = self.store.save_attachment(source.name, stream)
                current = source.stat()
                if (current.st_size, current.st_mtime_ns) != (signature.st_size, signature.st_mtime_ns):
                    # Only dispose of the just-created application-owned snapshot.
                    with self.store.connection() as db:
                        db.execute("DELETE FROM files WHERE id=?", (item["id"],))
                    self.store._owned_path(item["path"]).unlink(missing_ok=True)
                    raise HTTPException(409, "产物正在变化，请稍后重试")
            with self.store.connection() as db:
                db.execute("INSERT OR REPLACE INTO artifact_snapshots VALUES (?,?,?,?)", (key, str(source), item["id"], now()))
            return {"id": item["id"], "name": item["name"], "path": str(source), "type": file_type(source),
                    "size": signature.st_size, "url": f"/api/files/{item['id']}", "image": raster}


class ArtifactInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)


def register_artifacts(app):
    service = ArtifactResolver(app.state.store)
    app.state.artifacts = service
    router = APIRouter(prefix="/api/artifacts")

    @router.post("/resolve")
    def resolve(body: ArtifactInput):
        try:
            return service.resolve(body.session_id, body.path)
        except KeyError as error:
            raise HTTPException(404, "会话不存在") from error
        except OSError as error:
            raise HTTPException(422, "产物文件无法读取，请检查路径与权限") from error

    app.include_router(router)
