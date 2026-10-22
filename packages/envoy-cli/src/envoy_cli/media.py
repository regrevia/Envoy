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
from typing import Any
from urllib.parse import urlsplit, urlunsplit


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


def selected_asr_model() -> str:
    override = os.getenv("ENVOY_ASR_MODEL", "").strip()
    if override:
        return override
    model = str(_read_asr_config().get("model") or "base")
    return model if model in SUPPORTED_ASR_MODELS else "base"


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
    if model_file_present and configured_digest:
        model_verified = (
            _sha256_file(asr_model_cache_dir() / f"{model_name}.pt")
            == configured_digest
        )
    model_available = asr_installed and model_file_present and model_verified is True
    operational = "not_checked"
    if verify_asr and not asr_installed:
        operational = "engine_missing"
    elif verify_asr and not model_file_present:
        operational = "model_missing"
    elif verify_asr:
        expected_sha256 = configured_digest or _model_expected_sha256(model_name)
        model_verified = bool(
            expected_sha256
            and _sha256_file(asr_model_cache_dir() / f"{model_name}.pt")
            == expected_sha256
        )
        if model_verified:
            try:
                _verify_whisper_inference(model_name)
                operational = "verified"
            except Exception:
                operational = "failed"
            else:
                persist_asr_selection(model_name, expected_sha256)
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

    partial = target.with_name(target.name + ".part")
    resumed = partial.is_file() and partial.stat().st_size > 0
    last_error = None
    last_progress = 0
    for attempt in range(20):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = urllib.request.Request(url, headers=headers)
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
                mode = "ab" if append else "wb"
                with partial.open(mode) as destination:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        downloaded = destination.tell()
                        if downloaded - last_progress >= 16 * 1024 * 1024:
                            print(
                                f"Whisper model download checkpoint: {downloaded} bytes",
                                file=sys.stderr,
                                flush=True,
                            )
                            last_progress = downloaded
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
    try:
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
                candidates = []
                for item in info.get("requested_downloads", []) or []:
                    if isinstance(item, dict) and item.get("filepath"):
                        candidates.append(Path(str(item["filepath"])))
                if not candidates:
                    try:
                        candidates.append(Path(ydl.prepare_filename(info)))
                    except Exception:
                        pass
                candidates.extend(path for path in root.iterdir() if path.is_file())
    except MediaProcessingError:
        raise
    except Exception as exc:
        raise MediaProcessingError(f"Audio download failed: {_safe_error(exc)}") from exc

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
    try:
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
                candidates = []
                for item in info.get("requested_downloads", []) or []:
                    if isinstance(item, dict) and item.get("filepath"):
                        candidates.append(Path(str(item["filepath"])))
                try:
                    candidates.append(Path(ydl.prepare_filename(info)))
                except Exception:
                    pass
                candidates.extend(path for path in root.iterdir() if path.is_file())
    except MediaProcessingError:
        raise
    except Exception as exc:
        raise MediaProcessingError(f"Video download failed: {_safe_error(exc)}") from exc

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
            audio = whisper.load_audio(audio_path, sr=16_000)
            audio_duration = len(audio) / 16_000
            end_seconds = min(float(end_seconds), audio_duration)
            language = None
            cursor = float(start_seconds)
            while cursor < end_seconds:
                chunk_duration = min(float(ASR_CHUNK_SECONDS), end_seconds - cursor)
                start_sample = int(cursor * 16_000)
                end_sample = int((cursor + chunk_duration) * 16_000)
                audio_chunk = audio[start_sample:end_sample]
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


def _transcribe_with_worker(
    source: Path,
    model_name: str,
    model_root: Path,
    start: float,
    end: float,
    duration: float,
    timeout: int,
    started: float,
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_whisper_worker,
        args=(str(source), str(model_name), str(model_root), start, end, send_connection),
        daemon=True,
    )
    process_started = False
    segments = []
    language = ""
    next_seconds = start
    completed = False
    failure = None
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
                segments.extend(event.get("segments") or [])
                language = str(event.get("language") or language)
                next_seconds = float(event.get("end_seconds") or next_seconds)
            elif event.get("event") == "complete":
                completed = True
                language = str(event.get("language") or language)
                next_seconds = float(event.get("end_seconds") or next_seconds)
            elif event.get("event") == "failed":
                failure = str(event.get("error") or "ASR failed")
        if completed or failure is not None:
            process.join(5)
        elif not process.is_alive():
            process.join(1)
        if failure and not segments:
            raise MediaProcessingError(failure)
        if not segments and not completed:
            raise MediaBudgetExceeded(f"ASR produced no cues within max_work_seconds ({timeout})")
        return {
            "status": "success" if completed and segments else "partial" if segments else "no_speech",
            "language": language,
            "segments": segments,
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
    with asr_model_slot(model_root, max_wait_seconds=remaining):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise MediaBudgetExceeded("ASR work budget expired while waiting for its model slot")
        return _transcribe_with_worker(
            source,
            model_name,
            model_root,
            start,
            end,
            duration,
            max(int(remaining), 1),
            started,
        )
