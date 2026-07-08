"""SSRF guard for outbound URLs (MCP attach + HTTP tool handlers).

Blocks non-http(s) schemes, loopback, link-local, private, and cloud
metadata addresses. Resolves hostnames and checks every resolved IP,
and also rejects hostnames that *look* like IPs in ranges we block
(defense against the `http://127.0.0.1` literal and against DNS
rebinding is best-effort here — full DNS-rebinding mitigation would
re-pin the resolved IP on the httpx transport, which is Phase 2).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.logging import get_logger

log = get_logger(__name__)

_BLOCKED_HOST_LITERALS = {
    # The loopback hostnames are only blocked when ``allow_loopback``
    # is False. With ``allow_loopback=True`` (the call site for
    # self-hosted Ollama etc.) they fall through to the IP-resolve
    # check below, which permits 127.0.0.0/8 and ::1.
    "localhost",
    "ip6-localhost",
    # Cloud metadata endpoints are NEVER loopback — they live on
    # link-local addresses but use a memorable hostname on every
    # major cloud. Block them regardless of ``allow_loopback``.
    "metadata.google.internal",
}

# Cloud metadata hostnames whose resolution we always block regardless of IP.
_BLOCKED_HOST_PATTERNS = (
    "metadata.google.internal",
)

# AWS / Azure / GCP / Aliyun metadata IPs
_BLOCKED_IPS = {
    ipaddress.ip_network("169.254.169.254/32"),  # AWS / Azure / GCP / Aliyun
    ipaddress.ip_network("169.254.170.2/32"),    # AWS ECS task metadata
    ipaddress.ip_network("fd00:ec2::254/128"),   # AWS IPv6 metadata (host bit)
    ipaddress.ip_network("100.100.100.200/32"),  # Aliyun? (kept conservative)
}

_ALLOWED_SCHEMES = {"http", "https"}

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),        # loopback v4
    ipaddress.ip_network("::1/128"),            # loopback v6
    ipaddress.ip_network("10.0.0.0/8"),         # private
    ipaddress.ip_network("172.16.0.0/12"),      # private
    ipaddress.ip_network("192.168.0.0/16"),     # private
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("fc00::/7"),           # unique-local v6
    ipaddress.ip_network("fe80::/10"),          # link-local v6
    ipaddress.ip_network("0.0.0.0/8"),         # "this network"
    ipaddress.ip_network("::/128"),            # unspecified v6
    ipaddress.ip_network("::ffff:0:0/96"),      # v4-mapped (also check inner v4)
    *_BLOCKED_IPS,
)


class SSRFError(ValueError):
    """Raised when a URL is not safe to fetch."""


def _ip_is_blocked(ip: ipaddress.ip_address) -> bool:
    for net in _BLOCKED_NETWORKS:
        if net.version == ip.version and ip in net:
            return True
    # v4-mapped v6: also inspect the embedded v4 address.
    if isinstance(ip, ipaddress.IPv6Address):
        try:
            mapped = ip.ipv4_mapped
        except ValueError:
            mapped = None
        if mapped is not None:
            for net in _BLOCKED_NETWORKS:
                if net.version == mapped.version and mapped in net:
                    return True
    return False


def assert_safe_url(url: str, *, allow_loopback: bool = False) -> None:
    """Raise SSRFError if `url` is not safe to fetch.

    With `allow_loopback=True`, 127.0.0.1/::1 are permitted (used by the
    dev default Ollama URL — but that one is server-configured, not
    attacker-supplied, so the default Ollama client does NOT route through
    this check).
    """
    if not url or not isinstance(url, str):
        raise SSRFError("empty url")
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise SSRFError(f"unparseable url: {exc}") from exc

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFError(f"scheme not allowed: {parsed.scheme!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFError("no host in url")

    if host in _BLOCKED_HOST_LITERALS:
        # ``localhost`` / ``ip6-localhost`` are the canonical DNS
        # names for the loopback block. We allow them through when
        # ``allow_loopback=True`` (the call site for self-hosted
        # Ollama etc.) so the DNS-name form is treated the same as
        # the IP-literal form (127.0.0.1 / ::1), which is already
        # permitted by the IP check below. Cloud-metadata hostnames
        # are NOT loopback, so they stay blocked either way.
        if allow_loopback and host in ("localhost", "ip6-localhost"):
            pass  # fall through to the IP / resolve check
        else:
            raise SSRFError(f"blocked host: {host}")
    for pat in _BLOCKED_HOST_PATTERNS:
        if host == pat or host.endswith("." + pat):
            raise SSRFError(f"blocked host: {host}")

    # If the host is an IP literal, validate it directly.
    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        host_ip = None
    if host_ip is not None:
        if _ip_is_blocked(host_ip) and not (
            allow_loopback
            and (host_ip in ipaddress.ip_network("127.0.0.0/8")
                 or host_ip in ipaddress.ip_network("::1/128"))
        ):
            raise SSRFError(f"blocked ip literal: {host_ip}")
        return

    # Hostname: resolve and check every A/AAAA record.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"unable to resolve host {host!r}: {exc}") from exc

    if not infos:
        raise SSRFError(f"no addresses for host {host!r}")
    for info in infos:
        sockaddr = info[4]
        try:
            candidate = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            continue
        if _ip_is_blocked(candidate) and not (
            allow_loopback
            and (candidate in ipaddress.ip_network("127.0.0.0/8")
                 or candidate in ipaddress.ip_network("::1/128"))
        ):
            raise SSRFError(f"{host!r} resolves to blocked address {candidate}")


__all__ = ["SSRFError", "assert_safe_url"]