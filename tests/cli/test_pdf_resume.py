"""A PDF download that dies mid-body continues from the bytes it has.

A read timeout used to discard a 20 MB partial file and ask for the whole
document again, so on a flaky link three attempts could never add up to a
complete download. The fetch path now sends ``Range: bytes=<size>-`` and keeps
the partial file when the server agrees.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from envoy_cli import reader

BODY = b"%PDF-1.4\n" + bytes(range(97, 123)) * 400
CUT = len(BODY) // 3


def _serve(monkeypatch, handler) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)


def _truncated_response() -> httpx.Response:
    def exhausted():
        yield BODY[:CUT]
        raise httpx.ReadError("connection reset by peer")

    return httpx.Response(
        200,
        headers={
            "content-type": "application/pdf",
            "content-length": str(len(BODY)),
        },
        content=exhausted(),
    )


def _partial_response(start: int) -> httpx.Response:
    return httpx.Response(
        206,
        headers={
            "content-type": "application/pdf",
            "content-range": f"bytes {start}-{len(BODY) - 1}/{len(BODY)}",
            "content-length": str(len(BODY) - start),
        },
        content=BODY[start:],
    )


def test_a_truncated_download_asks_only_for_the_missing_tail(monkeypatch):
    asked: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("range"))
        if request.headers.get("range") is None:
            return _truncated_response()
        start = int(request.headers["range"].split("=", 1)[1].rstrip("-"))
        return _partial_response(start)

    _serve(monkeypatch, handler)

    source = reader._fetch_pdf_source("https://example.com/paper.pdf", 1_000_000, {})

    assert asked == [None, f"bytes={CUT}-"]
    assert source["size_bytes"] == len(BODY)
    assert source["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert source["resumed_bytes"] == CUT
    assert source["input_kind"] == "remote_pdf"
    assert Path(source["path"]).read_bytes() == BODY


def test_a_server_that_ignores_the_range_restarts_the_file(monkeypatch):
    asked: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("range"))
        if request.headers.get("range") is None and len(asked) == 1:
            return _truncated_response()
        return httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-length": str(len(BODY)),
            },
            content=BODY,
        )

    _serve(monkeypatch, handler)

    source = reader._fetch_pdf_source("https://example.com/paper.pdf", 1_000_000, {})

    assert asked == [None, f"bytes={CUT}-"]
    assert source["size_bytes"] == len(BODY)
    assert source["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert source["resumed_bytes"] == 0
    assert Path(source["path"]).read_bytes() == BODY


def test_a_mismatched_content_range_is_not_spliced_into_the_file(monkeypatch):
    asked: list[str | None] = []
    answers = [None, "wrong-range", "full"]

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("range"))
        answer = answers[len(asked) - 1]
        if answer is None:
            return _truncated_response()
        if answer == "wrong-range":
            return _partial_response(0)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-length": str(len(BODY)),
            },
            content=BODY,
        )

    _serve(monkeypatch, handler)

    source = reader._fetch_pdf_source("https://example.com/paper.pdf", 1_000_000, {})

    assert asked == [None, f"bytes={CUT}-", None]
    assert source["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert source["resumed_bytes"] == 0
    assert Path(source["path"]).read_bytes() == BODY


def test_a_range_that_cannot_be_satisfied_starts_from_zero(monkeypatch):
    asked: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("range"))
        if request.headers.get("range") is None and len(asked) == 1:
            return _truncated_response()
        if request.headers.get("range") is not None and len(asked) == 2:
            return httpx.Response(416, headers={"content-range": f"bytes */{len(BODY)}"})
        return httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-length": str(len(BODY)),
            },
            content=BODY,
        )

    _serve(monkeypatch, handler)

    source = reader._fetch_pdf_source("https://example.com/paper.pdf", 1_000_000, {})

    assert asked == [None, f"bytes={CUT}-", None]
    assert source["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert Path(source["path"]).read_bytes() == BODY


def test_the_byte_cap_counts_the_resumed_bytes_once(monkeypatch):
    """A resumed download cannot deliver more than max_bytes in total."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("range") is None:
            return _truncated_response()
        return _partial_response(CUT)

    _serve(monkeypatch, handler)

    with pytest.raises(reader.PublicPageError):
        reader._fetch_pdf_source("https://example.com/paper.pdf", CUT + 10, {})
