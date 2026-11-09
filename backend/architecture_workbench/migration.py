"""Back up and import retired anonymous/member data as independent local workspaces.

The old product stored an anonymous database at the data root and one database
per member below ``members/``. Removing accounts must not silently merge those
databases into one another: every legacy scope becomes its own local data
directory under the current data root's ``imports`` folder. The source tree is
read-only; a verified backup is created before any row is read for import.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .documents import safe_name
from .store import Store

BACKUP_FILES = ("workbench.sqlite3", "attachments", "knowledge", "resources", "reports")
SAFE_COLUMNS = {
    "sessions": ("id", "title", "workspace", "provider", "skills", "created_at", "updated_at", "profile", "pinned"),
    "messages": ("id", "session_id", "role", "content", "metadata", "created_at"),
    "todos": ("id", "session_id", "position", "data"),
    "canvases": ("id", "data", "updated_at"),
    "files": ("id", "name", "path", "type", "size", "created_at"),
    "resources": ("id", "name", "path", "source_path", "type", "size", "created_at"),
    "knowledge_items": ("id", "name", "path", "source_path", "type", "size", "char_count",
                        "chunk_count", "content_hash", "created_at"),
    "knowledge_chunks": ("id", "item_id", "chunk_index", "content", "parent_id", "parent_text", "child_index"),
    "reports": ("id", "title", "path", "asset_count", "size", "created_at"),
    "memories": ("id", "content", "category", "created_at", "updated_at"),
}


@dataclass(frozen=True)
class LegacyScope:
    key: str
    label: str
    root: Path
    database: Path

    @property
    def fingerprint(self) -> str:
        try:
            stat = self.database.stat()
            value = f"{self.database.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            value = str(self.database.resolve())
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "workspace"


def discover(source: str | Path) -> list[LegacyScope]:
    root = Path(source).expanduser()
    if root.is_symlink():
        raise ValueError("旧数据目录不能是链接")
    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise ValueError("旧数据目录不存在或无法读取") from error
    if not root.is_dir():
        raise ValueError("请选择旧版本数据目录")
    scopes = []
    if (root / "workbench.sqlite3").is_file():
        scopes.append(LegacyScope("local", "旧本机工作区", root, root / "workbench.sqlite3"))
    members = root / "members"
    if members.is_dir():
        candidates = sorted(child for child in members.iterdir()
                            if child.is_dir() and not child.is_symlink()
                            and (child / "workbench.sqlite3").is_file())
        for index, child in enumerate(candidates, 1):
            scopes.append(LegacyScope(f"member-{index}", f"旧会员工作区 {index}", child,
                                     child / "workbench.sqlite3"))
    if not scopes:
        raise ValueError("没有找到旧版本 workbench.sqlite3")
    return scopes


def _tables(database: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(database: Path, table: str) -> list[dict]:
    if table not in _tables(database):
        return []
    with contextlib.closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        available = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        columns = [column for column in SAFE_COLUMNS.get(table, ()) if column in available]
        if not columns:
            return []
        return [dict(row) for row in db.execute(f"SELECT {','.join(columns)} FROM {table}")]


def preview(source: str | Path) -> dict:
    scopes = discover(source)
    result = []
    # Read a temporary copy: the old directory must not receive WAL/SHM writes
    # merely because someone scanned it before deciding to import.
    with tempfile.TemporaryDirectory(prefix="architecture-legacy-preview-") as temporary:
        for index, scope in enumerate(scopes):
            database = Path(temporary) / (str(index) + ".sqlite3")
            _copy_database(scope, database)
            result.append({"key": scope.key, "label": scope.label, "fingerprint": scope.fingerprint,
                           "sessions": len(_rows(database, "sessions")),
                           "messages": len(_rows(database, "messages")),
                           "files": len(_rows(database, "files")),
                           "knowledge": len(_rows(database, "knowledge_items")),
                           "reports": len(_rows(database, "reports"))})
    return {"source": str(Path(source).expanduser().resolve()), "scopes": result}


def _copy_database(scope: LegacyScope, destination: Path) -> None:
    """Copy a database and any SQLite sidecars without writing to the source."""
    shutil.copy2(scope.database, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(scope.database) + suffix)
        if sidecar.is_file() and not sidecar.is_symlink():
            shutil.copy2(sidecar, Path(str(destination) + suffix))


def _copy_tree(source: Path, target: Path) -> None:
    """Copy owned files without following symlinks outside the old data scope."""
    def ignore(directory, names):
        base = Path(directory)
        return [name for name in names if (base / name).is_symlink()]
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)


def _backup(scope: LegacyScope, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    for name in BACKUP_FILES:
        source = scope.root / name
        if not source.exists():
            continue
        if name == "workbench.sqlite3":
            _copy_database(scope, destination / name)
        elif source.is_dir() and not source.is_symlink():
            _copy_tree(source, destination / name)
        elif source.is_file() and not source.is_symlink():
            shutil.copy2(source, destination / name)
    (destination / "manifest.json").write_text(json.dumps({
        "source": str(scope.root), "label": scope.label, "fingerprint": scope.fingerprint,
        "backed_up_at": _now(), "note": "只读备份；导入使用此副本，不回写旧目录。"}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if not (destination / "workbench.sqlite3").is_file():
        raise ValueError("旧数据库备份未生成")
    return destination / "workbench.sqlite3"


def _copy_asset(source: Path, folder: Path, name: str, root: Path) -> Path | None:
    """Copy only a real file that belonged to the old data scope."""
    try:
        resolved = source.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file() or resolved.is_symlink():
        return None
    try:
        clean = safe_name(name)
    except ValueError:
        clean = safe_name(resolved.name)
    destination = folder / (secrets.token_hex(16) + Path(clean).suffix.lower())
    shutil.copy2(resolved, destination)
    return destination


def _existing_import(target_root: Path, fingerprint: str) -> Path | None:
    for manifest in target_root.rglob("legacy-import.json"):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("fingerprint") == fingerprint and value.get("status") == "imported":
            return manifest.parent
    return None


def _import_scope(scope: LegacyScope, database: Path, destination: Path) -> dict:
    store = Store(destination)
    rows = {table: _rows(database, table) for table in SAFE_COLUMNS}
    copied = {"files": 0, "resources": 0, "knowledge": 0, "reports": 0}
    skipped = {"files": 0, "resources": 0, "knowledge": 0, "reports": 0}

    # Files and resources keep their ids because the destination database is new.
    for row in rows["files"]:
        path = _copy_asset(Path(row["path"]), store.data_dir / "attachments", row["name"], scope.root)
        if path is None:
            skipped["files"] += 1
            continue
        row["path"] = str(path)
        row["size"] = path.stat().st_size
        copied["files"] += 1
        with store.connection() as db:
            db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["files"]))
    for row in rows["resources"]:
        path = _copy_asset(Path(row["path"]), store.data_dir / "resources", row["name"], scope.root)
        if path is None:
            skipped["resources"] += 1
            continue
        row["path"] = str(path)
        copied["resources"] += 1
        with store.connection() as db:
            db.execute("INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["resources"]))

    with store.connection() as db:
        for row in rows["knowledge_items"]:
            path = _copy_asset(Path(row["path"]), store.data_dir / "knowledge", row["name"], scope.root)
            if path is None:
                skipped["knowledge"] += 1
                continue
            existing = db.execute("SELECT id FROM knowledge_items WHERE content_hash=?", (row["content_hash"],)).fetchone()
            if existing:
                continue
            row["path"] = str(path)
            row["size"] = path.stat().st_size
            copied["knowledge"] += 1
            db.execute("INSERT OR REPLACE INTO knowledge_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["knowledge_items"]))
            chunks = [chunk for chunk in rows["knowledge_chunks"] if chunk["item_id"] == row["id"]]
            for chunk in chunks:
                db.execute("INSERT OR REPLACE INTO knowledge_chunks (id,item_id,chunk_index,content,parent_id,parent_text,child_index) VALUES (?,?,?,?,?,?,?)",
                           tuple(chunk[column] for column in SAFE_COLUMNS["knowledge_chunks"]))
                if store.fts_available:
                    db.execute("INSERT INTO knowledge_fts(chunk_id,content) VALUES (?,?)", (chunk["id"], chunk["content"]))

    for row in rows["reports"]:
        source = Path(row["path"])
        try:
            source = source.resolve(strict=True)
        except OSError:
            skipped["reports"] += 1
            continue
        folder = store.data_dir / "reports" / row["id"]
        if not source.is_file() or source.is_symlink() or not source.is_relative_to(scope.root.resolve()):
            skipped["reports"] += 1
            continue
        target = folder / source.name
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        row["path"] = str(target)
        copied["reports"] += 1
        with store.connection() as db:
            db.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["reports"]))

    with store.connection() as db:
        for row in rows["memories"]:
            db.execute("INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["memories"]))
        for row in rows["sessions"]:
            workspace = Path(str(row["workspace"]))
            if not workspace.is_dir() or workspace.is_symlink():
                workspace = store.data_dir / "workspaces" / "imported"
                workspace.mkdir(parents=True, exist_ok=True)
            provider = row.get("provider") if row.get("provider") in {"codex", "codebuddy"} else "codex"
            skills = row.get("skills")
            if not isinstance(skills, str):
                skills = json.dumps(skills if isinstance(skills, list) else [], ensure_ascii=False)
            values = [row.get(column, "") for column in SAFE_COLUMNS["sessions"]]
            values[SAFE_COLUMNS["sessions"].index("workspace")] = str(workspace)
            values[SAFE_COLUMNS["sessions"].index("provider")] = provider
            values[SAFE_COLUMNS["sessions"].index("skills")] = skills
            values[SAFE_COLUMNS["sessions"].index("profile")] = str(row.get("profile") or "")
            values[SAFE_COLUMNS["sessions"].index("pinned")] = int(row.get("pinned") or 0)
            values[SAFE_COLUMNS["sessions"].index("title")] = str(row.get("title") or "导入的对话")[:200]
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", tuple(values))
        for row in rows["messages"]:
            if row["session_id"] not in {session["id"] for session in rows["sessions"]}:
                continue
            db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["messages"]))
        for row in rows["todos"]:
            db.execute("INSERT OR REPLACE INTO todos VALUES (?,?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["todos"]))
        for row in rows["canvases"]:
            db.execute("INSERT OR REPLACE INTO canvases VALUES (?,?,?)",
                       tuple(row[column] for column in SAFE_COLUMNS["canvases"]))
        db.execute("INSERT OR REPLACE INTO legacy_imports VALUES (?,?,?,?,?,?)",
                   (scope.fingerprint, str(scope.root), scope.label,
                    len(rows["sessions"]), copied["files"], _now()))
    imported_workspace = store.data_dir / "workspaces" / "imported"
    imported_workspace.mkdir(parents=True, exist_ok=True)
    store.save_settings({"workspace": str(imported_workspace),
                         "skill_source": str(store.data_dir / "skills")})
    return {
        "key": scope.key, "label": scope.label, "path": str(destination),
        "sessions": len(rows["sessions"]), "messages": len(rows["messages"]),
        "copied": copied, "skipped": skipped, "fingerprint": scope.fingerprint,
    }


def import_legacy(source: str | Path, target_root: str | Path, *, dry_run: bool = False) -> dict:
    scopes = discover(source)
    root = Path(target_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    backup_root = root.parent / "backups" / ("legacy-" + stamp)
    workspaces = []
    for scope in scopes:
        existing = _existing_import(root, scope.fingerprint)
        if existing:
            workspaces.append({"key": scope.key, "label": scope.label, "path": str(existing),
                               "already_imported": True, "fingerprint": scope.fingerprint})
            continue
        if dry_run:
            workspaces.append({"key": scope.key, "label": scope.label, "path": "", "dry_run": True,
                               "sessions": len(_rows(scope.database, "sessions")),
                               "messages": len(_rows(scope.database, "messages")),
                               "fingerprint": scope.fingerprint})
            continue
        backup_database = _backup(scope, backup_root / scope.key)
        destination = root / stamp / _slug(scope.key)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            result = _import_scope(scope, backup_database, destination)
        except Exception:
            # The just-created destination is the only cleanup target.
            if destination.resolve().is_relative_to(root.resolve()):
                shutil.rmtree(destination, ignore_errors=True)
            raise
        (destination / "legacy-import.json").write_text(json.dumps({
            **result, "source": str(scope.root), "status": "imported", "imported_at": _now(),
            "backup": str(backup_root / scope.key),
            "note": "独立本地工作区；不与其他旧目录自动合并。使用 --data-dir 指向此目录打开。"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        workspaces.append(result)
    return {"source": str(Path(source).expanduser().resolve()), "backup": str(backup_root),
            "workspaces": workspaces, "dry_run": dry_run}
