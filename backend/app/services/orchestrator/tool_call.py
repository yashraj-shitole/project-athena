"""Tool-call validation, retry, and deterministic fallback (FR-23).

When the LLM emits a tool call:
  1. Parse arguments against the tool's JSON schema
  2. On failure, retry once with a corrective system note
  3. If still invalid, fall back to extracting keywords from the user's
     message via a deterministic heuristic, so the turn never crashes
     (NFR-10).
"""
from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft7Validator, ValidationError

from app.core.logging import get_logger
from app.services.text import clean_text

log = get_logger(__name__)


_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have he her his i in is it its of on
    or our she that the their them they this to was we were will with you your
    can could may might shall should would do does did doing done about above
    after again against all am any because been before being below between
    both but by could didn't doesn't don't during each few for from further
    had hadn't hasn't haven't he he'll he's herself himself herself him
    himself his how how's i i'd i'll i'm i've if in into is isn't it it's its
    itself let's me more most mustn't my myself need no nor not of off on
    once only or other ought our ours ourselves out over own same she she'd
    she'll she's should shouldn't so some such than that that's the their
    theirs them themselves then there there's these they they'd they'll
    they're they've this those through to too under until up very was wasn't
    we we'd we'll we're we've were weren't what what's when when's where
    where's which while who who's whom why why's with won't would wouldn't
    you you'd you'll you're you've your yours yourself yourselves also etc eg
    ie using use used uses using
    """.split()
)

# Common imperative verbs users use to *frame* a question but which
# should not be passed to the tsquery as a literal term (they would
# narrow the match to documents that contain the verb). Examples:
#   "Search for Fusce vitae"        -> keywords = ["fusce", "vitae"]
#   "Find documents about tincidunt" -> keywords = ["tincidunt"]
#   "Look up vestibulum velit"      -> keywords = ["vestibulum", "velit"]
_QUERY_VERB_STOPWORDS = frozenset(
    """
    search find lookup look show tell give list get fetch retrieve
    bring pull display view open read write tell say talk discuss explain
    describe outline summarize summary help please need want
    """.split()
)

# Common *meta* nouns that refer to the corpus itself rather than
# the answer. Including these in the tsquery forces an exact match
# on the word "document" / "file" etc., which narrows results to
# chunks that happen to contain that word — almost never what the
# user wants.
#   "What does the document say about X?" -> keywords = ["x"]
#   "Find files mentioning Y"             -> keywords = ["y"]
#   "Summarize the PDF"                   -> keywords = ["summarize", "pdf"]
_CORPUS_META_STOPWORDS = frozenset(
    """
    document documents doc docs file files pdf csv txt md html docx xlsx
    page pages snippet snippets chunk chunks passage passages text content
    data row rows column columns table tables spreadsheet sheet sheets
    row item items entry entries record records database index indexed
    corpus source sources material materials
    """.split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")


def validate_arguments(args: dict | None, schema: dict) -> tuple[bool, str | None]:
    """Return (ok, error_message)."""
    if args is None:
        args = {}
    try:
        Draft7Validator.check_schema(schema)
        Draft7Validator(schema).validate(args)
        return True, None
    except ValidationError as exc:
        return False, str(exc.message)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def fallback_keywords(message: str, top_k: int = 6) -> list[str]:
    """Deterministic keyword fallback (NFR-10)."""
    if not message:
        return []
    cleaned = clean_text(message).lower()
    counts: dict[str, int] = {}
    for m in _WORD_RE.finditer(cleaned):
        w = m.group(0)
        if (
            w in _STOPWORDS
            or w in _QUERY_VERB_STOPWORDS
            or w in _CORPUS_META_STOPWORDS
            or w.isdigit()
        ):
            continue
        counts[w] = counts.get(w, 0) + 1
    # Prefer content words; sort by freq desc, then alphabetical for stability.
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in items[: max(1, top_k)]]


def coerce_arguments(raw: Any) -> dict | None:
    """Normalize whatever the LLM gave us into a dict (or None)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"_raw": s[:500]}
    return {"value": str(raw)[:500]}


def build_corrective_note(tool_name: str, error: str) -> str:
    """A short system note appended for a retry turn (FR-23)."""
    return (
        f"Your previous tool call to '{tool_name}' was invalid: {error}. "
        "Please re-emit a single JSON object that exactly matches the "
        "function's parameters schema. Do not include any prose."
    )


__all__ = [
    "validate_arguments",
    "fallback_keywords",
    "coerce_arguments",
    "build_corrective_note",
]
