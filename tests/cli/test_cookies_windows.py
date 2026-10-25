"""Windows cookie import: AES-256-GCM, DPAPI key handling, app-bound refusal.

The DPAPI call itself only exists on Windows, so the tests inject a stand-in for
`CryptUnprotectData` and verify everything Envoy owns: reading `Local State`,
stripping the `DPAPI` prefix, decrypting `v10` GCM payloads, and refusing
app-bound (`v20`) stores with an actionable message. The AES-GCM implementation
is checked against published NIST/McGrew vectors rather than against itself.
"""

from __future__ import annotations

import base64
import json
import sqlite3

import pytest

from envoy_cli import aesgcm, cookies


def test_aes256_encrypts_the_fips_197_vector():
    key = bytes.fromhex(
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    )
    block = bytes.fromhex("00112233445566778899aabbccddeeff")
    assert aesgcm._encrypt_block(aesgcm._expand_key(key), block).hex() == (
        "8ea2b7ca516745bfeafc49904b496089"
    )


@pytest.mark.parametrize(
    ("plaintext_hex", "nonce_hex", "ciphertext_hex", "tag_hex"),
    [
        ("", "00" * 12, "", "530f8afbc74536b9a963b4f1c4cb738b"),
        (
            "00" * 16,
            "00" * 12,
            "cea7403d4d606b6e074ec5d3baf39d18",
            "d0d1c8a799996bf0265b98b5d48ab919",
        ),
        (
            "d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
            "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255",
            "cafebabefacedbaddecaf888",
            "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
            "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad",
            "b094dac5d93471bdec1a502270e3cc6c",
        ),
    ],
)
def test_aes_gcm_matches_published_vectors(plaintext_hex, nonce_hex, ciphertext_hex, tag_hex):
    key = (
        bytes.fromhex("feffe9928665731c6d6a8f9467308308" * 2)
        if plaintext_hex.startswith("d931")
        else bytes(32)
    )
    blob = aesgcm.encrypt(key, bytes.fromhex(plaintext_hex), bytes.fromhex(nonce_hex))
    assert blob[12:-16].hex() == ciphertext_hex
    assert blob[-16:].hex() == tag_hex
    assert aesgcm.decrypt(key, blob) == bytes.fromhex(plaintext_hex)


def test_aes_gcm_rejects_a_modified_tag():
    blob = bytearray(aesgcm.encrypt(bytes(32), b"cookie", bytes(12)))
    blob[-1] ^= 0x01
    with pytest.raises(ValueError):
        aesgcm.decrypt(bytes(32), bytes(blob))


def _windows_root(tmp_path, state: dict) -> "object":
    root = tmp_path / "User Data"
    (root / "Default" / "Network").mkdir(parents=True, exist_ok=True)
    (root / "Local State").write_text(json.dumps({"os_crypt": state}), encoding="utf-8")
    return root


def test_local_state_dpapi_key_is_unwrapped(monkeypatch, tmp_path):
    root = _windows_root(
        tmp_path,
        {"encrypted_key": base64.b64encode(b"DPAPI" + b"wrapped-key").decode()},
    )
    monkeypatch.setattr(cookies, "_chromium_roots", lambda name: [root])
    monkeypatch.setattr(cookies, "_dpapi_unprotect", lambda blob: b"master-" + blob)
    assert cookies._windows_master_key("chrome") == b"master-wrapped-key"


def test_local_state_without_a_key_reports_no_key(monkeypatch, tmp_path):
    root = _windows_root(tmp_path, {})
    monkeypatch.setattr(cookies, "_chromium_roots", lambda name: [root])
    assert cookies._windows_master_key("chrome") is None


def test_app_bound_encryption_is_named_as_the_blocker(monkeypatch, tmp_path):
    root = _windows_root(tmp_path, {"app_bound_encrypted_key": "AAAA"})
    monkeypatch.setattr(cookies, "_chromium_roots", lambda name: [root])
    with pytest.raises(cookies.CookieImportError, match="app-bound"):
        cookies._windows_master_key("chrome")
    with pytest.raises(cookies.CookieImportError, match="ENVOY_VISIBLE_BROWSER=1"):
        cookies._chromium_plaintext(b"v20payload", b"k" * 32, platform="win32")


def test_a_v10_value_is_decrypted_with_the_windows_gcm_key():
    key = bytes(range(32))
    encrypted = aesgcm.encrypt(key, b"SID=douyin-session", bytes(range(12)))
    assert (
        cookies._chromium_plaintext(b"v10" + encrypted, key, platform="win32")
        == "SID=douyin-session"
    )


def test_an_unencrypted_value_passes_through_on_windows():
    assert cookies._chromium_plaintext(b"plain", None, platform="win32") == "plain"


def test_windows_import_reads_a_gcm_cookie_store(monkeypatch, tmp_path):
    key = bytes(range(32))
    root = _windows_root(
        tmp_path,
        {"encrypted_key": base64.b64encode(b"DPAPI" + b"wrapped").decode()},
    )
    database = root / "Default" / "Network" / "Cookies"
    connection = sqlite3.connect(database)
    connection.execute(
        "create table cookies (host_key text, name text, value text, "
        "encrypted_value blob, path text, expires_utc integer, "
        "is_secure integer, is_httponly integer)"
    )
    connection.execute(
        "insert into cookies values (?, ?, '', ?, '/', ?, 1, 1)",
        (
            ".douyin.com",
            "sessionid",
            b"v10" + aesgcm.encrypt(key, b"real-session", bytes(range(12))),
            13_435_000_000_000_000,
        ),
    )
    connection.commit()
    connection.close()

    store = tmp_path / "imported-cookies.json"
    monkeypatch.setattr(cookies, "_chromium_roots", lambda name: [root])
    monkeypatch.setattr(cookies, "_dpapi_unprotect", lambda blob: key)
    monkeypatch.setattr(cookies, "cookie_store_path", lambda: store)
    monkeypatch.setattr(cookies.sys, "platform", "win32")

    result = cookies.import_chromium_cookies("chrome", ["douyin.com"])
    assert result["imported"] == 1
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["cookies"][0]["name"] == "sessionid"
    assert saved["cookies"][0]["value"] == "real-session"
