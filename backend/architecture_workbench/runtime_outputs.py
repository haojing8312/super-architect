"""Collect native CLI image outputs that exec JSONL does not expose as tool items."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import time
import stat as file_stat

from PIL import Image


def _linked(path: Path) -> bool:
    info = path.lstat()
    return file_stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def collect_codex_images(thread_id: str, workspace: str, started: float, *, roots=None) -> list[dict]:
    if not re.fullmatch(r"[a-fA-F0-9-]{36}", thread_id or ""):
        return []
    roots = roots or [Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "generated_images", Path.home() / ".codex" / "generated_images"]
    scope = Path(workspace).resolve()
    destination = scope / "outputs" / "generated-images"
    if any(path.exists() and _linked(path) for path in [scope / "outputs", destination]) or not destination.resolve().is_relative_to(scope):
        raise ValueError("生成图片输出目录包含越界链接")
    seen, result = set(), []
    for root in roots:
        root = Path(root).resolve()
        folder = root / thread_id
        if not folder.is_dir() or _linked(folder) or folder.resolve() != root / thread_id:
            continue
        for source in folder.rglob("*"):
            if len(result) >= 12:
                break
            if not source.is_file() or _linked(source) or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            if any(parent.exists() and _linked(parent) for parent in source.parents if parent.is_relative_to(folder)):
                continue
            resolved = source.resolve()
            if resolved in seen or not resolved.is_relative_to(folder.resolve()):
                continue
            seen.add(resolved)
            stat = source.stat()
            if stat.st_mtime < started - 3 or stat.st_size > 80 * 1024 * 1024:
                continue
            try:
                with Image.open(source) as picture:
                    picture.verify()
            except (OSError, ValueError):
                continue
            destination.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            target = destination / (digest[:16] + source.suffix.lower())
            if not target.exists():
                shutil.copy2(source, target)
            result.append({"name": target.name, "path": str(target), "sha256": digest, "source": "codex-native-image-generation", "runtime_thread_id": thread_id})
    return result
