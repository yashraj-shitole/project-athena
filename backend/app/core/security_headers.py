"""M-28 — security headers middleware.

Adds the standard hardening headers to every response:

* ``Strict-Transport-Security`` — only when behind TLS (the
  X-Forwarded-Proto header is ``https`` OR we are not in dev).
* ``X-Content-Type-Options: nosniff`` — disable MIME sniffing.
* ``X-Frame-Options: DENY`` — no clickjacking surface.
* ``Referrer-Policy: no-referrer`` — don't leak URLs to third
  parties on navigation.
* ``Content-Security-Policy`` — restrict script/style sources.
  The default is permissive (self + inline + unsafe-eval for the
  SPA dev server); tighten in prod via env.
* ``Permissions-Policy`` — explicitly deny features the SPA
  does not use (geolocation, camera, microphone, etc.).
* ``Cross-Origin-Opener-Policy: same-origin`` — isolate browsing
  context.
* ``Cross-Origin-Embedder-Policy: require-corp`` — only embed
  same-origin or CORP-tagged resources.

HSTS is only sent in non-dev environments or when the request
came in over TLS. Sending HSTS over plain HTTP is a developer
footgun (the browser pins the HSTS policy for the duration of
``max-age`` even though we cannot actually deliver HTTPS).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_CSP_DEV = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_CSP_PROD = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_PERMISSIONS_POLICY = (
    "geolocation=(), "
    "microphone=(), "
    "camera=(), "
    "payment=(), "
    "usb=(), "
    "magnetometer=(), "
    "gyroscope=(), "
    "accelerometer=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        settings = get_settings()

        # HSTS — only on TLS-terminated requests in non-dev, or
        # unconditionally in prod. The ``include_sub_domains``
        # flag tells the browser to apply the policy to all
        # subdomains; ``preload`` opts into the browser's HSTS
        # preload list (only set if you've submitted to the
        # preload list).
        is_tls = (
            request.headers.get("x-forwarded-proto", "").lower() == "https"
            or request.url.scheme == "https"
        )
        is_prod = settings.environment.lower() in {"prod", "production"}
        if is_tls or is_prod:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )

        # Universal hardening headers (always safe).
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        # COEP is opt-in: it breaks cross-origin embeds, including
        # some legit ones. We send ``credentialless`` so cross-origin
        # resources can still load without credentials.
        response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"

        # CSP — prod is strict; dev allows Vite's HMR and inline.
        response.headers["Content-Security-Policy"] = (
            _CSP_PROD if is_prod else _CSP_DEV
        )

        return response
