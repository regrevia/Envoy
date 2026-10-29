from __future__ import annotations

import errno
import ipaddress
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import multiprocessing
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager, redirect_stdout
from multiprocessing.connection import wait as wait_for_connections
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from .netpolicy import proxy_plan


class MediaProcessingError(RuntimeError):
    pass


class MediaBudgetExceeded(MediaProcessingError):
    pass


class MediaCapabilityMissing(MediaProcessingError):
    pass


class MediaNoSpeech(MediaProcessingError):
    pass


class MediaUnsafeAddress(MediaProcessingError):
    pass


MIN_YT_DLP_VERSION = (2026, 8, 19)
_YTDLP_NETWORK_LOCK = threading.RLock()
_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS_BY_PATH: dict[str, tuple[threading.RLock, int]] = {}
_ACTIVE_FILE_LOCKS = threading.local()
_YTDLP_PROXY_HOSTS: set[str] = set()
_YTDLP_URL_VALIDATOR = None
SUPPORTED_ASR_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large", "large-v1", "large-v2",
    "large-v3", "large-v3-turbo",
}


def _config_root() -> Path:
    configured = os.getenv("ENVOY_CONFIG_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".envoy"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _asr_config_path() -> Path:
    return _config_root() / "asr.json"


def cached_asr_models() -> list[str]:
    """Whisper checkpoints already on disk, largest first."""

    directory = asr_model_cache_dir()
    found = [
        name for name in SUPPORTED_ASR_MODELS if (directory / f"{name}.pt").is_file()
    ]

    def size(name: str) -> int:
        try:
            return (directory / f"{name}.pt").stat().st_size
        except OSError:
            return 0

    return sorted(found, key=size, reverse=True)


def selected_asr_model() -> str:
    override = os.getenv("ENVOY_ASR_MODEL", "").strip()
    if override:
        return override
    configured = str(_read_asr_config().get("model") or "").strip()
    if configured in SUPPORTED_ASR_MODELS:
        # An explicit or persisted selection wins even when its weights are not
        # on disk: silently switching models would change the transcript.
        return configured
    # Checkpoints on disk do not choose the model: a stale cache must never
    # override the configured name, and envoy asr status reports both.
    return "base"


def _read_asr_config() -> dict[str, Any]:
    try:
        payload = json.loads(_asr_config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def persist_asr_selection(model_name: str, sha256: str | None = None) -> None:
    if model_name not in SUPPORTED_ASR_MODELS:
        raise ValueError(f"Unsupported Whisper model name: {model_name}")
    payload: dict[str, Any] = {"model": model_name, "enabled": True}
    if sha256 and re.fullmatch(r"[0-9a-f]{64}", sha256):
        payload["sha256"] = sha256
    path = _asr_config_path()
    descriptor, temporary = tempfile.mkstemp(prefix=".asr-", dir=str(path.parent))
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_yt_dlp():
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return None
    return YoutubeDL


def _bounded_worker_entry(target, args: tuple, connection) -> None:
    try:
        connection.send({"ok": True, "value": target(*args)})
    except Exception as exc:
        try:
            connection.send({
                "ok": False,
                "error_type": type(exc).__name__,
                "error": _safe_error(exc),
            })
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def run_bounded_worker(target, *args, max_work_seconds: int):
    timeout = max(int(max_work_seconds), 1)
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_bounded_worker_entry,
        args=(target, args, send_connection),
        daemon=True,
    )
    started = time.monotonic()
    process_started = False
    payload = None
    try:
        process.start()
        process_started = True
        send_connection.close()
        deadline = started + timeout
        while payload is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MediaBudgetExceeded(
                    f"Media worker exceeded max_work_seconds ({timeout})"
                )
            ready = wait_for_connections(
                [receive_connection, process.sentinel],
                timeout=min(0.25, remaining),
            )
            if receive_connection in ready:
                try:
                    payload = receive_connection.recv()
                except EOFError:
                    break
            elif process.sentinel in ready:
                break
        if payload is None:
            if time.monotonic() >= deadline:
                raise MediaBudgetExceeded(
                    f"Media worker exceeded max_work_seconds ({timeout})"
                )
            raise MediaProcessingError("Media worker exited without a result")
        remaining = max(deadline - time.monotonic(), 0)
        if remaining:
            process.join(min(1, remaining))
        if not payload.get("ok"):
            error = str(payload.get("error") or "Media worker failed")
            error_type = payload.get("error_type")
            exception_types = {
                "MediaBudgetExceeded": MediaBudgetExceeded,
                "MediaCapabilityMissing": MediaCapabilityMissing,
                "MediaNoSpeech": MediaNoSpeech,
                "MediaUnsafeAddress": MediaUnsafeAddress,
            }
            exception_type = exception_types.get(error_type, MediaProcessingError)
            raise exception_type(error)
        return payload.get("value")
    finally:
        if process_started and process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        receive_connection.close()
        send_connection.close()


@contextmanager
def public_network_guard():
    """Reject non-global addresses from every yt-dlp DNS resolution."""
    global _YTDLP_PROXY_HOSTS, _YTDLP_URL_VALIDATOR
    with _YTDLP_NETWORK_LOCK:
        original_resolver = socket.getaddrinfo
        original_proxy_hosts = _YTDLP_PROXY_HOSTS
        original_url_validator = _YTDLP_URL_VALIDATOR
        _YTDLP_PROXY_HOSTS = _configured_proxy_hosts()

        try:
            from yt_dlp.networking._urllib import RedirectHandler
        except ImportError:
            RedirectHandler = None
        original_redirect_request = (
            RedirectHandler.redirect_request if RedirectHandler is not None else None
        )

        def resolve_public_only(host, port, *args, **kwargs):
            if host is None:
                raise MediaUnsafeAddress("Media extraction attempted a non-public network target")
            results = original_resolver(host, port, *args, **kwargs)
            if not results:
                raise MediaProcessingError("Media hostname did not resolve")
            normalized_host = _normalize_host(str(host))
            if normalized_host in _YTDLP_PROXY_HOSTS:
                return results
            for result in results:
                try:
                    address = ipaddress.ip_address(str(result[4][0]).split("%", 1)[0])
                except (IndexError, TypeError, ValueError):
                    raise MediaUnsafeAddress(
                        "Media extraction attempted a non-public network target"
                    ) from None
                if not address.is_global:
                    raise MediaUnsafeAddress(
                        "Media extraction attempted a non-public network target"
                    )
            return results

        def validate_public_url(value: str) -> None:
            parsed = urlsplit(str(value))
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise MediaUnsafeAddress("Media extraction attempted a non-public HTTP(S) URL")
            try:
                address = ipaddress.ip_address(parsed.hostname.split("%", 1)[0])
                addresses = [address]
            except ValueError:
                try:
                    answers = original_resolver(
                        parsed.hostname,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                        0,
                        socket.SOCK_STREAM,
                    )
                except OSError as exc:
                    raise MediaProcessingError(
                        f"Media hostname resolution failed: {_safe_error(exc)}"
                    ) from exc
                addresses = []
                for answer in answers:
                    try:
                        addresses.append(
                            ipaddress.ip_address(str(answer[4][0]).split("%", 1)[0])
                        )
                    except (IndexError, TypeError, ValueError):
                        raise MediaUnsafeAddress(
                            "Media extraction attempted a non-public network target"
                        ) from None
            if not addresses or any(not address.is_global for address in addresses):
                raise MediaUnsafeAddress(
                    "Media extraction attempted a non-public network target"
                )

        _YTDLP_URL_VALIDATOR = validate_public_url

        if RedirectHandler is not None:
            def validate_redirect(handler, req, fp, code, msg, headers, newurl):
                validate_public_url(newurl)
                return original_redirect_request(handler, req, fp, code, msg, headers, newurl)

            RedirectHandler.redirect_request = validate_redirect

        socket.getaddrinfo = resolve_public_only
        try:
            yield
        finally:
            socket.getaddrinfo = original_resolver
            if RedirectHandler is not None:
                RedirectHandler.redirect_request = original_redirect_request
            _YTDLP_PROXY_HOSTS = original_proxy_hosts
            _YTDLP_URL_VALIDATOR = original_url_validator


def _normalize_host(host: str) -> str:
    value = host.strip().lower().rstrip(".")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value


def _configured_proxy_hosts() -> set[str]:
    hosts = set()
    for name, value in urllib.request.getproxies().items():
        if name.lower() == "no":
            continue
        try:
            parsed = urlsplit(value if "://" in value else f"http://{value}")
            if parsed.hostname:
                hosts.add(_normalize_host(parsed.hostname))
        except ValueError:
            continue
    return hosts


def _media_proxy_attempts() -> list[str]:
    """Ordered yt-dlp proxy attempts: direct first, then the advertised proxy.

    yt-dlp reads the environment and the operating system proxy settings on its
    own, so a media download would otherwise silently take a different egress
    path than the page read that found it. ``""`` pins yt-dlp to a direct
    connection; the shared policy supplies the fallback only when one exists.
    """
    return [proxy or "" for proxy in proxy_plan()]


def guard_yt_dlp_client(ydl) -> None:
    director = getattr(ydl, "_request_director", None)
    handlers = getattr(director, "handlers", None)
    if isinstance(handlers, dict):
        public_handler = handlers.get("Urllib")
        if public_handler is None:
            raise MediaCapabilityMissing(
                "Safe public media fetching requires yt-dlp's urllib request handler"
            )
        director.handlers = {"Urllib": public_handler}
    original_urlopen = getattr(ydl, "urlopen", None)
    if not callable(original_urlopen):
        return

    def guarded_urlopen(request):
        value = request if isinstance(request, str) else getattr(request, "url", None)
        if value is None:
            value = getattr(request, "full_url", "")
        validator = _YTDLP_URL_VALIDATOR
        if validator is None:
            raise MediaProcessingError("Media URL validation guard is not active")
        validator(str(value))
        return original_urlopen(request)

    ydl.urlopen = guarded_urlopen


# A state directory can be present but unwritable: a sandboxed host, a
# read-only container, a CI job that mounts $HOME read-only. Losing the lock is
# survivable (two reads then run concurrently), losing the read is not, so the
# wrappers below downgrade an unwritable lock path to "ran unlocked" instead of
# failing the request with an errno the caller cannot act on.
_UNWRITABLE_LOCK_ERRNOS = frozenset({
    errno.EACCES,
    errno.EPERM,
    errno.EROFS,
    errno.ENOTDIR,
    errno.ENOSPC,
    errno.EDQUOT,
})


@contextmanager
def optional_interprocess_lock(
    lock_path: str | Path,
    *,
    max_wait_seconds: float,
    busy_message: str,
) -> Iterator[bool]:
    """Yield whether the lock was acquired; an unwritable path yields ``False``."""

    manager = interprocess_file_lock(
        lock_path,
        max_wait_seconds=max_wait_seconds,
        busy_message=busy_message,
    )
    try:
        manager.__enter__()
    except OSError as exc:
        if exc.errno not in _UNWRITABLE_LOCK_ERRNOS:
            raise
        yield False
        return
    try:
        yield True
    except BaseException:
        manager.__exit__(*sys.exc_info())
        raise
    else:
        manager.__exit__(None, None, None)


@contextmanager
def interprocess_file_lock(
    lock_path: str | Path,
    *,
    max_wait_seconds: float,
    busy_message: str,
):
    lock_path = Path(lock_path).expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = str(lock_path.resolve())
    active_keys = getattr(_ACTIVE_FILE_LOCKS, "keys", None)
    if active_keys is None:
        active_keys = set()
        _ACTIVE_FILE_LOCKS.keys = active_keys
    if key in active_keys:
        yield
        return
    with _FILE_LOCKS_GUARD:
        entry = _FILE_LOCKS_BY_PATH.get(key)
        local_lock, references = entry if entry is not None else (threading.RLock(), 0)
        _FILE_LOCKS_BY_PATH[key] = (local_lock, references + 1)
    timeout = max(float(max_wait_seconds), 0)
    deadline = time.monotonic() + timeout
    descriptor = None
    lock_handle = None
    lock_kind = None
    registered = False
    acquired = False
    try:
        if not local_lock.acquire(timeout=timeout):
            raise TimeoutError(busy_message)
        acquired = True
        if lock_path.is_symlink():
            raise MediaProcessingError("Cross-process lock must not be a symlink")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        lock_handle = os.fdopen(descriptor, "r+b", buffering=0)
        descriptor = None
        os.chmod(lock_path, 0o600)
        if os.name == "nt":
            import msvcrt

            lock_handle.seek(0, os.SEEK_END)
            if lock_handle.tell() == 0:
                lock_handle.write(b"\0")
            while True:
                try:
                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    lock_kind = "windows"
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EDEADLK, errno.EAGAIN}:
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(busy_message) from None
                    time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_kind = "posix"
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(busy_message) from None
                    time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))
        active_keys.add(key)
        registered = True
        yield
    finally:
        if lock_handle is not None:
            if lock_kind == "windows":
                import msvcrt

                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif lock_kind == "posix":
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
        elif descriptor is not None:
            os.close(descriptor)
        if registered:
            active_keys.remove(key)
            if not active_keys:
                del _ACTIVE_FILE_LOCKS.keys
        if acquired:
            local_lock.release()
        with _FILE_LOCKS_GUARD:
            current = _FILE_LOCKS_BY_PATH.get(key)
            if current is not None and current[0] is local_lock:
                if current[1] <= 1:
                    del _FILE_LOCKS_BY_PATH[key]
                else:
                    _FILE_LOCKS_BY_PATH[key] = (local_lock, current[1] - 1)


@contextmanager
def asr_model_slot(model_root: str | Path, *, max_wait_seconds: float):
    root = Path(model_root).expanduser()
    try:
        with interprocess_file_lock(
            root / ".envoy-asr.lock",
            max_wait_seconds=max_wait_seconds,
            busy_message="ASR runtime is busy; the work budget expired waiting for its model slot",
        ):
            yield
    except TimeoutError as exc:
        raise MediaBudgetExceeded(str(exc)) from None


def asr_model_cache_dir() -> Path:
    return Path(
        os.getenv("ENVOY_ASR_MODEL_CACHE", str(Path.home() / ".cache" / "whisper"))
    ).expanduser()


def _version_tuple(value: str | None) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(item) for item in numbers[:3])


def _model_expected_sha256(model_name: str) -> str | None:
    try:
        import whisper
        url = str(whisper._MODELS[model_name])
        digest = urlsplit(url).path.rstrip("/").split("/")[-2]
    except Exception:
        return None
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None


def _load_whisper_model(model_name: str):
    import whisper
    return whisper.load_model(
        model_name,
        device="cpu",
        download_root=str(asr_model_cache_dir()),
    )


def _verify_whisper_inference(model_name: str) -> None:
    import numpy

    model = _load_whisper_model(model_name)
    result = model.transcribe(
        numpy.zeros(16_000, dtype=numpy.float32),
        fp16=False,
    )
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise MediaProcessingError("Whisper smoke inference returned an invalid result")


def media_runtime_status(*, verify_asr: bool = False) -> dict[str, Any]:
    try:
        asr_installed = importlib.util.find_spec("whisper") is not None
    except (ImportError, ValueError):
        asr_installed = False
    try:
        yt_dlp_version = version("yt-dlp")
    except PackageNotFoundError:
        yt_dlp_version = None
    model_name = selected_asr_model()
    model_file_present = (
        model_name in SUPPORTED_ASR_MODELS
        and (asr_model_cache_dir() / f"{model_name}.pt").is_file()
    )
    asr_config = _read_asr_config()
    configured_digest = (
        str(asr_config.get("sha256") or "")
        if asr_config.get("model") == model_name
        else ""
    )
    model_verified: bool | None = None
    checkpoint_digest = None
    if model_file_present and configured_digest:
        checkpoint_digest = _sha256_file(
            asr_model_cache_dir() / f"{model_name}.pt"
        )
        model_verified = checkpoint_digest == configured_digest
        if not model_verified and asr_installed:
            model_verified = checkpoint_digest == _model_expected_sha256(model_name)
    elif model_file_present and verify_asr and asr_installed:
        registry_digest = _model_expected_sha256(model_name)
        if registry_digest:
            checkpoint_digest = _sha256_file(
                asr_model_cache_dir() / f"{model_name}.pt"
            )
            model_verified = checkpoint_digest == registry_digest
    model_available = asr_installed and model_file_present and model_verified is True
    operational = "not_checked"
    if verify_asr and not asr_installed:
        operational = "engine_missing"
    elif verify_asr and not model_file_present:
        operational = "model_missing"
    elif verify_asr:
        if model_verified:
            try:
                _verify_whisper_inference(model_name)
                operational = "verified"
            except Exception:
                operational = "failed"
            else:
                persist_asr_selection(model_name, checkpoint_digest)
        else:
            operational = "failed"
        model_available = bool(model_verified and operational == "verified")
    return {
        "ffmpeg": shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg")),
        "ffprobe": shutil.which(os.getenv("FFPROBE_BIN", "ffprobe")),
        "yt_dlp": yt_dlp_version,
        "yt_dlp_supported": bool(
            yt_dlp_version and _version_tuple(yt_dlp_version) >= MIN_YT_DLP_VERSION
        ),
        "asr_engine": "openai-whisper" if asr_installed else None,
        "asr_installed": asr_installed,
        "asr_model": model_name,
        "asr_models_cached": cached_asr_models(),
        "asr_model_from_cache": model_name != str(asr_config.get("model") or ""),
        "asr_model_available": model_available,
        "asr_configured": bool(asr_config.get("enabled")) or bool(os.getenv("ENVOY_ASR_MODEL", "").strip()),
        "asr_model_file_present": model_file_present,
        "asr_model_verified": model_verified,
        "asr_operational": operational,
        "asr_next_step": (
            "rerun envoy asr install --model <model>; runtime updates may replace injected media dependencies"
            if bool(asr_config.get("enabled")) and not asr_installed
            else "run envoy asr setup --model <model>"
            if asr_installed and (not model_file_present or model_verified is False)
            else "run envoy doctor --verify-asr"
            if model_file_present and model_verified is None and operational == "not_checked"
            else None
        ),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_ASR_DOWNLOAD_CHUNK_BYTES = 256 * 1024
_ASR_DOWNLOAD_REPORT_SECONDS = 8.0


class _AsrDownloadBudgetExceeded(Exception):
    def __init__(self, downloaded: int, total: int = 0) -> None:
        super().__init__(downloaded, total)
        self.downloaded = downloaded
        self.total = total


def _asr_download_seconds_budget() -> float:
    raw = os.environ.get("ENVOY_ASR_DOWNLOAD_SECONDS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 600.0
    return value if value > 0 else 600.0


def _format_download_size(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MB"


def _format_download_eta(seconds: float) -> str:
    if seconds < 90:
        return f"{max(int(seconds), 1)}s"
    return f"{int(round(seconds / 60))}m"


def _response_total_bytes(response, offset: int) -> int:
    content_length = response.headers.get("Content-Length", "")
    try:
        length = int(content_length)
    except (TypeError, ValueError):
        return 0
    return offset + length if length > 0 else 0


def _download_whisper_model(whisper, model_name: str, cache_dir: Path) -> dict[str, Any]:
    url = str(whisper._MODELS[model_name])
    parsed = urlsplit(url)
    expected_sha256 = parsed.path.rstrip("/").split("/")[-2]
    filename = Path(parsed.path).name
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or not filename:
        raise MediaProcessingError("Whisper model registry has an invalid URL or checksum")

    target = cache_dir / filename
    if target.is_file() and _sha256_file(target) == expected_sha256:
        return {"downloaded_bytes": 0, "resumed": False, "sha256": expected_sha256}

    budget = _asr_download_seconds_budget()
    deadline = time.monotonic() + budget
    partial = target.with_name(target.name + ".part")
    resumed = partial.is_file() and partial.stat().st_size > 0
    last_error = None
    for attempt in range(20):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = urllib.request.Request(url, headers=headers)
        total = 0
        attempt_started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                status = int(status)
                append = offset > 0 and status == 206
                if offset > 0 and not append:
                    offset = 0
                content_range = response.headers.get("Content-Range", "")
                if append and content_range and not content_range.startswith(f"bytes {offset}-"):
                    raise MediaProcessingError("Whisper model server returned an unexpected range")
                total = _response_total_bytes(response, offset)
                mode = "ab" if append else "wb"
                last_report = time.monotonic()
                with partial.open(mode) as destination:
                    while True:
                        if time.monotonic() >= deadline:
                            raise _AsrDownloadBudgetExceeded(destination.tell(), total)
                        chunk = response.read(_ASR_DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        destination.write(chunk)
                        now = time.monotonic()
                        if now - last_report >= _ASR_DOWNLOAD_REPORT_SECONDS:
                            downloaded = destination.tell()
                            elapsed = max(now - attempt_started, 1e-6)
                            rate = max(downloaded - offset, 0) / elapsed
                            message = (
                                f"Whisper model download: {_format_download_size(downloaded)}"
                            )
                            if total > downloaded:
                                message += f" of {_format_download_size(total)}"
                            if rate > 0:
                                message += f" ({_format_download_size(int(rate))}/s"
                                if total > downloaded:
                                    message += ", eta " + _format_download_eta(
                                        (total - downloaded) / rate
                                    )
                                message += ")"
                            print(message, file=sys.stderr, flush=True)
                            last_report = now
                    destination.flush()
                    os.fsync(destination.fileno())
            if _sha256_file(partial) == expected_sha256:
                os.replace(partial, target)
                return {
                    "downloaded_bytes": target.stat().st_size,
                    "resumed": resumed,
                    "sha256": expected_sha256,
                }
            last_error = "downloaded model checksum does not match"
            resumed = True
        except _AsrDownloadBudgetExceeded as exc:
            downloaded = max(exc.downloaded, partial.stat().st_size if partial.exists() else 0)
            where = f"{_format_download_size(downloaded)} downloaded"
            if exc.total > 0:
                where += f" of {_format_download_size(exc.total)}"
            raise MediaProcessingError(
                f"Whisper {model_name} model download stopped after "
                f"{_format_download_eta(budget)} ({where}); the partial file is kept, so "
                f"rerun `envoy asr install --model {model_name}` to resume, or lower the "
                "profile with ENVOY_SETUP_MEDIA=0"
            ) from None
        except (OSError, urllib.error.URLError, MediaProcessingError) as exc:
            last_error = _safe_error(exc)
            resumed = resumed or (partial.is_file() and partial.stat().st_size > 0)
    raise MediaProcessingError(
        f"Whisper model download did not complete after resumable retries ({last_error or 'unknown error'})"
    )


def prepare_asr_model(model_name: str = "base") -> dict[str, Any]:
    try:
        asr_installed = importlib.util.find_spec("whisper") is not None
    except (ImportError, ValueError):
        asr_installed = False
    if not asr_installed:
        raise MediaCapabilityMissing("Install openai-whisper before preparing a local ASR model")
    if model_name not in SUPPORTED_ASR_MODELS:
        raise MediaCapabilityMissing(f"Unsupported Whisper model name: {model_name}")
    cache_dir = asr_model_cache_dir()
    try:
        with asr_model_slot(cache_dir, max_wait_seconds=3600):
            import whisper

            download = _download_whisper_model(whisper, model_name, cache_dir)
            model = whisper.load_model(
                model_name,
                device="cpu",
                download_root=str(cache_dir),
            )
            del model
        persist_asr_selection(model_name, str(download.get("sha256") or ""))
    except Exception as exc:
        raise MediaProcessingError(f"ASR model setup failed: {_safe_error(exc)}") from exc
    return {
        "model": model_name,
        "cache_dir": str(cache_dir),
        "available": True,
        **download,
    }


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    scrubbed = []
    for token in text.split():
        if token.startswith(("https://", "http://")):
            parts = urlsplit(token.rstrip(".,);"))
            token = urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
            if parts.query:
                token += " [query redacted]"
        scrubbed.append(token)
    return " ".join(scrubbed)[:1200] or type(exc).__name__


def _download_progress_hook(
    max_download_bytes: int,
    deadline: float,
    clock=time.monotonic,
):
    downloaded_by_file: dict[str, int] = {}

    def report(update: dict) -> None:
        if clock() > deadline:
            raise MediaBudgetExceeded("Media download exceeded max_work_seconds")
        downloaded = max(
            int(update.get("total_bytes_downloaded") or 0),
            int(update.get("downloaded_bytes") or 0),
        )
        info = update.get("info_dict")
        context_id = info.get("ctx_id") if isinstance(info, dict) else None
        info_path = info.get("filepath") if isinstance(info, dict) else None
        file_key = str(
            context_id
            or update.get("tmpfilename")
            or update.get("filename")
            or info_path
            or "default"
        )
        downloaded_by_file[file_key] = max(
            downloaded_by_file.get(file_key, 0),
            downloaded,
        )
        aggregate_downloaded = sum(downloaded_by_file.values())
        if aggregate_downloaded > max_download_bytes:
            raise MediaBudgetExceeded("Media download exceeded max_download_bytes")

    return report


def download_audio_for_asr(
    source_url: str,
    output_dir: str | Path,
    *,
    max_download_bytes: int,
    max_work_seconds: int,
) -> dict[str, Any]:
    work_limit = max(int(max_work_seconds), 1)
    return run_bounded_worker(
        _download_audio_for_asr_worker,
        source_url,
        str(output_dir),
        max(int(max_download_bytes), 0),
        work_limit,
        max_work_seconds=work_limit,
    )


def _download_audio_for_asr_worker(
    source_url: str,
    output_dir: str,
    max_download_bytes: int,
    max_work_seconds: int,
) -> dict[str, Any]:
    YoutubeDL = _load_yt_dlp()
    if YoutubeDL is None:
        raise MediaCapabilityMissing("yt-dlp is not installed")
    byte_limit = max(int(max_download_bytes), 0)
    work_limit = max(int(max_work_seconds), 1)
    if byte_limit <= 0:
        raise MediaBudgetExceeded("max_download_bytes does not allow media download")

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    deadline = started + work_limit
    options = {
        "ignoreconfig": True,
        "compat_opts": ["prefer-legacy-http-handler"],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": False,
        "cachedir": False,
        "retries": 0,
        "fragment_retries": 0,
        "socket_timeout": min(work_limit, 30),
        "max_filesize": byte_limit,
        "format": "bestaudio/best",
        "outtmpl": str(root / "audio.%(ext)s"),
        "progress_hooks": [_download_progress_hook(byte_limit, deadline)],
    }
    def attempt(proxy: str) -> tuple[dict[str, Any], list[Path]]:
        options["proxy"] = proxy
        with public_network_guard():
            with YoutubeDL(options) as ydl:
                guard_yt_dlp_client(ydl)
                info = ydl.extract_info(source_url, download=True)
                if time.monotonic() >= deadline:
                    raise MediaBudgetExceeded(
                        "Audio extraction exceeded max_work_seconds"
                    )
                if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                    raise MediaProcessingError("Source did not resolve to one video")
                found = []
                for item in info.get("requested_downloads", []) or []:
                    if isinstance(item, dict) and item.get("filepath"):
                        found.append(Path(str(item["filepath"])))
                if not found:
                    try:
                        found.append(Path(ydl.prepare_filename(info)))
                    except Exception:
                        pass
                found.extend(path for path in root.iterdir() if path.is_file())
                return info, found

    info: dict[str, Any] = {}
    candidates: list[Path] = []
    attempts = _media_proxy_attempts()
    for index, proxy in enumerate(attempts):
        try:
            info, candidates = attempt(proxy)
            break
        except MediaBudgetExceeded:
            raise
        except MediaProcessingError:
            if index == len(attempts) - 1:
                raise
        except Exception as exc:
            error = MediaProcessingError(f"Audio download failed: {_safe_error(exc)}")
            if index == len(attempts) - 1:
                raise error from exc

    media_path = next(
        (
            candidate.resolve()
            for candidate in candidates
            if candidate.is_file()
            and candidate.resolve().parent == root
            and candidate.stat().st_size > 0
        ),
        None,
    )
    if media_path is None:
        raise MediaProcessingError("yt-dlp completed without an audio file")
    downloaded_bytes = media_path.stat().st_size
    if downloaded_bytes > byte_limit:
        media_path.unlink(missing_ok=True)
        raise MediaBudgetExceeded("Downloaded audio exceeded max_download_bytes")
    elapsed = time.monotonic() - started
    if elapsed > work_limit:
        media_path.unlink(missing_ok=True)
        raise MediaBudgetExceeded("Audio download exceeded max_work_seconds")
    return {
        "path": str(media_path),
        "video_id": str(info.get("id") or ""),
        "title": str(info.get("title") or ""),
        "duration": info.get("duration"),
        "downloaded_bytes": downloaded_bytes,
        "elapsed_ms": int(elapsed * 1000),
    }


def download_video_for_frames(
    source_url: str,
    output_dir: str | Path,
    *,
    max_download_bytes: int,
    max_work_seconds: int,
) -> dict[str, Any]:
    work_limit = max(int(max_work_seconds), 1)
    return run_bounded_worker(
        _download_video_for_frames_worker,
        source_url,
        str(output_dir),
        max(int(max_download_bytes), 0),
        work_limit,
        max_work_seconds=work_limit,
    )


def _download_video_for_frames_worker(
    source_url: str,
    output_dir: str,
    max_download_bytes: int,
    max_work_seconds: int,
) -> dict[str, Any]:
    YoutubeDL = _load_yt_dlp()
    if YoutubeDL is None:
        raise MediaCapabilityMissing("yt-dlp is not installed")
    try:
        yt_dlp_version = version("yt-dlp")
    except PackageNotFoundError:
        yt_dlp_version = None
    if not yt_dlp_version or _version_tuple(yt_dlp_version) < MIN_YT_DLP_VERSION:
        raise MediaCapabilityMissing(
            f"Video frame extraction requires yt-dlp>={'.'.join(map(str, MIN_YT_DLP_VERSION))}"
        )
    byte_limit = max(int(max_download_bytes), 0)
    work_limit = max(int(max_work_seconds), 1)
    if byte_limit <= 0:
        raise MediaBudgetExceeded("max_download_bytes does not allow video download")
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    deadline = started + work_limit
    options = {
        "ignoreconfig": True,
        "compat_opts": ["prefer-legacy-http-handler"],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "retries": 0,
        "fragment_retries": 0,
        "socket_timeout": min(work_limit, 30),
        "max_filesize": byte_limit,
        "format": "best[height<=720]/bestvideo[height<=720]+bestaudio/best",
        "outtmpl": str(root / "video.%(ext)s"),
        "progress_hooks": [_download_progress_hook(byte_limit, deadline)],
    }
    def attempt(proxy: str) -> tuple[dict[str, Any], list[Path]]:
        options["proxy"] = proxy
        with public_network_guard():
            with YoutubeDL(options) as ydl:
                guard_yt_dlp_client(ydl)
                info = ydl.extract_info(source_url, download=True)
                if time.monotonic() >= deadline:
                    raise MediaBudgetExceeded(
                        "Video extraction exceeded max_work_seconds"
                    )
                if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                    raise MediaProcessingError("Source did not resolve to one video")
                found = []
                for item in info.get("requested_downloads", []) or []:
                    if isinstance(item, dict) and item.get("filepath"):
                        found.append(Path(str(item["filepath"])))
                try:
                    found.append(Path(ydl.prepare_filename(info)))
                except Exception:
                    pass
                found.extend(path for path in root.iterdir() if path.is_file())
                return info, found

    info: dict[str, Any] = {}
    candidates: list[Path] = []
    attempts = _media_proxy_attempts()
    for index, proxy in enumerate(attempts):
        try:
            info, candidates = attempt(proxy)
            break
        except MediaBudgetExceeded:
            raise
        except MediaProcessingError:
            if index == len(attempts) - 1:
                raise
        except Exception as exc:
            error = MediaProcessingError(f"Video download failed: {_safe_error(exc)}")
            if index == len(attempts) - 1:
                raise error from exc

    video_path = next(
        (
            candidate.resolve()
            for candidate in candidates
            if candidate.is_file()
            and candidate.resolve().parent == root
            and candidate.stat().st_size > 0
        ),
        None,
    )
    if video_path is None:
        raise MediaProcessingError("yt-dlp completed without a video file")
    downloaded_bytes = video_path.stat().st_size
    if downloaded_bytes > byte_limit:
        video_path.unlink(missing_ok=True)
        raise MediaBudgetExceeded("Downloaded video exceeded max_download_bytes")
    elapsed = time.monotonic() - started
    if elapsed > work_limit:
        video_path.unlink(missing_ok=True)
        raise MediaBudgetExceeded("Video download exceeded max_work_seconds")
    return {
        "path": str(video_path),
        "video_id": str(info.get("id") or ""),
        "title": str(info.get("title") or ""),
        "duration": info.get("duration"),
        "downloaded_bytes": downloaded_bytes,
        "elapsed_ms": int(elapsed * 1000),
    }


def extract_video_frames(
    video_path: str | Path,
    output_dir: str | Path,
    timestamps: list[float],
    *,
    max_visual_pixels_per_input: int,
    max_work_seconds: int,
    duration_seconds: float | None = None,
) -> list[dict[str, Any]]:
    ffmpeg = shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe = shutil.which(os.getenv("FFPROBE_BIN", "ffprobe"))
    if not ffmpeg or not ffprobe:
        raise MediaCapabilityMissing("Frame extraction requires FFmpeg and FFprobe")
    pixel_limit = max(int(max_visual_pixels_per_input), 0)
    if pixel_limit <= 0:
        raise MediaBudgetExceeded("max_visual_pixels_per_input does not allow frame delivery")
    source = Path(video_path).expanduser().resolve()
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    deadline = started + max(int(max_work_seconds), 1)
    try:
        probe = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(source),
            ],
            capture_output=True,
            text=True,
            timeout=min(max(int(max_work_seconds), 1), 20),
            check=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        source_width = int(stream["width"])
        source_height = int(stream["height"])
    except Exception as exc:
        raise MediaProcessingError(f"Could not inspect video dimensions: {_safe_error(exc)}") from exc
    if source_width <= 0 or source_height <= 0:
        raise MediaProcessingError("Video dimensions are invalid")
    scale = min(1.0, (pixel_limit / (source_width * source_height)) ** 0.5)
    target_width = max(2, int(source_width * scale) // 2 * 2)
    target_height = max(2, int(source_height * scale) // 2 * 2)
    frames = []
    for index, timestamp in enumerate(timestamps):
        seconds = max(float(timestamp), 0)
        if duration_seconds and duration_seconds > 0:
            # Seeking at (or past) the exact end decodes no frame; stay inside
            # the stream that actually exists.
            seconds = min(seconds, max(float(duration_seconds) - 0.05, 0.0))
        if time.monotonic() >= deadline:
            raise MediaBudgetExceeded("Frame extraction exceeded max_work_seconds")
        output = root / f"frame-{index + 1:03d}.jpg"
        remaining = max(1, int(deadline - time.monotonic()))
        try:
            subprocess.run(
                [
                    ffmpeg, "-nostdin", "-v", "error", "-ss", f"{seconds:.3f}",
                    "-i", str(source), "-frames:v", "1", "-vf",
                    f"scale={target_width}:{target_height}:flags=fast_bilinear",
                    "-q:v", "3", "-y", str(output),
                ],
                capture_output=True,
                timeout=remaining,
                check=True,
            )
        except Exception as exc:
            raise MediaProcessingError(f"Frame extraction failed at {seconds:.3f}s: {_safe_error(exc)}") from exc
        if not output.is_file() or output.stat().st_size <= 0:
            raise MediaProcessingError(f"No frame was decoded at {seconds:.3f}s")
        data = output.read_bytes()
        frames.append({
            "path": str(output),
            "timestamp": seconds,
            "width_px": target_width,
            "height_px": target_height,
            "pixels": target_width * target_height,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return frames


def normalize_asr_segments(segments: list[dict], offset_seconds: float = 0) -> list[dict]:
    cues = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        try:
            no_speech_prob = float(item.get("no_speech_prob") or 0)
            average_log_probability = float(item.get("avg_logprob") or 0)
            compression_ratio = float(item.get("compression_ratio") or 0)
        except (TypeError, ValueError):
            no_speech_prob = 0
            average_log_probability = 0
            compression_ratio = 0
        if (
            no_speech_prob > 0.6
            or average_log_probability < -1.0
            or compression_ratio > 2.4
        ):
            continue
        try:
            start = float(item.get("start")) + float(offset_seconds)
            end = float(item.get("end")) + float(offset_seconds)
        except (TypeError, ValueError):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text and re.search(r"\w", text, flags=re.UNICODE) and start >= 0 and end > start:
            cues.append({"start": start, "end": end, "text": text})
    return sorted(cues, key=lambda item: (item["start"], item["end"]))


ASR_CHUNK_SECONDS = 60


def probe_media_duration(
    media_path: str | Path,
    *,
    max_work_seconds: int = 15,
) -> float:
    ffprobe = shutil.which(os.getenv("FFPROBE_BIN", "ffprobe"))
    if not ffprobe:
        raise MediaCapabilityMissing("FFprobe is required to inspect media duration")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            timeout=max(int(max_work_seconds), 1),
            check=True,
        )
        duration = float(result.stdout.strip())
    except Exception as exc:
        raise MediaProcessingError(f"Could not probe audio duration: {_safe_error(exc)}") from exc
    if duration <= 0:
        raise MediaProcessingError("Media duration is empty or invalid")
    return duration


def probe_video_stream(
    media_path: str | Path,
    *,
    max_work_seconds: int = 15,
) -> bool:
    ffprobe = shutil.which(os.getenv("FFPROBE_BIN", "ffprobe"))
    if not ffprobe:
        raise MediaCapabilityMissing("FFprobe is required to inspect local video input")
    source = Path(media_path).expanduser().resolve()
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "v",
                "-show_entries", "stream=codec_type",
                "-of", "json",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=max(int(max_work_seconds), 1),
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
    except Exception as exc:
        raise MediaProcessingError(
            f"Could not inspect local video streams ({type(exc).__name__})"
        ) from exc
    streams = payload.get("streams") if isinstance(payload, dict) else None
    return any(
        isinstance(stream, dict) and stream.get("codec_type") == "video"
        for stream in streams or []
    )


_probe_duration = probe_media_duration


def _decode_audio_window(
    path: str | Path,
    start_seconds: float,
    duration_seconds: float,
    *,
    ffmpeg: str | None = None,
) -> Any:
    """Decode one PCM window into a float32 mono 16 kHz array via FFmpeg.

    Memory stays at one window instead of the whole file, which is what makes a
    multi-hour recording readable on a small machine. A missing FFmpeg raises
    so the caller can fall back to the whole-file decode rather than silently
    returning an empty transcript.
    """

    binary = ffmpeg or shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg"))
    if not binary:
        raise MediaCapabilityMissing("FFmpeg is required to decode audio in windows")
    # Imported after the binary check so a machine without FFmpeg reports the
    # missing decoder rather than an unrelated missing array library.
    import numpy as np
    duration = max(float(duration_seconds), 0.0)
    command = [
        binary,
        "-nostdin",
        "-v", "error",
        "-ss", f"{max(float(start_seconds), 0.0):.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(path),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ac", "1",
        "-ar", "16000",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=max(30.0, duration * 4 + 10),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaProcessingError(
            f"Could not decode audio window ({type(exc).__name__})"
        ) from exc
    if completed.returncode != 0:
        raise MediaProcessingError(
            f"Could not decode audio window (ffmpeg exit {completed.returncode})"
        )
    return np.frombuffer(completed.stdout, dtype="<f4").astype("float32", copy=True)


def _whisper_worker(
    audio_path: str,
    model_name: str,
    download_root: str,
    start_seconds: float,
    end_seconds: float,
    result_connection,
) -> None:
    try:
        with redirect_stdout(sys.stderr):
            import whisper

            model = whisper.load_model(
                model_name,
                device="cpu",
                download_root=download_root,
            )
            # A whole-file decode pins the whole recording in memory (~230 MB
            # per hour at 16 kHz), so a long video would be limited by RAM
            # instead of the work budget. Each chunk is decoded on its own
            # instead, which keeps the footprint at one window (~4 MB per
            # minute) and reads the same samples the whole-file path produced.
            # The whole-file decode stays as the fallback for a machine whose
            # FFmpeg is missing or refuses the file.
            audio = None
            ffmpeg = shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg"))
            language = None
            cursor = float(start_seconds)
            while cursor < end_seconds:
                chunk_duration = min(float(ASR_CHUNK_SECONDS), end_seconds - cursor)
                if audio is not None:
                    start_sample = int(cursor * 16_000)
                    end_sample = int((cursor + chunk_duration) * 16_000)
                    audio_chunk = audio[start_sample:end_sample]
                else:
                    try:
                        audio_chunk = _decode_audio_window(
                            audio_path, cursor, chunk_duration, ffmpeg=ffmpeg
                        )
                    except (MediaCapabilityMissing, MediaProcessingError, ImportError):
                        audio = whisper.load_audio(audio_path, sr=16_000)
                        audio_duration = len(audio) / 16_000
                        end_seconds = min(float(end_seconds), audio_duration)
                        continue
                if len(audio_chunk) <= 0:
                    break
                result = model.transcribe(
                    audio_chunk,
                    task="transcribe",
                    language=language,
                    temperature=0,
                    fp16=False,
                    verbose=False,
                    condition_on_previous_text=False,
                )
                language = str(result.get("language") or language or "")
                chunk_end = min(end_seconds, cursor + (len(audio_chunk) / 16_000))
                result_connection.send({
                    "event": "chunk",
                    "start_seconds": cursor,
                    "end_seconds": chunk_end,
                    "language": language,
                    "segments": normalize_asr_segments(
                        result.get("segments") or [],
                        offset_seconds=cursor,
                    ),
                })
                cursor = chunk_end
            result_connection.send({"event": "complete", "end_seconds": cursor, "language": language or ""})
    except Exception as exc:
        try:
            result_connection.send({"event": "failed", "error": _safe_error(exc)})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        result_connection.close()


def asr_checkpoint_dir() -> Path:
    override = os.getenv("ENVOY_ASR_CHECKPOINT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".envoy" / "asr"


ASR_DECODE_CONFIG = (
    "sr=16000,task=transcribe,temperature=0,fp16=False,condition_on_previous_text=False"
)


def _asr_source_digest(source: Path) -> str:
    """Digest the bytes, never the path a download happened to land in.

    A remote read downloads into a fresh temp directory each run, so an identity
    built from the absolute path never matches and resume never happens. Reading
    the file is cheaper than re-transcribing it and is bounded by the caller's
    work budget.
    """
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asr_identity(
    source: Path,
    model_name: str,
    start: float,
    end: float,
    *,
    source_revision: str | None = None,
) -> str:
    """Name the work, not the file it currently lives in.

    The checkpoint must survive a different temp path with identical bytes,
    must miss when the source really changed, and must not be invalidated by a
    re-signed URL that serves the same bytes. A trusted revision (video id or
    content digest from the resolver) wins over hashing the file again.
    """
    trusted = str(source_revision or "").strip()
    if trusted:
        identity_source = f"revision:{trusted}"
    else:
        identity_source = f"content:{_asr_source_digest(Path(source))}"
    raw = (
        f"asr-checkpoint/v2|{identity_source}|model={model_name}"
        f"|decode={ASR_DECODE_CONFIG}|range={float(start):.3f}-{float(end):.3f}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _merge_asr_segments(*groups: list[dict] | None) -> list[dict]:
    """Merge by chunk identity so a resumed run cannot duplicate cues."""
    merged: dict[tuple[float, float], dict] = {}
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            try:
                start = round(float(item.get("start", item.get("start_seconds")) or 0), 3)
                end = round(float(item.get("end", item.get("end_seconds")) or 0), 3)
            except (TypeError, ValueError):
                continue
            key = (start, end)
            if key not in merged or len(str(item.get("text") or "")) > len(
                str(merged[key].get("text") or "")
            ):
                merged[key] = item
    return [merged[key] for key in sorted(merged)]


def _load_asr_checkpoint(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_asr_checkpoint(path: Path, payload: dict) -> None:
    """One finished chunk is worth keeping even if the process dies next second."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return


def _transcribe_with_worker(
    source: Path,
    model_name: str,
    model_root: Path,
    start: float,
    end: float,
    duration: float,
    timeout: int,
    started: float,
    *,
    checkpoint_path: Path | None = None,
    prior_segments: list[dict] | None = None,
    resume_from: float | None = None,
    prior_language: str = "",
    checkpoint_identity: str = "",
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_whisper_worker,
        args=(
            str(source),
            str(model_name),
            str(model_root),
            start if resume_from is None else float(resume_from),
            end,
            send_connection,
        ),
        daemon=True,
    )
    process_started = False
    segments = []
    # A resumed run continues the language it had already confirmed, instead of
    # letting the first new chunk override it or lose it on a re-timeout.
    language = str(prior_language or "")
    start_from = float(resume_from) if resume_from is not None else float(start)
    confirmed = start_from
    next_seconds = start_from
    completed = False
    failure = None
    carried = list(prior_segments or [])

    def persist(status: str) -> None:
        nonlocal next_seconds
        # The cursor only ever moves forward: a no-new-chunk round, a stalled
        # worker or a second timeout must leave the previous progress intact.
        next_seconds = max(next_seconds, confirmed)
        if checkpoint_path is None:
            return
        if status != "complete" and next_seconds <= start_from and not segments:
            return
        payload = {
            "audio": str(source),
            "model": str(model_name),
            "start": start,
            "end": end,
            "duration_seconds": duration,
            "next_seconds": next_seconds,
            "language": language,
            "segments": _merge_asr_segments(carried, segments),
            "status": status,
            "updated_at": time.time(),
        }
        if checkpoint_identity:
            payload["identity"] = checkpoint_identity
        _write_asr_checkpoint(checkpoint_path, payload)

    try:
        process.start()
        process_started = True
        send_connection.close()
        deadline = started + timeout
        while not completed and failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready = wait_for_connections(
                [receive_connection, process.sentinel],
                timeout=min(1, remaining),
            )
            if receive_connection not in ready:
                if process.sentinel in ready:
                    break
                continue
            try:
                event = receive_connection.recv()
            except EOFError:
                break
            if event.get("event") == "chunk":
                # A chunk with no cues still means those seconds were listened
                # to; recording it keeps a silent stretch from being redone.
                segments = _merge_asr_segments(segments, event.get("segments") or [])
                language = str(event.get("language") or language)
                next_seconds = max(next_seconds, float(event.get("end_seconds") or next_seconds))
                confirmed = max(confirmed, next_seconds)
                persist("partial")
            elif event.get("event") == "complete":
                completed = True
                language = str(event.get("language") or language)
                next_seconds = max(next_seconds, float(event.get("end_seconds") or next_seconds))
                confirmed = max(confirmed, next_seconds)
                persist("complete")
            elif event.get("event") == "failed":
                failure = str(event.get("error") or "ASR failed")
        if completed or failure is not None:
            process.join(5)
        elif not process.is_alive():
            process.join(1)
        everything = _merge_asr_segments(carried, segments)
        progressed = next_seconds > start_from
        if failure and not everything and not progressed:
            raise MediaProcessingError(failure)
        if not everything and not completed and not progressed:
            raise MediaBudgetExceeded(f"ASR produced no cues within max_work_seconds ({timeout})")
        if not completed:
            persist("partial")
        if completed:
            status = "success" if everything else "no_speech"
        elif everything or progressed:
            status = "partial"
        else:
            status = "no_speech"
        return {
            "status": status,
            "language": language,
            "segments": everything,
            "next_seconds": next_seconds,
            "duration_seconds": duration,
            "model": str(model_name),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        if process_started and process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        receive_connection.close()
        send_connection.close()


def transcribe_audio_file(
    audio_path: str | Path,
    *,
    model_name: str = "base",
    download_root: str | Path | None = None,
    max_work_seconds: int = 180,
    start_seconds: float = 0,
    end_seconds: float | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    try:
        asr_installed = importlib.util.find_spec("whisper") is not None
    except (ImportError, ValueError):
        asr_installed = False
    if not asr_installed:
        raise MediaCapabilityMissing(
            "Local ASR is unavailable; install Envoy's media extra to enable Whisper transcription"
        )
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise MediaProcessingError("ASR audio input is missing or empty")
    timeout = max(int(max_work_seconds), 1)
    started = time.monotonic()
    duration = (
        float(end_seconds)
        if end_seconds is not None
        else probe_media_duration(source, max_work_seconds=timeout)
    )
    start = max(float(start_seconds), 0)
    end = min(duration, float(end_seconds)) if end_seconds is not None else duration
    if end <= start:
        raise MediaProcessingError("ASR time range is empty or outside the audio duration")
    model_root = Path(download_root or asr_model_cache_dir()).expanduser()
    model_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise MediaBudgetExceeded("ASR work budget expired before its model slot was acquired")
    identity = _asr_identity(source, model_name, start, end, source_revision=source_revision)
    checkpoint_path = asr_checkpoint_dir() / f"{identity}.json"
    stored = _load_asr_checkpoint(checkpoint_path)
    carried: list[dict] = []
    prior_language = ""
    resume_start = start
    if stored:
        stored_identity = str(stored.get("identity") or "")
        stored_segments = [
            item for item in (stored.get("segments") or []) if isinstance(item, dict)
        ]
        stored_end = float(stored.get("next_seconds") or start)
        # A checkpoint written in another naming scheme (or staged under a
        # different identity) is not this work and must not be resumed onto it.
        same_work = not stored_identity or stored_identity == identity
        if same_work and stored.get("status") == "complete" and stored_end >= end - 0.01:
            merged = _merge_asr_segments(stored_segments)
            return {
                "status": "success" if merged else "no_speech",
                "language": str(stored.get("language") or ""),
                "segments": merged,
                "next_seconds": stored_end,
                "duration_seconds": duration,
                "model": str(model_name),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "cached": True,
                "checkpoint": str(checkpoint_path),
            }
        if same_work and stored_segments and start <= stored_end <= end:
            carried = _merge_asr_segments(stored_segments)
            prior_language = str(stored.get("language") or "")
            resume_start = stored_end
    with asr_model_slot(model_root, max_wait_seconds=remaining):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise MediaBudgetExceeded("ASR work budget expired while waiting for its model slot")
        result = _transcribe_with_worker(
            source,
            model_name,
            model_root,
            start,
            end,
            duration,
            max(int(remaining), 1),
            started,
            checkpoint_path=checkpoint_path,
            prior_segments=carried,
            resume_from=resume_start,
            prior_language=prior_language,
            checkpoint_identity=identity,
        )
        result["cached"] = False
        result["checkpoint"] = str(checkpoint_path)
        return result
