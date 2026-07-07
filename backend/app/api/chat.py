"""Chat endpoints — non-streaming and SSE (FR-22..26, FR-31..33)."""
from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUserId, DbSession
from app.core.logging import get_logger
from app.models.conversation import Conversation, Message
from app.schemas.conversation import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationPublic,
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
    req: ChatRequest, user_id: CurrentUserId, session: DbSession
) -> StreamingResponse:
    """Streaming chat turn (SSE)."""
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
        },
    )
