"""Tests for app.services.text — pure tokenisation utilities."""
from __future__ import annotations

import sys
from pathlib import Path

# Make the backend root importable without spinning up the whole `app`
# package (which would create the SQLAlchemy engine).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_count_tokens_empty():
    from app.services.text import count_tokens

    assert count_tokens("") == 0


def test_count_tokens_simple():
    from app.services.text import count_tokens

    n = count_tokens("hello world")
    assert n > 0
    assert n < 10  # very short


def test_truncate_tokens_shorter():
    from app.services.text import truncate_tokens

    s = "the quick brown fox jumps over the lazy dog" * 20
    out = truncate_tokens(s, 8)
    assert out  # non-empty
    # Round-trip: re-tokenising should yield <= 8 tokens
    from app.services.text import count_tokens

    assert count_tokens(out) <= 8


def test_clean_text_normalizes():
    from app.services.text import clean_text

    s = "Hello    world\n\nfoo\tbar"
    out = clean_text(s)
    assert "  " not in out
    assert out.startswith("Hello")
    assert "world foo bar" in out


def test_split_sentences():
    from app.services.text import split_sentences

    s = "First sentence. Second sentence! Third one?"
    out = split_sentences(s)
    assert out == ["First sentence.", "Second sentence!", "Third one?"]
