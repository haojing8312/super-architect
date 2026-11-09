"""Local port of RagService's paragraph parents and sentence-window children.

The reference measures strings in .NET UTF-16 units. Boundaries use those units
but keep a Unicode code point intact, so SQLite never receives a split surrogate.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


# .NET Char.IsWhiteSpace; Python also treats U+001C..U+001F as whitespace.
DOTNET_WHITESPACE = "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
PARAGRAPH_BREAK = re.compile(r"\r?\n[" + re.escape(DOTNET_WHITESPACE) + r"]*\r?\n")
SENTENCE_BREAK = re.compile(r"(?<=[。！？.!?])")


@dataclass(frozen=True)
class KnowledgeChunk:
    content: str
    parent_id: str
    parent_text: str
    child_index: int


def utf16_length(text: str) -> int:
    """Number of UTF-16 code units without allocating an encoded document."""
    return len(text) + sum(ord(character) > 0xFFFF for character in text)


def _positive_integer(value: int, name: str, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _tail(text: str, units: int) -> str:
    used = count = 0
    for character in reversed(text):
        width = 2 if ord(character) > 0xFFFF else 1
        if used + width > units:
            break
        used += width
        count += 1
    return text[len(text) - count:] if count else ""


def split_paragraphs(text: str) -> list[str]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return [paragraph for part in PARAGRAPH_BREAK.split(text)
            if (paragraph := part.strip(DOTNET_WHITESPACE))]


def merge_into_parents(paragraphs: list[str], max_chars: int = 500, overlap_chars: int = 80) -> list[str]:
    """Match RagService.cs:684, including its soft limit and newline check."""
    _positive_integer(max_chars, "max_chars")
    _positive_integer(overlap_chars, "overlap_chars", 0)
    parents: list[str] = []
    parts: list[str] = []
    length = 0
    for paragraph in paragraphs:
        paragraph_length = utf16_length(paragraph)
        # The reference checks before adding the separating newline. It does
        # not split a single long paragraph to enforce ParentMaxChars.
        if length > 0 and length + paragraph_length > max_chars:
            previous = "".join(parts)
            parents.append(previous)
            parts = []
            length = 0
            if overlap_chars > 0 and utf16_length(previous) > overlap_chars:
                overlap = _tail(previous, overlap_chars)
                if overlap:
                    parts.append(overlap)
                    length = utf16_length(overlap)
        if length > 0:
            parts.append("\n")
            length += 1
        parts.append(paragraph)
        length += paragraph_length
    if length > 0:
        parents.append("".join(parts))
    return parents


def hard_wrap_children(children: list[str], max_chars: int = 400) -> list[str]:
    """Reference 400-unit, nonoverlapping wrap with intact Unicode boundaries."""
    _positive_integer(max_chars, "max_chars", 2)
    result: list[str] = []
    for child in children:
        if utf16_length(child) <= max_chars:
            result.append(child)
            continue
        start = 0
        used = 0
        for position, character in enumerate(child):
            width = 2 if ord(character) > 0xFFFF else 1
            if used + width > max_chars:
                result.append(child[start:position])
                start = position
                used = 0
            used += width
        if start < len(child):
            result.append(child[start:])
    return result


def split_into_children(parent_text: str, window_size: int = 3, step: int = 2,
                        max_child_chars: int = 400) -> list[str]:
    """Match RagService.cs:715/760, including the final sentence window."""
    _positive_integer(window_size, "window_size")
    _positive_integer(step, "step")
    sentences = [sentence for part in SENTENCE_BREAK.split(parent_text)
                 if (sentence := part.strip(DOTNET_WHITESPACE))]
    if len(sentences) <= window_size:
        children = [parent_text.strip(DOTNET_WHITESPACE)]
    else:
        children = ["".join(sentences[position:position + window_size])
                    for position in range(0, len(sentences) - window_size + 1, step)]
        tail = "".join(sentences[-window_size:])
        if not children or children[-1] != tail:
            children.append(tail)
    return hard_wrap_children(children, max_child_chars)


def build_knowledge_chunks(text: str, content_hash: str | None = None, *,
                           parent_max_chars: int = 500, parent_overlap_chars: int = 80,
                           child_window_size: int = 3, child_step_size: int = 2) -> list[KnowledgeChunk]:
    """Build stable parents and their globally ordered child rows for new imports."""
    digest = content_hash or hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("content_hash must be a lowercase SHA-256 digest")
    parents = merge_into_parents(split_paragraphs(text), parent_max_chars, parent_overlap_chars)
    chunks: list[KnowledgeChunk] = []
    for parent_index, parent in enumerate(parents):
        parent_digest = hashlib.sha256(parent.encode("utf-8")).hexdigest()[:16]
        # The position identifies a stable parent group within this content,
        # even if identical parent text occurs at different document positions.
        parent_id = f"{digest}:parent:{parent_index}:{parent_digest}"
        for child_index, child in enumerate(split_into_children(parent, child_window_size, child_step_size)):
            chunks.append(KnowledgeChunk(child, parent_id, parent, child_index))
    return chunks
