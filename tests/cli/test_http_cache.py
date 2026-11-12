"""Unit tests for the on-disk HTTP body cache (no network involved)."""

from __future__ import annotations

import json
import threading
import time as real_time
from types import SimpleNamespace

import pytest

from envoy_cli import http_cache

URL = "https://example.com/article"


@pytest.fixture(autouse=True)
def clean_cache_env(monkeypatch):
    # The host may carry cache overrides; each test starts from defaults.
    for name in (http_cache.ENV_CACHE_DIR, http_cache.ENV_DISABLE, http_cache.ENV_TTL):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def clock(monkeypatch):
    ticks = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(http_cache, "time", SimpleNamespace(time=lambda: ticks.now))
    return ticks


def _entry_file(url=URL, *, cookie="", max_bytes=0):
    key = http_cache.entry_key(url, cookie=cookie, max_bytes=max_bytes)
    return http_cache.cache_dir() / "{}.json".format(key)


def test_entry_key_is_stable_and_separates_requests():
    assert http_cache.entry_key(URL) == http_cache.entry_key(URL)
    assert http_cache.entry_key(URL) != http_cache.entry_key(URL + "?page=2")
    assert http_cache.entry_key(URL, cookie="a=1") != http_cache.entry_key(URL, cookie="a=2")
    assert http_cache.entry_key(URL, max_bytes=1024) != http_cache.entry_key(URL)
    # The cookie only reaches disk as part of the digest.
    key = http_cache.entry_key(URL, cookie="session=secret")
    assert "secret" not in key
    assert len(key) == 64 and all(char in "0123456789abcdef" for char in key)


def test_store_load_round_trip_writes_atomically():
    headers = {"ETag": '"abc"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    assert http_cache.store(
        URL, body="hello body", final_url="https://example.com/final", headers=headers
    )
    entry = http_cache.load(URL)
    assert entry is not None
    assert entry["body"] == "hello body"
    assert entry["final_url"] == "https://example.com/final"
    assert entry["etag"] == '"abc"'
    assert entry["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert isinstance(entry["fetched_at"], float)
    assert 0 <= entry["age_seconds"] < 60
    # Atomic replace leaves the entry as the only file next to the lock.
    leftovers = [path.name for path in http_cache.cache_dir().iterdir() if ".tmp-" in path.name]
    assert leftovers == []


def test_load_ignores_missing_and_corrupt_entries():
    assert http_cache.load(URL) is None
    path = _entry_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json{", encoding="utf-8")
    assert http_cache.load(URL) is None
    path.write_text(json.dumps({"schema": "other/1", "body": "x", "fetched_at": 1.0}), "utf-8")
    assert http_cache.load(URL) is None


def test_load_rejects_body_over_stored_cap():
    assert http_cache.store(URL, max_bytes=4, body="abcdefgh", final_url=URL)
    assert http_cache.load(URL, max_bytes=4) is None
    assert http_cache.load(URL) is None  # different key, nothing stored there


def test_fresh_respects_age_and_ttl_override(clock, monkeypatch):
    http_cache.store(URL, body="body", final_url=URL)
    clock.now += 100
    assert http_cache.fresh(http_cache.load(URL))
    monkeypatch.setenv(http_cache.ENV_TTL, "60")
    assert not http_cache.fresh(http_cache.load(URL))
    assert http_cache.fresh({"age_seconds": 10.0})
    assert not http_cache.fresh(None)


def test_malformed_ttl_falls_back_to_default(clock, monkeypatch):
    http_cache.store(URL, body="body", final_url=URL)
    clock.now += 100
    for bad in ("soon", "0", "-5", ""):
        monkeypatch.setenv(http_cache.ENV_TTL, bad)
        assert http_cache.fresh(http_cache.load(URL)), bad


@pytest.mark.parametrize(
    "value",
    ["no-store", "private", "max-age=0, no-store", "PRIVATE"],
)
def test_store_refuses_no_store_and_private(value):
    assert not http_cache.store(URL, body="body", final_url=URL, headers={"Cache-Control": value})
    assert http_cache.load(URL) is None


@pytest.mark.parametrize("value", ["Authorization", "*", "Cookie, Authorization"])
def test_store_refuses_vary_on_authorization_or_star(value):
    assert not http_cache.store(URL, body="body", final_url=URL, headers={"Vary": value})
    assert http_cache.load(URL) is None


@pytest.mark.parametrize("value", ["max-age=0", "s-maxage=0", "MAX-AGE=0"])
def test_store_refuses_a_zero_origin_lifetime(value):
    assert not http_cache.store(
        URL, body="body", final_url=URL, headers={"Cache-Control": value}
    )
    assert http_cache.load(URL) is None


def test_origin_lifetime_clamps_freshness(clock):
    assert http_cache.store(
        URL, body="body", final_url=URL, headers={"Cache-Control": "max-age=120"}
    )
    clock.now += 100
    assert http_cache.fresh(http_cache.load(URL))
    clock.now += 40
    assert not http_cache.fresh(http_cache.load(URL))


def test_store_allows_vary_on_cookie():
    assert http_cache.store(URL, body="body", final_url=URL, headers={"Vary": "Cookie"})


@pytest.mark.parametrize("status", [404, 500, 302])
def test_store_refuses_non_2xx(status):
    assert not http_cache.store(URL, body="body", final_url=URL, status=status)


def test_store_refuses_body_over_max_entry_bytes():
    oversized = "x" * (http_cache.MAX_ENTRY_BYTES + 1)
    assert not http_cache.store(URL, body=oversized, final_url=URL)
    assert http_cache.load(URL) is None
    assert http_cache.store(
        URL, body="x" * http_cache.MAX_ENTRY_BYTES, final_url=URL
    )


def test_disabled_cache_bypasses_everything(monkeypatch):
    monkeypatch.setenv(http_cache.ENV_DISABLE, "off")
    assert not http_cache.cache_enabled()
    assert not http_cache.store(URL, body="body", final_url=URL)
    assert http_cache.load(URL) is None
    with http_cache.fetch_lock(URL, timeout_seconds=0.1):
        pass
    assert not http_cache.cache_dir().exists()


def test_touch_refreshes_age(clock):
    http_cache.store(URL, body="body", final_url=URL)
    clock.now += 300
    assert http_cache.load(URL)["age_seconds"] == pytest.approx(300.0)
    http_cache.touch(URL)
    entry = http_cache.load(URL)
    assert entry["age_seconds"] == pytest.approx(0.0)
    assert http_cache.fresh(entry)
    # Touching an absent entry is a no-op, not an error.
    http_cache.touch(URL + "/missing")


def test_fetch_lock_serialises_threads_and_degrades_on_timeout():
    events = []
    gate = threading.Event()

    def holder(name, hold_seconds):
        with http_cache.fetch_lock(URL, timeout_seconds=5):
            gate.set()
            events.append(name + "-in")
            real_time.sleep(hold_seconds)
            events.append(name + "-out")

    first = threading.Thread(target=holder, args=("a", 0.2))
    first.start()
    assert gate.wait(5)
    second = threading.Thread(target=holder, args=("b", 0.0))
    second.start()
    second.join(10)
    first.join(10)
    assert events == ["a-in", "a-out", "b-in", "b-out"]

    # A lock that cannot be taken in time must yield without raising.
    blocking = threading.Event()

    def stuck():
        with http_cache.fetch_lock(URL, timeout_seconds=5):
            blocking.set()
            real_time.sleep(0.5)

    thread = threading.Thread(target=stuck)
    thread.start()
    assert blocking.wait(5)
    with http_cache.fetch_lock(URL, timeout_seconds=0.05):
        pass
    thread.join(10)


def test_cache_dir_honors_override_and_defaults_beside_materials(tmp_path, monkeypatch):
    from envoy_cli import storage

    expected_default = storage.resolve_material_root() / "http-cache"
    assert http_cache.cache_dir() == expected_default
    monkeypatch.setenv(http_cache.ENV_CACHE_DIR, str(tmp_path / "custom-cache"))
    assert http_cache.cache_dir() == tmp_path / "custom-cache"
    assert http_cache.store(URL, body="body", final_url=URL)
    assert (tmp_path / "custom-cache" / "{}.json".format(http_cache.entry_key(URL))).is_file()


@pytest.mark.parametrize(
    "expires",
    [
        "Thu, 01 Jan 1970 00:00:00 GMT",
        "0",
        "not-a-date",
    ],
)
def test_store_refuses_an_expired_expires(expires):
    assert http_cache.store(
        URL, body="body", final_url=URL, status=200, headers={"expires": expires}
    ) is False
    assert http_cache.load(URL) is None


def test_expires_stands_in_for_a_missing_max_age(clock):
    future = "Wed, 21 Oct 2037 07:28:00 GMT"
    assert http_cache.store(
        URL, body="body", final_url=URL, status=200, headers={"expires": future}
    ) is True
    entry = http_cache.load(URL)
    assert entry["lifetime"] and entry["lifetime"] > 300_000
    # the default window of 600s still wins when the origin asks for more
    clock.now += 599.0
    assert http_cache.fresh(http_cache.load(URL)) is True
    clock.now += 2.0
    assert http_cache.fresh(http_cache.load(URL)) is False


def test_max_age_wins_over_expires():
    headers = {
        "cache-control": "max-age=60",
        "expires": "Thu, 01 Jan 1970 00:00:00 GMT",
    }
    assert http_cache._declared_lifetime(headers) == 60.0


def test_age_is_subtracted_from_the_origin_lifetime(clock):
    headers = {"cache-control": "max-age=120", "age": "100"}
    assert http_cache.store(URL, body="body", final_url=URL, status=200, headers=headers) is True
    entry = http_cache.load(URL)
    assert entry["lifetime"] == 20.0
    clock.now += 10.0
    assert http_cache.fresh(http_cache.load(URL)) is True
    clock.now += 20.0
    assert http_cache.fresh(http_cache.load(URL)) is False


def test_an_age_past_the_lifetime_refuses_storage():
    headers = {"cache-control": "max-age=60", "age": "90"}
    assert http_cache._declared_lifetime(headers) == 0.0
    assert http_cache.store(URL, body="body", final_url=URL, status=200, headers=headers) is False
