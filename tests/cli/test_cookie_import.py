"""Cookie import: the second way a signed-in page can be read.

These tests pin the pieces that decide whether a user's session is read at all
(store layout, domain scoping, padding and key handling) without touching a
real browser profile.
"""

from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from envoy_cli import cookies


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "imported-cookies.json"
    monkeypatch.setattr(cookies, "cookie_store_path", lambda: path)
    return path


def _cookie(name: str, domain: str, value: str = "v") -> dict:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": 0.0,
        "httpOnly": False,
        "secure": True,
    }


def test_saved_cookies_are_private_and_filtered_by_domain(store):
    report = cookies._save_cookies(
        "chrome",
        ["douyin.com"],
        [_cookie("sessionid", ".douyin.com"), _cookie("ttwid", "www.douyin.com")],
    )

    assert report["imported"] == 2
    assert store.is_file()
    if sys.platform != "win32":
        assert store.stat().st_mode & 0o777 == 0o600

    assert [item["name"] for item in cookies.load_cookies_for(
        "https://www.douyin.com/video/1"
    )] == ["sessionid", "ttwid"]
    # A lookalike host must not receive the session.
    assert cookies.load_cookies_for("https://notdouyin.com/video/1") == []
    assert cookies.cookies_status()["source"] == "chrome"


def test_clear_removes_the_store(store):
    cookies._save_cookies("chrome", ["douyin.com"], [_cookie("a", ".douyin.com")])
    assert cookies.clear_imported_cookies() is True
    assert cookies.clear_imported_cookies() is False
    assert cookies.load_cookies_for("https://www.douyin.com/") == []


def test_domain_filter_rejects_an_empty_request():
    assert cookies._domain_filter([], "https://www.douyin.com/video/1") == ["douyin.com"]
    assert cookies._domain_filter([".Xiaohongshu.com"], None) == ["xiaohongshu.com"]
    with pytest.raises(cookies.CookieImportError):
        cookies._domain_filter([], None)


def test_matches_domain_covers_subdomains_only():
    assert cookies._matches_domain(".douyin.com", ["douyin.com"]) is True
    assert cookies._matches_domain("www.douyin.com", ["douyin.com"]) is True
    assert cookies._matches_domain("douyin.com.evil.test", ["douyin.com"]) is False


def test_chromium_plaintext_strips_the_host_prefix_and_padding(monkeypatch):
    monkeypatch.setattr(
        cookies,
        "_openssl_decrypt",
        lambda key, payload: b"\x00" * 32 + b"session-value" + b"\x06" * 6,
    )
    assert cookies._chromium_plaintext(b"v10" + b"cipher", b"k" * 16) == "session-value"
    assert cookies._chromium_plaintext(b"plain-value", None) == "plain-value"


def test_unpad_only_strips_a_full_padding_tail():
    assert cookies._unpad(b"value" + b"\x03" * 3) == b"value"
    assert cookies._unpad(b"value") == b"value"


def test_chromium_import_reads_a_synthetic_database(tmp_path, store, monkeypatch):
    database = tmp_path / "Cookies"
    connection = sqlite3.connect(database)
    connection.execute(
        "create table cookies (host_key text, name text, value text, encrypted_value blob, "
        "path text, expires_utc integer, is_secure integer, is_httponly integer)"
    )
    connection.executemany(
        "insert into cookies values (?,?,?,?,?,?,?,?)",
        [
            (".douyin.com", "sessionid", "abc", b"", "/", 0, 1, 1),
            ("example.com", "unrelated", "xyz", b"", "/", 0, 1, 0),
        ],
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(cookies, "_cookie_databases", lambda name: [database])
    report = cookies.import_chromium_cookies("chrome", ["douyin.com"])

    assert report["imported"] == 1
    assert report["cookies"] == ["sessionid"]
    assert cookies.load_cookies_for("https://www.douyin.com/video/1")[0]["httpOnly"] is True


def test_unknown_source_is_rejected(store):
    with pytest.raises(cookies.CookieImportError):
        cookies.import_cookies("netscape", ["douyin.com"])


def _binarycookies_pages(cookies_in: list[dict]) -> bytes:
    records = []
    for item in cookies_in:
        url = item["domain"].encode() + b"\x00"
        name = item["name"].encode() + b"\x00"
        path = item["path"].encode() + b"\x00"
        value = item["value"].encode() + b"\x00"
        header = bytearray(56)
        record = bytearray(56)
        url_offset = len(record)
        record += url
        name_offset = len(record)
        record += name
        path_offset = len(record)
        record += path
        value_offset = len(record)
        record += value
        record[0:4] = len(record).to_bytes(4, "little")
        record[8:12] = (0x1 | 0x4).to_bytes(4, "little")
        record[16:20] = url_offset.to_bytes(4, "little")
        record[20:24] = name_offset.to_bytes(4, "little")
        record[24:28] = path_offset.to_bytes(4, "little")
        record[28:32] = value_offset.to_bytes(4, "little")
        del header
        records.append(bytes(record))

    page = bytearray(8 + 4 * len(records))
    page[0:4] = (0x00000100).to_bytes(4, "big")
    page[4:8] = len(records).to_bytes(4, "little")
    for index, record in enumerate(records):
        page[8 + 4 * index:12 + 4 * index] = (8 + 4 * len(records) + sum(
            len(other) for other in records[:index]
        )).to_bytes(4, "little" if False else "little")
        page += record
    header = bytearray(b"cook" + (1).to_bytes(4, "big") + len(page).to_bytes(4, "big"))
    return bytes(header + page)


def test_binarycookies_parser_reads_name_value_and_domain():
    payload = _binarycookies_pages([_cookie("sessionid", ".xiaohongshu.com", "xyz")])
    parsed = cookies._parse_binarycookies(payload)

    assert len(parsed) == 1
    assert parsed[0]["name"] == "sessionid"
    assert parsed[0]["value"] == "xyz"
    assert parsed[0]["domain"] == ".xiaohongshu.com"
    assert parsed[0]["secure"] is True
    assert parsed[0]["httpOnly"] is True


def test_binarycookies_parser_rejects_foreign_files():
    with pytest.raises(cookies.CookieImportError):
        cookies._parse_binarycookies(b"not-a-cookie-file")


def test_apply_imported_cookies_is_a_noop_without_a_store(store):
    from envoy_cli import browser

    class Context:
        def __init__(self):
            self.added = []

        def add_cookies(self, values):
            self.added.extend(values)

    context = Context()
    assert browser._apply_imported_cookies(context, "https://www.douyin.com/video/1") == 0
    assert context.added == []

    cookies._save_cookies("chrome", ["douyin.com"], [_cookie("sessionid", ".douyin.com")])
    assert browser._apply_imported_cookies(context, "https://www.douyin.com/video/1") == 1
    assert [item["name"] for item in context.added] == ["sessionid"]
    # Another site receives nothing even with a populated store.
    assert browser._apply_imported_cookies(context, "https://example.com/") == 0


def test_status_reports_an_empty_store(store):
    assert cookies.cookies_status() == {"imported": 0}
    assert json.dumps(cookies.cookies_status()) == '{"imported": 0}'
