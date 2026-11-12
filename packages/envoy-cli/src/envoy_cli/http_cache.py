"""A small on-disk cache for public HTTP response bodies.

Reading the same public URL twice should not download it twice. This module
keeps the body of a successful GET beside the other Envoy state, keyed by the
request identity (url, cookies, and the caller's size cap). A fresh entry can be
served without touching the network; a stale entry still carries its ``ETag``
and ``Last-Modified`` so the next attempt can be turned into a conditional
request and answered with ``304 Not Modified``.

The cache is deliberately boring: one JSON document per key, written through a
temp file and ``os.replace`` so a second process never reads half a body, and
every write is best-effort. A cache that fails must never fail the read that
populated it, so all filesystem errors collapse to ``False``/``None``.

Cookies are part of the key and never part of the entry: the key is a SHA-256
over the request identity, and only the digest reaches disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator

from .storage import resolve_material_root

SCHEMA = "envoy.http-cache/1"
ENV_CACHE_DIR = "ENVOY_HTTP_CACHE_DIR"
ENV_DISABLE = "ENVOY_HTTP_CACHE"
ENV_TTL = "ENVOY_HTTP_CACHE_TTL_SECONDS"
DEFAULT_TTL_SECONDS = 600
MAX_ENTRY_BYTES = 4 * 1024 * 1024

_DISABLED_VALUES = frozenset({"off", "0", "false"})
_CACHE_SUBDIR = "http-cache"
_BUSY_MESSAGE = "Another Envoy process is fetching the same URL"
# Cache-Control directives that forbid keeping the body at all, and the Vary
# values that mean the response depends on credentials this cache does not key
# on. ``Vary: Cookie`` is fine: the key already digests the cookie.
_FORBIDDEN_CACHE_CONTROL = frozenset({"no-store", "private", "no-cache"})
_FORBIDDEN_VARY = frozenset({"*", "authorization"})


def cache_dir() -> Path:
    """Return the directory holding cache entries and their lock files."""

    configured = os.getenv(ENV_CACHE_DIR, "").strip()
    if configured:
        return Path(configured).expanduser()
    return resolve_material_root() / _CACHE_SUBDIR


def cache_enabled() -> bool:
    """Whether the cache may be read or written in this process."""

    value = os.getenv(ENV_DISABLE, "").strip().lower()
    return value not in _DISABLED_VALUES


def entry_key(url: str, *, cookie: str = "", max_bytes: int = 0) -> str:
    """Return the stable filename stem for one request identity.

    The cookie participates only through the digest, so the key separates
    logged-in reads without recording which cookie produced it.
    """

    material = "{}\0{}\0{}".format(url, cookie, int(max_bytes))
    return hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()


def _entry_path(url: str, cookie: str, max_bytes: int) -> Path:
    return cache_dir() / "{}.json".format(entry_key(url, cookie=cookie, max_bytes=max_bytes))


def _lock_path(url: str, cookie: str, max_bytes: int) -> Path:
    return cache_dir() / ".{}.lock".format(entry_key(url, cookie=cookie, max_bytes=max_bytes))


def _ttl_seconds() -> float:
    """Return the freshness window, falling back to the default on bad input."""

    raw = os.getenv(ENV_TTL, "").strip()
    if raw:
        try:
            seconds = int(raw)
        except ValueError:
            seconds = -1
        if seconds > 0:
            return float(seconds)
    return float(DEFAULT_TTL_SECONDS)


def _read_entry(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    if not isinstance(data.get("body"), str):
        return None
    if not isinstance(data.get("fetched_at"), (int, float)):
        return None
    return data


def _entry_age(data: dict) -> float:
    try:
        fetched_at = float(data.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, time.time() - fetched_at)


def _public_entry(data: dict) -> dict:
    lifetime = data.get("lifetime")
    return {
        "body": data["body"],
        "final_url": str(data.get("final_url") or ""),
        "etag": str(data.get("etag") or ""),
        "last_modified": str(data.get("last_modified") or ""),
        "fetched_at": float(data.get("fetched_at") or 0.0),
        "age_seconds": _entry_age(data),
        "lifetime": float(lifetime) if isinstance(lifetime, (int, float)) else None,
    }


def load(url: str, *, cookie: str = "", max_bytes: int = 0) -> dict | None:
    """Return a cached entry, or ``None`` when there is nothing usable.

    Missing, disabled, corrupt, and oversized entries all read as a miss: the
    caller then fetches normally instead of reasoning about why the cache
    could not answer.
    """

    if not cache_enabled():
        return None
    path = _entry_path(url, cookie, max_bytes)
    data = _read_entry(path)
    if data is None:
        return None
    cap = data.get("max_bytes")
    if isinstance(cap, int) and cap > 0:
        try:
            body_bytes = len(data["body"].encode("utf-8"))
        except UnicodeEncodeError:
            return None
        if body_bytes > cap:
            return None
    return _public_entry(data)


def fresh(entry: dict | None) -> bool:
    """Whether ``entry`` is still inside the freshness window."""

    if not isinstance(entry, dict):
        return False
    try:
        age = entry.get("age_seconds")
        age = float(age) if age is not None else _entry_age(entry)
    except (TypeError, ValueError):
        return False
    window = _ttl_seconds()
    lifetime = entry.get("lifetime")
    if isinstance(lifetime, (int, float)) and lifetime >= 0:
        window = min(window, float(lifetime))
    return age < window


def validators(entry: dict | None) -> dict[str, str]:
    """Return the conditional-request headers a stale entry can supply."""

    if not isinstance(entry, dict):
        return {}
    headers = {}
    etag = str(entry.get("etag") or "").strip()
    if etag:
        headers["If-None-Match"] = etag
    last_modified = str(entry.get("last_modified") or "").strip()
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def _lower_headers(headers: Any) -> dict:
    if not headers:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    lowered = {}
    try:
        for name, value in items:
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(part) for part in value)
            lowered[str(name).strip().lower()] = str(value).strip()
    except (TypeError, ValueError):
        return {}
    return lowered


def _cache_control_tokens(value: str) -> set:
    tokens = set()
    for part in value.split(","):
        token = part.split("=", 1)[0].strip().lower()
        if token:
            tokens.add(token)
    return tokens


def _expires_lifetime(value: str) -> float | None:
    """Turn an ``Expires`` date into seconds from now, or ``None`` when stale.

    RFC 9111 says an unparseable ``Expires`` makes the response stale, so this
    reports ``None`` instead of inventing a window the origin never granted.
    """

    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - datetime.now(timezone.utc)).total_seconds()


def _declared_lifetime(headers: dict) -> float | None:
    """Origin-declared freshness in seconds; ``None`` when none was given.

    ``max-age=0`` (or a non-positive value) means the stored copy must never be
    served without revalidation, so it returns ``0.0`` and storage is refused.
    Without either directive an ``Expires`` date stands in for them, and ``Age``
    is subtracted because that part of the window was already spent in some
    other cache before this copy arrived.
    """

    lifetime = None
    for part in headers.get("cache-control", "").split(","):
        name, _, value = part.partition("=")
        if name.strip().lower() not in {"max-age", "s-maxage"}:
            continue
        try:
            seconds = float(value.strip().strip('"'))
        except ValueError:
            continue
        if seconds <= 0:
            return 0.0
        lifetime = seconds if lifetime is None else min(lifetime, seconds)
    if lifetime is None:
        expires = headers.get("expires", "").strip()
        if not expires:
            return None
        lifetime = _expires_lifetime(expires)
        if lifetime is None or lifetime <= 0:
            return 0.0
    age = headers.get("age", "").strip()
    if age:
        try:
            lifetime -= float(age)
        except ValueError:
            pass
    return max(0.0, lifetime)


def _store_allowed(status: int, headers: dict) -> bool:
    if not 200 <= int(status) < 300:
        return False
    if _cache_control_tokens(headers.get("cache-control", "")) & _FORBIDDEN_CACHE_CONTROL:
        return False
    if _declared_lifetime(headers) == 0.0:
        return False
    vary_tokens = {token for token in headers.get("vary", "").lower().split(",") if token.strip()}
    vary_tokens = {token.strip() for token in vary_tokens}
    if vary_tokens & _FORBIDDEN_VARY:
        return False
    return True


def _write_atomic(path: Path, payload: bytes) -> bool:
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".{}.tmp-".format(path.name), dir=str(parent)
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(str(temp_path), str(path))
        except BaseException:
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
    except OSError:
        return False
    return True


def store(
    url: str,
    *,
    cookie: str = "",
    max_bytes: int = 0,
    body: str,
    final_url: str,
    status: int = 200,
    headers: Any = None,
) -> bool:
    """Cache one response body; return whether an entry was written.

    Refusals mirror what the origin asked for and what this cache cannot keep
    safely. Every failure is swallowed: the caller already has the body.
    """

    if not cache_enabled():
        return False
    if not isinstance(body, str):
        return False
    lowered = _lower_headers(headers)
    if not _store_allowed(int(status), lowered):
        return False
    try:
        body_bytes = len(body.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    if body_bytes > MAX_ENTRY_BYTES:
        return False
    payload = {
        "schema": SCHEMA,
        "url": url,
        "max_bytes": int(max_bytes),
        "final_url": final_url,
        "status": int(status),
        "etag": lowered.get("etag", ""),
        "last_modified": lowered.get("last-modified", ""),
        "fetched_at": time.time(),
        "lifetime": _declared_lifetime(lowered),
        "body": body,
    }
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        return False
    return _write_atomic(_entry_path(url, cookie, max_bytes), encoded)


def touch(url: str, *, cookie: str = "", max_bytes: int = 0) -> None:
    """Refresh ``fetched_at`` after a ``304 Not Modified``; best-effort."""

    if not cache_enabled():
        return
    path = _entry_path(url, cookie, max_bytes)
    data = _read_entry(path)
    if data is None:
        return
    data["fetched_at"] = time.time()
    try:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        return
    _write_atomic(path, encoded)


@contextmanager
def fetch_lock(
    url: str,
    *,
    cookie: str = "",
    max_bytes: int = 0,
    timeout_seconds: float = 30,
) -> Iterator[None]:
    """Serialise fetchers of the same key across threads and processes.

    A busy or unavailable lock degrades to no coalescing: the caller fetches at
    worst a second time, and a lock problem never becomes a read failure.
    """

    if not cache_enabled():
        yield
        return
    try:
        # media.py pulls in the media stack; only pay for it when locking.
        from .media import interprocess_file_lock
    except Exception:  # pragma: no cover - degraded install without media extras
        yield
        return
    stack = ExitStack()
    try:
        try:
            stack.enter_context(
                interprocess_file_lock(
                    _lock_path(url, cookie, max_bytes),
                    max_wait_seconds=timeout_seconds,
                    busy_message=_BUSY_MESSAGE,
                )
            )
        except (TimeoutError, OSError):
            pass
        yield
    finally:
        stack.close()
