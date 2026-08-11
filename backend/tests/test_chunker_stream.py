"""Tests for the streaming chunker (``iter_chunks``).

The pipeline is being moved to the generator API so each batch's
text can be released after the encoder has consumed it. The list
form (``chunk``) is kept as `list(iter_chunks(...))` for back-compat
— these tests guard that contract.
"""
from __future__ import annotations

from typing import List

from app.services.ingestion import chunker
from app.services.ingestion.chunker import (
    Chunk,
    chunk,
    chunk_prose,
    chunk_tabular,
    iter_chunks,
    iter_prose_chunks,
    iter_tabular_chunks,
)
from app.services.ingestion.extractors import ExtractionResult


# --- Prose --------------------------------------------------------------


def test_iter_prose_matches_list_form_short():
    """Short prose: identical output from generator and list forms."""
    text = "First sentence. Second sentence. Third sentence."
    as_list = chunk_prose(text)
    as_iter = list(iter_prose_chunks(text))
    assert [c.content for c in as_list] == [c.content for c in as_iter]
    assert len(as_list) == len(as_iter)


def test_iter_prose_matches_list_form_long():
    """Long prose (multiple buckets): the streaming walk must produce
    the same chunks in the same order with the same char offsets."""
    text = (
        "Athena is a personal research assistant. It indexes your "
        "documents and answers questions grounded in them. The pipeline "
        "splits long text into overlapping chunks, embeds each chunk "
        "with a sentence-transformer model, and stores the vectors in "
        "Postgres via pgvector. Retrieval is hybrid: lexical (BM25 over "
        "tsvector) plus vector (HNSW cosine), fused with reciprocal rank. "
    ) * 8
    as_list = chunk_prose(text)
    as_iter = list(iter_prose_chunks(text))
    assert len(as_list) == len(as_iter)
    for a, b in zip(as_list, as_iter):
        assert a.content == b.content
        assert a.char_start == b.char_start
        assert a.char_end == b.char_end
        assert a.meta == b.meta


def test_iter_prose_empty_input_yields_nothing():
    assert list(iter_prose_chunks("")) == []


def test_iter_prose_no_sentence_boundary_yields_one_chunk():
    """A text without sentence-end punctuation produces a single chunk
    containing the cleaned text in full, matching the old behavior."""
    text = "no boundary here just a long run of words without periods"
    chunks = list(iter_prose_chunks(text))
    assert len(chunks) == 1
    assert chunks[0].content
    assert "no boundary" in chunks[0].content


# --- Tabular ------------------------------------------------------------


def test_iter_tabular_matches_list_form():
    """Tabular: streamed output equals the list output, ignoring the
    per-chunk ``meta["index"]`` (the generator doesn't track the
    global position — that's the caller's job via enumerate)."""
    tables: list[tuple[str, list[list[str]]]] = [
        (
            "sheet1",
            [
                ["col_a", "col_b"],
                ["1", "alpha"],
                ["2", "beta"],
                ["3", "gamma"],
                ["4", "delta"],
                ["5", "epsilon"],
            ],
        )
    ]
    as_list = chunk_tabular(tables)
    as_iter = list(iter_tabular_chunks(tables))
    assert len(as_list) == len(as_iter)
    for a, b in zip(as_list, as_iter):
        assert a.content == b.content
        assert a.row_range == b.row_range
        # Both must carry the sheet name; the list form also had an
        # "index" key in meta. The streaming form does not — that's
        # a deliberate behavior change; downstream callers don't
        # read it (grep clean in the repo).
        assert a.meta["sheet"] == b.meta["sheet"]


def test_iter_tabular_empty():
    assert list(iter_tabular_chunks([])) == []


def test_iter_tabular_single_sheet_no_body():
    tables = [("sheet1", [["col_a", "col_b"]])]
    # A header-only sheet is a no-op (the old code did the same).
    assert list(iter_tabular_chunks(tables)) == []


def test_iter_tabular_multiple_sheets():
    tables = [
        ("alpha", [["x"], ["1"], ["2"]]),
        ("beta", [["y"], ["3"], ["4"], ["5"], ["6"]]),
    ]
    chunks = list(iter_tabular_chunks(tables))
    assert len(chunks) >= 1
    sheets = [c.meta["sheet"] for c in chunks]
    # Both sheets produced at least one chunk.
    assert "alpha" in sheets
    assert "beta" in sheets


# --- Dispatch -----------------------------------------------------------


def test_iter_chunks_dispatches_prose():
    result = ExtractionResult(mode="prose", text="Hello world. Goodbye world.", tables=[])
    out = list(iter_chunks(result))
    ref = chunk(result)
    assert [c.content for c in out] == [c.content for c in ref]


def test_iter_chunks_dispatches_tabular():
    tables = [("s", [["c"], ["v1"], ["v2"], ["v3"]])]
    result = ExtractionResult(mode="tabular", text="", tables=tables)
    out = list(iter_chunks(result))
    ref = chunk(result)
    assert [c.content for c in out] == [c.content for c in ref]


# --- Lazy semantics -----------------------------------------------------


def test_iter_chunks_is_a_generator():
    """`iter_chunks` should return a generator (not a list) so the
    pipeline can stream. `chunk` is the list form."""
    tables = [("s", [["c"], ["1"]])]
    result = ExtractionResult(mode="tabular", text="", tables=tables)
    it = iter_chunks(result)
    # The `Iterator` protocol: `next()` works, no `__len__`.
    assert not hasattr(it, "__len__")
    first = next(it)
    assert isinstance(first, Chunk)
    # Closing the generator shouldn't raise.
    it.close()
