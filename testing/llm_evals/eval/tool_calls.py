"""Tool-calling-specific scorers.

A "tool call" eval grades the `used_tools` list on the assistant
message. We assume the orchestrator writes a row per tool with at
least `{name, arguments, status}` (matches `app/schemas/...`).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .matchers import EvalResultLike, ScorerResult


def tool_selection_accuracy() -> Callable[[Any], ScorerResult]:
    """The set of tool names the model invoked is exactly the gold set.

    Gold set comes from `raw.expected_tool_names` (list[str]). Score is:
      1.0  exact match
      0.5  subset (model called fewer tools, all of which were correct)
      0.0  any extra / wrong tool, or missing a required one
    """
    def _score(result: EvalResultLike) -> ScorerResult:
        gold = set((result.raw or {}).get("expected_tool_names", []))
        if not gold:
            return ScorerResult(0.0, "tool_selection: no expected_tool_names on raw")
        actual = {t.get("name") for t in (result.used_tools or [])}
        if actual == gold:
            return ScorerResult(1.0, f"tool_selection: exact {sorted(actual)}",
                                details={"actual": sorted(actual), "gold": sorted(gold)})
        if actual.issubset(gold):
            return ScorerResult(0.5, f"tool_selection: subset {sorted(actual)} of {sorted(gold)}",
                                details={"actual": sorted(actual), "gold": sorted(gold)})
        return ScorerResult(
            0.0,
            f"tool_selection: mismatch actual={sorted(actual)} gold={sorted(gold)}",
            details={"actual": sorted(actual), "gold": sorted(gold)},
        )
    return _score


def parameter_schema_adherence() -> Callable[[Any], ScorerResult]:
    """Every tool call's `arguments` parses as JSON AND validates against
    the per-tool JSON schema registered in the Tool table.

    Falls back to "the arguments JSON-parse" if no schema is found for
    the tool (still a 1.0 if it parses).
    """
    def _score(result: EvalResultLike) -> ScorerResult:
        from jsonschema import Draft7Validator
        tools = result.used_tools or []
        if not tools:
            return ScorerResult(0.0, "param_schema: no tools called")
        ok = 0
        bad = []
        for t in tools:
            name = t.get("name")
            args = t.get("arguments")
            # Accept either a dict or a JSON string.
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception as e:
                    bad.append(f"{name}: not JSON ({e})")
                    continue
            schema = (t.get("schema") or _known_tool_schema(name))
            if schema is None:
                # No schema -> count as pass if it's a dict.
                if isinstance(args, dict):
                    ok += 1
                else:
                    bad.append(f"{name}: not a dict (no schema)")
                continue
            try:
                Draft7Validator(schema).validate(args)
                ok += 1
            except Exception as e:
                bad.append(f"{name}: {e.message}")
        if not tools:
            return ScorerResult(0.0, "param_schema: no tools")
        score = ok / len(tools)
        return ScorerResult(
            score, f"param_schema: {ok}/{len(tools)} valid; bad={bad}",
            details={"ok": ok, "total": len(tools), "bad": bad},
        )
    return _score


def _known_tool_schema(name: str | None) -> dict[str, Any] | None:
    """Best-effort lookup of a tool's JSON schema from the in-process
    tool registry. We deliberately don't import `app.tools.registry`
    here to keep the eval package hermetic — the integration tests
    populate `used_tools[].schema` directly."""
    return None
