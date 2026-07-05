"""Tests for the orchestrator's tool-call validator + fallback (FR-23, NFR-10)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_validate_arguments_ok():
    from app.services.orchestrator.tool_call import validate_arguments

    schema = {
        "type": "object",
        "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 16},
        },
        "required": ["keywords"],
    }
    ok, err = validate_arguments({"keywords": ["a", "b"], "top_k": 4}, schema)
    assert ok and err is None


def test_validate_arguments_missing_required():
    from app.services.orchestrator.tool_call import validate_arguments

    schema = {
        "type": "object",
        "properties": {"keywords": {"type": "array"}},
        "required": ["keywords"],
    }
    ok, err = validate_arguments({}, schema)
    assert not ok
    assert err


def test_validate_arguments_wrong_type():
    from app.services.orchestrator.tool_call import validate_arguments

    schema = {
        "type": "object",
        "properties": {"top_k": {"type": "integer"}},
    }
    ok, err = validate_arguments({"top_k": "not-a-number"}, schema)
    assert not ok


def test_coerce_arguments_dict():
    from app.services.orchestrator.tool_call import coerce_arguments

    assert coerce_arguments({"k": "v"}) == {"k": "v"}


def test_coerce_arguments_json_string():
    from app.services.orchestrator.tool_call import coerce_arguments

    out = coerce_arguments('{"keywords": ["a"]}')
    assert out == {"keywords": ["a"]}


def test_coerce_arguments_garbage_string():
    from app.services.orchestrator.tool_call import coerce_arguments

    out = coerce_arguments("not json")
    assert out and "_raw" in out


def test_fallback_keywords_deterministic():
    from app.services.orchestrator.tool_call import fallback_keywords

    msg = "Tell me about quarterly revenue and the marketing strategy"
    kws = fallback_keywords(msg, top_k=5)
    # All alpha, all unique, no stopwords
    assert len(kws) <= 5
    assert all(w.isalpha() for w in kws)
    assert len(set(kws)) == len(kws)
    for banned in ("about", "the", "and", "me"):
        assert banned not in kws


def test_fallback_keywords_empty():
    from app.services.orchestrator.tool_call import fallback_keywords

    assert fallback_keywords("") == []
    assert fallback_keywords("the a an") == []


def test_corrective_note_mentions_tool():
    from app.services.orchestrator.tool_call import build_corrective_note

    note = build_corrective_note("search_documents", "missing 'keywords'")
    assert "search_documents" in note
    assert "missing 'keywords'" in note
