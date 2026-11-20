from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from envoy_cli import media


class _WeightHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server contract
        payload = self.server.payload  # type: ignore[attr-defined]
        start = 0
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            start = int(range_header.split("=", 1)[1].split("-", 1)[0] or "0")
        body = payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        if start:
            self.send_header(
                "Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}"
            )
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def log_message(self, *args) -> None:  # silence test output
        pass


@pytest.fixture()
def weight_server():
    servers = []

    def start(payload: bytes):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _WeightHandler)
        server.payload = payload  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        port = server.server_address[1]
        return SimpleNamespace(url=f"http://127.0.0.1:{port}/ggml-base.bin")

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _use_small_weights(monkeypatch, payload: bytes) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(media.GGML_MODEL_TABLE, "base", (len(payload), digest))


def _serve(tmp_path: Path, monkeypatch, weight_server, payload: bytes) -> Path:
    _use_small_weights(monkeypatch, payload)
    monkeypatch.setenv("ENVOY_WHISPER_CPP_MODEL_CACHE", str(tmp_path))
    server = weight_server(payload)
    monkeypatch.setattr(media, "ggml_model_sources", lambda model: [server.url])
    return tmp_path / "ggml-base.bin"


def test_download_writes_verified_weights_into_the_cpp_cache(
    tmp_path: Path, weight_server, monkeypatch: pytest.MonkeyPatch
):
    payload = bytes(range(256)) * 4000  # 1 MB
    target = _serve(tmp_path, monkeypatch, weight_server, payload)

    result = media._download_ggml_model("base")

    assert result == {
        "downloaded_bytes": len(payload),
        "resumed": False,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert target.read_bytes() == payload
    assert not target.with_name(target.name + ".part").exists()


def test_download_falls_back_to_the_next_mirror(
    tmp_path: Path, weight_server, monkeypatch: pytest.MonkeyPatch
):
    payload = bytes(range(256)) * 4000
    target = _serve(tmp_path, monkeypatch, weight_server, payload)
    good = media.ggml_model_sources("base")[0]
    dead = "http://127.0.0.1:9/ggml-base.bin"  # discard port: refused at once
    monkeypatch.setattr(media, "ggml_model_sources", lambda model: [dead, good])

    result = media._download_ggml_model("base")

    assert result["downloaded_bytes"] == len(payload)
    assert target.read_bytes() == payload


def test_download_rejects_weights_that_do_not_match_the_published_checksum(
    tmp_path: Path, weight_server, monkeypatch: pytest.MonkeyPatch
):
    payload = bytes(range(256)) * 4000
    _serve(tmp_path, monkeypatch, weight_server, payload)
    monkeypatch.setitem(
        media.GGML_MODEL_TABLE, "base", (len(payload), "0" * 64)
    )

    with pytest.raises(media.MediaProcessingError) as excinfo:
        media._download_ggml_model("base")

    assert "checksum does not match" in str(excinfo.value)
    assert not (tmp_path / "ggml-base.bin").exists()


def test_unknown_model_names_never_reach_the_network(monkeypatch: pytest.MonkeyPatch):
    def explode(model: str):
        raise AssertionError("no source should be contacted")

    monkeypatch.setattr(media, "ggml_model_sources", explode)

    with pytest.raises(media.MediaCapabilityMissing) as excinfo:
        media._download_ggml_model("moon-language-v9")

    assert "expected one of" in str(excinfo.value)


def test_source_order_is_hugging_face_then_its_mirror_then_the_override(
    monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("ENVOY_GGML_MIRROR_BASE", raising=False)
    assert media.ggml_model_sources("base") == [
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
        "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
    ]

    monkeypatch.setenv("ENVOY_GGML_MIRROR_BASE", "https://cnb.cool/acme/envoy-weights")
    assert media.ggml_model_sources("base")[-1] == (
        "https://cnb.cool/acme/envoy-weights/ggml-base.bin"
    )
