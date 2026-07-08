"""Chat endpoints — non-streaming and SSE (FR-22..26, FR-31..33)."""
from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUserId, DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.conversation import Conversation, Message
from app.schemas.conversation import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationPublic,
    ConversationRename,
    MessagePublic,
)
from app.services.orchestrator.agent import run_turn, stream_turn

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


# -------- conversations (FR-31) --------
@router.post(
    "/conversations",
    response_model=ConversationPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreate,
    user_id: CurrentUserId,
    session: DbSession,
) -> Conversation:
    conv = Conversation(user_id=user_id, title=payload.title)
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


@router.get("/conversations", response_model=list[ConversationPublic])
async def list_conversations(
    user_id: CurrentUserId, session: DbSession
) -> list[ConversationPublic]:
    res = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    rows = list(res.scalars())
    out: list[ConversationPublic] = []
    for c in rows:
        out.append(
            ConversationPublic(
                id=c.id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at,
                message_count=len(c.messages or []),
            )
        )
    return out


@router.get(
    "/conversations/{conversation_id}",
    response_model=list[MessagePublic],
)
async def get_conversation(
    conversation_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
) -> list[MessagePublic]:
    res = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return [
        MessagePublic(
            id=m.id,
            seq=m.seq,
            role=m.role,
            content=m.content,
            citations=m.citations or [],
            used_tools=m.used_tools or [],
            created_at=m.created_at,
            connector_id=m.connector_id,
            model=m.model,
        )
        for m in (conv.messages or [])
    ]


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
):
    res = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    await session.delete(conv)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationPublic,
)
async def rename_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationRename,
    user_id: CurrentUserId,
    session: DbSession,
) -> ConversationPublic:
    """Rename a conversation.

    Ownership is enforced by ``Conversation.user_id == user_id`` (the
    same filter ``get``/``delete`` use), on top of the RLS policy set
    upstream by ``DbSession`` — a cross-tenant rename is a 404, not a
    403, so the existence of another user's conversation is not leaked.
    The title is already stripped + capped at 100 chars by the schema.
    """
    res = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    conv.title = payload.title
    await session.commit()
    await session.refresh(conv)
    return ConversationPublic(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=len(conv.messages or []),
    )


# -------- turn (FR-22..26) --------
@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, user_id: CurrentUserId, session: DbSession) -> ChatResponse:
    """Non-streaming chat turn."""
    out = await run_turn(
        session,
        user_id=user_id,
        message=req.message,
        conversation_id=req.conversation_id,
        tool_subset=req.tool_subset,
        connector_id=req.connector_id,
        model=req.model,
    )
    return ChatResponse(
        conversation_id=out["conversation_id"],
        message=MessagePublic(**out["message"]),
    )


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> StreamingResponse:
    """Streaming chat turn (SSE).

    M-22 — CSWSH (cross-site WebSocket hijack) defence: the Origin
    header on every SSE request is checked against the CORS
    allowlist. A cross-origin POST (i.e. an attacker's page
    streaming under the victim's cookies) is refused with a 403.
    The check is duplicated against the CORSMiddleware logic
    (which only fires for credentialed requests) because the
    preflight is non-streaming while the actual stream is
    streaming; the middleware still runs but the explicit check
    here is a belt-and-braces second line of defence.

    M-23 — the response sets ``Vary: Accept, Origin`` so a proxy
    / cache does not serve a stream request to a different
    Accept header (or origin) than the one the response was
    produced for. Without this, an intermediate cache can
    confuse non-stream clients with a stream response, or
    stream responses across origins.
    """
    _check_sse_origin(request)
    # Open a streaming response and run the agent. The session is closed
    # by FastAPI when the request ends; the agent must commit its own
    # work before yielding RUN_FINISHED (it does, in `stream_turn`).
    async def event_iter() -> AsyncIterator[bytes]:
        async for chunk in stream_turn(
            session,
            user_id=user_id,
            message=req.message,
            conversation_id=req.conversation_id,
            tool_subset=req.tool_subset,
            connector_id=req.connector_id,
            model=req.model,
        ):
            yield chunk

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # M-23 — Vary on Accept so a JSON-asking client does not
            # get a stream response (and vice versa) from a shared
            # cache, and on Origin so the Origin check below is
            # not defeated by a cache that served the stream
            # response to a different origin.
            "Vary": "Accept, Origin",
        },
    )


def _check_sse_origin(request: Request) -> None:
    """M-22 — reject SSE connections whose Origin is not on the
    CORS allowlist. The CORSMiddleware already refuses credentialed
    cross-origin requests at the preflight level, but SSE has
    special properties (EventSource does not send a preflight;
    a simple cross-origin GET or POST is enough to initiate a
    stream). The check here is the second line of defence.

    Rules:
    * Missing Origin: allowed. The header is set by browsers
      and some non-browser clients (curl, server-to-server) do
      not send it. We rely on the JWT in the Authorization
      header for the actual authentication — Origin is a
      CSRF-style gate, not an auth gate.
    * Present Origin: must be in the configured CORS allowlist
      (case-insensitive, scheme+host+port match).
    * Same-origin requests (Origin matches the API host) are
      always allowed.
    """
    origin = request.headers.get("origin")
    if not origin:
        return  # non-browser; auth gate handles it
    settings = get_settings()
    allowed = {o.strip().rstrip("/").lower() for o in settings.cors_origins}
    if origin.strip().rstrip("/").lower() in allowed:
        return
    # Same-origin: Origin host == Host header
    host = request.headers.get("host", "")
    if host and origin.lower().endswith(f"//{host.lower()}"):
        return
    log = get_logger(__name__)
    log.warning(
        "sse.origin_rejected",
        origin=origin,
        host=host,
        path=request.url.path,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Origin not allowed for SSE.",
    )
