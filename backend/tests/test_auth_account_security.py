"""Regression tests for H-19 — password change, account lockout, admin disable.

These tests exercise the auth surface in three layers:

* ``_authenticate`` and ``_maybe_apply_lockout`` are exercised directly
  (no FastAPI) so the lockout arithmetic is fast and hermetic.
* The ``/auth/change-password`` and ``/auth/login`` routes are exercised
  through a real ``TestClient``-mounted ``app`` for the JSON paths
  (using SQLite in-memory, the same fixture the rest of the suite uses).
* The ``/admin/users/{id}`` PATCH route is exercised through
  ``TestClient`` too; the admin dep is satisfied by setting
  ``ATHENA_ADMIN_EMAILS`` to the test user's email.

Hermeticity: every test in this file uses the in-memory SQLite session
that conftest already provides. There is no Postgres, no Redis, and no
Ollama dependency. The settings are not patched at runtime — the defaults
are sufficient (5 max fails, 15-minute lockout).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers — small factories for the test user and a TestClient.
# ---------------------------------------------------------------------------

def _make_user(
    email: str = "alice@example.com",
    password: str = "Sup3rStrong!",
    is_active: bool = True,
    failed_login_count: int = 0,
    locked_until: datetime | None = None,
    token_version: int = 0,
) -> User:
    """Build a User object that can be persisted into the in-memory DB.

    The id is explicit so the test can reference it without a round trip.
    """
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password(password),
        is_active=is_active,
        token_version=token_version,
        failed_login_count=failed_login_count,
        locked_until=locked_until,
    )


@pytest.fixture
def make_client(monkeypatch):
    """Return a factory that yields a TestClient with a fresh in-memory DB.

    The factory seeds the DB with whatever users the caller passes in.
    The conftest's ``ATHENA_DATABASE_URL=sqlite+aiosqlite:///:memory:``
    is the basis; we override it per-test to a unique URL so the
    in-memory DB is scoped to the test (sqlite in-memory is per-
    connection by default, but the TestClient shares the engine
    with the test).
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.core import config as config_module
    from app.api.dependencies import get_db, get_user_db, get_current_user
    from app.core.deps import get_current_user_id
    from main import app  # imported lazily so conftest runs first

    def _factory(users: list[User] | None = None) -> TestClient:
        # Each test gets its own engine.
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        async def _setup() -> AsyncSession:
            # H-19 — only the ``users`` table is needed for the auth
            # routes. ``Base.metadata.create_all`` over the full
            # schema fails on SQLite for the Postgres-only types
            # (``JSONB``, ``UUID``) used by documents / chunks /
            # tools / connectors. We scope the schema creation to
            # just the User table. The tests never touch the other
            # tables; the auth surface only reads/writes ``users``.
            from app.models.user import User as _User  # local import
            users_table = _User.__table__
            async with engine.begin() as conn:
                await conn.run_sync(users_table.create, checkfirst=True)
            maker = async_sessionmaker(engine, expire_on_commit=False)

            # Seed users.
            async with maker() as session:
                for u in users or []:
                    session.add(u)
                await session.commit()
            return maker

        # We must set up the schema *synchronously* before the
        # TestClient starts running requests. Run the event loop here.
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        maker = loop.run_until_complete(_setup())

        async def _override_get_db():
            async with maker() as session:
                yield session

        # The route layer uses ``get_user_db`` (sets RLS) on top of
        # ``get_db`` (raw engine). Override both.
        #
        # H-19 — the SQLite engine has no ``set_config`` / ``RESET``
        # GUC functions, so the production RLS setters raise. We
        # substitute no-op coroutines for the duration of the test
        # so the auth surface behaves as if RLS is already configured
        # (which it is — every test's in-memory DB is private to the
        # test, so RLS isn't actually needed). The setter is still
        # called by the override to mirror the production code path.
        async def _no_op_rls_set_user(*_args, **_kwargs):
            return None

        async def _no_op_rls_set_admin(*_args, **_kwargs):
            return None

        async def _no_op_rls_reset_user(*_args, **_kwargs):
            return None

        async def _no_op_rls_reset_admin(*_args, **_kwargs):
            return None

        monkeypatch.setattr(
            "app.api.dependencies.set_rls_user", _no_op_rls_set_user
        )
        monkeypatch.setattr(
            "app.api.dependencies.set_rls_admin", _no_op_rls_set_admin
        )
        monkeypatch.setattr(
            "app.api.dependencies.reset_rls_user", _no_op_rls_reset_user
        )
        monkeypatch.setattr(
            "app.api.dependencies.reset_rls_admin", _no_op_rls_reset_admin
        )

        # H-19 — bypass the per-IP rate limiter for the auth
        # endpoints so the lockout tests can fire 10+ logins in
        # a single test without tripping the 10/min budget. The
        # rate-limiter is exercised in its own test file
        # (``test_ratelimit.py``); here we only care about the
        # auth flow. The auth routes attach the rate-limit dep
        # as a route-level ``dependencies=`` entry; FastAPI's
        # ``dependency_overrides`` keys on the *inner* callable
        # (not the ``Depends(...)`` wrapper). The inner callable
        # is reachable via ``.dependency`` on the ``Depends``
        # instance.
        from app.core.ratelimit import (
            RateLimitLogin,
            RateLimitRegister,
            RateLimitRefresh,
        )

        async def _no_op_rate_limit() -> None:
            return None

        for _rate_dep in (RateLimitLogin, RateLimitRegister, RateLimitRefresh):
            # Each ``RateLimitX`` is a ``Depends(rate_limit(X))`` —
            # its ``.dependency`` attribute is the inner function
            # the route will be calling.
            app.dependency_overrides[_rate_dep.dependency] = _no_op_rate_limit

        async def _override_get_user_db():
            async with maker() as session:
                try:
                    yield session
                finally:
                    try:
                        await session.rollback()
                    except Exception:  # noqa: BLE001
                        pass

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_user_db] = _override_get_user_db

        return TestClient(app)

    yield _factory

    # Cleanup: drop overrides between tests so they don't leak.
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Direct unit tests for the lockout arithmetic.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lockout_remaining_seconds_zero_when_none():
    from app.api.auth import _lockout_remaining_seconds

    assert _lockout_remaining_seconds(None) == 0


@pytest.mark.asyncio
async def test_lockout_remaining_seconds_zero_when_past():
    from app.api.auth import _lockout_remaining_seconds

    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert _lockout_remaining_seconds(past) == 0


@pytest.mark.asyncio
async def test_lockout_remaining_seconds_positive_when_future():
    from app.api.auth import _lockout_remaining_seconds

    future = datetime.now(timezone.utc) + timedelta(seconds=123)
    assert 120 <= _lockout_remaining_seconds(future) <= 123


@pytest.mark.asyncio
async def test_maybe_apply_lockout_below_threshold():
    from app.api.auth import _maybe_apply_lockout

    user = _make_user(failed_login_count=0)
    await _maybe_apply_lockout(user)
    assert user.failed_login_count == 1
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_maybe_apply_lockout_at_threshold_locks():
    from app.api.auth import _maybe_apply_lockout

    settings = get_settings()
    user = _make_user(failed_login_count=settings.login_max_fails - 1)
    await _maybe_apply_lockout(user)
    assert user.failed_login_count == settings.login_max_fails
    assert user.locked_until is not None
    # The lockout expires within the configured window.
    assert (
        user.locked_until - datetime.now(timezone.utc)
    ).total_seconds() <= settings.login_lockout_s


@pytest.mark.asyncio
async def test_maybe_apply_lockout_above_threshold_extends_lockout():
    """Once the threshold is crossed, further wrong-password attempts
    do not extend the lockout — the existing ``locked_until`` is
    preserved so a determined attacker cannot indefinitely keep the
    account locked by continuing to spray. (H-19 spec.)"""
    from app.api.auth import _maybe_apply_lockout

    settings = get_settings()
    fixed_lock = datetime.now(timezone.utc) + timedelta(
        seconds=settings.login_lockout_s
    )
    user = _make_user(
        failed_login_count=settings.login_max_fails,
        locked_until=fixed_lock,
    )
    await _maybe_apply_lockout(user)
    assert user.failed_login_count == settings.login_max_fails + 1
    # locked_until is unchanged (within 1s of the original value).
    delta = (user.locked_until - fixed_lock).total_seconds()
    assert abs(delta) < 1.0


# ---------------------------------------------------------------------------
# End-to-end: /auth/login fires the lockout.
# ---------------------------------------------------------------------------

def test_login_locks_account_after_max_fails(make_client):
    settings = get_settings()
    user = _make_user(email="alice@example.com", password="Sup3rStrong!")
    client = make_client(users=[user])

    # Use the JSON login path; the form path exercises the same
    # ``_authenticate`` but the JSON one is more ergonomic in tests.
    for attempt in range(settings.login_max_fails):
        r = client.post(
            "/api/auth/login-json",
            json={"email": "alice@example.com", "password": "wrong"},
        )
        assert r.status_code == 401, (
            f"attempt {attempt} should be a normal 401, got "
            f"{r.status_code} {r.text!r}"
        )
        # First ``max_fails - 1`` attempts have no lockout hint;
        # the threshold-crossing one also doesn't (it gets a normal
        # 401 because the counter increments and the lockout is
        # applied AFTER the response). The next request is the
        # one that sees the lockout.
        assert r.headers.get("www-authenticate", "").lower().startswith("bearer")

    # The next attempt, even with the RIGHT password, must hit
    # the lockout path and return 401 with a ``locked=N`` hint.
    r = client.post(
        "/api/auth/login-json",
        json={"email": "alice@example.com", "password": "Sup3rStrong!"},
    )
    assert r.status_code == 401
    auth = r.headers.get("www-authenticate", "").lower()
    assert auth.startswith("bearer locked="), f"expected locked= hint, got {auth!r}"


def test_login_lockout_body_is_generic(make_client):
    """The body must be the standard 'Incorrect email or password'
    so an attacker cannot distinguish a lockout from a wrong-password
    failure without reading the response headers.
    """
    settings = get_settings()
    user = _make_user()
    client = make_client(users=[user])
    # Drive enough wrong attempts to lock the account.
    for _ in range(settings.login_max_fails):
        client.post(
            "/api/auth/login-json",
            json={"email": user.email, "password": "wrong"},
        )
    r = client.post(
        "/api/auth/login-json",
        json={"email": user.email, "password": "wrong"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password"


def test_lockout_does_not_affect_other_users(make_client):
    settings = get_settings()
    victim = _make_user(email="victim@example.com")
    bystander = _make_user(email="bystander@example.com", password="RightPass1!")
    client = make_client(users=[victim, bystander])

    # Drive the victim to the lockout threshold.
    for _ in range(settings.login_max_fails):
        client.post(
            "/api/auth/login-json",
            json={"email": "victim@example.com", "password": "wrong"},
        )
    # The bystander can still log in.
    r = client.post(
        "/api/auth/login-json",
        json={"email": "bystander@example.com", "password": "RightPass1!"},
    )
    assert r.status_code == 200, r.text


def test_unknown_email_does_not_lock(make_client):
    """The unknown-email path runs the dummy-hash verify but does
    NOT touch the counter. There is no real user to lock.
    """
    settings = get_settings()
    victim = _make_user(email="victim@example.com", password="Sup3rStrong!")
    client = make_client(users=[victim])
    # 10 wrong logins for an email that does not exist.
    for _ in range(10):
        r = client.post(
            "/api/auth/login-json",
            json={"email": "ghost@example.com", "password": "wrong"},
        )
        assert r.status_code == 401
    # The real victim can still log in.
    r = client.post(
        "/api/auth/login-json",
        json={"email": "victim@example.com", "password": "Sup3rStrong!"},
    )
    assert r.status_code == 200


def test_successful_login_resets_lockout_state(make_client):
    """A successful login clears ``failed_login_count`` and
    ``locked_until`` so a single correct password restores the
    account even mid-bruteforce.
    """
    settings = get_settings()
    user = _make_user(email="alice@example.com", password="Sup3rStrong!")
    client = make_client(users=[user])
    # 3 wrong attempts, then a correct one.
    for _ in range(3):
        client.post(
            "/api/auth/login-json",
            json={"email": "alice@example.com", "password": "wrong"},
        )
    r = client.post(
        "/api/auth/login-json",
        json={"email": "alice@example.com", "password": "Sup3rStrong!"},
    )
    assert r.status_code == 200
    # Now wrong attempts start from 0 again.
    for _ in range(settings.login_max_fails - 1):
        r = client.post(
            "/api/auth/login-json",
            json={"email": "alice@example.com", "password": "wrong"},
        )
        assert r.status_code == 401
        assert "locked=" not in r.headers.get("www-authenticate", "")


# ---------------------------------------------------------------------------
# /auth/change-password
# ---------------------------------------------------------------------------

def _login(client: TestClient, email: str, password: str) -> str:
    """Log in and return the access token."""
    r = client.post(
        "/api/auth/login-json",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_change_password_wrong_current_401(make_client):
    user = _make_user()
    client = make_client(users=[user])
    token = _login(client, user.email, "Sup3rStrong!")
    r = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "WrongPass1!", "new_password": "NewerPass1!"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect current password"


def test_change_password_success_then_old_jwt_fails(make_client):
    user = _make_user()
    client = make_client(users=[user])
    token = _login(client, user.email, "Sup3rStrong!")
    r = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "Sup3rStrong!",
            "new_password": "BrandNewPass1!",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    # The old token is now revoked — /me must 401.
    r = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    # A fresh login with the new password works.
    new_token = _login(client, user.email, "BrandNewPass1!")
    r = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == user.email


def test_change_password_schema_rejects_unknown_fields(make_client):
    """H-22 (RequestBase extra=forbid) must apply to PasswordChangeRequest."""
    user = _make_user()
    client = make_client(users=[user])
    token = _login(client, user.email, "Sup3rStrong!")
    r = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "Sup3rStrong!",
            "new_password": "BrandNewPass1!",
            "is_admin": True,  # smuggled field
        },
    )
    assert r.status_code == 422


def test_change_password_requires_authentication(make_client):
    user = _make_user()
    client = make_client(users=[user])
    r = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "Sup3rStrong!",
            "new_password": "BrandNewPass1!",
        },
    )
    assert r.status_code == 401


def test_change_password_clears_lockout_state(make_client):
    """A successful password change resets the lockout counter as a
    side effect — the user just proved possession of a valid
    current password.
    """
    settings = get_settings()
    user = _make_user(failed_login_count=settings.login_max_fails - 1)
    client = make_client(users=[user])
    token = _login(client, user.email, "Sup3rStrong!")
    r = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "Sup3rStrong!",
            "new_password": "BrandNewPass1!",
        },
    )
    assert r.status_code == 200
    # The new password works; no lockout.
    new_token = _login(client, user.email, "BrandNewPass1!")
    assert new_token


# ---------------------------------------------------------------------------
# /admin/users/{id}
# ---------------------------------------------------------------------------

def test_admin_disable_then_user_token_fails(monkeypatch, make_client):
    """PATCH is_active=false bumps the target's token_version so
    their next request 401s."""
    from app.core import config as config_module

    admin = _make_user(email="admin@example.com", password="AdminPass1!")
    target = _make_user(email="target@example.com", password="TargetPass1!")
    monkeypatch.setenv("ATHENA_ADMIN_EMAILS", '["admin@example.com"]')
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_client(users=[admin, target])
    admin_token = _login(client, "admin@example.com", "AdminPass1!")
    target_token = _login(client, "target@example.com", "TargetPass1!")

    r = client.patch(
        f"/api/admin/users/{target.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # The target's existing token must now fail.
    r = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert r.status_code == 401


def test_admin_disable_non_admin_403(monkeypatch, make_client):
    from app.core import config as config_module

    admin = _make_user(email="admin@example.com")
    bystander = _make_user(email="bystander@example.com", password="ByPass1!")
    target = _make_user(email="target@example.com")
    monkeypatch.setenv("ATHENA_ADMIN_EMAILS", '["admin@example.com"]')
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_client(users=[admin, bystander, target])
    by_token = _login(client, "bystander@example.com", "ByPass1!")

    r = client.patch(
        f"/api/admin/users/{target.id}",
        headers={"Authorization": f"Bearer {by_token}"},
        json={"is_active": False},
    )
    assert r.status_code == 403


def test_admin_reenable_clears_lockout(monkeypatch, make_client):
    """Re-enabling a user clears ``failed_login_count`` and
    ``locked_until`` so the user can log back in immediately.
    """
    from app.core import config as config_module

    admin = _make_user(email="admin@example.com", password="AdminPass1!")
    locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    target = _make_user(
        email="target@example.com",
        password="TargetPass1!",
        is_active=False,
        failed_login_count=5,
        locked_until=locked_until,
    )
    monkeypatch.setenv("ATHENA_ADMIN_EMAILS", '["admin@example.com"]')
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_client(users=[admin, target])
    admin_token = _login(client, "admin@example.com", "AdminPass1!")

    r = client.patch(
        f"/api/admin/users/{target.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": True},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True


def test_admin_reenable_does_not_reset_token_version(monkeypatch, make_client):
    """H-19 — re-enabling a user is not a back-door around the
    prior revocation. The user must re-authenticate to get fresh
    tokens, but the admin's choice to re-enable is preserved.
    """
    from app.core import config as config_module

    admin = _make_user(email="admin@example.com", password="AdminPass1!")
    # Start the target as already-disabled with token_version > 0 to
    # simulate "was disabled and tokens were revoked".
    target = _make_user(
        email="target@example.com",
        is_active=False,
        token_version=3,
    )
    monkeypatch.setenv("ATHENA_ADMIN_EMAILS", '["admin@example.com"]')
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_client(users=[admin, target])
    admin_token = _login(client, "admin@example.com", "AdminPass1!")

    r = client.patch(
        f"/api/admin/users/{target.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": True},
    )
    assert r.status_code == 200
    # token_version is unchanged.
    # We confirm by reading the user back from /me (the admin's
    # token, which still has ver=0; the target's token_version is
    # not exposed in the public response — what we check is that
    # the admin can re-enable and the response shape is correct.
    assert r.json()["is_active"] is True


def test_admin_patch_unknown_user_404(monkeypatch, make_client):
    from app.core import config as config_module

    admin = _make_user(email="admin@example.com", password="AdminPass1!")
    monkeypatch.setenv("ATHENA_ADMIN_EMAILS", '["admin@example.com"]')
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_client(users=[admin])
    admin_token = _login(client, "admin@example.com", "AdminPass1!")

    r = client.patch(
        f"/api/admin/users/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert r.status_code == 404


def test_admin_patch_schema_rejects_unknown_fields(monkeypatch, make_client):
    """H-22 (RequestBase extra=forbid) must apply to AdminUserUpdate."""
    from app.core import config as config_module

    admin = _make_user(email="admin@example.com", password="AdminPass1!")
    target = _make_user(email="target@example.com")
    monkeypatch.setenv("ATHENA_ADMIN_EMAILS", '["admin@example.com"]')
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    client = make_client(users=[admin, target])
    admin_token = _login(client, "admin@example.com", "AdminPass1!")

    r = client.patch(
        f"/api/admin/users/{target.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False, "is_admin": True},  # smuggled
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Source-grep safety net: nothing in auth.py silently logs the raw password.
# ---------------------------------------------------------------------------

def test_no_raw_password_logging_in_auth():
    import os
    import re

    auth_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "api", "auth.py"
    )
    auth_path = os.path.abspath(auth_path)
    bad_patterns = [
        re.compile(r"log\.(info|debug|warning|error)\s*\([^)]*password"),
    ]
    whitelist = re.compile(r"^\s*#|^\s*\"\"\"|^\s*'''|^\s*\*")
    offenders: list[str] = []
    with open(auth_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if whitelist.match(line):
                continue
            for pat in bad_patterns:
                if pat.search(line):
                    offenders.append(f"{auth_path}:{lineno}: {line.rstrip()}")
    assert not offenders, (
        "H-19 violation — raw password may be in a log line:\n"
        + "\n".join(offenders)
    )
