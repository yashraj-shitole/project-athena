"""Micro-benchmark for the document ingestion pipeline.

Goal
----
Measure end-to-end and per-stage wall-clock for the pipeline on
representative inputs, so we can compare a "before" run to an
"after" run when we refactor for speed. This is *not* a regression
test — it has hard external dependencies (live Postgres, the
embedding model) and is skipped from the regular pytest run.

How to run
----------
The script connects to the database and the embedding model the
running dev stack provides. Easiest path:

  1. Bring the stack up:  .\\docker-up.ps1
  2. Open a shell in the api container and run the script there, so
     the embedding model is loaded once and shared with the running
     API:  docker exec -it athena-api python -m tests.perf.ingestion
  3. The script writes a fresh "latest" table to docs/perf-ingestion.md
     and prints the same table to stdout. Compare two runs side by
     side to see the deltas.

Caveats
-------
- The first call pays the model-load cost (one-time, ~3-5s on the
  MiniLM model). The script reports it as a "warmup" run; the
  "median" column is over the remaining runs and is the number to
  compare.
- This script ASSERTS that the DB is Postgres. SQLite (the unit-test
  engine) doesn't support asyncpg's copy_records_to_table, so any
  numbers it produces there would be misleading.
- The script creates one row in `users` (a fixture user) and one
  row per input in `documents` + N rows in `document_chunks`. All
  rows are tagged with a unique `filename` prefix so a re-run
  doesn't collide; old rows are NOT deleted — keep the DB small by
  running `docker compose down -v` between major runs.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import statistics
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, List, Sequence

# Make `app` importable when invoked as a module from any CWD.
# File lives at backend/tests/perf/ingestion.py → parents[1] is
# `backend/`, and that's where the `app` package lives.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402  — only for the @pytest.mark.skip marker

from sqlalchemy import insert, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal, set_rls_user, reset_rls_user  # noqa: E402
from app.models.chunk import DocumentChunk  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.ingestion.pipeline import ingest_document  # noqa: E402


# Skip from the regular pytest run. The file is intentionally a
# pytest test file (so it lives next to the suite) but the actual
# benchmark is driven by `python -m backend.tests.perf.ingestion`.
pytestmark = pytest.mark.skip(
    reason="perf-only; invoke via `python -m backend.tests.perf.ingestion`"
)


# ---------------------------------------------------------------------
# Synthetic input generators
# ---------------------------------------------------------------------


def _prose_body(paragraphs: int) -> str:
    """A short prose block — exercises chunk_prose."""
    return (
        "Athena is a personal research assistant. It indexes your "
        "documents and answers questions grounded in them. The pipeline "
        "splits long text into overlapping chunks, embeds each chunk "
        "with a sentence-transformer model, and stores the vectors in "
        "Postgres via pgvector. Retrieval is hybrid: lexical (BM25 over "
        "tsvector) plus vector (HNSW cosine), fused with reciprocal rank. "
    ) * max(1, paragraphs)


def _make_txt(rows_of_prose: int) -> bytes:
    """Small input — 1k tokens of prose. Exercises chunk_prose."""
    return _prose_body(rows_of_prose).encode("utf-8")


def _make_csv(num_rows: int, cols: int = 6) -> bytes:
    """Medium/large input — a CSV with `num_rows` rows, exercises
    chunk_tabular. Tabular mode has its own chunker; the column
    count barely changes the chunk size, so we hold it constant
    while we vary rows to dial the byte budget."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"col_{i}" for i in range(cols)] + ["total"])
    for r in range(num_rows):
        w.writerow([f"r{r}", f"k{r}", "alpha", "beta", "gamma", "delta", f"{r*7%1000}"])
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------
# Per-stage timing harness
# ---------------------------------------------------------------------


@dataclass
class StageSample:
    name: str
    started: float
    ended: float

    @property
    def ms(self) -> float:
        return (self.ended - self.started) * 1000.0


@dataclass
class RunResult:
    label: str
    warmup: bool
    total_ms: float
    stages: List[StageSample] = field(default_factory=list)
    error: str | None = None
    # Sub-stage split of the "embedding" stage, populated from the
    # `indexed` event payload (embed compute / keyword encode / DB COPY
    # / progress flush). Lets the table show DB-bound vs encoder-bound.
    sub_ms: dict = field(default_factory=dict)


StatusCb = Callable[[str, dict], Awaitable[None]]


def _make_status_cb(
    sink: List[StageSample], sub_ms: dict | None = None
) -> StatusCb:
    """Translate pipeline events into StageSample rows.

    The pipeline emits: "processing" → "extracted" → "chunked" →
    "embedding" (per batch) → "embedded" → "indexed" (or "failed").
    We pair events to derive per-stage durations:

      extracting = processing→extracted
      chunking   = extracted→chunked
      embedding  = chunked→embedded    (includes every batch callback)
      indexing   = embedded→indexed

    On the terminal `indexed` event we also copy the pipeline's sub-stage
    split (`embed_ms`/`keyword_ms`/`copy_ms`/`flush_ms`) out of the
    payload into `sub_ms` so the reporter can break the embedding stage
    into DB-bound vs encoder-bound pieces.
    """
    seen: dict[str, float] = {}

    async def cb(event: str, payload: dict) -> None:
        now = time.perf_counter()
        if event == "processing":
            seen["processing"] = now
        elif event == "extracted":
            seen["extracting"] = seen.get("processing", now)
            sink.append(StageSample("extracting", seen["extracting"], now))
        elif event == "chunked":
            seen["chunked"] = now
            seen["extracting"] = seen.get("extracting", seen["chunked"])
            # chunking elapsed = chunked - extracted
            sink.append(StageSample("chunking", seen["extracting"], now))
        elif event == "embedded":
            seen["embedded"] = now
            sink.append(StageSample("embedding", seen["chunked"], now))
        elif event == "indexed":
            seen["indexed"] = now
            sink.append(StageSample("indexing", seen["embedded"], now))
            sink.append(StageSample("total", seen["processing"], now))
            if sub_ms is not None:
                for k in ("embed_ms", "keyword_ms", "copy_ms", "flush_ms"):
                    if k in payload:
                        sub_ms[k] = int(payload[k])
        elif event == "failed":
            seen["failed"] = now
            sink.append(StageSample("failed", seen["processing"], now))
    return cb


# ---------------------------------------------------------------------
# Per-input run
# ---------------------------------------------------------------------


@dataclass
class InputSpec:
    label: str
    ext: str
    body: bytes
    # Approximate "expected chunk count" for the row table. The script
    # doesn't enforce this; it just reads it back from the doc row.
    expected_chunks: int


async def _run_once(
    *,
    user_id: uuid.UUID,
    spec: InputSpec,
    storage_dir: Path,
    warmup: bool,
) -> RunResult:
    """Run ingest_document once on `spec`; return the timings."""
    doc_id = uuid.uuid4()
    storage_dir.mkdir(parents=True, exist_ok=True)
    file_path = storage_dir / f"{doc_id}.{spec.ext}"
    file_path.write_bytes(spec.body)

    samples: List[StageSample] = []
    sub_ms: dict = {}
    cb = _make_status_cb(samples, sub_ms)
    error: str | None = None
    started = time.perf_counter()
    try:
        async with SessionLocal() as session:
            await set_rls_user(session, user_id)
            try:
                doc = Document(
                    id=doc_id,
                    user_id=user_id,
                    filename=f"perf-{spec.label}-{doc_id}.{spec.ext}",
                    file_type=spec.ext,
                    storage_path=str(file_path),
                    size_bytes=len(spec.body),
                    status="uploaded",
                )
                session.add(doc)
                await session.commit()
                await session.refresh(doc)
                await ingest_document(
                    session,
                    document=doc,
                    file_path=file_path,
                    status_cb=cb,
                )
            finally:
                await reset_rls_user(session)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    ended = time.perf_counter()

    # Read back chunk_count so the user sees real numbers.
    chunk_count = 0
    if error is None:
        async with SessionLocal() as session:
            await set_rls_user(session, user_id)
            try:
                res = await session.execute(
                    text("SELECT chunk_count FROM documents WHERE id = :id"),
                    {"id": str(doc_id)},
                )
                row = res.first()
                if row is not None and row[0] is not None:
                    chunk_count = int(row[0])
            finally:
                await reset_rls_user(session)

    result = RunResult(
        label=spec.label,
        warmup=warmup,
        total_ms=(ended - started) * 1000.0,
        stages=samples,
        error=error,
        sub_ms=sub_ms,
    )
    # Stash chunk_count on the result for the printer — dataclass is
    # closed by default; we attach it as an attribute.
    setattr(result, "chunk_count", chunk_count)
    return result


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------


def _format_table(results: Sequence[RunResult]) -> str:
    """Render a Markdown table of per-stage medians (excluding warmups)."""
    by_label: dict[str, list[RunResult]] = {}
    for r in results:
        by_label.setdefault(r.label, []).append(r)

    header = [
        "input",
        "size",
        "chunks",
        "extracting",
        "chunking",
        "embedding",
        "embed",
        "keyword",
        "copy",
        "indexing",
        "total",
        "runs",
    ]
    lines: list[str] = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    for label, runs in by_label.items():
        non_warmup = [r for r in runs if not r.warmup and r.error is None]
        warmup = [r for r in runs if r.warmup and r.error is None]

        def _med(stage_name: str) -> float:
            vals = [
                s.ms
                for r in non_warmup
                for s in r.stages
                if s.name == stage_name
            ]
            return statistics.median(vals) if vals else float("nan")

        def _med_sub(key: str) -> float:
            vals = [r.sub_ms[key] for r in non_warmup if key in r.sub_ms]
            return statistics.median(vals) if vals else float("nan")

        spec = _input_for_label(label)
        size_mb = f"{len(spec.body) / (1024 * 1024):.1f}MB" if spec else "?"
        rep = (
            max(non_warmup, key=lambda r: getattr(r, "chunk_count", 0))
            if non_warmup
            else (warmup[0] if warmup else None)
        )
        chunks = f"{getattr(rep, 'chunk_count', 0):,}" if rep else "?"
        total_med = (
            f"{statistics.median([r.total_ms for r in non_warmup]):.0f}"
            if non_warmup
            else "—"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    size_mb,
                    chunks,
                    f"{_med('extracting'):.0f}",
                    f"{_med('chunking'):.0f}",
                    f"{_med('embedding'):.0f}",
                    f"{_med_sub('embed_ms'):.0f}",
                    f"{_med_sub('keyword_ms'):.0f}",
                    f"{_med_sub('copy_ms'):.0f}",
                    f"{_med('indexing'):.0f}",
                    total_med,
                    f"{len(non_warmup)} (+{len(warmup)}w)",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


# Helper: a tiny registry so _format_table can look up the byte size
# of each labelled input without threading the list through the print
# path. Set in main() before the runs.
_INPUTS_BY_LABEL: dict[str, InputSpec] = {}


def _input_for_label(label: str) -> InputSpec | None:
    return _INPUTS_BY_LABEL.get(label)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


INPUTS: list[InputSpec] = [
    # Prose: 2 paragraphs ≈ 200 tokens. A small but realistic case.
    InputSpec(label="small_prose", ext="txt", body=_make_txt(2), expected_chunks=1),
    # Prose: 60 paragraphs ≈ 6k tokens. Mid-sized prose doc.
    InputSpec(label="medium_prose", ext="txt", body=_make_txt(60), expected_chunks=20),
    # CSV: 5k rows (~1MB). Tabular path, mid-size.
    InputSpec(label="medium_csv", ext="csv", body=_make_csv(5_000), expected_chunks=5),
    # CSV: 50k rows (~10MB). Tabular path, near the 25MB cap.
    # The OLD pipeline spends ~26 minutes on this one input (per-chunk
    # keyword encoder is N forward passes). Skip it via the env var
    # `ATHENA_PERF_SKIP_LARGE=1` to keep the bench wall-clock under 5
    # minutes; the medium_csv row already demonstrates the indexing-
    # stage win. Set in both the "before" and "after" runs so the
    # comparison is apples-to-apples.
    InputSpec(label="large_csv", ext="csv", body=_make_csv(50_000), expected_chunks=50),
]


async def _make_fixture_user() -> uuid.UUID:
    """Create (or look up) a User row we can attach the perf docs to.

    We don't go through the auth path — bcrypt-hashing a password
    here is a one-line cost; this row is for measurements only.
    """
    email = f"perf+{uuid.uuid4().hex[:8]}@athena.local"
    async with SessionLocal() as session:
        # `users` has no RLS, so no GUC set.
        res = await session.execute(
            insert(User)
            .values(email=email, password_hash="(perf-only)")
            .returning(User.id)
        )
        uid = res.scalar_one()
        await session.commit()
    return uid


async def _delete_fixture_user(user_id: uuid.UUID) -> None:
    """Best-effort: nuke the fixture user (cascades to documents + chunks)."""
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
        await session.commit()


async def main() -> int:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        print(
            f"refusing to run: this benchmark needs real Postgres (got "
            f"{settings.database_url!r}). Start the docker stack first.",
            file=sys.stderr,
        )
        return 2

    # Sanity: confirm the dialect via a probe query.
    async with SessionLocal() as session:
        res = await session.execute(text("SELECT current_database()"))
        print(f"connected to {res.scalar()}")

    for spec in INPUTS:
        _INPUTS_BY_LABEL[spec.label] = spec

    storage_dir = Path(tempfile.gettempdir()) / f"athena-perf-{os.getpid()}"
    user_id = await _make_fixture_user()
    print(f"fixture user: {user_id}")
    print(f"storage dir:  {storage_dir}")

    try:
        all_results: list[RunResult] = []
        # Honor the skip-large env var. We don't mutate the module-
        # level INPUTS list (callers may read it); we filter the
        # local iteration instead.
        run_inputs = [s for s in INPUTS if not (
            s.label == "large_csv" and os.environ.get("ATHENA_PERF_SKIP_LARGE") == "1"
        )]
        for spec in run_inputs:
            # Warmup + 3 measured runs per spec. Warmup pays the
            # one-time encoder-load cost; the medians below are over
            # the non-warmup runs only.
            for i, warmup in enumerate([True, False, False, False]):
                print(f"  → {spec.label} run {i+1}/4 ...", flush=True)
                res = await _run_once(
                    user_id=user_id,
                    spec=spec,
                    storage_dir=storage_dir / spec.label,
                    warmup=warmup,
                )
                if res.error:
                    print(f"    ERROR: {res.error}", file=sys.stderr)
                else:
                    cc = getattr(res, "chunk_count", 0)
                    print(
                        f"    ok: total={res.total_ms:.0f}ms  "
                        f"chunks={cc:,}  "
                        f"stages="
                        f"{[(s.name, f'{s.ms:.0f}') for s in res.stages]}  "
                        f"sub={ {k: f'{v}ms' for k, v in res.sub_ms.items()} }"
                    )
                all_results.append(res)
    finally:
        await _delete_fixture_user(user_id)
        # Best-effort: also delete the temp storage dir.
        try:
            for sub in storage_dir.iterdir():
                for f in sub.iterdir():
                    f.unlink(missing_ok=True)
                sub.rmdir()
            storage_dir.rmdir()
        except OSError:
            pass

    table = _format_table(all_results)

    print()
    print(table)

    docs = ROOT.parent / "docs"
    out_path = docs / "perf-ingestion.md"
    # The container's rootfs is read-only (api runs as `athena` with
    # read_only: True), so `docs/` may not exist or be writable. The
    # perf script lives in the image but the output directory does
    # not — we write to /tmp (tmpfs) and the operator can `docker cp`
    # the file out, OR set ATHENA_PERF_OUTPUT to a writable path.
    out_env = os.environ.get("ATHENA_PERF_OUTPUT")
    if out_env:
        out_path = Path(out_env)
    else:
        out_path = Path("/tmp/perf-ingestion.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Document ingestion micro-benchmark\n\n"
        f"Generated by `backend/tests/perf/ingestion.py` on "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
        f"Stages are medians over 3 measured runs (1 warmup run is "
        f"excluded; it pays the encoder-load cost).\n"
        f"All times in milliseconds.\n\n"
    )
    out_path.write_text(header + table + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
