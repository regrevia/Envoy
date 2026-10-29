from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
import subprocess
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


WHISPER_PACKAGE = "openai-whisper==20250625"
PROBE_PACKAGES = ("openai-whisper", "torch")


@dataclass(frozen=True)
class PackageIndex:
    url: str
    source: str


@dataclass(frozen=True)
class IndexProbe:
    index: PackageIndex
    ok: bool
    latency_ms: int


def _normalize_index(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Package index must be a credential-free HTTP(S) URL without query or fragment"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", ""))


def default_package_indexes() -> list[PackageIndex]:
    configured = os.getenv("ENVOY_PYPI_INDEXES", "").strip()
    if configured:
        values = [item for item in configured.split(",") if item.strip()]
        return [
            PackageIndex(_normalize_index(item), "configured")
            for item in values
        ]
    return [
        PackageIndex("https://pypi.org/simple/", "pypi"),
        PackageIndex("https://pypi.tuna.tsinghua.edu.cn/simple/", "regional"),
        PackageIndex("https://mirrors.aliyun.com/pypi/simple/", "regional"),
        PackageIndex("https://pypi.mirrors.ustc.edu.cn/simple/", "regional"),
    ]


def _explicit_package_index() -> PackageIndex | None:
    for variable in (
        "ENVOY_PYPI_INDEX_URL",
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "PIP_INDEX_URL",
    ):
        value = os.getenv(variable, "").strip()
        if value:
            return PackageIndex(_normalize_index(value), "explicit")
    return None


def _probe_index(
    index: PackageIndex,
    *,
    timeout_seconds: float = 4.0,
) -> IndexProbe:
    started = monotonic()
    try:
        for package in PROBE_PACKAGES:
            request = Request(
                urljoin(index.url, f"{package}/"),
                headers={"User-Agent": "Envoy/package-index-probe"},
            )
            with urlopen(request, timeout=timeout_seconds) as response:
                if int(response.status or 200) >= 400:
                    return IndexProbe(index, False, int((monotonic() - started) * 1000))
        return IndexProbe(index, True, int((monotonic() - started) * 1000))
    except Exception:
        return IndexProbe(index, False, int((monotonic() - started) * 1000))


def select_package_indexes() -> list[PackageIndex]:
    explicit = _explicit_package_index()
    if explicit is not None:
        return [explicit]
    candidates = default_package_indexes()
    probes: list[IndexProbe] = []
    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as executor:
        futures = [executor.submit(_probe_index, item) for item in candidates]
        for future in as_completed(futures):
            probes.append(future.result())
    healthy = sorted(
        (probe for probe in probes if probe.ok),
        key=lambda probe: probe.latency_ms,
    )
    if healthy:
        selected = [
            PackageIndex(probe.index.url, "adaptive")
            for probe in healthy
        ]
        latencies = {
            probe.index.url: probe.latency_ms
            for probe in healthy
        }
        print(
            "Envoy package indexes by measured latency: "
            + ", ".join(
                f"{item.url} ({latencies[item.url]} ms)"
                for item in selected
            ),
            file=sys.stderr,
            flush=True,
        )
        return selected
    print(
        "Envoy could not preflight a package index; trying configured sources in order.",
        file=sys.stderr,
        flush=True,
    )
    return candidates


def select_package_index() -> PackageIndex:
    indexes = select_package_indexes()
    return indexes[0]


UV_VERSION = "0.13.6"
_UV_RELEASE = "https://github.com/astral-sh/uv/releases/download"


def _uv_asset() -> str:
    """The uv release asset that runs on this machine."""

    system = platform.system().lower()
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if system == "windows":
        return "uv-aarch64-pc-windows-msvc.zip" if arm else "uv-x86_64-pc-windows-msvc.zip"
    if system == "darwin":
        return (
            "uv-aarch64-apple-darwin.tar.gz"
            if arm
            else "uv-x86_64-apple-darwin.tar.gz"
        )
    if arm:
        return "uv-aarch64-unknown-linux-gnu.tar.gz"
    return "uv-x86_64-unknown-linux-gnu.tar.gz"


def _uv_binary_name() -> str:
    return "uv.exe" if platform.system().lower() == "windows" else "uv"


def _fetch(url: str, *, limit: int = 64 * 1024 * 1024) -> bytes:
    request = Request(url, headers={"User-Agent": "envoy"})
    with urlopen(request, timeout=120) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"download exceeded {limit} bytes: {url}")
    return data


def ensure_uv() -> str:
    """Return a uv executable, installing one under Envoy's home if needed.

    A default (full) install provisions the ASR model, which needs uv. Asking the
    user to install it first is the cost this project is trying to remove, so the
    same binary uv publishes is downloaded once, verified against its own
    SHA-256, and kept next to Envoy's other state.
    """

    existing = shutil.which("uv")
    if existing:
        return existing
    target_dir = Path.home() / ".envoy" / "tools" / "uv" / UV_VERSION
    target = target_dir / _uv_binary_name()
    if target.is_file() and os.access(target, os.X_OK):
        return str(target)
    asset = _uv_asset()
    base = f"{_UV_RELEASE}/{UV_VERSION}/{asset}"
    expected = _fetch(f"{base}.sha256").decode("utf-8", "replace").split()[0].strip()
    if len(expected) != 64:
        raise RuntimeError(f"uv published no usable checksum for {asset}")
    archive = _fetch(base)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != expected:
        raise RuntimeError(
            f"uv download failed its SHA-256 check ({asset}): "
            f"got {digest[:12]}…, expected {expected[:12]}…"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="envoy-uv-") as scratch:
        path = Path(scratch) / asset
        path.write_bytes(archive)
        if asset.endswith(".zip"):
            with zipfile.ZipFile(path) as bundle:
                member = next(
                    (name for name in bundle.namelist() if name.endswith(_uv_binary_name())),
                    "",
                )
                if not member:
                    raise RuntimeError(f"uv archive {asset} contains no {_uv_binary_name()}")
                with bundle.open(member) as source:
                    target.write_bytes(source.read())
        else:
            with tarfile.open(path) as bundle:
                member = next(
                    (
                        item
                        for item in bundle.getmembers()
                        if item.isfile() and item.name.endswith("/" + _uv_binary_name())
                    ),
                    None,
                )
                if member is None:
                    raise RuntimeError(f"uv archive {asset} contains no {_uv_binary_name()}")
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"uv archive {asset} is unreadable")
                target.write_bytes(extracted.read())
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(target)


def resolve_uv() -> str:
    path = shutil.which("uv")
    if path:
        return path
    return ensure_uv()


def install_media_dependencies(
    *,
    model: str = "base",
    max_attempts_per_index: int = 1,
) -> dict:
    if model not in {"tiny", "tiny.en", "base", "base.en", "small", "small.en",
                     "medium", "medium.en", "large", "large-v1", "large-v2",
                     "large-v3", "large-v3-turbo"}:
        raise ValueError(f"Unsupported Whisper model name: {model}")
    indexes = select_package_indexes()
    uv = resolve_uv()
    last_error = ""
    attempts = 0
    for index_number, index in enumerate(indexes, start=1):
        for attempt in range(max(1, int(max_attempts_per_index))):
            attempts += 1
            print(
                f"Installing {WHISPER_PACKAGE} from {index.url} "
                f"(source {index_number}/{len(indexes)}, attempt {attempt + 1})",
                file=sys.stderr,
                flush=True,
            )
            command = [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--index-url",
                index.url,
                WHISPER_PACKAGE,
            ]
            environment = {
                **os.environ,
                "UV_HTTP_TIMEOUT": "60",
                "UV_HTTP_RETRIES": "5",
            }
            result = subprocess.run(
                command,
                text=True,
                check=False,
                env=environment,
            )
            if result.returncode == 0:
                return {
                    "status": "ready",
                    "index_url": index.url,
                    "index_source": index.source,
                    "attempts": attempts,
                    "model": model,
                }
            last_error = f"uv pip install exited with status {result.returncode}"
            print(
                f"Installation from {index.url} failed; "
                "Envoy will try the next reachable index if one is available.",
                file=sys.stderr,
                flush=True,
            )
            if attempt + 1 < max(1, int(max_attempts_per_index)):
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(
        f"Could not install {WHISPER_PACKAGE} after {attempts} attempt(s): {last_error}"
    )
