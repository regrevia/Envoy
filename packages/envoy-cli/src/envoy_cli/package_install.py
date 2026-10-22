from __future__ import annotations

from dataclasses import dataclass
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import shutil
import sys
import time
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
        return [
            PackageIndex(probe.index.url, "adaptive")
            for probe in healthy
        ]
    return candidates


def select_package_index() -> PackageIndex:
    indexes = select_package_indexes()
    return indexes[0]


def resolve_uv() -> str:
    path = shutil.which("uv")
    if not path:
        raise RuntimeError("uv is required to install Envoy media dependencies")
    return path


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
    for index in indexes:
        for attempt in range(max(1, int(max_attempts_per_index))):
            attempts += 1
            command = [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--index-url",
                index.url,
                "--no-progress",
                WHISPER_PACKAGE,
            ]
            environment = {
                **os.environ,
                "UV_HTTP_TIMEOUT": "60",
                "UV_HTTP_RETRIES": "5",
            }
            result = subprocess.run(
                command,
                capture_output=True,
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
            last_error = (result.stderr or result.stdout or "uv pip install failed")[-2000:]
            if attempt + 1 < max(1, int(max_attempts_per_index)):
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(
        f"Could not install {WHISPER_PACKAGE} after {attempts} attempt(s): {last_error}"
    )
