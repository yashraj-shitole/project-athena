"""Tests for the C-1 (Critical) tool-wrapper user_id invariant
and the per-impl kwargs allowlist in
``app.tools.registry``.

The invariant under test:

* An internal tool's implementation function is called with
  ``user_id`` and ``session`` injected by the *registry*, not by
  the caller.
* A caller that supplies ``user_id`` (or any other key not in the
  per-impl allowlist) in ``arguments`` is silently dropped — the
  injected value wins.
* A new internal impl that has no allowlist entry is rejected
  with a clear error (fail-closed).

We exercise the registry's ``_run_internal`` and ``execute``
functions directly with a stub session + stub user_id, and a
monkey-patched function (so we don't depend on the
``search_documents:run`` impl being available).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# --- Helper: build a Tool row that points at a stub function -------------


class _StubTool:
    """Mimics the ORM ``Tool`` row — only the fields ``_run_internal``
    actually reads."""

    def __init__(self, name: str, impl: str) -> None:
        self.name = name
        self.handler_type = "internal"
        self.handler_cfg = {"impl": impl}


def _make_capture_stub():
    """Return ``(module_stub, captured_dict)``.

    The module stub is what ``importlib.import_module`` will return;
    it has a ``run`` attribute pointing at an async function that
    records its kwargs in ``captured``.
    """
    captured: dict[str, Any] = {}

    async def run(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    module = MagicMock()
    module.run = run
    return module, captured


def _run(coro):
    """Run an awaitable to completion — small shim so the test
    body stays linear without ``@pytest.mark.asyncio`` everywhere.
    """
    return asyncio.run(coro)


# --- The privilege invariant itself -------------------------------------


def test_run_internal_injects_user_id_and_session():
    """The registry, not the caller, owns ``user_id`` + ``session``."""
    from app.tools import registry

    user_id = uuid.uuid4()
    session = MagicMock(name="session")
    tool = _StubTool("t", "app.tools.builtin.search_documents:run")
    module, captured = _make_capture_stub()

    with patch(
        "app.tools.registry.importlib.import_module",
        return_value=module,
    ):
        _run(
            registry._run_internal(
                tool, {"keywords": ["a"]}, user_id=user_id, session=session
            )
        )

    # The function was called with the privilege-bearing kwargs injected.
    assert "user_id" in captured
    assert "session" in captured
    assert captured["user_id"] == str(user_id)
    assert captured["session"] is session
    # The legitimate kwarg survived.
    assert captured["keywords"] == ["a"]


def test_run_internal_drops_caller_supplied_user_id():
    """A caller that supplies ``user_id`` in arguments is ignored
    — the registry's injected value wins.
    """
    from app.tools import registry

    user_id = uuid.uuid4()
    other_user = uuid.uuid4()
    session = MagicMock(name="session")
    tool = _StubTool("t", "app.tools.builtin.search_documents:run")
    module, captured = _make_capture_stub()

    with patch(
        "app.tools.registry.importlib.import_module",
        return_value=module,
    ):
        _run(
            registry._run_internal(
                tool,
                # The caller is trying to spoof a different user.
                {"keywords": ["x"], "user_id": str(other_user), "session": "spoof"},
                user_id=user_id,
                session=session,
            )
        )

    # The injected user_id wins — ``other_user`` is gone.
    assert captured["user_id"] == str(user_id)
    # The injected session wins — the string "spoof" is gone.
    assert captured["session"] is session
    # The legitimate kwarg survived.
    assert captured["keywords"] == ["x"]


def test_run_internal_drops_unknown_kwargs():
    """A kwarg the impl does not declare is silently dropped and
    logged. The function never sees it.
    """
    from app.tools import registry

    user_id = uuid.uuid4()
    session = MagicMock(name="session")
    tool = _StubTool("t", "app.tools.builtin.search_documents:run")
    module, captured = _make_capture_stub()

    with patch(
        "app.tools.registry.importlib.import_module",
        return_value=module,
    ):
        _run(
            registry._run_internal(
                tool,
                {
                    "keywords": ["x"],
                    "top_k": 4,  # allowed
                    "evil_param": "smuggled",  # not in allowlist
                    "another_evil": 42,
                },
                user_id=user_id,
                session=session,
            )
        )

    assert captured["keywords"] == ["x"]
    assert captured["top_k"] == 4
    assert "evil_param" not in captured
    assert "another_evil" not in captured
    # The privilege-bearing kwargs are present (injected).
    assert "user_id" in captured
    assert "session" in captured


def test_run_internal_rejects_impl_without_allowlist():
    """A new internal impl that has no entry in
    ``_INTERNAL_IMPL_KWARGS`` is rejected with a clear error.
    This is fail-closed: the tool cannot be invoked.
    """
    from app.tools import registry

    user_id = uuid.uuid4()
    session = MagicMock(name="session")
    tool = _StubTool("t", "app.tools.builtin.search_documents:run")

    with patch.dict(registry._INTERNAL_IMPL_KWARGS, {}, clear=True):
        async def run():
            return await registry._run_internal(
                tool,
                {"keywords": ["x"]},
                user_id=user_id,
                session=session,
            )

        with pytest.raises(ValueError, match="no kwarg allowlist"):
            _run(run())


def test_run_internal_rejects_unknown_impl():
    """An impl path that is not in ``_ALLOWED_INTERNAL_IMPLS`` is
    rejected — same as before the refactor.
    """
    from app.tools import registry

    user_id = uuid.uuid4()
    session = MagicMock(name="session")
    tool = _StubTool("t", "app.tools.builtin.totally_made_up:run")

    async def run():
        return await registry._run_internal(
            tool, {}, user_id=user_id, session=session
        )

    with pytest.raises(ValueError, match="is not allowed"):
        _run(run())


def test_execute_requires_user_id_for_internal_tool():
    """``execute()`` rejects an internal-tool call with no user_id.

    This is the secondary line of defense: even if some future
    caller forgot to thread the user through, the registry fails
    closed.

    We mock ``get_by_name`` to short-circuit the DB lookup — the
    test cares about the privilege check, not the SQL.
    """
    from unittest.mock import AsyncMock

    from app.tools import registry

    session = MagicMock(name="session")
    tool_row = _StubTool("t", "app.tools.builtin.search_documents:run")

    with patch.object(registry, "get_by_name", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = tool_row
        with patch.object(registry, "_run_internal", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"ok": True}

            async def run():
                return await registry.execute(
                    session, tool_name="t", arguments={}
                )

            tool, result, status_label, latency = _run(run())
    assert status_label == "error"
    assert "requires a user_id" in str(result.get("error", ""))
    # The internal runner was never called.
    mock_run.assert_not_called()


def test_execute_threads_user_id_to_internal_runner():
    """``execute()`` threads the supplied ``user_id`` into the
    internal runner (the orchestrator / route don't have to know
    about the registry's per-impl allowlist).
    """
    from unittest.mock import AsyncMock

    from app.tools import registry

    user_id = uuid.uuid4()
    session = MagicMock(name="session")
    tool_row = _StubTool("t", "app.tools.builtin.search_documents:run")

    with patch.object(registry, "get_by_name", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = tool_row
        with patch.object(registry, "_run_internal", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"ok": True}

            async def run():
                return await registry.execute(
                    session,
                    tool_name="t",
                    arguments={"keywords": ["x"]},
                    user_id=user_id,
                )

            _run(run())
    mock_run.assert_awaited_once()
    # The user_id passed to the runner is the one the caller supplied.
    args, kwargs = mock_run.call_args
    assert kwargs["user_id"] == user_id
    assert kwargs["session"] is session
    # ``arguments`` is passed positionally in ``_run_internal(tool, arguments, *, user_id, session)``.
    assert args[0] is tool_row
    assert args[1] == {"keywords": ["x"]}
