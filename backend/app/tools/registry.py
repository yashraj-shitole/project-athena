"""Tool registry.

A thin wrapper around the `tools` table that:
  - lists enabled tool definitions
  - formats them as Ollama tool schemas (FR-30)
  - caches the snapshot in Redis (FR-35)
  - delegates execution to the matching handler
"""
from __future__ import annotations

import importlib
import json
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_client as get_redis
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.tool import Tool

log = get_logger(__name__)
_settings = get_settings()

# Allowlist of internal tool implementations. A tool's handler_cfg.impl
# must be one of these (and must match the shape regex) before it is
# imported and called — otherwise any admin who can upsert a tool could
# point `impl` at an arbitrary installed-package callable and invoke it
# with attacker-supplied kwargs. New builtins are added here.
_IMPL_RE = re.compile(r"^[A-Za-z0-9_.]+:[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_INTERNAL_IMPLS = {
    "app.tools.builtin.search_documents:run",
}


# ---------- registry snapshots ----------
async def list_enabled(session: AsyncSession) -> list[Tool]:
    res = await session.execute(
        select(Tool).where(Tool.enabled.is_(True)).order_by(Tool.name)
    )
    return list(res.scalars())


async def get_by_name(session: AsyncSession, name: str) -> Tool | None:
    res = await session.execute(
        select(Tool).where(Tool.name == name, Tool.enabled.is_(True))
    )
    return res.scalar_one_or_none()


# ---------- cache helpers (FR-35) ----------
def _snapshot_key() -> str:
    return "athena:tooldef:snapshot:v1"


async def get_cached_snapshot() -> list[dict] | None:
    try:
        raw = await get_redis().get(_snapshot_key())
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def set_cached_snapshot(snapshot: list[dict], ttl: int | None = None) -> None:
    try:
        await get_redis().set(
            _snapshot_key(),
            json.dumps(snapshot, default=str),
            ex=ttl or _settings.cache_ttl_seconds,
        )
    except Exception:  # noqa: BLE001
        # cache is best-effort
        pass


async def invalidate_snapshot() -> None:
    try:
        await get_redis().delete(_snapshot_key())
    except Exception:  # noqa: BLE001
        pass


# ---------- public surface ----------
def _tool_to_schema(tool: Tool) -> dict[str, Any]:
    """Render a Tool row as an Ollama tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


async def snapshot(session: AsyncSession, *, use_cache: bool = True) -> list[dict]:
    """Return a list of Ollama-shaped tool schemas for the active registry."""
    if use_cache:
        cached = await get_cached_snapshot()
        if cached is not None:
            return cached
    tools = await list_enabled(session)
    snap = [_tool_to_schema(t) for t in tools]
    await set_cached_snapshot(snap)
    return snap


async def select_subset(
    session: AsyncSession,
    *,
    requested: Iterable[str] | None = None,
) -> list[dict]:
    """FR-30: return a possibly-reduced subset of tool schemas."""
    snap = await snapshot(session)
    if not requested:
        return snap
    wanted = set(requested)
    return [t for t in snap if t["function"]["name"] in wanted]


# ---------- execution ----------
async def _run_internal(tool: Tool, arguments: dict) -> dict:
    impl_path: str = (tool.handler_cfg or {}).get("impl", "")
    if not impl_path or not _IMPL_RE.match(impl_path):
        raise ValueError(f"Internal tool '{tool.name}' has an invalid handler_cfg.impl")
    if impl_path not in _ALLOWED_INTERNAL_IMPLS:
        # Defence-in-depth: even though tool upsert is admin-gated, the
        # callable surface must not widen to arbitrary installed modules.
        raise ValueError(
            f"Internal tool '{tool.name}' impl '{impl_path}' is not allowed"
        )
    mod_name, fn_name = impl_path.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn: Callable[..., Awaitable[dict]] = getattr(mod, fn_name)
    return await fn(**arguments)


async def _run_http(tool: Tool, arguments: dict) -> dict:
    import httpx

    cfg = tool.handler_cfg or {}
    url = cfg.get("url")
    method = (cfg.get("method") or "POST").upper()
    if not url:
        raise ValueError(f"HTTP tool '{tool.name}' missing handler_cfg.url")
    timeout = float(cfg.get("timeout_s") or 20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, url, json=arguments)
    return {"status_code": resp.status_code, "body": _safe_json(resp)}


async def _run_mcp(tool: Tool, arguments: dict) -> dict:
    """FR-29: forward to a remote MCP server (see app/tools/mcp.py)."""
    from app.tools.mcp import MCPError, call_tool

    cfg = tool.handler_cfg or {}
    server_url = cfg.get("server_url")
    remote_name = cfg.get("remote_name")
    if not server_url or not remote_name:
        raise ValueError(f"MCP tool '{tool.name}' missing handler_cfg fields")
    try:
        return await call_tool(
            server_url, remote_name=remote_name, arguments=arguments or {}
        )
    except MCPError as exc:
        raise RuntimeError(str(exc)) from exc


def _safe_json(resp) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text[:2000]


async def execute(
    session: AsyncSession,
    *,
    tool_name: str,
    arguments: dict,
) -> tuple[Tool | None, dict, str, int]:
    """Run a registered tool.

    Returns: (Tool, result_dict, status, latency_ms)
    status ∈ {"ok", "error", "fallback"}
    """
    tool = await get_by_name(session, tool_name)
    start = time.perf_counter()
    if tool is None:
        return None, {"error": f"tool_not_found: {tool_name}"}, "error", 0
    try:
        if tool.handler_type == "internal":
            out = await _run_internal(tool, arguments)
        elif tool.handler_type == "http":
            out = await _run_http(tool, arguments)
        elif tool.handler_type == "mcp":
            out = await _run_mcp(tool, arguments)
        else:
            return tool, {"error": f"unknown_handler_type: {tool.handler_type}"}, "error", int(
                (time.perf_counter() - start) * 1000
            )
        elapsed = int((time.perf_counter() - start) * 1000)
        return tool, out, "ok", elapsed
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - start) * 1000)
        log.warning("tool.exec.error", tool=tool_name, error=str(exc))
        return tool, {"error": str(exc)[:500]}, "error", elapsed


# ---------- CRUD (used by /api/tools) ----------
async def upsert_tool(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    parameters: dict,
    handler_type: str,
    handler_cfg: dict,
    enabled: bool = True,
) -> Tool:
    existing = await session.execute(select(Tool).where(Tool.name == name))
    row = existing.scalar_one_or_none()
    if row is None:
        row = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler_type=handler_type,
            handler_cfg=handler_cfg,
            enabled=enabled,
            is_builtin=False,
            version=1,
        )
        session.add(row)
    else:
        row.description = description
        row.parameters = parameters
        row.handler_type = handler_type
        row.handler_cfg = handler_cfg
        row.enabled = enabled
        row.version += 1
    await session.commit()
    await session.refresh(row)
    await invalidate_snapshot()
    return row


async def set_enabled(session: AsyncSession, tool_id: uuid.UUID, enabled: bool) -> Tool | None:
    res = await session.execute(select(Tool).where(Tool.id == tool_id))
    row = res.scalar_one_or_none()
    if row is None:
        return None
    row.enabled = enabled
    row.version += 1
    await session.commit()
    await session.refresh(row)
    await invalidate_snapshot()
    return row


# Re-exports for callers that want to await registry work outside the event loop
__all__ = [
    "list_enabled",
    "get_by_name",
    "snapshot",
    "select_subset",
    "execute",
    "upsert_tool",
    "set_enabled",
    "invalidate_snapshot",
]
