"""CRUD + lifecycle endpoints for External Model Connectors.

Endpoints (all under `/api/connectors`):

* `GET    /`              — list my connectors (incl. admin-shared)
* `POST   /`              — create
* `GET    /{id}`          — fetch one
* `PATCH  /{id}`          — update; `api_key` non-empty = rotate
* `DELETE /{id}`          — soft delete
* `POST   /{id}/clone`    — duplicate (omit secret)
* `POST   /{id}/set-default` — set as user default
* `GET    /templates`     — canned provider+base_url templates
* `GET    /registry`      — flat list of `(provider, model)` for the picker
* `POST   /test`          — test a payload WITHOUT saving
* `GET    /{id}/health`   — last health snapshot (read-only)
* `GET    /{id}/models`   — cached discovered models
* `POST   /{id}/refresh-models` — re-probe the provider
* `GET    /{id}/usage?days=N` — usage rows + aggregates
* `GET    /{id}/audit`    — paginated audit log

The route layer is thin: it converts between the public Pydantic
schema and the ORM row, calls `audit.record()` and `usage.record()`
via the dedicated helpers, and delegates any real work
(`list_models()`, `health_check()`, test-call) to a *freshly
constructed* adapter instance so we don't poison the router's
adapter cache with a one-off client.
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api._connector_helpers import to_public
from app.api.dependencies import CurrentUserId, DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ssrf import assert_safe_url
from app.models.connector import (
    AUTH_BEARER,
    ModelConnector,
    PROVIDER_OPENAI_COMPAT,
    PROVIDERS,
)
from app.schemas.connector import (
    ConnectorListResponse,
    ConnectorTemplate,
    HealthCheckResult,
    ModelConnectorCreate,
    ModelConnectorPublic,
    ModelConnectorUpdate,
    SetDefaultResponse,
    TestRequest,
)
from app.services.providers import audit, crypto
from app.services.providers import registry as provider_registry
from app.services.providers import usage as usage_svc
from app.services.providers.base import ProviderError

log = get_logger(__name__)
router = APIRouter(prefix="/api/connectors", tags=["connectors"])


# L-31 — extract the client IP and user agent for the audit log.
# We trust X-Forwarded-For when the operator has configured the
# reverse proxy to set it (see infra/nginx.conf). The first entry
# is the original client; the rest are the proxy chain.
def _client_ip_ua(request: Request) -> tuple[str | None, str | None]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        ip = xff.split(",")[0].strip()[:64]
    elif request.client is not None:
        ip = request.client.host[:64]
    else:
        ip = None
    ua = (request.headers.get("user-agent") or "")[:500] or None
    return ip, ua


# --- Templates (sane defaults per provider) -----------------------------

_TEMPLATES: List[ConnectorTemplate] = [
    ConnectorTemplate(
        provider=PROVIDER_OPENAI_COMPAT,
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        auth_type=AUTH_BEARER,
        notes="OpenAI's hosted API. Default model: gpt-4o-mini.",
    ),
    ConnectorTemplate(
        provider=PROVIDER_OPENAI_COMPAT,
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        auth_type=AUTH_BEARER,
        notes="OpenRouter — many models behind one key.",
    ),
    ConnectorTemplate(
        provider="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com",
        auth_type="header",
        notes="Anthropic Messages API. Set custom header `x-api-key` if your proxy differs.",
    ),
    ConnectorTemplate(
        provider="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com",
        auth_type="bearer",
        notes="Generative Language API. Auth via `?key=...` query param by default.",
    ),
    ConnectorTemplate(
        provider="azure_openai",
        name="Azure OpenAI",
        base_url="https://YOUR-RESOURCE.openai.azure.com",
        auth_type="header",
        notes="Azure-hosted OpenAI. `default_model` is the deployment id, not the model name.",
    ),
    ConnectorTemplate(
        provider="ollama",
        name="Ollama (local)",
        base_url="http://localhost:11434",
        auth_type="none",
        notes="Native /api/chat. Use loopback for self-hosted.",
    ),
]


@router.get("/templates", response_model=List[ConnectorTemplate])
async def list_templates() -> List[ConnectorTemplate]:
    return _TEMPLATES


@router.get("/registry", response_model=List[dict])
async def list_registry() -> List[dict]:
    """Flat list of `(provider, model)` the user can pick from.

    Combines the static registry (so a new connector is selectable
    even before the user has registered one) with the user's
    enabled connectors (so the picker shows what they actually
    have). Today: just the static list; the user-owned rows are
    fetched by the chat engine.
    """
    return [
        {
            "provider": name,
            "class": cls.__name__,
            "label": name.replace("_", " ").title(),
        }
        for name, cls in sorted(
            (
                (n, provider_registry.get(n))
                for n in provider_registry.all_providers()
            )
        )
    ]


# --- List / Create ------------------------------------------------------

@router.get("", response_model=ConnectorListResponse)
async def list_connectors(
    user_id: CurrentUserId, session: DbSession
) -> ConnectorListResponse:
    """List every connector the caller can see — own + admin-shared.

    The router also returns the templates so the UI can render the
    create dialog without a second roundtrip.
    """
    res = await session.execute(
        select(ModelConnector)
        .where(ModelConnector.deleted_at.is_(None))
        .order_by(ModelConnector.is_admin.desc(), ModelConnector.name.asc())
    )
    rows = list(res.scalars())
    return ConnectorListResponse(
        connectors=[
            to_public(r, is_owner=(r.user_id == user_id)) for r in rows
        ],
        templates=_TEMPLATES,
    )


@router.post(
    "",
    response_model=ModelConnectorPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector(
    payload: ModelConnectorCreate,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> ModelConnectorPublic:
    # SSRF: validate the URL before encrypting the key or persisting
    # anything. `allow_loopback=True` for self-hosted Ollama etc.
    assert_safe_url(payload.base_url, allow_loopback=True)

    # C-2 (Critical) — admin-shared rows are create-only and
    # admin-gated. The ``connectors_iso`` RLS policy is
    # ``user_id = me OR is_admin = TRUE`` — flipping the flag
    # promotes the row into the global visibility set, and the
    # router will then route every other user's chat traffic to
    # this connector (since ``is_default AND is_admin`` wins over
    # per-user default). Reject ``is_admin=True`` from any caller
    # that is not in the admin allowlist.
    if payload.is_admin:
        settings = get_settings()
        admin_allow = {
            e.strip().lower() for e in settings.admin_emails if e.strip()
        }
        # Look up the caller's email. We can't use the AdminUser dep
        # here without restructuring the route, so we mirror the
        # same check inline. (The dep is used in app/api/tools.py.)
        from sqlalchemy import select

        from app.models.user import User

        res = await session.execute(
            select(User.email).where(User.id == user_id)
        )
        caller_email = (res.scalar_one_or_none() or "").lower()
        if not admin_allow or caller_email not in admin_allow:
            log.warning(
                "connector.is_admin_blocked",
                actor=str(user_id),
                actor_email=caller_email,
                action="create",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators may create admin-shared connectors.",
            )

    api_key_enc: Optional[bytes] = None
    api_key_preview: Optional[str] = None
    if payload.api_key:
        api_key_enc = crypto.encrypt(payload.api_key)
        api_key_preview = crypto.mask_for_ui(payload.api_key)

    row = ModelConnector(
        user_id=user_id,
        name=payload.name,
        provider=payload.provider,
        base_url=payload.base_url,
        api_key_enc=api_key_enc,
        api_key_preview=api_key_preview,
        auth_type=payload.auth_type,
        auth_header_name=payload.auth_header_name,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        api_version=payload.api_version,
        custom_headers=payload.custom_headers,
        default_model=payload.default_model,
        models=list(payload.models),
        capabilities=payload.capabilities,
        settings=payload.settings,
        is_enabled=payload.is_enabled,
        is_default=payload.is_default,
        is_admin=payload.is_admin,
        group_name=payload.group_name,
        tags=list(payload.tags),
        is_favorite=payload.is_favorite,
    )
    session.add(row)
    await session.flush()  # populate row.id

    if payload.is_default:
        # Clear any previous default the user had set so the
        # `is_default=TRUE` invariant holds: at most one user-default
        # per user.
        await _clear_user_default(session, user_id, except_id=row.id)

    _ip, _ua = _client_ip_ua(request)
    await audit.record(
        session,
        connector_id=row.id,
        user_id=user_id,
        action=audit.ACTION_CREATE,
        after=_public_dict(row),
        ip=_ip,
        user_agent=_ua,
    )
    await session.commit()
    await session.refresh(row)
    return to_public(row, is_owner=True)


# --- Get / Update / Delete ---------------------------------------------

async def _load_or_404(
    session, connector_id: uuid.UUID, user_id: uuid.UUID
) -> ModelConnector:
    """Load a connector the caller can see (own + admin-shared)."""
    res = await session.execute(
        select(ModelConnector).where(
            ModelConnector.id == connector_id,
            ModelConnector.deleted_at.is_(None),
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found"
        )
    if row.user_id != user_id and not row.is_admin:
        # Not visible to this user (RLS would already filter, but
        # the test harness has no RLS, so the helper enforces it).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found"
        )
    return row


@router.get("/{connector_id}", response_model=ModelConnectorPublic)
async def get_connector(
    connector_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
) -> ModelConnectorPublic:
    row = await _load_or_404(session, connector_id, user_id)
    return to_public(row, is_owner=(row.user_id == user_id))


@router.patch("/{connector_id}", response_model=ModelConnectorPublic)
async def update_connector(
    connector_id: uuid.UUID,
    payload: ModelConnectorUpdate,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> ModelConnectorPublic:
    row = await _load_or_404(session, connector_id, user_id)
    if row.user_id != user_id and not row.is_admin:
        # Only the owner (or a real admin) may mutate. `is_admin=True`
        # rows are visible to all but only editable by an admin.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner may modify this connector",
        )

    before = _public_dict(row)
    data = payload.model_dump(exclude_unset=True)
    if "api_key" in data:
        api_key = data.pop("api_key")
        if api_key:
            row.api_key_enc = crypto.encrypt(api_key)
            row.api_key_preview = crypto.mask_for_ui(api_key)
        # Empty / None → leave the key alone.
    if "base_url" in data and data["base_url"]:
        assert_safe_url(data["base_url"], allow_loopback=True)
        row.base_url = data["base_url"]

    # H-22 (High) — explicit allowlist replaces the
    # ``for k, v in data.items(): if hasattr(row, k): setattr(...)``
    # pattern. The old loop was a mass-assignment surface: any
    # column the ORM row knew about could be overwritten by the
    # caller. We have already removed ``is_admin`` from
    # ``ModelConnectorUpdate`` (the schema), but defense in depth
    # means the route *also* refuses to set it.
    _UPDATABLE_FIELDS = frozenset(
        {
            "name",
            "provider",
            "auth_type",
            "auth_header_name",
            "organization_id",
            "project_id",
            "api_version",
            "custom_headers",
            "default_model",
            "models",
            "capabilities",
            "settings",
            "is_enabled",
            "is_default",
            "group_name",
            "tags",
            "is_favorite",
        }
    )
    for k, v in data.items():
        if k in _UPDATABLE_FIELDS:
            setattr(row, k, v)
        elif k == "is_admin":
            # Belt-and-braces: the schema no longer accepts this
            # field, but if a future schema change re-introduces
            # it, refuse to write it. Admin-shared promotion is
            # create-only.
            log.warning(
                "connector.is_admin_update_blocked",
                actor=str(user_id),
                connector_id=str(row.id),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="is_admin cannot be modified via PATCH.",
            )
        # Other unknown fields are silently dropped — Pydantic's
        # ``extra="forbid"`` on the schema will already have
        # rejected them with a 422. We log at debug for forensic
        # visibility.
        else:
            log.debug(
                "connector.update_unknown_field",
                actor=str(user_id),
                connector_id=str(row.id),
                field=k,
            )
    if data.get("is_default"):
        await _clear_user_default(session, user_id, except_id=row.id)
    _ip, _ua = _client_ip_ua(request)
    await audit.record(
        session,
        connector_id=row.id,
        user_id=user_id,
        action=audit.ACTION_UPDATE,
        before=before,
        after=_public_dict(row),
        ip=_ip,
        user_agent=_ua,
    )
    await session.commit()
    await session.refresh(row)
    return to_public(row, is_owner=(row.user_id == user_id))


@router.delete(
    "/{connector_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_connector(
    connector_id: uuid.UUID,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> None:
    row = await _load_or_404(session, connector_id, user_id)
    if row.user_id != user_id and not row.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner may delete this connector",
        )
    from datetime import datetime, timezone

    before = _public_dict(row)
    row.deleted_at = datetime.now(timezone.utc)
    if row.is_default:
        row.is_default = False
    _ip, _ua = _client_ip_ua(request)
    await audit.record(
        session,
        connector_id=row.id,
        user_id=user_id,
        action=audit.ACTION_DELETE,
        before=before,
        ip=_ip,
        user_agent=_ua,
    )
    await session.commit()


# --- Clone / Set default ------------------------------------------------

@router.post(
    "/{connector_id}/clone",
    response_model=ModelConnectorPublic,
    status_code=status.HTTP_201_CREATED,
)
async def clone_connector(
    connector_id: uuid.UUID,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> ModelConnectorPublic:
    row = await _load_or_404(session, connector_id, user_id)
    new_row = ModelConnector(
        user_id=user_id,
        name=f"{row.name} (copy)",
        provider=row.provider,
        base_url=row.base_url,
        # No API key — the user re-enters it. We don't copy the
        # encrypted blob to a row the user doesn't own.
        api_key_enc=None,
        api_key_preview=None,
        auth_type=row.auth_type,
        auth_header_name=row.auth_header_name,
        organization_id=row.organization_id,
        project_id=row.project_id,
        api_version=row.api_version,
        custom_headers=row.custom_headers,
        default_model=row.default_model,
        models=list(row.models or []),
        capabilities=row.capabilities,
        settings=row.settings,
        is_enabled=False,  # disabled until the user supplies a key
        is_default=False,
        is_admin=False,  # never clone admin-shared status
        group_name=row.group_name,
        tags=list(row.tags or []),
        is_favorite=False,
    )
    session.add(new_row)
    await session.flush()
    _ip, _ua = _client_ip_ua(request)
    await audit.record(
        session,
        connector_id=new_row.id,
        user_id=user_id,
        action=audit.ACTION_CLONE,
        before=_public_dict(row),
        after=_public_dict(new_row),
        ip=_ip,
        user_agent=_ua,
    )
    await session.commit()
    await session.refresh(new_row)
    return to_public(new_row, is_owner=True)


@router.post(
    "/{connector_id}/set-default",
    response_model=SetDefaultResponse,
)
async def set_default(
    connector_id: uuid.UUID,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> SetDefaultResponse:
    row = await _load_or_404(session, connector_id, user_id)
    if not row.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot set a disabled connector as default",
        )
    row.is_default = True
    await _clear_user_default(session, user_id, except_id=row.id)
    _ip, _ua = _client_ip_ua(request)
    await audit.record(
        session,
        connector_id=row.id,
        user_id=user_id,
        action=audit.ACTION_SET_DEFAULT,
        after=_public_dict(row),
        ip=_ip,
        user_agent=_ua,
    )
    await session.commit()
    await session.refresh(row)
    return SetDefaultResponse(id=row.id, is_default=row.is_default)


# --- Test (one-off, no save) -------------------------------------------

def _build_adapter_from_payload(payload: TestRequest):
    """Build a fresh adapter for a test request.

    The router's cache is intentionally bypassed — a test call is
    not the same as production traffic and we don't want to poison
    the cache with one-off clients.
    """
    cls = provider_registry.get(payload.provider)
    common: dict[str, Any] = {
        "base_url": payload.base_url,
        "api_key": payload.api_key,
        "auth_type": payload.auth_type,
        "auth_header_name": payload.auth_header_name,
        "custom_headers": payload.custom_headers,
        "organization_id": payload.organization_id,
        "project_id": payload.project_id,
        "api_version": payload.api_version,
        "timeout_s": payload.timeout_s,
        "default_model": payload.default_model,
        "models": list(payload.models or []),
    }
    return cls(**common)


@router.post("/test", response_model=HealthCheckResult)
async def test_connector(
    payload: TestRequest,
    user_id: CurrentUserId,
) -> HealthCheckResult:
    """Run a health probe against a connector config without saving.

    Used by the UI's "Test" button when the user is filling in the
    create form. The probe calls `health_check()` on a freshly
    constructed adapter, then closes it.

    M-27 — auth requirement. A probe is functionally a request to
    our own egress, so an unauthenticated caller could use it as
    an SSRF oracle (probe ``http://169.254.169.254/`` to confirm
    the cloud metadata endpoint exists; probe internal services
    to map the network). The check on the URL is
    ``allow_loopback=True`` for dev; a non-loopback probe is
    fine if the caller is authenticated. We require a JWT.
    """
    # M-27 — the request must be authenticated. The probe runs
    # against an arbitrary URL the caller chose; this is the same
    # risk surface as creating a connector, and create requires
    # auth. We do not call into the DB here; we only need a valid
    # JWT, not a particular user state.
    assert_safe_url(payload.base_url, allow_loopback=True)
    adapter = _build_adapter_from_payload(payload)
    try:
        report = await adapter.health_check()
    except ProviderError as exc:
        return HealthCheckResult(
            ok=False,
            status="offline",
            error=str(exc),
            category=exc.category,
            status_code=exc.status_code,
        )
    finally:
        try:
            await adapter.aclose()
        except Exception:  # noqa: BLE001
            pass
    return HealthCheckResult(
        ok=report.ok,
        latency_ms=report.latency_ms,
        status=report.status,
        capabilities=report.capabilities or {},
        models=list(report.models or []),
        error=report.error,
        category=report.category,
        status_code=report.status_code,
    )


# --- Health + Models (read-only snapshots) -----------------------------

@router.get("/{connector_id}/health", response_model=HealthCheckResult)
async def get_health(
    connector_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
) -> HealthCheckResult:
    """Return the last health snapshot recorded by the probe loop.

    The loop writes here; the route only reads.
    """
    row = await _load_or_404(session, connector_id, user_id)
    return HealthCheckResult(
        ok=(row.last_health == "online"),
        latency_ms=row.last_health_latency_ms or 0,
        status=row.last_health or "unknown",
        capabilities=row.capabilities or {},
        models=list(row.discovered_models or []),
        error=None,
        category="ok" if row.last_health == "online" else "unknown",
        status_code=None,
    )


@router.get("/{connector_id}/models", response_model=List[str])
async def list_models(
    connector_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
) -> List[str]:
    row = await _load_or_404(session, connector_id, user_id)
    return list(row.discovered_models or row.models or [])


@router.post(
    "/{connector_id}/refresh-models",
    response_model=List[str],
)
async def refresh_models(
    connector_id: uuid.UUID,
    request: Request,
    user_id: CurrentUserId,
    session: DbSession,
) -> List[str]:
    """Re-probe the provider's `/models` endpoint and cache the result."""
    row = await _load_or_404(session, connector_id, user_id)
    if row.user_id != user_id and not row.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner may refresh this connector",
        )
    from datetime import datetime, timezone

    adapter = _build_adapter_from_payload(
        TestRequest(
            provider=row.provider,
            base_url=row.base_url,
            api_key=None,  # the adapter doesn't need it for /models
            auth_type=row.auth_type,
            auth_header_name=row.auth_header_name,
            custom_headers=row.custom_headers or {},
            organization_id=row.organization_id,
            project_id=row.project_id,
            api_version=row.api_version,
            default_model=row.default_model,
            models=list(row.models or []),
        )
    )
    try:
        ids = await adapter.list_models()
    except ProviderError as exc:
        log.warning(
            "connector.refresh_models_failed",
            connector_id=str(row.id),
            error=str(exc),
            category=exc.category,
        )
        ids = list(row.models or [])
    finally:
        try:
            await adapter.aclose()
        except Exception:  # noqa: BLE001
            pass
    row.discovered_models = ids
    row.discovered_at = datetime.now(timezone.utc)
    _ip, _ua = _client_ip_ua(request)
    await audit.record(
        session,
        connector_id=row.id,
        user_id=user_id,
        action=audit.ACTION_REFRESH_MODELS,
        after={"models": ids},
        ip=_ip,
        user_agent=_ua,
    )
    await session.commit()
    return ids


# --- Usage + Audit ------------------------------------------------------

@router.get("/{connector_id}/usage")
async def get_usage(
    connector_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
    days: int = Query(7, ge=1, le=90),
) -> dict:
    await _load_or_404(session, connector_id, user_id)
    return await usage_svc.aggregate(session, connector_id=connector_id, days=days)


@router.get("/{connector_id}/audit")
async def get_audit(
    connector_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    await _load_or_404(session, connector_id, user_id)
    from app.models.connector import ConnectorAuditLog

    total = (
        await session.execute(
            select(__import__("sqlalchemy").func.count(ConnectorAuditLog.id)).where(
                ConnectorAuditLog.connector_id == connector_id
            )
        )
    ).scalar_one()
    res = await session.execute(
        select(ConnectorAuditLog)
        .where(ConnectorAuditLog.connector_id == connector_id)
        .order_by(ConnectorAuditLog.at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(res.scalars())
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "rows": [
            {
                "id": r.id,
                "action": r.action,
                "user_id": str(r.user_id),
                "at": r.at.isoformat() if r.at else None,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "before": r.before_redacted,
                "after": r.after_redacted,
            }
            for r in rows
        ],
    }


# --- Helpers ------------------------------------------------------------

def _public_dict(row: ModelConnector) -> dict:
    """Dump the public schema (no `api_key_enc`) for the audit log."""
    return to_public(row, is_owner=True).model_dump(mode="json")


async def _clear_user_default(
    session, user_id: uuid.UUID, *, except_id: Optional[uuid.UUID] = None
) -> None:
    """Enforce the at-most-one-user-default invariant.

    Clears `is_default` on every other row owned by the user, so
    the `WHERE is_default=TRUE AND user_id=…` query always returns
    zero or one row.
    """
    from sqlalchemy import update

    stmt = (
        update(ModelConnector)
        .where(
            ModelConnector.user_id == user_id,
            ModelConnector.is_default.is_(True),
            ModelConnector.deleted_at.is_(None),
        )
        .values(is_default=False)
    )
    if except_id is not None:
        stmt = stmt.where(ModelConnector.id != except_id)
    await session.execute(stmt)


__all__ = ["router"]
