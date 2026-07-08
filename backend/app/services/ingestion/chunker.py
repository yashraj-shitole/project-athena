"""Chunker for both prose and tabular inputs.

Prose: sentence-boundary aware, target ~300 tokens, ~50 overlap.
Tabular: groups rows into chunks whose combined size lands near the
         prose target. The header row is repeated in every chunk so
         each row's context is preserved.

Two entry points:
  * ``chunk(result)`` — returns a list. The original API; kept as a
    thin wrapper over the generator for back-compat with tests and
    any future caller that wants the full list in one go.
  * ``iter_chunks(result)`` — yields chunks one at a time. Use this
    from the pipeline so each batch's text can be released as soon as
    the encoder has consumed it; the previous list-based flow
    materialised the entire corpus in memory before any embedding
    work began.

Both produce the *same* chunks in the *same* order — ``chunk`` is
literally ``list(iter_chunks(...))``. The pipeline is being moved
to the generator; existing callers of ``chunk`` (and the per-chunk
sanity tests) are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

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


def iter_prose_chunks(text: str) -> Iterator[Chunk]:
    """Sentence-aware prose chunker, streaming.

    The same algorithm as the old `chunk_prose`, but each chunk is
    yielded as it is produced. The char-offset cursor lives in a
    closure and is advanced as chunks are consumed — callers that
    hold onto earlier chunks (e.g. the pipeline accumulating a batch
    of N chunks before embedding) are fine, because the cursor only
    matters for the *next* chunk's start position.
    """
    cleaned = clean_text(text)
    if not cleaned:
        return
    sentences = split_sentences(cleaned)
    if not sentences:
        # Whole cleaned text is a single sentence (or no boundary found).
        # Match the old behaviour: emit one chunk containing the cleaned
        # text in full, with no char offsets.
        yield Chunk(content=cleaned)
        return
    target = settings.CHUNK_TARGET_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS
    buckets = _pack_sentences(sentences, target=target, overlap=overlap)

    cursor = 0
    for idx, bucket in enumerate(buckets):
        body = " ".join(bucket)
        start = cleaned.find(bucket[0], cursor) if bucket else cursor
        if start < 0:
            start = cursor
        end = start + len(body) if start >= 0 else cursor + len(body)
        cursor = max(end, cursor)
        yield Chunk(
            content=body,
            char_start=start,
            char_end=end,
            meta={"index": idx},
        )


def _row_tokens(row: List[str]) -> int:
    return max(1, count_tokens("\t".join(row)))


def iter_tabular_chunks(tables: List[Tuple[str, List[List[str]]]]) -> Iterator[Chunk]:
    """Row-grouping tabular chunker, streaming. Each chunk = header + N rows."""
    target = settings.CHUNK_TARGET_TOKENS
    for sheet, rows in tables:
        if not rows:
            continue
        header, *body = rows
        if not header:
            continue
        cur: list[list[str]] = []
        cur_tokens = _row_tokens(header)
        # `chunk_index` is the position of this chunk within the
        # whole document, not just within the sheet — but we can't
        # know that without a single counter, which only the caller
        # can supply. We emit `meta={"sheet": sheet}` and let the
        # caller set "index" if it needs the global position.
        for ridx, row in enumerate(body):
            r_tokens = _row_tokens(row)
            if cur and cur_tokens + r_tokens > target:
                content_lines = ["\t".join(header)] + ["\t".join(r) for r in cur]
                yield Chunk(
                    content="\n".join(content_lines),
                    row_range=(ridx - len(cur), ridx - 1),
                    meta={"sheet": sheet},
                )
                cur = []
                cur_tokens = _row_tokens(header)
            cur.append(row)
            cur_tokens += r_tokens
        if cur:
            content_lines = ["\t".join(header)] + ["\t".join(r) for r in cur]
            yield Chunk(
                content="\n".join(content_lines),
                row_range=(len(body) - len(cur), len(body) - 1),
                meta={"sheet": sheet},
            )


def iter_chunks(result: ExtractionResult) -> Iterator[Chunk]:
    """Top-level streaming chunker, dispatched on extraction mode."""
    if result.mode == "tabular":
        return iter_tabular_chunks(result.tables)
    return iter_prose_chunks(result.text)


def chunk(result: ExtractionResult) -> List[Chunk]:
    """Materialise every chunk at once. Use `iter_chunks` from the
    pipeline; this exists for tests and any future caller that
    genuinely needs the list."""
    return list(iter_chunks(result))


def chunk_prose(text: str) -> List[Chunk]:
    """Sentence-aware prose chunker (list form)."""
    return list(iter_prose_chunks(text))


def chunk_tabular(tables: List[Tuple[str, List[List[str]]]]) -> List[Chunk]:
    """Row-grouping chunker (list form). Each chunk = header + N rows."""
    return list(iter_tabular_chunks(tables))
