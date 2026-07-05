"""Chunker for both prose and tabular inputs.

Prose: sentence-boundary aware, target ~300 tokens, ~50 overlap.
Tabular: groups rows into chunks whose combined size lands near the
         prose target. The header row is repeated in every chunk so
         each row's context is preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from app.core.config import settings
from app.services.ingestion.extractors import ExtractionResult
from app.services.text import clean_text, count_tokens, split_sentences


@dataclass
class Chunk:
    content: str
    page_number: Optional[int] = None
    row_range: Optional[Tuple[int, int]] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    meta: dict = field(default_factory=dict)


def _pack_sentences(sentences: List[str], target: int, overlap: int) -> List[List[str]]:
    """Greedy pack sentences into buckets, each near `target` tokens.

    `overlap` is realized at the chunk level (the last N sentences of the
    previous chunk are prepended to the next).
    """
    if not sentences:
        return []
    buckets: list[list[str]] = []
    cur: list[str] = []
    cur_tokens = 0
    for sent in sentences:
        s_tokens = max(1, count_tokens(sent))
        if cur and cur_tokens + s_tokens > target:
            buckets.append(cur)
            tail = cur[-overlap:] if overlap > 0 else []
            cur = list(tail)
            cur_tokens = sum(count_tokens(x) for x in cur)
        cur.append(sent)
        cur_tokens += s_tokens
    if cur:
        buckets.append(cur)
    return buckets


def chunk_prose(text: str) -> List[Chunk]:
    """Sentence-aware prose chunker."""
    cleaned = clean_text(text)
    if not cleaned:
        return []
    sentences = split_sentences(cleaned)
    if not sentences:
        return [Chunk(content=cleaned)]
    target = settings.CHUNK_TARGET_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS
    buckets = _pack_sentences(sentences, target=target, overlap=overlap)

    # Estimate char offsets by walking the original cleaned text once.
    chunks: list[Chunk] = []
    cursor = 0
    for idx, bucket in enumerate(buckets):
        body = " ".join(bucket)
        start = cleaned.find(bucket[0], cursor) if bucket else cursor
        end = start + len(body) if start >= 0 else cursor + len(body)
        if start < 0:
            start = cursor
        cursor = max(end, cursor)
        chunks.append(
            Chunk(
                content=body,
                char_start=start,
                char_end=end,
                meta={"index": idx},
            )
        )
    return chunks


def _row_tokens(row: List[str]) -> int:
    return max(1, count_tokens("\t".join(row)))


def chunk_tabular(tables: List[Tuple[str, List[List[str]]]]) -> List[Chunk]:
    """Row-grouping chunker. Each chunk = header + N rows."""
    if not tables:
        return []
    target = settings.CHUNK_TARGET_TOKENS
    chunks: list[Chunk] = []
    for sheet, rows in tables:
        if not rows:
            continue
        header, *body = rows
        if not header:
            continue
        cur: list[list[str]] = []
        cur_tokens = _row_tokens(header)
        for ridx, row in enumerate(body):
            r_tokens = _row_tokens(row)
            if cur and cur_tokens + r_tokens > target:
                # Flush
                content_lines = ["\t".join(header)] + ["\t".join(r) for r in cur]
                chunks.append(
                    Chunk(
                        content="\n".join(content_lines),
                        row_range=(ridx - len(cur), ridx - 1),
                        meta={"sheet": sheet, "index": len(chunks)},
                    )
                )
                cur = []
                cur_tokens = _row_tokens(header)
            cur.append(row)
            cur_tokens += r_tokens
        if cur:
            content_lines = ["\t".join(header)] + ["\t".join(r) for r in cur]
            chunks.append(
                Chunk(
                    content="\n".join(content_lines),
                    row_range=(len(body) - len(cur), len(body) - 1),
                    meta={"sheet": sheet, "index": len(chunks)},
                )
            )
    return chunks


def chunk(result: ExtractionResult) -> List[Chunk]:
    """Top-level chunker dispatching on extraction mode."""
    if result.mode == "tabular":
        return chunk_tabular(result.tables)
    return chunk_prose(result.text)
