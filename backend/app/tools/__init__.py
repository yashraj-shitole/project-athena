"""Tools subsystem.

Public surface:
  - registry: DB-backed tool registry + cache (FR-27..30, FR-35)
  - builtin:  internal tools (e.g. `search_documents`) whose handlers are
              imported lazily by the registry
  - mcp:      minimal MCP client (FR-29) — discovers remote tools and
              forwards invocations over JSON-RPC
"""
from app.tools import builtin, mcp, registry

__all__ = ["registry", "builtin", "mcp"]
