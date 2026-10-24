"""Outbound connection policy shared by the HTTP and browser read paths.

Envoy connects directly unless the operator asks for a proxy. A system or
environment proxy is only a fallback: a source that decides what to serve from
the egress address must not have that decision changed by unrelated traffic,
and a block page reached through a proxy would otherwise look like a property
of the source itself.

``ENVOY_BROWSER_PROXY`` pins the policy. A proxy URL forces that proxy and
disables the fallback; ``none``, ``off``, ``direct``, ``false`` or ``0`` force
a direct connection and never fall back.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies

ENV_VAR = "ENVOY_BROWSER_PROXY"

#: Values that mean "never use a proxy".
DIRECT_VALUES = frozenset({"none", "off", "direct", "false", "0"})

_SCHEME_PORTS = {"http": 80, "https": 443, "socks5": 1080}


def _configured() -> str:
    return os.getenv(ENV_VAR, "").strip()


def direct_only() -> bool:
    """True when the operator pinned Envoy to a direct connection."""
    return _configured().lower() in DIRECT_VALUES


def _normalize(candidate: str) -> str | None:
    value = candidate if "://" in candidate else f"http://{candidate}"
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in _SCHEME_PORTS
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or _SCHEME_PORTS[parsed.scheme.lower()]
    except (KeyError, ValueError):
        return None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return urlunsplit((parsed.scheme.lower(), f"{host}:{port}", "", "", ""))


def explicit_proxy() -> str | None:
    """The proxy the operator named, or None when none is pinned."""
    value = _configured()
    if not value or value.lower() in DIRECT_VALUES:
        return None
    return _normalize(value)


def detected_proxy() -> str | None:
    """The proxy the environment or operating system advertises, if usable."""
    if direct_only():
        return None
    proxies = getproxies()
    for key in ("https", "http", "all", "socks"):
        value = proxies.get(key)
        if value:
            normalized = _normalize(str(value))
            if normalized:
                return normalized
    return None


def proxy_plan() -> list[str | None]:
    """Ordered connection attempts: direct first, then a fallback proxy."""
    pinned = explicit_proxy()
    if pinned:
        return [pinned]
    if direct_only():
        return [None]
    fallback = detected_proxy()
    return [None, fallback] if fallback else [None]


def proxy_configured() -> bool:
    """Whether any proxy is available to Envoy at all."""
    return explicit_proxy() is not None or detected_proxy() is not None
