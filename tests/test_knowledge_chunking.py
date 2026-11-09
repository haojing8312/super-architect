from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from architecture_workbench.chunking import (
    build_knowledge_chunks, hard_wrap_children, merge_into_parents,
    split_into_children, split_paragraphs, utf16_length,
)
from architecture_workbench.documents import text_chunks
from architecture_workbench.store import Store


def test_source_blank_paragraphs_and_dotnet_whitespace():
    assert split_paragraphs(" \r\n甲\r\n续行\r\n \t\r\n乙\n\n\u2000丙\u2000 ") == ["甲\r\n续行", "乙", "丙"]
    assert split_paragraphs("\x1c保留\x1f\n\n\u00a0去首尾\u00a0") == ["\x1c保留\x1f", "去首尾"]


def test_parent_soft_limit_and_original_separator_check():
    parents = merge_into_parents(["甲" * 250, "乙" * 250, "丙" * 100])
    assert parents == ["甲" * 250 + "\n" + "乙" * 250, "乙" * 80 + "\n" + "丙" * 100]
    assert utf16_length(parents[0]) == 501  # Reference check excludes the next newline.
    assert merge_into_parents(["甲" * 80, "乙" * 30], 100, 80) == ["甲" * 80, "乙" * 30]


def test_long_paragraph_is_one_parent_but_children_hard_wrap_400():
    text = "甲" * 1201
    assert merge_into_parents(split_paragraphs(text)) == [text]
    chunks = build_knowledge_chunks(text)
    assert [len(chunk.content) for chunk in chunks] == [400, 400, 400, 1]
    assert {chunk.parent_text for chunk in chunks} == {text}
    assert len({chunk.parent_id for chunk in chunks}) == 1
    assert [chunk.child_index for chunk in chunks] == [0, 1, 2, 3]
    assert text_chunks(text) == [chunk.content for chunk in chunks]


def test_three_sentence_step_two_and_last_window():
    assert split_into_children("一。二。三。四。五。六。七。") == ["一。二。三。", "三。四。五。", "五。六。七。"]
    assert split_into_children("一。二。三。四。五。六。") == ["一。二。三。", "三。四。五。", "四。五。六。"]
    assert split_into_children("A! B? C. D。 E！ F？") == ["A!B?C.", "C.D。E！", "D。E！F？"]
    assert split_into_children("\n一。\r\n二。\n") == ["一。\r\n二。"]
    assert split_into_children("") == [""]


def test_utf16_boundaries_keep_cjk_and_emoji_intact():
    text = "甲" * 399 + "😀" + "乙" * 3
    children = hard_wrap_children([text])
    assert children == ["甲" * 399, "😀" + "乙" * 3]
    assert "".join(children) == text
    assert all(utf16_length(child) <= 400 for child in children)
    assert utf16_length("中😀文") == 4
    previous = "甲" * 10 + "😀" + "乙" * 79
    parents = merge_into_parents([previous, "丙" * 20], 100, 80)
    assert parents == [previous, "乙" * 79 + "\n" + "丙" * 20]
    for parent in parents:
        parent.encode("utf-8")
        parent.encode("utf-16-le")


def test_parent_identity_is_content_and_position_stable():
    text = "相同段落。" * 90 + "\n\n" + "相同段落。" * 90
    first = build_knowledge_chunks(text)
    assert first == build_knowledge_chunks(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert all(chunk.parent_id.startswith(digest + ":parent:") for chunk in first)
    assert len({chunk.parent_id for chunk in first}) == 2
    changed = build_knowledge_chunks(text + "附记。")
    assert {chunk.parent_id for chunk in first}.isdisjoint({chunk.parent_id for chunk in changed})


@pytest.mark.parametrize("argument,value", [("parent_max_chars", 0), ("parent_overlap_chars", -1),
                                            ("child_window_size", 0), ("child_step_size", 0)])
def test_invalid_configuration_cannot_loop(argument, value):
    with pytest.raises(ValueError):
        build_knowledge_chunks("建筑。结构。消防。道路。", **{argument: value})


def test_real_import_parent_rows_fts_dedup_and_delete(tmp_path):
    source = tmp_path / "建筑分组.md"
    text = ("甲" * 250 + "。\n\n" + "乙" * 250 + "。\n\n" + "建筑结构。消防通道。采光庭院。入口。出口。😀")
    source.write_text(text, encoding="utf-8")
    store = Store(tmp_path / "data")
    imported = store.knowledge_import(source)
    expected = build_knowledge_chunks(text, imported["content_hash"])
    with store.connection() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM knowledge_chunks WHERE item_id=? ORDER BY chunk_index", (imported["id"],))]
        assert [row["chunk_index"] for row in rows] == list(range(len(expected)))
        assert [(row["content"], row["parent_id"], row["parent_text"], row["child_index"]) for row in rows] == [
            (chunk.content, chunk.parent_id, chunk.parent_text, chunk.child_index) for chunk in expected]
        if store.fts_available:
            assert db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0] == len(rows)
    assert imported["chunk_count"] == len(expected)
    assert Path(imported["path"]).read_text(encoding="utf-8") == text
    duplicate = Store(store.data_dir).knowledge_import(source)
    assert duplicate["id"] == imported["id"] and duplicate["deduplicated"]
    with store.connection() as db:
        assert [row["id"] for row in db.execute("SELECT id FROM knowledge_chunks ORDER BY chunk_index")] == [row["id"] for row in rows]
    store.delete_knowledge(imported["id"])
    assert source.exists() and not Path(imported["path"]).exists()
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0] == 0
        if store.fts_available:
            assert db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0] == 0


def test_legacy_sqlite_migration_keeps_content_cache_and_dedup(tmp_path):
    data_dir = tmp_path / "data"
    knowledge_dir = data_dir / "knowledge"
    knowledge_dir.mkdir(parents=True)
    text = "旧知识内容" * 120
    source = tmp_path / "旧文档.txt"
    source.write_text(text, encoding="utf-8")
    cached = knowledge_dir / "legacy.txt"
    cached.write_bytes(source.read_bytes())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with sqlite3.connect(data_dir / "workbench.sqlite3") as db:
        db.executescript("""
            CREATE TABLE knowledge_items (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,source_path TEXT NOT NULL,
              type TEXT NOT NULL,size INTEGER NOT NULL,char_count INTEGER NOT NULL,chunk_count INTEGER NOT NULL,
              content_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
            CREATE TABLE knowledge_chunks (
              id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
              chunk_index INTEGER NOT NULL,content TEXT NOT NULL);
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(chunk_id UNINDEXED,content,tokenize='unicode61');
            CREATE TABLE existing_cache (chunk_id TEXT PRIMARY KEY,payload BLOB);
        """)
        db.execute("INSERT INTO knowledge_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("legacy", source.name, str(cached), str(source), "text/plain", source.stat().st_size, len(text), 1, digest, "2026-01-01T00:00:00+00:00"))
        db.execute("INSERT INTO knowledge_chunks VALUES (?,?,?,?)", ("old-chunk", "legacy", 0, text))
        db.execute("INSERT INTO knowledge_fts VALUES (?,?)", ("old-chunk", text))
        db.execute("INSERT INTO existing_cache VALUES (?,?)", ("old-chunk", b"unchanged-vector-cache"))
    store = Store(data_dir)
    assert store.knowledge_import(source)["deduplicated"]
    reopened = Store(data_dir)
    with reopened.connection() as db:
        row = dict(db.execute("SELECT * FROM knowledge_chunks").fetchone())
        assert row["id"] == "old-chunk" and row["content"] == text
        assert row["parent_id"] is row["parent_text"] is row["child_index"] is None
        assert db.execute("SELECT payload FROM existing_cache").fetchone()[0] == b"unchanged-vector-cache"
        assert db.execute("SELECT content FROM knowledge_fts").fetchone()[0] == text
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert cached.read_bytes() == source.read_bytes()


def test_import_sql_failure_rolls_back_index_and_only_new_copy(tmp_path):
    store = Store(tmp_path / "data")
    source = tmp_path / "真实资料.txt"
    source.write_text("甲。乙。丙。丁。戊。", encoding="utf-8")
    with store.connection() as db:
        db.execute("CREATE TRIGGER reject_chunk BEFORE INSERT ON knowledge_chunks BEGIN SELECT RAISE(ABORT,'test rejection'); END")
    with pytest.raises(sqlite3.IntegrityError, match="test rejection"):
        store.knowledge_import(source)
    assert source.exists() and store.knowledge_items() == []
    assert list((store.data_dir / "knowledge").iterdir()) == []
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0] == 0
        if store.fts_available:
            assert db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0] == 0


def test_long_parent_storage_budget_rejects_before_copy(tmp_path):
    store = Store(tmp_path / "data")
    source = tmp_path / "无段落大文件.txt"
    source.write_text("A" * 500_000, encoding="utf-8")
    with pytest.raises(ValueError, match="父子分块索引超过 80 MB"):
        store.knowledge_import(source)
    assert source.exists() and store.knowledge_items() == []
    assert list((store.data_dir / "knowledge").iterdir()) == []
