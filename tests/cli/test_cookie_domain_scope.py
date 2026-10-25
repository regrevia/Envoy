"""Cookie import must scope by site, not by shared suffix.

`envoy auth import --url https://shop.example.co.uk/...` used to derive the
import domain from the last two labels, i.e. "co.uk".  Because domain matching
is a suffix match, that pulled every *.co.uk session in the user's browser into
the import store.  These tests pin the intended boundary with synthetic cookie
rows only: no real browser profile, no real session, no network.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from envoy_cli import cookies


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "imported-cookies.json"
    monkeypatch.setattr(cookies, "cookie_store_path", lambda: path)
    monkeypatch.setattr(cookies, "_decryption_key", lambda name: None)
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


def _synthetic_chromium(tmp_path, rows: list[tuple[str, str]]) -> sqlite3.Connection:
    database = tmp_path / "Cookies"
    connection = sqlite3.connect(database)
    connection.execute(
        "create table cookies (host_key text, name text, value text, encrypted_value blob, "
        "path text, expires_utc integer, is_secure integer, is_httponly integer)"
    )
    connection.executemany(
        "insert into cookies values (?,?,?,?,?,?,?,?)",
        [(host, name, "value-" + name, b"", "/", 0, 1, 0) for host, name in rows],
    )
    connection.commit()
    connection.close()
    return database


# --- inference from a URL -------------------------------------------------


def test_url_inference_uses_the_registrable_domain_not_the_last_two_labels():
    # shop.example.co.uk is a site; co.uk is a public suffix shared by strangers.
    assert cookies._domain_filter([], "https://shop.example.co.uk/cart") == ["example.co.uk"]


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.example.co.jp/path", "example.co.jp"),
        ("https://shop.example.com.au/", "example.com.au"),
        ("https://www.bbc.co.uk/news", "bbc.co.uk"),
        ("https://m.example.com.cn/a", "example.com.cn"),
        ("https://sub.deep.example.org/x", "example.org"),
        ("https://www.douyin.com/video/1", "douyin.com"),
    ],
)
def test_url_inference_respects_multi_label_public_suffixes(url, expected):
    assert cookies._domain_filter([], url) == [expected]


def test_registrable_domain_is_itself_a_public_suffix_when_url_is_bare():
    # A URL that is nothing but a public suffix names no site to import.
    for url in ("https://co.uk/", "https://com.au/", "https://co.jp/"):
        with pytest.raises(cookies.CookieImportError):
            cookies._domain_filter([], url)


def test_public_suffix_cannot_be_used_as_an_explicit_scope():
    for scope in ("co.uk", ".co.uk", "CO.UK", "co.jp", "com.au", "com"):
        with pytest.raises(cookies.CookieImportError):
            cookies._domain_filter([scope], None)


def test_explicit_domain_keeps_working_for_sites_and_their_subdomains():
    assert cookies._domain_filter([".Xiaohongshu.com"], None) == ["xiaohongshu.com"]
    assert cookies._domain_filter(["shop.example.co.uk"], None) == ["shop.example.co.uk"]
    # An explicit scope wins over what the URL would imply.
    assert cookies._domain_filter(["douyin.com"], "https://evil.test/") == ["douyin.com"]


# --- selection over synthetic cookie rows --------------------------------


def test_import_does_not_cross_sites_sharing_a_public_suffix(tmp_path, store, monkeypatch):
    database = _synthetic_chromium(
        tmp_path,
        [
            (".example.co.uk", "example_session"),
            ("shop.example.co.uk", "cart"),
            (".other.co.uk", "stranger_session"),
            (".co.uk", "suffix_wide"),
        ],
    )
    monkeypatch.setattr(cookies, "_cookie_databases", lambda name: [database])

    report = cookies.import_chromium_cookies("chrome", cookies._domain_filter(
        [], "https://shop.example.co.uk/cart"
    ))

    assert report["domains"] == ["example.co.uk"]
    assert sorted(report["cookies"]) == ["cart", "example_session"]
    assert cookies.load_cookies_for("https://evil.co.uk/") == []
    assert cookies.load_cookies_for("https://www.other.co.uk/") == []


def test_public_suffix_scope_is_refused_before_any_cookie_store_is_read(tmp_path, store, monkeypatch):
    def _must_not_read(name):
        raise AssertionError("cookie store read despite an invalid import scope")

    monkeypatch.setattr(cookies, "_cookie_databases", _must_not_read)
    with pytest.raises(cookies.CookieImportError):
        cookies.import_cookies("chrome", ["co.uk"])


def test_safari_import_uses_the_same_scope_rule(tmp_path, store, monkeypatch):
    parsed = [
        _cookie("example_session", ".example.co.uk"),
        _cookie("stranger_session", ".other.co.uk"),
        _cookie("suffix_wide", ".co.uk"),
    ]
    binary = tmp_path / "Cookies.binarycookies"
    binary.write_bytes(b"cook")
    monkeypatch.setattr(cookies, "safari_cookie_files", lambda: [binary])
    monkeypatch.setattr(cookies, "_parse_binarycookies", lambda data: list(parsed))

    report = cookies.import_safari_cookies(["example.co.uk"])

    assert report["cookies"] == ["example_session"]


# --- matching rule -------------------------------------------------------


def test_subdomain_matching_stays_inside_the_target_site():
    assert cookies._matches_domain("www.example.co.uk", ["example.co.uk"]) is True
    assert cookies._matches_domain(".example.co.uk", ["example.co.uk"]) is True
    assert cookies._matches_domain("example.co.uk", ["example.co.uk"]) is True
    # Same public suffix, different site.
    assert cookies._matches_domain("other.co.uk", ["example.co.uk"]) is False
    assert cookies._matches_domain("notexample.co.uk", ["example.co.uk"]) is False
    # Suffix of the label sequence, not of the string.
    assert cookies._matches_domain("example.co.uk.evil.test", ["example.co.uk"]) is False


# --- local development hosts are not public suffixes ---------------------


def test_loopback_hosts_are_allowed_as_scope():
    assert cookies._domain_filter(["localhost"], None) == ["localhost"]
    assert cookies._domain_filter([".LOCALHOST"], None) == ["localhost"]
    assert cookies._domain_filter(["app.localhost"], None) == ["app.localhost"]
    assert cookies._domain_filter(["127.0.0.1"], None) == ["127.0.0.1"]
    assert cookies._domain_filter(["::1"], None) == ["::1"]


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://localhost:3000/a", "localhost"),
        ("http://app.localhost/login", "app.localhost"),
        ("http://127.0.0.1:8000/x", "127.0.0.1"),
        ("http://[::1]:8000/x", "::1"),
    ],
)
def test_loopback_urls_keep_their_own_host(url, expected):
    assert cookies._domain_filter([], url) == [expected]


@pytest.mark.parametrize("scope", ["test", "internal", "corp", "lan"])
def test_other_single_label_scopes_are_refused(scope):
    with pytest.raises(cookies.CookieImportError):
        cookies._domain_filter([scope], None)


def test_localhost_import_selects_only_the_localhost_rows(tmp_path, store, monkeypatch):
    database = _synthetic_chromium(
        tmp_path, [("app.localhost", "dev"), (".example.com", "stranger")]
    )
    monkeypatch.setattr(cookies, "_cookie_databases", lambda name: [database])

    report = cookies.import_chromium_cookies(
        "chrome", cookies._domain_filter([], "http://app.localhost:3000/")
    )

    assert report["domains"] == ["app.localhost"]
    assert report["cookies"] == ["dev"]


# --- private sections: one customer per name under a shared host suffix ---


@pytest.mark.parametrize(
    "host, expected",
    [
        ("evil.github.io", "evil.github.io"),
        ("a.blogspot.com", "a.blogspot.com"),
        ("myapp.vercel.app", "myapp.vercel.app"),
        ("site.netlify.app", "site.netlify.app"),
        ("bucket.s3.amazonaws.com", "bucket.s3.amazonaws.com"),
        ("x.compute.amazonaws.com", "x.compute.amazonaws.com"),
        ("d123.cloudfront.net", "d123.cloudfront.net"),
        ("app.azurewebsites.net", "app.azurewebsites.net"),
    ],
)
def test_private_section_hosts_are_their_own_site(host, expected):
    assert cookies._scope_from_host(host) == expected


@pytest.mark.parametrize(
    "suffix", ["github.io", "blogspot.com", "vercel.app", "s3.amazonaws.com", "workers.dev"]
)
def test_private_section_suffix_is_refused_as_a_scope(suffix):
    with pytest.raises(cookies.CookieImportError):
        cookies._scope_for(suffix)


def test_one_tenant_cannot_pull_the_shared_suffix_cookies(tmp_path, store, monkeypatch):
    database = _synthetic_chromium(
        tmp_path, [(".github.io", "gh_wide"), ("evil.github.io", "gh_evil")]
    )
    monkeypatch.setattr(cookies, "_cookie_databases", lambda name: [database])

    report = cookies.import_cookies("chrome", None, "https://evil.github.io/x")

    assert report["domains"] == ["evil.github.io"]
    assert report["cookies"] == ["gh_evil"]
    assert cookies.load_cookies_for("https://other.github.io/") == []


def test_a_legacy_suffix_cookie_is_not_served_any_more(store):
    store.write_text(
        json.dumps({"source": "chrome", "cookies": [
            {"name": "gh_wide", "value": "1", "domain": "github.io", "path": "/"},
        ]}),
        encoding="utf-8",
    )
    assert cookies.load_cookies_for("https://evil.github.io/") == []


# --- ports ----------------------------------------------------------------


def test_ports_are_stripped_before_validation():
    assert cookies._scope_for("example.com:8080") == "example.com"
    assert cookies._scope_for("example.com:443") == "example.com"
    assert cookies._scope_for("[::1]:443") == "::1"
    with pytest.raises(cookies.CookieImportError):
        cookies._scope_for("co.uk:443")


def test_a_scope_with_a_port_still_selects_cookies(tmp_path, store, monkeypatch):
    database = _synthetic_chromium(tmp_path, [("example.com", "sid")])
    monkeypatch.setattr(cookies, "_cookie_databases", lambda name: [database])

    report = cookies.import_cookies("chrome", ["example.com:8080"])

    assert report["domains"] == ["example.com"]
    assert report["cookies"] == ["sid"]
    assert cookies._matches_domain("example.com", report["domains"]) is True


# --- IPv6 and IDN spelling ------------------------------------------------


def test_ipv6_brackets_are_one_spelling():
    assert cookies._scope_for("[::1]") == "::1"
    assert cookies._scope_from_host("[::1]") == "::1"
    assert cookies._matches_domain("[::1]", ["::1"]) is True
    assert cookies._matches_domain("::1", ["[::1]"]) is True


def test_ipv6_scope_selects_the_loopback_cookie(tmp_path, store, monkeypatch):
    database = _synthetic_chromium(tmp_path, [("::1", "v6")])
    monkeypatch.setattr(cookies, "_cookie_databases", lambda name: [database])

    report = cookies.import_cookies("chrome", ["[::1]:443"])

    assert report["domains"] == ["::1"]
    assert report["cookies"] == ["v6"]
    assert [item["name"] for item in cookies.load_cookies_for("http://[::1]:8000/")] == ["v6"]


def test_unicode_and_punycode_domains_match_each_other():
    unicode_domain = "例子.中国"
    punycode_domain = "xn--fsqu00a.xn--fiqs8s"
    assert unicode_domain != punycode_domain
    assert cookies._matches_domain(unicode_domain, [punycode_domain]) is True
    assert cookies._matches_domain(punycode_domain, [unicode_domain]) is True


@pytest.mark.parametrize(
    "stored, url",
    [
        ("例子.中国", "https://xn--fsqu00a.xn--fiqs8s/"),
        ("xn--fsqu00a.xn--fiqs8s", "https://例子.中国/"),
        ("例子.中国", "https://例子.中国/"),
    ],
)
def test_stored_idn_cookie_is_served_to_both_spellings(store, stored, url):
    store.write_text(
        json.dumps({"cookies": [{"name": "idn", "value": "1", "domain": stored, "path": "/"}]}),
        encoding="utf-8",
    )
    assert [item["name"] for item in cookies.load_cookies_for(url)] == ["idn"]


# --- trailing dot ---------------------------------------------------------


def test_trailing_dot_is_ignored_on_both_sides():
    assert cookies._matches_domain("example.com.", ["example.com"]) is True
    assert cookies._matches_domain("example.com", ["example.com."]) is True
    assert cookies._domain_filter(["example.com."], None) == ["example.com"]
