"""File-type aware text extractor.

Supported: PDF, DOCX, XLSX, CSV, TXT/MD, HTML. Each extractor returns
either a plain string (prose) or a list of (sheet, rows) tuples
(tabular). The caller branches on the return shape.

Resource caps (`MAX_PAGES`, `MAX_ROWS`, `MAX_CHARS`) bound extraction
so a single pathological file (huge PDF, million-row sheet) cannot
monopolize memory/CPU or produce an unbounded number of chunks.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import List, Tuple

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Resource caps — defend against decompression / row-count DoS.
MAX_PAGES = 500
MAX_ROWS = 50_000
MAX_CHARS = 2_000_000  # ~ a few hundred pages of text; chunker will split it
MAX_HTML_CHARS = 2_000_000


class ExtractionResult:
    """Holds the output of an extract step. `mode` is 'prose' or 'tabular'."""

    __slots__ = ("mode", "text", "tables", "meta")

    def __init__(
        self,
        mode: str,
        text: str = "",
        tables: List[Tuple[str, List[List[str]]]] | None = None,
        meta: dict | None = None,
    ) -> None:
        self.mode = mode
        self.text = text
        self.tables = tables or []
        self.meta = meta or {}


def _cap_text(text: str) -> str:
    return text[:MAX_CHARS]


def _ext_pdf(path: Path) -> ExtractionResult:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        if i >= MAX_PAGES:
            log.warning("extract.pdf.page_cap", path=str(path), cap=MAX_PAGES)
            break
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("extract.pdf.page_failed", page=i, error=str(exc))
            continue
        if total + len(page_text) > MAX_CHARS:
            page_text = page_text[: max(0, MAX_CHARS - total)]
            parts.append(page_text)
            log.warning("extract.pdf.char_cap", path=str(path))
            break
        parts.append(page_text)
        total += len(page_text)
    return ExtractionResult(mode="prose", text="\n\n".join(parts), meta={"pages": len(parts)})


def _ext_docx(path: Path) -> ExtractionResult:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    parts = []
    total = 0
    for p in doc.paragraphs:
        if not p.text:
            continue
        if total >= MAX_CHARS:
            log.warning("extract.docx.char_cap", path=str(path))
            break
        parts.append(p.text)
        total += len(p.text)
    return ExtractionResult(mode="prose", text="\n\n".join(parts))


def _ext_xlsx(path: Path) -> ExtractionResult:
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    tables: list[Tuple[str, List[List[str]]]] = []
    total_rows = 0
    for ws in wb.worksheets:
        rows: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            if total_rows >= MAX_ROWS:
                log.warning("extract.xlsx.row_cap", sheet=ws.title, cap=MAX_ROWS)
                break
            rows.append(["" if c is None else str(c) for c in row])
            total_rows += 1
        if rows:
            tables.append((ws.title, rows))
        if total_rows >= MAX_ROWS:
            break
    return ExtractionResult(mode="tabular", tables=tables, meta={"sheets": len(tables)})


def _ext_csv(path: Path) -> ExtractionResult:
    # Read bytes first so we can reject binary files masquerading as CSV
    # (a NUL-heavy file would otherwise be ingested as garbage text).
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("File contains NUL bytes; not a valid CSV/text file.")
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows: list[list[str]] = []
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            log.warning("extract.csv.row_cap", path=str(path), cap=MAX_ROWS)
            break
        rows.append([c for c in row])
    return ExtractionResult(mode="tabular", tables=[(path.stem, rows)])


def _ext_text(path: Path) -> ExtractionResult:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("File contains NUL bytes; not a valid text file.")
    text = raw.decode("utf-8", errors="replace")
    return ExtractionResult(mode="prose", text=_cap_text(text))


def _ext_html(path: Path) -> ExtractionResult:
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._buf: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ARG002
            if tag in {"script", "style"}:
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in {"script", "style"} and self._skip:
                self._skip -= 1

        def handle_data(self, data: str) -> None:
            if not self._skip and data.strip():
                self._buf.append(data)

    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("File contains NUL bytes; not a valid HTML file.")
    text = raw.decode("utf-8", errors="replace")[:MAX_HTML_CHARS]
    s = _Stripper()
    s.feed(text)
    return ExtractionResult(mode="prose", text="\n".join(s._buf))


def _ext_doc(path: Path) -> ExtractionResult:
    """Legacy binary .doc is not supported.

    We accept the extension at upload (so the UI can tell the user to
    convert) but extraction fails with a clear, actionable message
    rather than a confusing KeyError deep in the pipeline.
    """
    raise ValueError(
        "Legacy .doc files are not supported. Please convert to .docx "
        "(or PDF) and re-upload."
    )


_EXTRACTORS = {
    "pdf": _ext_pdf,
    "docx": _ext_docx,
    "xlsx": _ext_xlsx,
    "csv": _ext_csv,
    "txt": _ext_text,
    "md": _ext_text,
    "html": _ext_html,
    "htm": _ext_html,
    "doc": _ext_doc,
}


def extract(path: Path) -> ExtractionResult:
    """Dispatch to the correct extractor based on file extension."""
    ext = path.suffix.lower().lstrip(".")
    if ext not in settings.ALLOWED_UPLOAD_EXTS:
        raise ValueError(f"Unsupported file type: .{ext}")
    fn = _EXTRACTORS.get(ext)
    if fn is None:
        raise ValueError(f"No extractor registered for .{ext}")
    log.info("extract.start", path=str(path), ext=ext)
    result = fn(path)
    log.info(
        "extract.done",
        path=str(path),
        mode=result.mode,
        chars=len(result.text),
        tables=len(result.tables),
    )
    return result