"""Orchestrator package.

  - llm_client: low-level LLM wrapper (streaming + completion)
  - prompter:   budgeted prompt assembly + citation extraction
  - tool_call:  validate / retry / deterministic fallback (FR-23, NFR-10)
  - agent:      the per-turn agent loop (tools → LLM → answer)
"""
from app.services.orchestrator import agent, llm_client, prompter, tool_call

__all__ = ["agent", "llm_client", "prompter", "tool_call"]
