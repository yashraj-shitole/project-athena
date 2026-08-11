"""Per-chunk keyword extraction.

Strategy:
  1. Tokenize + lowercase + drop stopwords + keep alpha tokens.
  2. Score by n-gram frequency (unigrams + bigrams).
  3. Use cosine similarity between candidate phrase embedding and the
     chunk embedding to keep only on-topic keywords (cheap filter).
  4. MMR (Maximal Marginal Relevance) over candidate embeddings to
     diversify the final keyword set.

Two entry points:
  * ``extract_keywords(text, chunk_embedding)`` — single-chunk
    convenience that calls the encoder per call. Kept for callers
    that don't care about batching the encoder.
  * ``candidate_phrases`` + ``select_keywords`` — the split used by
    the pipeline. ``candidate_phrases`` does the cheap
    tokenisation, the pipeline batches the per-chunk phrase lists
    into one shared ``encode()`` call, then splits the resulting
    matrix back and feeds each slice to ``select_keywords``. For a
    200-chunk doc this drops the keyword-encoder cost from
    ~200 forward passes to ~one.
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

import numpy as np

from app.core.config import settings
from app.services.embedding import encode
from app.services.text import clean_text

_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have he her his i in is it its
    of on or our she that the their them they this to was we were will with
    you your our ours theirs these those what when where which who why how
    can could may might shall should would do does did doing done about
    above after again against all am any because been before being below
    between both but by could didn't doesn't don't during each few for from
    further had hadn't hasn't haven't he he'll he's herself himself herself
    him himself his how how's i i'd i'll i'm i've if in into is isn't it
    it's its itself let's me more most mustn't my myself need no nor not of
    off on once only or other ought our ours ourselves out over own same
    she she'd she'll she's should shouldn't so some such than that that's
    the their theirs them themselves then there there's these they they'd
    they'll they're they've this those through to too under until up very
    was wasn't we we'd we'll we're we've were weren't what what's when
    when's where where's which while who who's whom why why's with won't
    would wouldn't you you'd you'll you're you've your yours yourself
    yourselves also etc eg ie using use used uses using
    """.split()
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")


def _candidates(text: str) -> List[Tuple[str, int, int]]:
    """Return [(phrase, start_offset, end_offset)] candidates.

    Unigrams + bigrams of content words.
    """
    cleaned = clean_text(text).lower()
    tokens: list[tuple[str, int, int]] = []
    for m in _WORD_RE.finditer(cleaned):
        tok = m.group(0)
        if tok in _STOPWORDS or tok.isdigit():
            continue
        tokens.append((tok, m.start(), m.end()))
    out: list[tuple[str, int, int]] = []
    for i, (t, s, e) in enumerate(tokens):
        out.append((t, s, e))
        if i + 1 < len(tokens):
            nxt, s2, e2 = tokens[i + 1]
            # Bigram only when the two tokens are adjacent (small gap).
            # The previous guard used `e2 - e` (end-to-end distance), which
            # is always >= 3 because every token is >= 3 chars, so no bigram
            # was ever emitted. Use the gap `s2 - e` instead.
            if s2 - e <= 2 and nxt not in _STOPWORDS:
                out.append((f"{t} {nxt}", s, e2))
    return out


def _mmr(
    query: np.ndarray,
    candidates: np.ndarray,
    top_k: int,
    lam: float = 0.7,
) -> List[int]:
    """Maximal Marginal Relevance selection over normalized vectors."""
    if candidates.shape[0] == 0 or top_k <= 0:
        return []
    sim_to_query = candidates @ query
    selected: list[int] = []
    mask = np.ones(candidates.shape[0], dtype=bool)
    for _ in range(min(top_k, candidates.shape[0])):
        if not selected:
            idx = int(np.argmax(sim_to_query))
        else:
            sel_vecs = candidates[selected]
            redundancy = sel_vecs @ candidates[mask].T
            max_red = redundancy.max(axis=0) if redundancy.size else np.zeros(mask.sum())
            scores = lam * sim_to_query[mask] - (1 - lam) * max_red
            local = int(np.argmax(scores))
            idx = int(np.where(mask)[0][local])
        selected.append(idx)
        mask[idx] = False
    return selected


def extract_keywords(
    text: str,
    chunk_embedding: Sequence[float] | None = None,
    top_k: int = 8,
) -> List[str]:
    """Return up to `top_k` diverse, on-topic keywords for the chunk.

    Single-chunk entry point. Calls the encoder per chunk. The
    pipeline uses ``candidate_phrases`` + ``select_keywords``
    instead, so a batch of chunks shares one ``encode()`` call
    instead of paying a forward pass per chunk.
    """
    phrases, cand_vecs = prepare_keyword_inputs(text, chunk_embedding=chunk_embedding)
    return select_keywords(
        phrases,
        cand_vecs=cand_vecs,
        chunk_embedding=chunk_embedding,
        top_k=top_k,
    )


def prepare_keyword_inputs(
    text: str,
    chunk_embedding: Sequence[float] | None,
) -> tuple[List[str], np.ndarray | None]:
    """Per-chunk: tokenize + (optionally) embed candidates.

    The expensive part of keyword extraction is the sentence-
    transformer forward pass on the candidate phrases. The batched
    pipeline path bypasses this and instead:
      1. Calls ``candidate_phrases(text)`` on each chunk to collect
         the per-chunk phrase lists.
      2. Concatenates every chunk's phrase list and calls
         ``encode(phrases_all, normalize=True)`` once on the union.
      3. Splits the resulting matrix back into per-chunk slices and
         runs ``select_keywords`` on each.

    Returns ``(phrases, cand_vecs)`` where ``cand_vecs`` is a numpy
    matrix aligned row-for-row with ``phrases``, or ``None`` when no
    chunk embedding was supplied (the on-topic filter is skipped in
    that case). Returns ``([], None)`` for empty / candidate-less
    text.
    """
    if not text:
        return [], None
    cands = _candidates(text)
    if not cands:
        return [], None
    freq: dict[str, int] = {}
    for phrase, _, _ in cands:
        freq[phrase] = freq.get(phrase, 0) + 1
    phrases = list(freq.keys())
    if chunk_embedding is not None:
        cand_vecs = encode(phrases, normalize=True)
        return phrases, cand_vecs
    return phrases, None


def candidate_phrases(text: str) -> List[str]:
    """Return the unigram+bigram candidate phrases for `text`
    without calling the encoder. The batched pipeline path uses
    this to gather per-chunk phrase lists before issuing one
    shared ``encode()`` call."""
    if not text:
        return []
    cands = _candidates(text)
    if not cands:
        return []
    freq: dict[str, int] = {}
    for phrase, _, _ in cands:
        freq[phrase] = freq.get(phrase, 0) + 1
    return list(freq.keys())


def select_keywords(
    phrases: List[str],
    *,
    cand_vecs: np.ndarray | None,
    chunk_embedding: Sequence[float] | None,
    top_k: int = 8,
) -> List[str]:
    """Pure (no encoder): from a list of candidate phrases and their
    pre-computed embeddings, return up to `top_k` diverse, on-topic
    keywords. The caller must have already built `cand_vecs` —
    typically by running one batched ``encode()`` across every
    chunk's candidates and slicing the rows for this chunk."""
    if not phrases:
        return []
    freq: dict[str, int] = {p: 1 for p in phrases}
    if cand_vecs is not None and chunk_embedding is not None:
        chunk_vec = np.asarray(chunk_embedding, dtype=np.float32)
        norm = float(np.linalg.norm(chunk_vec))
        if norm > 0:
            chunk_vec = chunk_vec / norm
            sims = cand_vecs @ chunk_vec
            keep_mask = sims >= settings.KEYWORD_MIN_SIM
            phrases = [p for p, k in zip(phrases, keep_mask) if k]
            if not phrases:
                return []
            cand_vecs = cand_vecs[keep_mask]
    if len(phrases) == 1:
        return phrases
    if cand_vecs is None or chunk_embedding is None:
        return sorted(phrases, key=lambda p: -freq[p])[:top_k]
    sel = _mmr(chunk_vec, cand_vecs, top_k=top_k)
    return [phrases[i] for i in sel]
