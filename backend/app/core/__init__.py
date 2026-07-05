"""Core cross-cutting modules: config, db, security, cache, deps, logging."""
from app.core.cache import (
    HITS,
    MISSES,
    delete_pattern,
    get_client,
    get_json,
    invalidate_user,
    set_json,
)
from app.core.config import Settings, get_settings, settings
from app.core.database import (
    Base,
    SessionLocal,
    engine,
    get_db,
    reset_rls_user,
    set_rls_user,
    user_scoped_session,
)
from app.core.deps import (
    CurrentUserId,
    DbSession,
    get_current_user_id,
    oauth2_scheme,
)
from app.core.logging import configure_logging, get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

__all__ = [
    "Settings",
    "settings",
    "get_settings",
    "Base",
    "SessionLocal",
    "engine",
    "get_db",
    "set_rls_user",
    "reset_rls_user",
    "user_scoped_session",
    "CurrentUserId",
    "DbSession",
    "get_current_user_id",
    "oauth2_scheme",
    "configure_logging",
    "get_logger",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_client",
    "get_json",
    "set_json",
    "delete_pattern",
    "invalidate_user",
    "HITS",
    "MISSES",
]
