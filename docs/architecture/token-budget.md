# Token budget enforcement (NFR-17)

The orchestrator hard-caps the prompt at `ATHENA_TOKEN_BUDGET` (default **3,000 tokens**). Exceeding the cap is impossible because we *truncate* before sending, not reject. This document explains how the budget is split and how the truncation is done.

## Why a hard cap?

The 1.5B model has a small attention window and slow generation. Stuffing 8k tokens of context doesn't help — it hurts. The budget is small enough to be cheap but large enough to fit a system prompt, a couple of tool defs, a short history, and 2-4 retrieved chunks.

## Budget breakdown (defaults)

```
ATHENA_TOKEN_BUDGET              3000
├─ system prompt                 ≤ 350  (ATHENA_SYSTEM_PROMPT_RESERVE)
├─ tool definitions              ≤ 600  (ATHENA_TOOL_DEF_RESERVE)
├─ conversation history          ≤ 800  (ATHENA_HISTORY_RESERVE)
├─ retrieved chunks              ≤ 1000 (ATHENA_CHUNK_RESERVE)
├─ user query                    (rest)
└─ answer reserve (advisory)     250    (ATHENA_ANSWER_RESERVE)
```

The 5 reserves sum to 3000; the "rest" is the actual size of the user query (which is always kept in full). The `answer_reserve` is documented but not used to set `num_predict` on the Ollama call in Phase 1 — we let the LLM generate until it stops.

## Truncation order

`build_prompt()` (`app/services/orchestrator/prompter.py`) fills the budget in this order, truncating each section to fit:

1. **System prompt** — always kept. If it exceeds `ATHENA_SYSTEM_PROMPT_RESERVE` it's truncated (rare, since the default is 350).
2. **Tool definitions** — kept in the order they appear in the snapshot. Stop when the next tool wouldn't fit.
3. **History** — walked newest → oldest; oldest dropped first.
4. **Chunks** — filled into the remaining space, sequential, with `per_chunk = min(CHUNK_RESERVE, remaining // len(chunks))`.
5. **User query** — always kept in full; we just measure it to know what's left.

Each section's truncation sets a flag on the returned `TurnPrompt` (`tool_def_truncated`, `history_truncated`, `chunks_truncated`) so the orchestrator can log when context was dropped.

## Token counting

`count_tokens(text)` uses `tiktoken` with the `cl100k_base` encoding. It's an approximation of the LLM's actual tokenizer (Qwen uses a BPE-based one), but for budgeting purposes the precision is good enough and it's 100x faster than asking the model itself.

For tool definitions we use a faster `count_tokens(str(rendered)) // 4` heuristic — these are mostly JSON Schema which is a repetitive structure, so a rough estimate is fine.

## Why truncation, not rejection?

If the user's question is too long, rejecting the request is hostile. Truncating the *context* (not the question) gives a degraded but still useful answer. The chat UI is a conversation, so the user can also re-ask a more focused question.

## Why "context budget" and not "max_tokens"?

We control the prompt; the model itself decides when to stop. The budget is for the *input*. The LLM's answer can be at most the remaining context window in the model (Qwen 1.5B is 32k, so the answer is unbounded by the budget).

## Configuration

See [configuration.md](../configuration.md) for the env vars. The defaults are tuned for `qwen2.5:1.5b-instruct`. For a 7B model you'd probably bump:

```
ATHENA_TOKEN_BUDGET=6000
ATHENA_HISTORY_RESERVE=1500
ATHENA_CHUNK_RESERVE=2500
```

## What is *not* enforced

- The answer length. We let the LLM emit as many tokens as it wants; the SSE stream ends when it emits a stop token or hits the model's own `num_predict` (we don't set it in Phase 1).
- The keyword extraction budget. The keyword extractor (`fallback_keywords`) returns at most `top_k` (default 6).
- Embedding cost. We embed each chunk once at ingestion; there's no per-turn cost.
