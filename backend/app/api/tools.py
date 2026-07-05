"""Tool registry API (FR-27..28, FR-30)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import AdminUser, CurrentUserId, DbSession
from app.core.logging import get_logger
from app.core.ssrf import SSRFError, assert_safe_url
from app.models.tool import Tool
from app.schemas.tool import ToolPublic, ToolUpsert
from app.tools import registry as tool_registry

log = get_logger(__name__)
router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[ToolPublic])
async def list_tools(
    user_id: CurrentUserId,  # noqa: ARG001  (auth required)
    session: DbSession,
) -> list[ToolPublic]:
    res = await session.execute(select(Tool).order_by(Tool.name, Tool.version.desc()))
    return list(res.scalars())


@router.post(
    "",
    response_model=ToolPublic,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_tool(
    payload: ToolUpsert,
    admin: AdminUser,  # noqa: ARG001  (admin-gated; see dependencies.require_admin)
    session: DbSession,
) -> ToolPublic:
    if payload.handler_type not in {"internal", "http", "mcp"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid handler_type: {payload.handler_type}",
        )
    if not isinstance(payload.parameters, dict) or "type" not in payload.parameters:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="parameters must be a JSON schema with a top-level 'type'",
        )
    # If this tool already exists and is a builtin, refuse to mutate its
    # handler_type / handler_cfg — a builtin must never be silently
    # re-pointed at an attacker-controlled URL.
    existing = await session.execute(select(Tool).where(Tool.name == payload.name))
    row = existing.scalar_one_or_none()
    if row is not None and row.is_builtin and (
        payload.handler_type != row.handler_type
        or (payload.handler_cfg or {}) != (row.handler_cfg or {})
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Refusing to change handler of a builtin tool.",
        )
    # SSRF guard for HTTP / MCP tools: the configured target URL must be safe.
    cfg = payload.handler_cfg or {}
    target_url = cfg.get("url") or cfg.get("server_url")
    if target_url:
        try:
            assert_safe_url(target_url)
        except SSRFError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"handler URL not allowed: {exc}",
            ) from exc
    tool = await tool_registry.upsert_tool(
        session,
        name=payload.name,
        description=payload.description,
        parameters=payload.parameters,
        handler_type=payload.handler_type,
        handler_cfg=payload.handler_cfg or {},
        enabled=payload.enabled,
    )
    return tool


@router.patch("/{tool_id}", response_model=ToolPublic)
async def set_tool_enabled(
    tool_id: uuid.UUID,
    enabled: bool,
    admin: AdminUser,  # noqa: ARG001
    session: DbSession,
) -> ToolPublic:
    tool = await tool_registry.set_enabled(session, tool_id, enabled)
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )
    return tool


@router.get("/snapshot")
async def tool_snapshot(
    user_id: CurrentUserId,  # noqa: ARG001
    session: DbSession,
) -> list[dict[str, Any]]:
    """Cached list of tool schemas (Ollama-shaped) — used by the orchestrator."""
    return await tool_registry.snapshot(session, use_cache=True)


@router.post("/mcp/attach", response_model=list[ToolPublic])
async def attach_mcp_server(
    server_url: str,
    admin: AdminUser,  # noqa: ARG001
    session: DbSession,
) -> list[ToolPublic]:
    """FR-29: discover tools from an MCP server and upsert them locally.

    Admin-gated + SSRF-validated: the server URL is attacker-supplied, so
    it must not point at internal/cloud-metadata addresses.
    """
    from app.tools.mcp import MCPError, discover_tools

    try:
        assert_safe_url(server_url)
    except SSRFError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"server_url not allowed: {exc}",
        ) from exc

    try:
        rows = await discover_tools(session, server_url=server_url)
    except MCPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return list(rows)


@router.post("/{tool_id}/invoke")
async def invoke_tool(
    tool_id: uuid.UUID,
    arguments: dict,
    admin: AdminUser,
    session: DbSession,
) -> dict[str, Any]:
    """Ad-hoc tool invocation (admin / debug). Internal HTTP/MCP tools
    run as the calling admin; the LLM is not involved.

    Admin-gated to prevent any authenticated user from invoking tools
    and — critically — from clobbering the per-request RLS GUC by
    passing a foreign `user_id` in the arguments (see the
    `rls-guc-set-from-caller-user-id` finding).
    """
    from sqlalchemy import select

    res = await session.execute(select(Tool).where(Tool.id == tool_id))
    tool = res.scalar_one_or_none()
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )
    if not tool.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Tool is disabled"
        )
    # Inject the DB session for internal tools that need it (search_documents).
    if tool.handler_type == "internal":
        impl: str = (tool.handler_cfg or {}).get("impl", "")
        if impl.endswith("search_documents:run"):
            arguments = dict(arguments or {})
            # Force-overwrite (NOT setdefault): a caller-supplied user_id
            # must never survive — it would re-bind the RLS GUC to a
            # different tenant and leak their documents.
            if arguments.get("user_id") not in (None, str(admin.id)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="user_id may not be supplied in arguments.",
                )
            arguments["user_id"] = str(admin.id)
            arguments["session"] = session
    executed, result, status_label, latency_ms = await tool_registry.execute(
        session, tool_name=tool.name, arguments=arguments or {}
    )
    return {
        "tool_id": str(executed.id) if executed else str(tool.id),
        "tool_name": tool.name,
        "status": status_label,
        "latency_ms": latency_ms,
        "result": result,
    }
