"""Minimal MCP (Model Context Protocol) connector.

This is a Phase-1-friendly implementation that:
  - opens an HTTP POST session against an MCP server's `/messages` endpoint
  - issues a `tools/list` JSON-RPC call
  - persists each discovered tool as a local `Tool` row with
    `handler_type='mcp'` and `handler_cfg={'server_url': ..., 'remote_name': ...}`
  - exposes a Python-side dispatcher that forwards an invocation back to
    the server via `tools/call` (so the orchestrator can use the same
    code path as any other internal tool).

The wire format follows MCP 2024-11-05:
  - Request:  POST {jsonrpc: "2.0", id, method, params}
  - Response: {jsonrpc: "2.0", id, result: {...} | error: {...}}
  - Tools/list result: {tools: [{name, description, inputSchema}]}

If `httpx` cannot reach the configured server the call surfaces a
structured error so the orchestrator can fall back (NFR-10).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

import httpx

from app.core.logging import get_logger
from app.models.tool import Tool

log = get_logger(__name__)


class MCPError(RuntimeError):
    """Raised for any non-2xx, malformed, or JSON-RPC-error response."""


def _normalize_remote_tool(server_url: str, remote: dict) -> dict:
    """Convert a remote MCP tool descriptor into the local Tool shape."""
    name = remote.get("name") or "remote_tool"
    return {
        "name": f"mcp:{server_url}:{name}",
        "description": remote.get("description") or f"MCP tool: {name}",
        "parameters": remote.get("inputSchema") or {"type": "object"},
        "handler_type": "mcp",
        "handler_cfg": {
            "server_url": server_url,
            "remote_name": name,
        },
    }


async def discover_tools(
    session,  # SQLAlchemy AsyncSession
    *,
    server_url: str,
    request_timeout: float = 10.0,
) -> list[dict]:
    """Connect to an MCP server, list its tools, and upsert them locally.

    Returns the list of `Tool` rows that were created or updated.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/list",
        "params": {},
    }
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            r = await client.post(server_url.rstrip("/"), json=payload)
    except httpx.HTTPError as exc:
        raise MCPError(f"mcp unreachable: {exc}") from exc

    if r.status_code != 200:
        raise MCPError(f"mcp {r.status_code}: {r.text[:200]}")

    try:
        body = r.json()
    except json.JSONDecodeError as exc:
        raise MCPError(f"mcp returned non-JSON: {exc}") from exc

    # JSON-RPC responses must be objects. A list/string/null body would
    # otherwise raise AttributeError on `.get` and surface as a 500.
    if not isinstance(body, dict):
        raise MCPError(f"mcp returned non-object response: {type(body).__name__}")

    if body.get("error") is not None:
        raise MCPError(f"mcp error: {body['error']}")

    tools = (body.get("result") or {}).get("tools") or []
    from sqlalchemy import select

    out: list[Tool] = []
    for remote in tools:
        norm = _normalize_remote_tool(server_url, remote)
        existing = await session.execute(
            select(Tool).where(Tool.name == norm["name"])
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = Tool(
                name=norm["name"],
                version=1,
                description=norm["description"],
                parameters=norm["parameters"],
                handler_type=norm["handler_type"],
                handler_cfg=norm["handler_cfg"],
                enabled=True,
                is_builtin=False,
            )
            session.add(row)
        else:
            row.description = norm["description"]
            row.parameters = norm["parameters"]
            row.handler_type = norm["handler_type"]
            row.handler_cfg = norm["handler_cfg"]
            row.version += 1
        out.append(row)
    await session.commit()
    log.info("mcp.discover", server=server_url, found=len(out))
    return out


async def call_tool(
    server_url: str,
    *,
    remote_name: str,
    arguments: dict,
    request_timeout: float = 30.0,
) -> dict:
    """Invoke a remote MCP tool and return the result dict.

    The MCP `tools/call` response is documented as:
        {content: [{type: 'text', text: '...'}], isError?: bool}
    We unwrap `content[0].text` if it parses as JSON, otherwise pass the
    raw text through.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": remote_name, "arguments": arguments or {}},
    }
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            r = await client.post(server_url.rstrip("/"), json=payload)
    except httpx.HTTPError as exc:
        raise MCPError(f"mcp unreachable: {exc}") from exc

    if r.status_code != 200:
        raise MCPError(f"mcp {r.status_code}: {r.text[:200]}")

    try:
        body = r.json()
    except json.JSONDecodeError as exc:
        raise MCPError(f"mcp returned non-JSON: {exc}") from exc

    if body.get("error"):
        raise MCPError(f"mcp error: {body['error']}")

    result = body.get("result") or {}
    if result.get("isError"):
        raise MCPError(f"mcp tool error: {result}")

    content = result.get("content") or []
    if content and isinstance(content, list):
        first = content[0] or {}
        text = first.get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return result


__all__ = ["MCPError", "discover_tools", "call_tool"]
