from __future__ import annotations

import hashlib
import http.server
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from envoy_cli import media


class _ModelHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server contract
        payload = self.server.payload  # type: ignore[attr-defined]
        rate = self.server.rate  # type: ignore[attr-defined]
        start = 0
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            start = int(range_header.split("=", 1)[1].split("-", 1)[0] or "0")
        body = payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
        self.end_headers()
        step = max(1, rate // 8) if rate else len(body) or 1
        sent = 0
        while sent < len(body):
            piece = body[sent : sent + step]
            self.wfile.write(piece)
            self.wfile.flush()
            sent += len(piece)
            if rate:
                time.sleep(0.125)

    def log_message(self, *args) -> None:  # silence test output
        pass


@pytest.fixture()
def model_server():
    servers = []

    def start(payload: bytes, rate: int = 0):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
        server.payload = payload  # type: ignore[attr-defined]
        server.rate = rate  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        port = server.server_address[1]
        digest = hashlib.sha256(payload).hexdigest()
        url = f"http://127.0.0.1:{port}/{digest}/base.pt"
        return SimpleNamespace(_MODELS={"base": url}, url=url, digest=digest)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_download_completes_and_verifies(tmp_path: Path, model_server):
    payload = bytes(range(256)) * 800  # 200 KiB
    whisper = model_server(payload)
    result = media._download_whisper_model(whisper, "base", tmp_path)
    assert result == {
        "downloaded_bytes": len(payload),
        "resumed": False,
        "sha256": whisper.digest,
    }
    assert (tmp_path / "base.pt").read_bytes() == payload


def test_download_resumes_from_partial_file(tmp_path: Path, model_server):
    payload = bytes(range(256)) * 2000  # 500 KiB
    whisper = model_server(payload)
    head = payload[: 300 * 1024]
    (tmp_path / "base.pt.part").write_bytes(head)
    result = media._download_whisper_model(whisper, "base", tmp_path)
    assert result["resumed"] is True
    assert result["downloaded_bytes"] == len(payload)
    assert (tmp_path / "base.pt").read_bytes() == payload


def test_slow_download_stops_at_budget_and_keeps_partial(
    tmp_path: Path, model_server, monkeypatch: pytest.MonkeyPatch
):
    payload = bytes(range(256)) * 2048  # 512 KiB
    whisper = model_server(payload, rate=128 * 1024)
    monkeypatch.setenv("ENVOY_ASR_DOWNLOAD_SECONDS", "1.5")
    started = time.monotonic()
    with pytest.raises(media.MediaProcessingError) as excinfo:
        media._download_whisper_model(whisper, "base", tmp_path)
    elapsed = time.monotonic() - started
    message = str(excinfo.value)
    assert "Whisper base model download stopped after" in message
    assert "partial file is kept" in message
    assert "envoy asr install --model base" in message
    assert elapsed < 12
    partial = tmp_path / "base.pt.part"
    assert partial.is_file()
    assert 0 < partial.stat().st_size < len(payload)
    assert not (tmp_path / "base.pt").exists()


def test_download_budget_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ENVOY_ASR_DOWNLOAD_SECONDS", raising=False)
    assert media._asr_download_seconds_budget() == 600.0
    monkeypatch.setenv("ENVOY_ASR_DOWNLOAD_SECONDS", "off")
    assert media._asr_download_seconds_budget() == 600.0
    monkeypatch.setenv("ENVOY_ASR_DOWNLOAD_SECONDS", "90")
    assert media._asr_download_seconds_budget() == 90.0


def test_download_reports_progress_on_slow_links(
    tmp_path: Path, model_server, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    payload = bytes(range(256)) * 4096  # 1 MiB
    whisper = model_server(payload, rate=256 * 1024)
    monkeypatch.setenv("ENVOY_ASR_DOWNLOAD_SECONDS", "30")
    monkeypatch.setattr(media, "_ASR_DOWNLOAD_REPORT_SECONDS", 0.5)
    media._download_whisper_model(whisper, "base", tmp_path)
    err = capsys.readouterr().err
    assert "Whisper model download:" in err
    assert "eta " in err
