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

import ipaddress
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
        if not value:
            continue
        candidate = str(value).strip()
        # A SOCKS entry is advertised without a scheme; defaulting it to http
        # would hand a SOCKS endpoint to an HTTP CONNECT proxy.
        if key == "socks" and "://" not in candidate:
            candidate = f"socks5://{candidate}"
        normalized = _normalize(candidate)
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


FAKE_IP_ENV_VAR = "ENVOY_FAKE_IP_RANGES"

#: RFC 2544 benchmarking space. A TUN proxy in fake-IP mode (Clash/mihomo,
#: Surge, Loon, Shadowrocket by default) answers its own DNS with a placeholder
#: from this pool and routes the connection by the name it was asked for, so the
#: answer never names the destination. Other pools differ, hence the override.
DEFAULT_FAKE_IP_RANGES = "198.18.0.0/15"


def fake_ip_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """The address pools a fake-IP DNS answer may come from, in configured order."""
    networks = []
    for item in os.getenv(FAKE_IP_ENV_VAR, DEFAULT_FAKE_IP_RANGES).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # A malformed entry must not disable the guard; the default pool
            # still applies when the variable is empty or unusable.
            continue
    return tuple(networks)


def is_fake_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether a resolved address can only be a fake-IP placeholder.

    This answers a question about a DNS *answer*, never about a URL written by a
    caller: a literal ``http://198.18.0.1/`` still has to be refused, because a
    pool address typed by hand is not a name a proxy will route.
    """
    return any(
        address.version == network.version and address in network
        for network in fake_ip_networks()
    )
