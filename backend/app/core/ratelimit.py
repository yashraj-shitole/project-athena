"""Fixed-window rate-limiter backed by Redis.

Why fixed-window: simple, atomic via ``INCR`` + ``EXPIRE``, fail-open
when Redis is down. Sliding-window is more accurate but adds a Lua
script and a hot dependency. For the rate we are protecting (auth
endpoints) the per-window accuracy of "10 / 60s" is plenty — a
worst-case burst of 20 in 2s at the window boundary is acceptable
for the threat model (slow-burn brute force on /login, mass
account-creation on /register).

Design notes
------------

* **Fail-open on Redis errors.** A Redis outage MUST NOT take down
  /login or /register — the failure mode of "rate-limit broke
  auth" is much worse than "rate-limit didn't fire for 30s". The
  helper logs a warning and returns ``(allowed=True, remaining=N)``
  so the route is unaffected.

* **Keyed by IP, not by user.** The auth endpoints are anonymous
  (no JWT yet); we cannot key on a user_id. We use the client IP
  from the request. If the deployment is behind a reverse proxy
  the operator MUST set ``X-Forwarded-For`` trust correctly —
  see ``infra/docker-compose.yml`` and ``infra/nginx.conf`` for
  the ``proxy_set_header X-Forwarded-For $remote_addr`` line.

* **Per-route policies.** The route layer passes the policy name
  (``login``, ``register``, ``refresh``) and the helper looks up the
  configured limit. Adding a new route is a config change, not a
  code change.

* **No Starlette middleware.** Middleware is global — every
  request gets the same treatment — and we need different limits
  per route. A per-route dependency is the right seam.
"""
from __future__ import annotations

import time
from typing import Tuple

from fastapi import Depends, HTTPException, Request, status

from app.core.cache import get_client
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# ---- policy table (FR-36 / NFR-22) ----
# Window + max per window. Conservative defaults; the operator can
# override per-deployment via env vars (see Settings below).
#
#   login    : 10 / 60s  — blunts brute-force password guessing
#   register :  3 / 60s  — blunts mass account creation
#   refresh  : 30 / 60s  — blunts refresh-token brute force
_POLICIES: dict[str, Tuple[int, int]] = {
    "login": (60, 10),
    "register": (60, 3),
    "refresh": (60, 30),
}


def _policies_from_settings() -> dict[str, Tuple[int, int]]:
    """Build the policy table from settings. The defaults above
    are the floor — operators can tighten (or relax) per policy
    via env vars.
    """
    s = get_settings()
    return {
        "login": (60, s.rate_limit_login_per_min),
        "register": (60, s.rate_limit_register_per_min),
        "refresh": (60, s.rate_limit_refresh_per_min),
    }


async def _client_ip(request: Request) -> str:
    """Extract the client IP, trusting the standard reverse-proxy
    header in dev/staging and falling back to the socket peer.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # X-Forwarded-For: client, proxy1, proxy2 — the left-most
        # is the original client. If the operator has misconfigured
        # the proxy chain, the left-most value is what we get.
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


async def check_rate_limit(
    request: Request, policy: str
) -> Tuple[bool, int]:
    """Atomically increment the counter for ``(ip, policy, window)``
    and return ``(allowed, remaining)``.

    The window is computed from the current epoch second; two
    requests in the same ``window_seconds`` window share a key.
    """
    policies = _policies_from_settings()
    if policy not in policies:
        # Unknown policy name — log and fail open. The route caller
        # is at fault; the request still goes through.
        log.warning("rate_limit.unknown_policy", policy=policy)
        return True, 0
    window_s, max_n = policies[policy]
    now = int(time.time())
    window_id = now // window_s
    ip = await _client_ip(request)
    key = f"athena:rl:{policy}:{ip}:{window_id}"
    try:
        client = get_client()
        n = await client.incr(key)
        if n == 1:
            # First request in this window — set the TTL. We do
            # this in a separate call rather than ``SET ... EX``
            # so the INCR is still atomic.
            await client.expire(key, window_s)
        remaining = max(0, max_n - int(n))
        if int(n) > max_n:
            return False, 0
        return True, remaining
    except Exception as exc:  # noqa: BLE001
        # Fail-open: a Redis outage MUST NOT take down auth.
        log.warning("rate_limit.redis_error", policy=policy, error=str(exc))
        return True, max_n


def rate_limit(policy: str):
    """FastAPI dependency factory: rate-limit the current request
    by ``policy`` name. Refuses with 429 when over budget.

    Usage::

        @router.post("/login", dependencies=[Depends(rate_limit("login"))])
        async def login(...): ...
    """

    async def _dep(request: Request) -> None:
        allowed, remaining = await check_rate_limit(request, policy)
        if not allowed:
            # Compute the seconds until the next window opens so the
            # client can back off intelligently.
            now = int(time.time())
            policies = _policies_from_settings()
            window_s, _ = policies[policy]
            retry_after = window_s - (now % window_s)
            log.warning(
                "rate_limit.exceeded",
                policy=policy,
                ip=await _client_ip(request),
                path=request.url.path,
                retry_after_s=retry_after,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep


# Convenience aliases: the actual Depends() call (rather than an
# Annotated alias) so they can be used directly in the
# ``dependencies=`` list of any FastAPI route. Using
# ``Annotated[None, Depends(...)]`` here is unsafe because
# ``typing.Annotated[None, ...]`` is interpreted as plain ``None``
# at module load time and FastAPI cannot introspect it.
RateLimitLogin = Depends(rate_limit("login"))
RateLimitRegister = Depends(rate_limit("register"))
RateLimitRefresh = Depends(rate_limit("refresh"))
