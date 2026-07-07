"""The per-turn agent loop.

Public entry points:
  - run_turn():  non-streaming structured response (ChatResponse)
  - stream_turn(): yields SSE bytes (RUN_STARTED → text deltas → tool
                  lifecycle → RUN_FINISHED / RUN_ERROR)

EMC integration: both entry points accept `connector_id` and `model`
kwargs. When set, the LLMClient routes the call to the named external
connector; when None, the router falls through to user default → system
default → built-in Ollama (preserving Phase 1 behaviour). The resolved
`(connector_id, model)` is persisted on the assistant Message and a
`connector_usage` row is written per turn.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any, AsyncIterator, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation, Message
from app.services.llm import streamer as sse
from app.services.orchestrator.llm_client import LLMClient
from app.services.orchestrator.prompter import (
    SYSTEM_PROMPT,
    build_prompt,
    extract_citations,
)
from app.services.orchestrator.tool_call import (
    build_corrective_note,
    coerce_arguments,
    fallback_keywords,
    validate_arguments,
)
from app.services.providers.usage import (
    STATUS_AUTH_FAILED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_STREAM_INTERRUPTED,
    STATUS_TIMEOUT,
    record as record_usage,
)
from app.services.providers import base as pal
from app.services.retrieval import search as retrieval_search
from app.tools import registry as tool_registry

log = get_logger(__name__)

# Single router instance per process. Adapters it constructs are
# cached, so the second turn with the same connector reuses the
# existing httpx pool rather than allocating fresh sockets.
_router = None  # type: ignore[var-annotated]


def _get_router():
    global _router
    if _router is None:
        from app.services.providers.router import ModelRouter

        _router = ModelRouter()
    return _router


def _make_llm(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    connector_id: Optional[uuid.UUID],
    model: Optional[str],
) -> LLMClient:
    return LLMClient(
        session,
        user_id=user_id,
        connector_id=connector_id,
        model=model,
        router=_get_router(),
    )


# ---------------------------------------------------------------------
# Conversation helpers
# ---------------------------------------------------------------------
async def _ensure_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title_seed: str | None,
) -> Conversation:
    if conversation_id is not None:
        from sqlalchemy import select

        res = await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        conv = res.scalar_one_or_none()
        if conv is None:
            raise ValueError("conversation_not_found")
        return conv
    conv = Conversation(
        user_id=user_id,
        title=(title_seed or "New conversation")[:120],
    )
    session.add(conv)
    await session.flush()
    return conv


async def _load_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limit: int = 16,
) -> list[dict]:
    from sqlalchemy import select

    res = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.user_id == user_id,
        )
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    msgs = list(res.scalars())[::-1]  # oldest → newest
    out: list[dict] = []
    for m in msgs:
        if m.role in {"user", "assistant"}:
            out.append({"role": m.role, "content": m.content})
    return out


def _persist_message(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    citations: list | None = None,
    used_tools: list | None = None,
    connector_id: uuid.UUID | None = None,
    model: str | None = None,
) -> Message:
    msg = Message(
        user_id=user_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        citations=citations or [],
        used_tools=used_tools or [],
        # Persist the connector + model that produced the message.
        # NULL for `user` messages and for messages that did not
        # invoke a model (e.g. pre-EMC history rows).
        connector_id=connector_id,
        model=model,
    )
    session.add(msg)
    return msg


def _record_usage_row(
    session: AsyncSession,
    *,
    llm: LLMClient,
    user_id: uuid.UUID,
    status: str = STATUS_OK,
    error_class: Optional[str] = None,
) -> None:
    """Append one `connector_usage` row for this turn.

    Skipped when the turn went to the built-in Ollama fallback
    (`resolved_connector_id is None`): the row is meant to track
    user-attributed external-connector traffic. Falling back is
    the absence of an EMC choice, not a separate connector.
    """
    if llm.resolved_connector_id is None:
        return
    try:
        record_usage(
            session,
            connector_id=llm.resolved_connector_id,
            user_id=user_id,
            model=llm.resolved_model or "",
            prompt_tokens=int((llm.last_usage or {}).get("prompt_tokens") or 0),
            completion_tokens=int((llm.last_usage or {}).get("completion_tokens") or 0),
            latency_ms=int(llm.last_latency_ms or 0),
            status=status,
            error_class=error_class,
            cost_estimate=Decimal("0"),
        )
    except Exception as exc:  # noqa: BLE001
        # A usage-row failure must not fail the chat turn. Log and
        # move on; the dashboard's aggregates will be slightly
        # under-counted for this turn, which is acceptable.
        log.warning("agent.usage_record_failed", error=str(exc))


# ---------------------------------------------------------------------
# Tool execution (validate → retry → fallback)
# ---------------------------------------------------------------------
async def _execute_tool_call(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tool_name: str,
    raw_args: Any,
    user_message: str,
) -> tuple[dict, str, dict | None]:
    """Returns: (result_dict, status, tool_row_dict_for_audit)."""
    args = coerce_arguments(raw_args)
    if args is None and raw_args is not None:
        # The LLM emitted arguments that are not a JSON object and cannot
        # be coerced into one (e.g. a bare string or malformed JSON). Treat
        # this as invalid so the corrective-retry / deterministic-fallback
        # path engages, rather than silently calling the tool with {}.
        return (
            {"error": "invalid_args: arguments must be a JSON object"},
            "error",
            None,
        )

    tool_row = await tool_registry.get_by_name(session, tool_name)
    if tool_row is None:
        return {"error": f"tool_not_found: {tool_name}"}, "error", None

    schema = tool_row.parameters or {"type": "object"}
    ok, err = validate_arguments(args, schema)
    if not ok:
        # FR-23: retry once — the orchestrator does this OUTSIDE, then
        # asks us again with a `correction=True` flag. Here we just
        # report the error.
        return {"error": err or "invalid_args"}, "error", None

    # Built-in tools need the session + user_id injected automatically.
    if tool_row.handler_type == "internal":
        impl: str = (tool_row.handler_cfg or {}).get("impl", "")
        if impl.endswith("search_documents:run"):
            # Inject the user_id and session so the LLM schema stays clean.
            merged = dict(args or {})
            # Force-overwrite (NOT setdefault): the LLM/tool caller must
            # never be able to select a different tenant's user_id — that
            # would re-bind the RLS GUC and leak another user's chunks.
            if merged.get("user_id") not in (None, str(user_id)):
                return (
                    {"error": "user_id may not be supplied in arguments"},
                    "error",
                    None,
                )
            merged["user_id"] = str(user_id)
            merged["session"] = session
            tool, result, status, _latency = await tool_registry.execute(
                session, tool_name=tool_name, arguments=merged
            )
            return result, status, {"tool_id": str(tool.id)} if tool else None

    tool, result, status, _latency = await tool_registry.execute(
        session, tool_name=tool_name, arguments=args or {}
    )
    return result, status, {"tool_id": str(tool.id)} if tool else None


# ---------------------------------------------------------------------
# Non-streaming turn
# ---------------------------------------------------------------------
async def run_turn(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None = None,
    tool_subset: list[str] | None = None,
    connector_id: uuid.UUID | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run a single chat turn end-to-end (no streaming)."""
    conv = await _ensure_conversation(
        session, user_id, conversation_id, title_seed=message
    )
    history = await _load_history(session, user_id, conv.id)

    # 1. Persist user message
    _persist_message(
        session,
        user_id=user_id,
        conversation_id=conv.id,
        role="user",
        content=message,
    )

    # 2. Snapshot tool schemas
    tool_schemas = await tool_registry.select_subset(session, requested=tool_subset)

    # 3. Decide whether to retrieve up-front (cheap heuristic — also
    # lets the LLM skip the tool call when not needed).
    initial_chunks: list[dict] = []
    initial_keywords = fallback_keywords(message, top_k=6)
    if initial_keywords:
        initial_chunks = await retrieval_search.retrieve(
            session=session,
            user_id=user_id,
            keywords=initial_keywords,
            top_k=4,
        )

    # 4. Build the budgeted prompt
    built = build_prompt(
        query=message,
        chunks=initial_chunks,
        history=history,
        tools=tool_schemas,
    )

    # 5. LLM first pass. One LLMClient instance per turn so we
    # share the resolved connector + accumulated usage across
    # the (possibly multi-pass) agent loop.
    llm = _make_llm(
        session,
        user_id=user_id,
        connector_id=connector_id,
        model=model,
    )
    error_class: Optional[str] = None
    turn_status: str = STATUS_OK
    try:
        resp = await llm.complete(messages=built.messages, tools=built.tools)
    except pal.ProviderError as exc:
        # Persist a clear assistant error message so the user sees
        # what happened, write a usage row, and return.
        error_class = exc.category
        turn_status = _status_from_error_class(exc.category)
        assistant = _persist_message(
            session,
            user_id=user_id,
            conversation_id=conv.id,
            role="assistant",
            content=f"[llm error: {exc}]",
            connector_id=llm.resolved_connector_id,
            model=llm.resolved_model or None,
        )
        _record_usage_row(
            session,
            llm=llm,
            user_id=user_id,
            status=turn_status,
            error_class=error_class,
        )
        await session.commit()
        await session.refresh(assistant)
        return {
            "conversation_id": str(conv.id),
            "message": {
                "id": str(assistant.id),
                "seq": assistant.seq,
                "role": "assistant",
                "content": assistant.content,
                "citations": [],
                "used_tools": [],
                "created_at": assistant.created_at,
                "connector_id": str(assistant.connector_id) if assistant.connector_id else None,
                "model": assistant.model,
            },
        }

    used_tools_log: list[dict] = []
    final_chunks = list(initial_chunks)
    fallback_used = False

    # 6. Tool round-trip — execute the LLM's tool call, if any.
    if resp.tool_call and resp.tool_call.get("name"):
        tc_name = resp.tool_call["name"]
        raw_args = resp.tool_call.get("arguments") or {}
        result, status, audit = await _execute_tool_call(
            session, user_id=user_id, tool_name=tc_name, raw_args=raw_args, user_message=message
        )

        # FR-23: if invalid, retry once with a corrective system note;
        # if still invalid, fall back to deterministic keyword extraction
        # so the turn never crashes (NFR-10).
        if status == "error" and "tool_not_found" not in (result.get("error") or ""):
            # Emit the corrective note into the message stream and re-ask
            # the LLM once. If it still doesn't produce a valid call, we
            # proceed with the deterministic fallback below.
            retry_messages = built.messages + [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": tc_name,
                                "arguments": json_dumps(raw_args),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "name": tc_name,
                    "content": json_dumps(result),
                },
                {
                    "role": "system",
                    "content": build_corrective_note(
                        tc_name, str(result.get("error", "invalid"))
                    ),
                },
            ]
            retry_resp = await llm.complete(
                messages=retry_messages, tools=built.tools
            )
            if retry_resp.tool_call and retry_resp.tool_call.get("name"):
                # Try the LLM's corrected call once. Use the name the LLM
                # actually emitted on retry — it may have switched tools.
                retry_name = retry_resp.tool_call.get("name")
                raw_args = retry_resp.tool_call.get("arguments") or {}
                result, status, audit = await _execute_tool_call(
                    session,
                    user_id=user_id,
                    tool_name=retry_name,
                    raw_args=raw_args,
                    user_message=message,
                )
            # Deterministic fallback (NFR-10) — runs unconditionally if we
            # still have no usable tool result.
            if status != "ok":
                fallback = fallback_keywords(message, top_k=6)
                fallback_used = True
                if fallback:
                    fb_result, fb_status, fb_audit = await _execute_tool_call(
                        session,
                        user_id=user_id,
                        tool_name=tc_name,
                        raw_args={"keywords": fallback},
                        user_message=message,
                    )
                    result = fb_result
                    status = "fallback" if fb_status == "ok" else fb_status
                    audit = fb_audit
                    final_chunks = _chunks_from_tool_result(fb_result) or final_chunks

        if status in {"ok", "fallback"}:
            used_tools_log.append(
                {
                    "name": tc_name,
                    "status": status,
                    **(audit or {}),
                }
            )
            # If we got fresh chunks from the tool, use them; otherwise keep.
            fresh = _chunks_from_tool_result(result)
            if fresh:
                final_chunks = fresh
            # Re-build the prompt with refreshed chunks, then re-ask LLM.
            built2 = build_prompt(
                query=message,
                chunks=final_chunks,
                history=history,
                tools=tool_schemas,
            )
            # Add the tool result to the message list so the LLM sees it.
            built2.messages = built2.messages + [
                {
                    "role": "tool",
                    "name": tc_name,
                    "content": json_dumps(result),
                }
            ]
            resp2 = await llm.complete(messages=built2.messages, tools=built2.tools)
            if resp2.text:
                resp = resp2
        else:
            used_tools_log.append(
                {"name": tc_name, "status": status, "error": result.get("error")}
            )

    citations = extract_citations(resp.text, final_chunks)

    # 7. Persist assistant message
    assistant = _persist_message(
        session,
        user_id=user_id,
        conversation_id=conv.id,
        role="assistant",
        content=resp.text or "",
        citations=citations,
        used_tools=used_tools_log,
        connector_id=llm.resolved_connector_id,
        model=llm.resolved_model or None,
    )
    # 8. One usage row per turn. `llm.last_usage` was filled by
    # `complete()`; for turns that re-ask after a tool result, the
    # final `complete()` call wins (which is what the dashboard
    # wants — total tokens / latency for the user-visible turn).
    _record_usage_row(session, llm=llm, user_id=user_id)
    await session.commit()
    await session.refresh(assistant)
    await session.refresh(conv)

    return {
        "conversation_id": str(conv.id),
        "message": {
            "id": str(assistant.id),
            "seq": assistant.seq,
            "role": "assistant",
            "content": assistant.content,
            "citations": citations,
            "used_tools": used_tools_log,
            "created_at": assistant.created_at,
            "connector_id": str(assistant.connector_id) if assistant.connector_id else None,
            "model": assistant.model,
        },
    }


# ---------------------------------------------------------------------
# Streaming turn (SSE)
# ---------------------------------------------------------------------
async def stream_turn(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None = None,
    tool_subset: list[str] | None = None,
    connector_id: uuid.UUID | None = None,
    model: str | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE bytes for one turn. Ends with RUN_FINISHED or RUN_ERROR."""
    run_id = uuid.uuid4()
    llm = _make_llm(
        session,
        user_id=user_id,
        connector_id=connector_id,
        model=model,
    )
    error_class: Optional[str] = None
    turn_status: str = STATUS_OK
    interrupted = False
    try:
        conv = await _ensure_conversation(
            session, user_id, conversation_id, title_seed=message
        )
        history = await _load_history(session, user_id, conv.id)

        _persist_message(
            session,
            user_id=user_id,
            conversation_id=conv.id,
            role="user",
            content=message,
        )
        await session.flush()

        yield sse.run_started(run_id, conv.id)

        tool_schemas = await tool_registry.select_subset(session, requested=tool_subset)

        initial_chunks: list[dict] = []
        kws = fallback_keywords(message, top_k=6)
        if kws:
            initial_chunks = await retrieval_search.retrieve(
                session=session, user_id=user_id, keywords=kws, top_k=4
            )

        built = build_prompt(
            query=message,
            chunks=initial_chunks,
            history=history,
            tools=tool_schemas,
        )

        # First pass: prefer non-streaming so we can detect a tool call
        # cheaply (streaming + tool calls requires incremental JSON parse,
        # which is overkill for Phase 1).
        try:
            first = await llm.complete(messages=built.messages, tools=built.tools)
        except pal.ProviderError as exc:
            error_class = exc.category
            turn_status = _status_from_error_class(exc.category)
            yield sse.run_error(run_id, error=str(exc))
            return

        used_tools_log: list[dict] = []
        final_chunks = list(initial_chunks)
        text_to_stream = first.text or ""
        streamed_content = False  # already emitted TEXT_MESSAGE_CONTENT?
        msg_id = uuid.uuid4()

        if first.tool_call and first.tool_call.get("name"):
            tc_name = first.tool_call["name"]
            raw_args = first.tool_call.get("arguments") or {}
            tc_id = uuid.uuid4()
            yield sse.tool_call_start(tc_id, tc_name)
            yield sse.tool_call_args(tc_id, raw_args or {})

            result, status, audit = await _execute_tool_call(
                session,
                user_id=user_id,
                tool_name=tc_name,
                raw_args=raw_args,
                user_message=message,
            )
            yield sse.tool_call_end(
                tc_id, result=result, status=status, latency_ms=0
            )
            used_tools_log.append({"name": tc_name, "status": status, **(audit or {})})

            if status in {"ok", "fallback"}:
                fresh = _chunks_from_tool_result(result)
                if fresh:
                    final_chunks = fresh
                # Re-build prompt with the tool result appended.
                built.messages = built.messages + [
                    {
                        "role": "tool",
                        "name": tc_name,
                        "content": json_dumps(result),
                    }
                ]
                # Stream the final answer under a single message id.
                yield sse.text_message_start(msg_id)
                chunks_seen: list[str] = []
                async for ev in llm.stream(messages=built.messages, tools=built.tools):
                    delta = ev.get("delta") or ""
                    if delta:
                        chunks_seen.append(delta)
                        yield sse.text_message_content(msg_id, delta)
                    if ev.get("error"):
                        # ProviderError-class failure surfaced during
                        # the stream. Note it for the usage row but
                        # keep what we already emitted (the user
                        # sees a partial answer + the SSE RUN_ERROR
                        # event the adapter / agent emits).
                        error_class = error_class or "stream_interrupted"
                        interrupted = True
                text_to_stream = "".join(chunks_seen)
                streamed_content = True
            else:
                # Tool failed — surface a graceful answer from the first
                # pass plus an error note. This is streamed below like the
                # no-tool path (single START / CONTENT / END).
                text_to_stream = (first.text or "") + (
                    f"\n\n(Tool error: {result.get('error')})"
                ).strip()

        # Emit exactly one TEXT_MESSAGE_START for the assistant text. If
        # we already streamed (tool-success branch), we've already emitted
        # START + CONTENT and only need END.
        if not streamed_content:
            yield sse.text_message_start(msg_id)
            for piece in _chunk_for_stream(text_to_stream):
                yield sse.text_message_content(msg_id, piece)

        citations = extract_citations(text_to_stream, final_chunks)
        yield sse.text_message_end(
            msg_id, citations=citations, used_tools=used_tools_log
        )

        assistant = _persist_message(
            session,
            user_id=user_id,
            conversation_id=conv.id,
            role="assistant",
            content=text_to_stream,
            citations=citations,
            used_tools=used_tools_log,
            connector_id=llm.resolved_connector_id,
            model=llm.resolved_model or None,
        )
        # If any streamed chunk carried `error`, mark the turn as
        # stream-interrupted for the usage dashboard. (Successful
        # turns keep the default STATUS_OK.)
        if interrupted:
            turn_status = STATUS_STREAM_INTERRUPTED
        _record_usage_row(
            session,
            llm=llm,
            user_id=user_id,
            status=turn_status,
            error_class=error_class,
        )
        await session.commit()
        await session.refresh(assistant)

        yield sse.run_finished(run_id, finish_reason="stop")
    except Exception as exc:  # noqa: BLE001
        log.error("agent.stream.error", error=str(exc))
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        # The unhandled-exception path. We still want a usage row so
        # the dashboard reflects the failure category; if the LLM
        # never resolved we have no connector_id to attribute to, so
        # `record_usage_row` is a no-op.
        if not error_class:
            error_class = "unknown"
        if turn_status == STATUS_OK:
            turn_status = STATUS_ERROR
        try:
            _record_usage_row(
                session,
                llm=llm,
                user_id=user_id,
                status=turn_status,
                error_class=error_class,
            )
            await session.commit()
        except Exception:  # noqa: BLE001
            pass
        yield sse.run_error(run_id, error=str(exc))


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _chunks_from_tool_result(result: Any) -> list[dict]:
    """Pull chunks out of a tool result dict (FR-20 shape)."""
    if not isinstance(result, dict):
        return []
    items = result.get("results") or []
    out: list[dict] = []
    for r in items:
        out.append(
            {
                "chunk_id": r.get("chunk_id"),
                "document_id": r.get("document_id"),
                "document_name": r.get("document_name"),
                "page_number": r.get("page_number"),
                "content": r.get("snippet", ""),
                "keywords": r.get("keywords", []),
                "score": r.get("score", 0.0),
            }
        )
    return out


def _chunk_for_stream(text: str, size: int = 32) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)


# Map PAL error category → usage `status` value. The taxonomy is
# stable; the dashboard's filter dropdown is keyed on these strings.
_PROVIDER_CATEGORY_TO_USAGE_STATUS = {
    "ok": STATUS_OK,
    "auth_failed": STATUS_AUTH_FAILED,
    "rate_limited": "rate_limited",
    "timeout": STATUS_TIMEOUT,
    "network": STATUS_ERROR,
    "server_error": STATUS_ERROR,
    "bad_request": STATUS_ERROR,
    "not_found": STATUS_ERROR,
    "invalid_response": STATUS_ERROR,
    "unsupported": STATUS_ERROR,
    "unknown": STATUS_ERROR,
}


def _status_from_error_class(category: str) -> str:
    return _PROVIDER_CATEGORY_TO_USAGE_STATUS.get(category, STATUS_ERROR)


__all__ = ["run_turn", "stream_turn"]
