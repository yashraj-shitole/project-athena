"""Tests for the `ModelRouter` resolution order.

The router's job is the four-tier fallback: explicit `connector_id` →
user default → system default → built-in Ollama. These tests pin that
ordering, the soft-delete filter, the ownership/admin-shared
visibility check, and the disabled-connector skip — all without
spinning up a real database. The stub `AsyncSession` returns canned
`ModelConnector` rows based on a per-test plan; the router never knows
it's talking to a stub.
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

import pytest

# Make the project root importable when pytest is invoked from any cwd.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.providers.router import ModelRouter
from app.models.connector import (
    AUTH_BEARER,
    PROVIDER_OPENAI_COMPAT,
)


# --- Test doubles --------------------------------------------------------

@dataclass
class _FakeRow:
    """Minimum shape of a `ModelConnector` row the router touches.

    We don't need every column — only the ones `_build_adapter` and the
    resolution helpers read. Adding more is safe; the tests just
    reflect the bits that matter.
    """
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    provider: str
    base_url: str
    default_model: str
    is_enabled: bool = True
    is_default: bool = False
    is_admin: bool = False
    deleted_at: Any = None
    api_key_enc: Optional[bytes] = None
    auth_type: str = AUTH_BEARER
    auth_header_name: Optional[str] = None
    custom_headers: Optional[dict] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    api_version: Optional[str] = None
    models: Optional[List[str]] = None
    settings: Optional[dict] = None


class _PlannedResult:
    """Stub for `session.execute(...)`.

    Each call pops the next queued result. The router calls
    `execute()` up to three times per `resolve()`:
      1. `_load_connector(...)` (only if `connector_id` is given)
      2. `_load_user_default(...)`
      3. `_load_system_default(...)`
    The test stages one result per expected call; any leftover
    represents the fallback path (adapter from `_ollama_fallback`).
    """

    def __init__(self, plan: list):
        # Use the parent stub's plan list directly (NOT a copy) so
        # popping here advances the shared queue — the second call
        # to `execute()` should see the second plan item, not the
        # first one again.
        self._plan = plan

    def scalar_one_or_none(self):
        if not self._plan:
            return None
        return self._plan.pop(0)


class _StubSession:
    """AsyncSession stand-in.

    A test stages a list of results in the order the router should
    encounter them. `_load_connector` runs first only when
    `connector_id` is set, so the test omits it from `plan` for the
    no-connector case.
    """

    def __init__(self, plan: list) -> None:
        self._plan = plan
        self.calls: list[Any] = []

    async def execute(self, _stmt: Any) -> _PlannedResult:
        self.calls.append(_stmt)
        return _PlannedResult(self._plan)


# --- Helpers -------------------------------------------------------------

def _row(
    *,
    owner: Optional[uuid.UUID] = None,
    name: str = "test",
    provider: str = PROVIDER_OPENAI_COMPAT,
    base_url: str = "https://example.com/v1",
    default_model: str = "test-model",
    is_enabled: bool = True,
    is_default: bool = False,
    is_admin: bool = False,
    deleted_at: Any = None,
) -> _FakeRow:
    return _FakeRow(
        id=uuid.uuid4(),
        user_id=owner or uuid.uuid4(),
        name=name,
        provider=provider,
        base_url=base_url,
        default_model=default_model,
        is_enabled=is_enabled,
        is_default=is_default,
        is_admin=is_admin,
        deleted_at=deleted_at,
        models=[],
        settings={},
        custom_headers={},
    )


def _user() -> uuid.UUID:
    return uuid.uuid4()


# --- Resolution-order tests ---------------------------------------------

@pytest.mark.asyncio
async def test_explicit_connector_id_takes_precedence_over_user_default():
    """When the request pins a `connector_id`, that row wins — even
    if the user has a default that would otherwise resolve first."""

    user = _user()
    explicit = _row(
        owner=user,
        name="pinned",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    )
    # The user also has a default that the router would otherwise pick.
    # We stage a result for `_load_user_default` so we can assert it
    # is never consulted.
    user_default = _row(
        owner=user,
        name="default",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
    )

    # Plan: first call is `_load_connector` (returns explicit), the
    # router should NOT consult `_load_user_default` after.
    session = _StubSession(plan=[explicit])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
        connector_id=explicit.id,
    )

    assert conn_id == explicit.id
    assert model == "gpt-4o-mini"
    assert adapter.name == PROVIDER_OPENAI_COMPAT
    # Only the explicit load ran; the default was bypassed.
    assert len(session.calls) == 1
    # Sanity: the un-returned default is unused.
    assert user_default.is_default is True  # not relied on


@pytest.mark.asyncio
async def test_user_default_used_when_no_explicit_connector():
    """Without an explicit `connector_id`, the user's `is_default`
    row is picked."""

    user = _user()
    default = _row(
        owner=user,
        name="my-default",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
    )

    session = _StubSession(plan=[default])  # user-default result
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
    )

    assert conn_id == default.id
    assert model == "gpt-4o"
    assert adapter.name == PROVIDER_OPENAI_COMPAT
    # One call: user-default returned the row, system-default was
    # never reached (the resolution short-circuited on the first
    # match).
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_system_default_used_when_no_user_default():
    """If the user has no default, the system-shared `is_admin`
    default is picked."""

    user = _user()
    system_default = _row(
        owner=_user(),  # someone else owns it
        name="org-default",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
        is_admin=True,
    )

    # Plan: user-default returns None, then system-default returns the row.
    session = _StubSession(plan=[None, system_default])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
    )

    assert conn_id == system_default.id
    assert model == "gpt-4o"
    # The system default is admin-shared, so it's visible to this user.
    assert adapter.name == PROVIDER_OPENAI_COMPAT


@pytest.mark.asyncio
async def test_ollama_fallback_when_no_connector_configured():
    """No default anywhere → built-in Ollama fallback. The resolved
    connector id is `None`, which is the agent's signal to skip the
    usage row."""

    # Plan: user-default None, system-default None. Router then calls
    # `_ollama_fallback()` which builds an OpenAICompatibleProvider
    # from settings.
    session = _StubSession(plan=[None, None])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        _user(),
    )

    assert conn_id is None
    assert model  # non-empty (settings.OLLAMA_MODEL)
    assert adapter.name == PROVIDER_OPENAI_COMPAT


# --- Visibility + state filters -----------------------------------------

@pytest.mark.asyncio
async def test_explicit_connector_owned_by_other_user_is_invisible():
    """A connector owned by someone else is not visible unless it's
    admin-shared. With `connector_id` set to a foreign row, the
    router should fall through to the user default (or fallback)."""

    user = _user()
    foreign = _row(
        owner=_user(),  # NOT `user`
        name="someone-elses",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
    )

    # Plan: `_load_connector` returns the foreign row; the router
    # must filter it out (not owned, not admin). The fallback path
    # then runs `_load_user_default` and `_load_system_default`, both
    # returning None.
    session = _StubSession(plan=[foreign, None, None])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
        connector_id=foreign.id,
    )

    # The foreign row was rejected → we fell all the way to Ollama.
    assert conn_id is None
    assert model  # settings.OLLAMA_MODEL


@pytest.mark.asyncio
async def test_admin_shared_connector_visible_to_other_users():
    """An `is_admin = True` row is visible to every user, even if
    they don't own it."""

    user = _user()
    shared = _row(
        owner=_user(),  # someone else owns it
        name="org-shared",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
        is_admin=True,
    )

    # Plan: user-default is None; system-default returns the shared row.
    session = _StubSession(plan=[None, shared])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
    )

    assert conn_id == shared.id
    assert model == "gpt-4o"


@pytest.mark.asyncio
async def test_disabled_connector_is_skipped():
    """A disabled `is_enabled = False` row is skipped even if it's
    the user's default — the router must fall through to the next
    tier instead of returning a non-functional adapter."""

    from datetime import datetime, timezone

    user = _user()
    disabled = _row(
        owner=user,
        name="killed",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
        is_enabled=False,
    )
    # Even though the row is the user's default, the router must
    # treat `is_enabled = False` as if it didn't exist.
    session = _StubSession(plan=[disabled, None, None])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
    )

    # Disabled → fall through to Ollama.
    assert conn_id is None


@pytest.mark.asyncio
async def test_soft_deleted_connector_is_never_returned():
    """The DB query filters `deleted_at IS NULL`; a soft-deleted
    row is invisible to the router regardless of its other flags."""

    user = _user()
    # Note: a soft-deleted row in real life would have `is_default`
    # still TRUE on disk, but the SQL filter strips it.
    deleted = _row(
        owner=user,
        name="tombstone",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
        deleted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # The stub's `_load_user_default` would return this row if we
    # let it, so the test simulates the SQL filter by simply
    # *not* putting it in the plan. That's the contract: the DB
    # layer is responsible for the `deleted_at IS NULL` filter; the
    # router trusts the result it gets.
    session = _StubSession(plan=[None, None])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
    )

    assert conn_id is None


# --- Model-name selection -----------------------------------------------

@pytest.mark.asyncio
async def test_model_hint_overrides_row_default():
    """If the chat request pins a `model`, that wins over the
    connector's `default_model`."""

    user = _user()
    default = _row(
        owner=user,
        name="my-default",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        is_default=True,
    )

    session = _StubSession(plan=[default])
    router = ModelRouter()

    _, model, _ = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
        model_hint="gpt-4o",
    )

    assert model == "gpt-4o"


# --- Adapter construction -----------------------------------------------

@pytest.mark.asyncio
async def test_disabled_explicit_connector_falls_through():
    """A disabled explicit `connector_id` is logged and the router
    falls through to user-default / system-default / Ollama — it
    does NOT raise, because the user might have a working default
    that should kick in."""

    user = _user()
    pinned = _row(
        owner=user,
        name="pinned",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_enabled=False,
    )
    # User default is the working row.
    working = _row(
        owner=user,
        name="working",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
        is_enabled=True,
    )

    # Plan: `_load_connector` returns the disabled row, the router
    # then asks for `_load_user_default` and gets `working`.
    session = _StubSession(plan=[pinned, working])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
        connector_id=pinned.id,
    )

    # We landed on the working default, not the disabled pinned one.
    assert conn_id == working.id
    assert model == "gpt-4o"
    assert adapter.name == PROVIDER_OPENAI_COMPAT


@pytest.mark.asyncio
async def test_explicit_connector_id_for_missing_row_falls_through():
    """If the explicit `connector_id` doesn't exist (or was
    soft-deleted), the router must continue down the chain — not
    raise a `NoResultFound`."""

    user = _user()
    fallback_default = _row(
        owner=user,
        name="fallback",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        is_default=True,
    )

    # Plan: `_load_connector` returns None, the router proceeds.
    session = _StubSession(plan=[None, fallback_default])
    router = ModelRouter()

    adapter, model, conn_id = await router.resolve(
        session,  # type: ignore[arg-type]
        user,
        connector_id=uuid.uuid4(),  # nonexistent
    )

    assert conn_id == fallback_default.id
    assert model == "gpt-4o"
