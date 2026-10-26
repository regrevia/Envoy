"""Import a local browser's cookies into Envoy's isolated profile.

Envoy never asks for a password and never posts a login form: the useful move
when a page hides its player behind a wall is to reuse the session that the
user's own browser already holds, for the requested domain only. This module
reads that browser's cookie store, extracts rows that belong to the target
domain, and saves them where the isolated browser can inject them.

Every read is explicit (``envoy auth import --from chrome``) because cookie
access is a deliberate act by the user; nothing here runs during a normal read.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

COOKIE_STORE_NAME = "imported-cookies.json"
STORE_MODE = 0o600
_MAX_AGE_SECONDS = 45 * 24 * 3600

_SAFE_STORAGE_LABELS = {
    "chrome": "Chrome Safe Storage",
    "edge": "Microsoft Edge Safe Storage",
    "brave": "Brave Safe Storage",
    "vivaldi": "Vivaldi Safe Storage",
    "chromium": "Chromium Safe Storage",
}


class CookieImportError(RuntimeError):
    """Raised when a browser store cannot be read on this machine."""


def cookie_store_path() -> Path:
    from .cache import envoy_home

    return envoy_home() / COOKIE_STORE_NAME


def _chromium_roots(name: str) -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        return {
            "chrome": [base / "Google" / "Chrome"],
            "chromium": [base / "Chromium"],
            "edge": [base / "Microsoft Edge"],
            "brave": [base / "BraveSoftware" / "Brave-Browser"],
            "vivaldi": [base / "Vivaldi"],
        }.get(name, [])
    if sys.platform.startswith("linux"):
        base = home / ".config"
        return {
            "chrome": [base / "google-chrome"],
            "chromium": [base / "chromium"],
            "edge": [base / "microsoft-edge"],
            "brave": [base / "BraveSoftware" / "Brave-Browser"],
            "vivaldi": [base / "vivaldi"],
        }.get(name, [])
    base = Path(os.getenv("LOCALAPPDATA") or (home / "AppData" / "Local"))
    return {
        "chrome": [base / "Google" / "Chrome" / "User Data"],
        "edge": [base / "Microsoft" / "Edge" / "User Data"],
        "brave": [base / "BraveSoftware" / "Brave-Browser" / "User Data"],
        "vivaldi": [base / "Vivaldi" / "User Data"],
    }.get(name, [])


def _cookie_databases(name: str) -> list[Path]:
    databases: list[Path] = []
    for root in _chromium_roots(name):
        for candidate in sorted(root.glob("*/Cookies")) + sorted(root.glob("*/Network/Cookies")):
            if candidate.is_file() and candidate not in databases:
                databases.append(candidate)
    return databases


def _safe_storage_password(name: str) -> bytes | None:
    label = _SAFE_STORAGE_LABELS.get(name, "Chrome Safe Storage")
    if sys.platform != "darwin":
        return None
    try:
        completed = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", label],
            capture_output=True,
            timeout=20,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    secret = completed.stdout.strip()
    return secret.encode("utf-8") if completed.returncode == 0 and secret else None


def _chromium_key(password: bytes) -> bytes:
    # Chromium's OSCrypt on macOS/Linux: PBKDF2-HMAC-SHA1, salt "saltysalt",
    # 1003 rounds, 16-byte key, AES-128-CBC with an IV of sixteen spaces.
    import hashlib

    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)


_APP_BOUND_MESSAGE = (
    "This browser protects its cookies with app-bound encryption, which only the "
    "browser's own elevation service can unlock; ask the user to sign in once "
    "in their own terminal with visible browser mode enabled"
)


def _local_state_path(name: str) -> Path | None:
    for root in _chromium_roots(name):
        candidate = root / "Local State"
        if candidate.is_file():
            return candidate
    return None


def _dpapi_unprotect(payload: bytes) -> bytes:
    """Decrypt one DPAPI blob with the current Windows user's login credentials."""

    if sys.platform != "win32":
        raise CookieImportError("Windows DPAPI is not available on this system")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buffer = ctypes.create_string_buffer(payload, len(payload))
    source = DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)
    ):
        raise CookieImportError(
            "Windows refused to unprotect the browser's cookie key; "
            "ask the user to sign in once in their own terminal with visible "
            "browser mode enabled"
        )
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _windows_master_key(name: str) -> bytes | None:
    """Read the AES-256 key that Chrome protects with DPAPI inside `Local State`."""

    path = _local_state_path(name)
    if path is None:
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    os_crypt = state.get("os_crypt") if isinstance(state.get("os_crypt"), dict) else {}
    encoded = str(os_crypt.get("encrypted_key") or "")
    if encoded:
        import base64

        try:
            blob = base64.b64decode(encoded)
        except ValueError:
            return None
        if not blob.startswith(b"DPAPI"):
            raise CookieImportError(f"{name} stores an unexpected cookie key format")
        return _dpapi_unprotect(blob[5:])
    if os_crypt.get("app_bound_encrypted_key"):
        raise CookieImportError(_APP_BOUND_MESSAGE)
    return None


def _decryption_key(name: str) -> bytes | None:
    if sys.platform == "win32":
        return _windows_master_key(name)
    password = _safe_storage_password(name)
    return _chromium_key(password) if password else None


def _chromium_gcm_plaintext(key: bytes, payload: bytes) -> str:
    from .aesgcm import decrypt

    try:
        return decrypt(key, payload).decode("utf-8", "ignore")
    except ValueError as exc:
        raise CookieImportError(
            "Could not decrypt the browser cookie store; "
            "ask the user to sign in once in their own terminal with visible "
            "browser mode enabled"
        ) from exc


def _openssl_decrypt(key: bytes, payload: bytes) -> bytes:
    completed = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-d", "-nopad", "-K", key.hex(), "-iv", "20" * 16],
        input=payload,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise CookieImportError("Could not decrypt the browser cookie store")
    return completed.stdout


def _unpad(value: bytes) -> bytes:
    if not value:
        return value
    padding = value[-1]
    if 1 <= padding <= 16 and value.endswith(bytes([padding]) * padding):
        return value[:-padding]
    return value


def _strip_host_prefix(value: bytes) -> bytes:
    """Drop the 32-byte SHA256(host_key) header Chrome 80+ prepends.

    The prefix is arbitrary binary, so it is detected by non-printable bytes
    rather than by "is this valid UTF-8" — a prefix of zeros is valid UTF-8.
    """

    if len(value) <= 32:
        return value
    if any(byte < 0x09 or byte > 0x7E for byte in value[:32]):
        return value[32:]
    return value


def _chromium_plaintext(raw: bytes, key: bytes | None, *, platform: str | None = None) -> str:
    if not raw:
        return ""
    platform = platform or sys.platform
    if raw[:3] == b"v20":
        raise CookieImportError(_APP_BOUND_MESSAGE)
    if raw[:3] not in (b"v10", b"v11"):
        if (
            platform == "win32"
            and key is None
            and any(byte < 0x09 or byte > 0x7E for byte in raw)
        ):
            # Chrome before version 80 protected each cookie value with DPAPI.
            try:
                return _dpapi_unprotect(raw).decode("utf-8", "ignore")
            except CookieImportError:
                return ""
        return raw.decode("utf-8", "ignore")
    if key is None:
        raise CookieImportError(
            "This browser protects its cookies with a key Envoy cannot read on this "
            "system; ask the user to sign in once in their own terminal with "
            "visible browser mode enabled"
        )
    if platform == "win32":
        # Windows keeps the AES-256-GCM key in Local State, protected by DPAPI,
        # and does not prepend the SHA256(host_key) header that CBC used.
        return _chromium_gcm_plaintext(key, raw[3:])
    plaintext = _strip_host_prefix(_unpad(_openssl_decrypt(key, raw[3:])))
    return plaintext.decode("utf-8", "ignore")


def _chromium_expiry(value) -> float:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return 0.0
    if stamp <= 0:
        return 0.0
    return max(float(stamp) / 1_000_000 - 11_644_473_600, 0.0)


# A built-in subset of the Public Suffix List, enough to tell "the site the user
# is on" from "a suffix strangers share".  The full list is ~10k rules and
# changes monthly; importing a snapshot would mean a shipping data file plus an
# update path, and for cookie import the cost of a missing rule is bounded: an
# unlisted ccTLD behaves like the PSL default rule "*" (the last two labels are
# the site), which can only make the scope narrower than the site, never wider
# than a public suffix.  Multi-label suffixes that would widen the scope into
# unrelated sites are the ones listed here.
_PUBLIC_SUFFIX_RULES = frozenset({
    # United Kingdom
    "ac.uk", "co.uk", "gov.uk", "ltd.uk", "me.uk", "net.uk", "nhs.uk", "org.uk",
    "plc.uk", "police.uk", "sch.uk",
    # Japan
    "ac.jp", "ad.jp", "co.jp", "ed.jp", "go.jp", "gr.jp", "lg.jp", "ne.jp", "or.jp",
    # Australia / New Zealand
    "asn.au", "com.au", "edu.au", "gov.au", "id.au", "net.au", "org.au",
    "ac.nz", "co.nz", "geek.nz", "gen.nz", "govt.nz", "kiwi.nz", "maori.nz",
    "net.nz", "org.nz", "school.nz",
    # Greater China
    "ac.cn", "com.cn", "edu.cn", "gov.cn", "net.cn", "org.cn",
    "com.hk", "edu.hk", "gov.hk", "idv.hk", "net.hk", "org.hk",
    "com.tw", "edu.tw", "gov.tw", "idv.tw", "net.tw", "org.tw",
    # South and South-East Asia
    "com.sg", "edu.sg", "gov.sg", "net.sg", "org.sg",
    "com.my", "edu.my", "gov.my", "net.my", "org.my",
    "ac.in", "co.in", "edu.in", "firm.in", "gen.in", "gov.in", "ind.in", "net.in",
    "org.in", "res.in",
    "ac.kr", "co.kr", "go.kr", "ne.kr", "or.kr", "pe.kr", "re.kr",
    "co.th", "ac.th", "go.th", "in.th", "net.th", "or.th",
    "com.ph", "edu.ph", "gov.ph", "net.ph", "org.ph",
    "ac.id", "co.id", "go.id", "net.id", "or.id", "sch.id", "web.id",
    "com.vn", "edu.vn", "gov.vn", "net.vn", "org.vn",
    "com.bd", "edu.bd", "gov.bd", "net.bd", "org.bd",
    "com.pk", "edu.pk", "gov.pk", "net.pk", "org.pk",
    "biz.np", "com.np", "edu.np", "gov.np", "info.np", "net.np", "org.np",
    # Africa
    "ac.za", "co.za", "gov.za", "net.za", "org.za", "web.za",
    "com.eg", "edu.eg", "gov.eg", "net.eg", "org.eg",
    "com.ng", "edu.ng", "gov.ng", "net.ng", "org.ng",
    # Americas
    "com.br", "edu.br", "gov.br", "net.br", "org.br", "art.br", "blog.br",
    "com.ar", "edu.ar", "gov.ar", "net.ar", "org.ar",
    "com.mx", "edu.mx", "gob.mx", "net.mx", "org.mx",
    "com.co", "edu.co", "gov.co", "net.co", "org.co",
    "com.pe", "edu.pe", "gob.pe", "net.pe", "nom.pe", "org.pe",
    "com.ve", "edu.ve", "gob.ve", "net.ve", "org.ve",
    "com.ec", "edu.ec", "gob.ec", "net.ec", "org.ec",
    "com.uy", "edu.uy", "gub.uy", "net.uy", "org.uy",
    # Africa, East
    "ac.ke", "co.ke", "go.ke", "info.ke", "me.ke", "mobi.ke", "ne.ke", "or.ke",
    "sc.ke",
    "co.ug", "ac.ug", "go.ug", "ne.ug", "or.ug", "sc.ug",
    "co.tz", "ac.tz", "go.tz", "ne.tz", "or.tz", "sc.tz",
    "com.gh", "edu.gh", "gov.gh", "org.gh",
    "co.ma", "net.ma", "org.ma", "ac.ma", "gov.ma", "press.ma",
    "com.dz", "edu.dz", "gov.dz", "org.dz", "net.dz",
    "com.tn", "ens.tn", "fin.tn", "gov.tn", "ind.tn", "intl.tn", "nat.tn",
    "net.tn", "org.tn", "info.tn", "perso.tn", "tourism.tn",
    # Europe, additional registry hierarchies
    "com.es", "nom.es", "org.es", "gob.es", "edu.es",
    "com.pt", "edu.pt", "gov.pt", "int.pt", "net.pt", "nome.pt", "org.pt", "publ.pt",
    "com.gr", "edu.gr", "gov.gr", "net.gr", "org.gr",
    "com.pl", "net.pl", "org.pl", "info.pl", "biz.pl", "edu.pl", "gov.pl",
    "com.ru", "net.ru", "org.ru", "pp.ru",
    "com.cy", "ac.cy", "net.cy", "gov.cy", "org.cy",
    "com.mt", "edu.mt", "net.mt", "org.mt",
    "ac.ae", "co.ae", "gov.ae", "net.ae", "org.ae", "sch.ae",
    # Europe and the Middle East
    "com.tr", "edu.tr", "gov.tr", "net.tr", "org.tr", "av.tr", "bel.tr", "biz.tr",
    "info.tr", "k12.tr", "name.tr", "pol.tr", "tel.tr", "web.tr",
    "com.ua", "edu.ua", "gov.ua", "net.ua", "org.ua",
    "ac.il", "co.il", "gov.il", "muni.il", "net.il", "org.il",
    "com.sa", "edu.sa", "gov.sa", "med.sa", "net.sa", "org.sa", "pub.sa", "sch.sa",
})

# Wildcard rules (the PSL's "*.suffix" form) and their exceptions ("!host").
# A wildcard makes every direct child of the suffix a public suffix, so
# foo.ck is a registry, not a site.
_PUBLIC_SUFFIX_WILDCARDS = frozenset({
    "bd", "ck", "er", "fk", "jm", "kh", "mm", "np", "pg",
    "kawasaki.jp", "kitakyushu.jp", "platform.sh",
})
_PUBLIC_SUFFIX_EXCEPTIONS = frozenset({
    "city.kawasaki.jp", "city.kitakyushu.jp", "www.ck",
})


# Private sections: hosting providers that give every customer its own name
# under a shared suffix (evil.github.io, myblog.blogspot.com, app.vercel.app).
# Tumblr and S3 are the classic case: the shared suffix is not a registry, so
# the site is "private suffix + one label".  These never take part in the
# ICANN wildcard/exception rules above; they are matched exactly.
_PRIVATE_SUFFIX_RULES = frozenset({
    "appspot.com", "azurewebsites.net", "bitbucket.io", "blogspot.com",
    "cloudfront.net", "compute.amazonaws.com", "firebaseapp.com", "github.io",
    "githubusercontent.com", "gitlab.io", "herokuapp.com", "myshopify.com",
    "netlify.app", "notion.site", "pages.dev", "readthedocs.io",
    "s3.amazonaws.com", "vercel.app", "web.app", "workers.dev",
})

def _host_labels(host: str) -> list[str]:
    return [label for label in str(host or "").strip().strip(".").lower().split(".") if label]


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(str(host or "").strip("[]"))
    except ValueError:
        return False
    return True


def _canonical_host(host: str) -> str:
    """One spelling for every host: no brackets, no port, no trailing dot."""

    if _is_ip_literal(host):
        return str(ipaddress.ip_address(str(host).strip("[]")))
    return ".".join(_host_labels(host))


def _strip_port(value: str) -> str:
    """Drop a port the way urlparse would, including [::1]:443."""

    raw = str(value or "").strip()
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end != -1 else raw.strip("[]")
    if raw.count(":") == 1:
        return raw.split(":", 1)[0]
    return raw


def _is_local_host(host: str) -> bool:
    host = _canonical_host(host)
    return host == "localhost" or host.endswith(".localhost") or _is_ip_literal(host)


def _idna(host: str) -> str:
    """Compare registrable names, not their Unicode spelling."""

    host = str(host or "").strip().lower().rstrip(".")
    if not host or _is_ip_literal(host):
        return host
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return host


def _public_suffix(host: str) -> str:
    """The ICANN boundary for host, per the built-in rules."""

    labels = _host_labels(host)
    if len(labels) <= 1:
        return ".".join(labels)
    # Exception rules win over wildcards: !www.ck means www.ck is a site.
    for index in range(len(labels)):
        if ".".join(labels[index:]) in _PUBLIC_SUFFIX_EXCEPTIONS:
            return ".".join(labels[index + 1:])
    matched = 1  # the PSL default rule "*": an unknown TLD is a suffix itself
    for index in range(len(labels)):
        candidate = ".".join(labels[index:])
        if candidate in _PUBLIC_SUFFIX_RULES:
            matched = max(matched, len(labels) - index)
        if index + 1 < len(labels) and ".".join(labels[index + 1:]) in _PUBLIC_SUFFIX_WILDCARDS:
            matched = max(matched, len(labels) - index)
    return ".".join(labels[len(labels) - matched:])


def _private_suffix(host: str) -> str:
    """The longest hosting-provider suffix that owns host, or ""."""

    labels = _host_labels(host)
    for index in range(len(labels)):
        candidate = ".".join(labels[index:])
        if candidate in _PRIVATE_SUFFIX_RULES:
            return candidate
    return ""


def _public_suffix_error(scope: str) -> CookieImportError:
    return CookieImportError(
        f"{scope} 是公共后缀，请输入站点域名，例如 example.co.uk"
    )


def _registrable_domain(host: str) -> str:
    """The site host belongs to: "suffix + one label", private sections included."""

    private = _private_suffix(host)
    boundary = private or _public_suffix(host)
    if host == boundary:
        raise _public_suffix_error(host)
    labels = _host_labels(host)
    return ".".join(labels[-(len(_host_labels(boundary)) + 1):])


def _scope_for(value: str) -> str:
    """Validate one import scope: a site, a subdomain of it, or a local host."""

    host = _canonical_host(_strip_port(value))
    if not host:
        raise CookieImportError("请输入站点域名，例如 example.co.uk")
    if _is_local_host(host):
        return host
    if "." not in host or host in (_public_suffix(host), _private_suffix(host)):
        raise _public_suffix_error(host)
    return host


def _scope_from_host(host: str) -> str:
    """The site to import for a page: a local host, or the registrable domain."""

    host = _canonical_host(_strip_port(host))
    if not host:
        raise CookieImportError("这个链接没有可用的站点域名，请输入站点域名，例如 example.co.uk")
    if _is_local_host(host):
        return host
    try:
        return _registrable_domain(host)
    except CookieImportError as error:
        raise CookieImportError(
            f"该链接只包含公共后缀 {host}，请输入站点域名，例如 example.co.uk"
        ) from error


def _matches_domain(host_key: str, domains: list[str]) -> bool:
    host = _idna(_canonical_host(_strip_port(host_key)))
    if not host:
        return False
    return any(
        host == domain or host.endswith("." + domain)
        for domain in (_idna(_canonical_host(_strip_port(item))) for item in domains)
        if domain
    )


def _domain_filter(domains: list[str] | None, source_url: str | None) -> list[str]:
    wanted = [_scope_for(str(item)) for item in domains or [] if str(item).strip()]
    if not wanted and source_url:
        wanted = [_scope_from_host(urlparse(source_url).hostname or "")]
    if not wanted:
        raise CookieImportError("Name the domain to copy cookies for, or pass the link to read")
    return wanted


def import_chromium_cookies(name: str, domains: list[str]) -> dict:
    databases = _cookie_databases(name)
    if not databases:
        raise CookieImportError(f"No {name} cookie store was found on this machine")
    key = _decryption_key(name)
    cookies: list[dict] = []
    now = datetime.now(timezone.utc).timestamp()
    for database in databases:
        with tempfile.TemporaryDirectory(prefix="envoy-cookies-") as staging:
            copy = Path(staging) / "Cookies"
            copy.write_bytes(database.read_bytes())
            try:
                connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
                rows = connection.execute(
                    "select host_key, name, value, encrypted_value, path, expires_utc, "
                    "is_secure, is_httponly from cookies"
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                raise CookieImportError(
                    f"The {name} cookie database is locked or unreadable ({type(exc).__name__}); "
                    f"quit {name} and retry"
                ) from exc
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
        for host_key, cookie_name, value, encrypted, path, expires_utc, secure, httponly in rows:
            if not _matches_domain(host_key, domains):
                continue
            plain = str(value or "") or _chromium_plaintext(bytes(encrypted or b""), key)
            if not plain:
                continue
            expiry = _chromium_expiry(expires_utc)
            if expiry and expiry < now - _MAX_AGE_SECONDS:
                continue
            cookies.append({
                "name": str(cookie_name),
                "value": plain,
                "domain": str(host_key),
                "path": str(path or "/"),
                "expires": expiry,
                "httpOnly": bool(httponly),
                "secure": bool(secure),
            })
    return _save_cookies(name, domains, cookies)


def safari_cookie_files() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Library" / "Cookies" / "Cookies.binarycookies",
        home / "Library" / "Containers" / "com.apple.Safari" / "Data" / "Library"
        / "Cookies" / "Cookies.binarycookies",
    ]
    return [path for path in candidates if path.is_file()]


def _binarycookies_field(record: bytes, offset: int) -> str:
    if offset <= 0 or offset >= len(record):
        return ""
    end = record.find(b"\x00", offset)
    if end < 0:
        end = len(record)
    return record[offset:end].decode("utf-8", "ignore")


def _parse_binarycookies(payload: bytes) -> list[dict]:
    if len(payload) < 8 or payload[:4] != b"cook":
        raise CookieImportError("Safari's cookie file is not in binarycookies format")
    count = int.from_bytes(payload[4:8], "big")
    cursor = 8
    sizes = []
    for _ in range(count):
        sizes.append(int.from_bytes(payload[cursor:cursor + 4], "big"))
        cursor += 4
    pages = []
    for size in sizes:
        pages.append(payload[cursor:cursor + size])
        cursor += size
    cookies: list[dict] = []
    for page in pages:
        if len(page) < 8:
            continue
        records = int.from_bytes(page[4:8], "little")
        positions = [
            int.from_bytes(page[8 + 4 * index:12 + 4 * index], "little")
            for index in range(records)
        ]
        for position in positions:
            if position + 56 > len(page):
                continue
            size = int.from_bytes(page[position:position + 4], "little")
            record = page[position:position + size]
            if len(record) < 56:
                continue
            flags = int.from_bytes(record[8:12], "little")
            url = _binarycookies_field(record, int.from_bytes(record[16:20], "little"))
            name = _binarycookies_field(record, int.from_bytes(record[20:24], "little"))
            path = _binarycookies_field(record, int.from_bytes(record[24:28], "little")) or "/"
            value = _binarycookies_field(record, int.from_bytes(record[28:32], "little"))
            expiry = float(int.from_bytes(record[40:48], "little")) or 0.0
            if expiry:
                expiry = max(expiry + 978_307_200, 0.0)
            if not name or not url:
                continue
            cookies.append({
                "name": name,
                "value": value,
                "domain": url,
                "path": path,
                "expires": expiry,
                "httpOnly": bool(flags & 0x4),
                "secure": bool(flags & 0x1),
            })
    return cookies


def import_safari_cookies(domains: list[str]) -> dict:
    files = safari_cookie_files()
    if not files:
        raise CookieImportError("No Safari cookie file was found on this machine")
    now = datetime.now(timezone.utc).timestamp()
    cookies: list[dict] = []
    for path in files:
        for cookie in _parse_binarycookies(path.read_bytes()):
            if not _matches_domain(cookie["domain"], domains):
                continue
            if cookie["expires"] and cookie["expires"] < now - _MAX_AGE_SECONDS:
                continue
            cookies.append(cookie)
    return _save_cookies("safari", domains, cookies)


def _save_cookies(source: str, domains: list[str], cookies: list[dict]) -> dict:
    if not cookies:
        raise CookieImportError(
            f"{source} holds no cookie for {' or '.join(domains)}; ask the user to sign "
            f"in to that site in {source}, in their own terminal with visible browser "
            f"mode enabled"
        )
    target = cookie_store_path()
    payload = {
        "source": source,
        "domains": domains,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cookies": cookies,
    }
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".cookies-", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.chmod(temporary, STORE_MODE)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "source": source,
        "imported": len(cookies),
        "domains": domains,
        "path": str(target),
        "cookies": sorted({cookie["name"] for cookie in cookies})[:32],
    }


def import_cookies(source: str, domains: list[str] | None = None, source_url: str | None = None) -> dict:
    name = str(source or "").strip().lower()
    wanted = _domain_filter(domains, source_url)
    if name == "safari":
        return import_safari_cookies(wanted)
    if name in _SAFE_STORAGE_LABELS:
        return import_chromium_cookies(name, wanted)
    raise CookieImportError(
        f"Unknown cookie source {source!r}; choose chrome, edge, brave, vivaldi, chromium, or safari"
    )


def load_imported_cookies() -> dict:
    path = cookie_store_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_cookies_for(url: str) -> list[dict]:
    """Cookies for one URL, filtered to its own domain."""

    payload = load_imported_cookies()
    cookies = payload.get("cookies") or []
    host = (urlparse(str(url or "")).hostname or "").lower()
    if not host:
        return []
    selected = []
    for cookie in cookies:
        try:
            domain = _scope_for(str(cookie.get("domain") or ""))
        except CookieImportError:
            # A store written before scopes were validated can hold a public
            # suffix (or a bare junk value); never hand those out.
            continue
        if _matches_domain(host, [domain]):
            selected.append({key: value for key, value in cookie.items() if value not in ("", None)})
    return selected[:200]


def clear_imported_cookies() -> bool:
    path = cookie_store_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def cookies_status() -> dict:
    payload = load_imported_cookies()
    if not payload:
        return {"imported": 0}
    return {
        "imported": len(payload.get("cookies") or []),
        "source": payload.get("source"),
        "domains": payload.get("domains") or [],
        "imported_at": payload.get("imported_at"),
        "path": str(cookie_store_path()),
    }
