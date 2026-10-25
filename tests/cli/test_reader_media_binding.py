"""Contracts for binding page players to the requested post and budgeting ASR.

These tests describe behaviour the browser video path must have:

* a page may expose several players (the requested title, a recommendation,
  a preload); a larger unrelated file must never be published as the post,
* the same first 64 KiB only proves a shared prefix, never file equivalence,
* prefix probing is a network request and must inherit the download budget
  and the existence of a consumer,
* the ASR transcript must go through the same char *and* token budget as every
  other text path, and must say what was processed, inventoried and returned.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time
import types

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import browser, reader
from envoy_core.schemas.read_v1 import (
    ErrorCode,
    ExecutionState,
    MaterialKind,
    MaterialOrigin,
    ReadRequest,
    ReadResponse,
    ReadResult,
)

PAGE_URL = "https://www.douyin.com/video/7698386448961683813"
WORK_ID = "7698386448961683813"
CURRENT_CLIP = "https://cdn.example.test/player/current.mp4"
RECOMMENDED_CLIP = "https://cdn.example.test/feed/recommended.mp4"


def _request(
    request_id: str,
    *,
    budget: dict | None = None,
    include_visual: bool = False,
    video_input: str = "none",
) -> ReadRequest:
    payload: dict = {
        "request_id": request_id,
        "source": {"url": PAGE_URL, "kind": "video"},
    }
    if budget:
        payload["budget"] = budget
    if include_visual:
        payload["scope"] = {"include_visual": True}
        payload["host_capabilities"] = {
            "image_input": "local_file",
            "video_input": video_input,
        }
    return ReadRequest.model_validate(payload)


def _empty_response(request: ReadRequest) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
    )


def _video_details(response: ReadResponse) -> dict:
    return response.coverage.modalities["video"].details


def _unresolved(response: ReadResponse) -> str:
    return " ".join(response.coverage.modalities["video"].unresolved)


def _asr_blocks(response: ReadResponse) -> list:
    return [
        block for block in response.blocks
        if block.origin == MaterialOrigin.ASR and block.kind == MaterialKind.TRANSCRIPT
    ]


def _bind_identities(
    monkeypatch,
    identities: dict[str, tuple[str, str, int]],
    *,
    asr_ready: bool = True,
) -> None:
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        reader,
        "_probe_page_media",
        lambda url, _cache: identities.get(url),
    )
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": asr_ready,
        "asr_model_available": asr_ready,
        "asr_model": "base",
    })


def _asr_ready(monkeypatch, *, segments, duration=600.0, status="success", next_seconds=None):
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "asr_model_cache_dir", lambda: Path("/tmp/envoy-asr"))
    monkeypatch.setattr(reader, "probe_media_duration", lambda *_a, **_k: duration)
    monkeypatch.setattr(reader, "transcribe_audio_file", lambda *_a, **_k: {
        "segments": segments,
        "language": "zh",
        "model": "base",
        "status": status,
        "next_seconds": next_seconds,
        "duration_seconds": duration,
        "elapsed_ms": 5,
    })


def _fake_download(tmp_path: Path, downloaded: list[str]):
    video_path = tmp_path / "download.mp4"
    video_path.write_bytes(b"video")

    def download(url, *_args, **_kwargs):
        downloaded.append(url)
        return {
            "path": str(video_path),
            "duration": 10,
            "downloaded_bytes": 5,
            "elapsed_ms": 1,
        }

    return download


# ---------------------------------------------------------------------------
# A. Which page player may be published as the requested post
# ---------------------------------------------------------------------------

def test_browser_video_ambiguous_players_are_not_downloaded(monkeypatch, tmp_path):
    """Two different players, no signal which is the post: download nothing."""

    request = _request("ambiguous-players")
    identities = {
        CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000),
        RECOMMENDED_CLIP: ("bbb", "video/mp4", 9_000_000),
    }
    _bind_identities(monkeypatch, identities)
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        lambda *_a, **_k: pytest.fail(
            "an unrelated recommendation player must never be downloaded as the post"
        ),
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP, RECOMMENDED_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP, RECOMMENDED_CLIP],
    )

    details = _video_details(response)
    assert response.result == ReadResult.PARTIAL
    assert details["media_selection"] == "unconfirmed"
    assert details["transcript_source"] == "unavailable"
    assert details["visual_inspection"] == "not_performed"
    assert "post" in _unresolved(response).lower()
    for url in (CURRENT_CLIP, RECOMMENDED_CLIP):
        assert url not in response.model_dump_json()


def test_browser_video_binds_the_candidate_carrying_the_requested_work_id(
    monkeypatch,
    tmp_path,
):
    """A candidate that carries the requested work id is the post, not the biggest."""

    current = f"https://cdn.example.test/media/{WORK_ID}.mp4"
    request = _request("work-id-binding")
    identities = {
        current: ("aaa", "video/mp4", 1_000_000),
        RECOMMENDED_CLIP: ("bbb", "video/mp4", 9_000_000),
    }
    _bind_identities(monkeypatch, identities)
    downloaded: list[str] = []
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        _fake_download(tmp_path, downloaded),
    )
    _asr_ready(monkeypatch, segments=[{"start": 0.0, "end": 2.0, "text": "hello"}])

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [current, RECOMMENDED_CLIP],
        _empty_response(request),
        bound_media_urls=[RECOMMENDED_CLIP, current],
    )

    assert downloaded == [current]
    details = _video_details(response)
    assert details["media_selection"] == "work_id"
    assert details["media_selected_work_id"] == WORK_ID
    assert details["transcript_source"] == "asr"


def test_browser_video_prefix_match_does_not_prove_file_equivalence(monkeypatch):
    """Same digest with a different total size is two files, not one mirror."""

    request = _request("prefix-not-equivalence")
    identities = {
        CURRENT_CLIP: ("same-prefix", "video/mp4", 1_000_000),
        RECOMMENDED_CLIP: ("same-prefix", "video/mp4", 9_000_000),
    }
    _bind_identities(monkeypatch, identities)
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        lambda *_a, **_k: pytest.fail(
            "a shared 64 KiB prefix must not be treated as file equivalence"
        ),
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP, RECOMMENDED_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP, RECOMMENDED_CLIP],
    )

    assert response.result == ReadResult.PARTIAL
    assert _video_details(response)["media_selection"] == "unconfirmed"


def test_browser_video_ambiguous_players_keep_the_visual_handoff(monkeypatch):
    """When the host asked for frames, ambiguity is a request for input, not a transcript."""

    request = _request(
        "ambiguous-visual-handoff",
        include_visual=True,
        video_input="frames",
    )
    identities = {
        CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000),
        RECOMMENDED_CLIP: ("bbb", "video/mp4", 9_000_000),
    }
    _bind_identities(monkeypatch, identities)
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        lambda *_a, **_k: pytest.fail("ambiguous media must not be downloaded"),
    )
    monkeypatch.setattr(
        reader,
        "extract_video_frames",
        lambda *_a, **_k: pytest.fail("no media means no frames"),
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP, RECOMMENDED_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP, RECOMMENDED_CLIP],
    )

    assert response.result == ReadResult.NEEDS_HOST
    assert response.execution_state == ExecutionState.WAITING_FOR_HOST
    assert [need.type for need in response.needs] == ["request_input"]
    assert _video_details(response)["media_selection"] == "unconfirmed"
    assert _video_details(response)["visual_inspection"] == "not_performed"


def test_browser_video_single_unidentified_player_is_still_bound(monkeypatch, tmp_path):
    """One player on the page is unambiguous even without a work id in its URL."""

    request = _request("single-player")
    _bind_identities(monkeypatch, {CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000)})
    downloaded: list[str] = []
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        _fake_download(tmp_path, downloaded),
    )
    _asr_ready(monkeypatch, segments=[{"start": 0.0, "end": 2.0, "text": "hello"}])

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP],
    )

    assert downloaded == [CURRENT_CLIP]
    assert _video_details(response)["media_selection"] == "single_player"


# ---------------------------------------------------------------------------
# B. ASR transcript budgeting and coverage
# ---------------------------------------------------------------------------

CHINESE_CUES = [
    {"start": float(index * 2), "end": float(index * 2 + 2), "text": f"第{index}段中文转写内容"}
    for index in range(10)
]


def test_browser_asr_respects_the_response_token_budget(monkeypatch, tmp_path):
    """A token budget must cap Chinese ASR text, and the count must be real."""

    request = _request(
        "asr-token-budget",
        budget={"max_response_tokens": 64, "max_response_chars": 12_000},
    )
    _bind_identities(monkeypatch, {CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000)})
    downloaded: list[str] = []
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        _fake_download(tmp_path, downloaded),
    )
    _asr_ready(monkeypatch, segments=CHINESE_CUES, duration=600.0)

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP],
    )

    blocks = _asr_blocks(response)
    details = _video_details(response)
    returned_chars = sum(len(block.content.text) for block in blocks)
    assert returned_chars <= 64, "64 estimated tokens caps Chinese text at ~64 chars"
    assert details["transcript_segment_count"] == 10
    assert details["transcript_segments_returned"] == len(blocks)
    assert len(blocks) < 10
    assert details["transcript_truncated"] is True
    assert details["estimated_returned_tokens"] <= 64
    assert details["continuation_available"] is True
    assert details["transcript_continuation_start_seconds"] == pytest.approx(
        blocks[-1].locator.end_seconds
    )
    assert "token" in _unresolved(response).lower()


def test_browser_asr_reports_processed_delivered_and_remaining_audio(monkeypatch, tmp_path):
    """A partial 30 s of a 600 s file must state the gap and how to continue."""

    request = _request("asr-partial-coverage")
    _bind_identities(monkeypatch, {CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000)})
    downloaded: list[str] = []
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        _fake_download(tmp_path, downloaded),
    )
    _asr_ready(
        monkeypatch,
        segments=[{"start": float(i * 6), "end": float(i * 6 + 6), "text": f"第{i}段"} for i in range(5)],
        duration=600.0,
        status="partial",
        next_seconds=30.0,
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        _empty_response(request),
        duration_seconds=600.0,
        bound_media_urls=[CURRENT_CLIP],
    )

    details = _video_details(response)
    assert response.result == ReadResult.PARTIAL
    assert details["transcript_processed_until_seconds"] == pytest.approx(30.0)
    assert details["transcript_delivered_until_seconds"] == pytest.approx(30.0)
    assert details["transcript_unprocessed_seconds"] == pytest.approx(570.0)
    unresolved = _unresolved(response)
    assert "570" in unresolved
    assert "remains untranscribed" in unresolved.lower()


def test_browser_asr_returns_the_processed_range_inside_a_partial_window(monkeypatch, tmp_path):
    """A requested 120-180 s window reports both the window and the processed part."""

    request = ReadRequest.model_validate({
        "request_id": "asr-scope-window",
        "source": {"url": PAGE_URL, "kind": "video"},
        "scope": {"kind": "range", "start_seconds": 120, "end_seconds": 180},
    })
    _bind_identities(monkeypatch, {CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000)})
    downloaded: list[str] = []
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        _fake_download(tmp_path, downloaded),
    )
    _asr_ready(
        monkeypatch,
        segments=[{"start": 120.0, "end": 150.0, "text": "窗口内的一段"}],
        duration=600.0,
        status="partial",
        next_seconds=150.0,
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        _empty_response(request),
        duration_seconds=600.0,
        bound_media_urls=[CURRENT_CLIP],
    )

    details = _video_details(response)
    assert details["requested_start_seconds"] == pytest.approx(120.0)
    assert details["requested_end_seconds"] == pytest.approx(180.0)
    assert details["transcript_processed_until_seconds"] == pytest.approx(150.0)
    assert details["transcript_delivered_until_seconds"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# C. The prefix probe is a network request under the read budget
# ---------------------------------------------------------------------------

def test_browser_media_probe_waits_for_a_consumer(monkeypatch):
    """No ASR and no frame consumer: no probe request may leave the machine."""

    request = _request("probe-no-consumer")
    _bind_identities(
        monkeypatch,
        {CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000)},
        asr_ready=False,
    )
    monkeypatch.setattr(
        reader,
        "_probe_page_media",
        lambda *_a, **_k: pytest.fail(
            "prefix probing must not run when nothing can consume the media"
        ),
    )
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        lambda *_a, **_k: pytest.fail("media nothing can consume must not be downloaded"),
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP],
    )

    details = _video_details(response)
    assert response.result == ReadResult.PARTIAL
    assert details["media_fetch_skipped"] == "local_asr_unavailable"
    assert details["media_fetched"] is False


def test_browser_media_probe_respects_zero_download_budget(monkeypatch):
    """A zero download budget forbids the prefix probe as well as the download."""

    request = _request("probe-zero-budget", budget={"max_download_bytes": 0})
    _bind_identities(monkeypatch, {CURRENT_CLIP: ("aaa", "video/mp4", 1_000_000)})
    monkeypatch.setattr(
        reader,
        "_probe_page_media",
        lambda *_a, **_k: pytest.fail("zero download budget must not start a probe"),
    )
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        lambda *_a, **_k: pytest.fail("zero download budget must not start a download"),
    )

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        _empty_response(request),
        bound_media_urls=[CURRENT_CLIP],
    )

    details = _video_details(response)
    assert response.result == ReadResult.PARTIAL
    assert details["media_fetch_skipped"] == "download_budget_exhausted"
    assert "max_download_bytes" in _unresolved(response)


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=(b"",), url="https://cdn.example.test/x.mp4"):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks
        self.url = url

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeClient:
    """Minimal httpx.Client stand-in that honours or exposes redirects."""

    def __init__(self, responses, created, *, follow_redirects=False):
        self._responses = list(responses)
        self.follow_redirects = follow_redirects
        self.requests: list[str] = []
        created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def stream(self, _method, url, headers=None):
        self.requests.append(url)
        response = self._responses.pop(0) if self._responses else _FakeResponse()
        hops = 0
        while (
            self.follow_redirects
            and response.status_code in {301, 302, 303, 307, 308}
            and response.headers.get("location")
            and hops < 5
        ):
            hops += 1
            url = response.headers["location"]
            self.requests.append(url)
            response = self._responses.pop(0) if self._responses else _FakeResponse()
        return response


def _httpx_with(monkeypatch, responses) -> list[_FakeClient]:
    import httpx as real_httpx

    created: list[_FakeClient] = []

    def factory(**kwargs):
        return _FakeClient(responses, created, follow_redirects=bool(kwargs.get("follow_redirects")))

    monkeypatch.setattr(reader, "httpx", types.SimpleNamespace(
        Timeout=real_httpx.Timeout,
        TimeoutException=real_httpx.TimeoutException,
        TransportError=real_httpx.TransportError,
        Client=factory,
    ))
    monkeypatch.setattr(reader, "proxy_plan", lambda: [None])
    return created


def test_page_media_probe_rejects_a_redirect_to_a_private_address(monkeypatch):
    """Every redirect hop must pass the public-address check, not just the first URL."""

    private_url = "http://127.0.0.1:8080/secret.mp4"
    monkeypatch.setattr(
        reader,
        "_is_public_http_url",
        lambda url, _cache: str(url).startswith("https://cdn.example.test/"),
    )
    created = _httpx_with(monkeypatch, [
        _FakeResponse(302, {"location": private_url}),
        _FakeResponse(200, {"content-type": "video/mp4"}, chunks=[b"secret"]),
    ])

    result = reader._probe_page_media("https://cdn.example.test/player.mp4", {})

    assert result is None
    requested = [url for client in created for url in client.requests]
    assert requested == ["https://cdn.example.test/player.mp4"]
    assert not any("127.0.0.1" in url for url in requested)


def test_page_media_probe_stops_at_the_hop_limit(monkeypatch):
    """A redirect chain cannot probe forever."""

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)
    responses = [
        _FakeResponse(302, {"location": f"https://cdn.example.test/hop-{index}.mp4"})
        for index in range(12)
    ]
    created = _httpx_with(monkeypatch, responses)

    assert reader._probe_page_media("https://cdn.example.test/player.mp4", {}) is None
    assert len(created[0].requests) <= reader._PAGE_MEDIA_PROBE_MAX_HOPS + 1


def test_page_media_ranking_stops_at_the_probe_byte_budget(monkeypatch):
    """Probing several candidates charges the same byte budget as downloading."""

    candidates = [
        "https://cdn.example.test/one.mp4",
        "https://cdn.example.test/two.mp4",
        "https://cdn.example.test/three.mp4",
    ]
    probed: list[str] = []

    def probe(url, _cache):
        probed.append(url)
        return f"digest-{url}", "video/mp4", 1000

    monkeypatch.setattr(reader, "_probe_page_media", probe)

    ranked = reader._rank_page_media_urls(
        candidates,
        {},
        max_probe_bytes=reader._PAGE_MEDIA_PROBE_BYTES,
    )

    assert probed == [candidates[0]]
    assert ranked == candidates


def test_page_media_ranking_stops_at_the_probe_deadline(monkeypatch):
    """An exhausted work budget means no further prefix requests."""

    monkeypatch.setattr(
        reader,
        "_probe_page_media",
        lambda *_a, **_k: pytest.fail("no probe may start after the deadline"),
    )

    ranked = reader._rank_page_media_urls(
        [CURRENT_CLIP, RECOMMENDED_CLIP],
        {},
        deadline=time.monotonic() - 1,
    )

    assert ranked == [CURRENT_CLIP, RECOMMENDED_CLIP]


def test_isolated_browser_failure_returns_the_exception_not_a_string(monkeypatch):
    """A formatted string silently degraded browser failures to empty content."""

    request = _request("browser-runtime-missing")

    def fail(*_args, **_kwargs):
        raise browser.BrowserRuntimeUnavailable("Chromium is not installed")

    monkeypatch.setattr(browser, "read_page", fail)

    page, error = reader._read_with_isolated_browser(request, PAGE_URL, {})

    assert page is None
    assert isinstance(error, browser.BrowserRuntimeUnavailable)
    assert reader._browser_read_failure_code(error) == ErrorCode.MISSING_CAPABILITY


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            browser.BrowserRuntimeUnavailable("Chromium is not installed"),
            ErrorCode.MISSING_CAPABILITY,
        ),
        (browser.BrowserReadError("navigation timed out"), ErrorCode.NETWORK_ERROR),
        (
            browser.BrowserChallengeRequired("login or verification required"),
            ErrorCode.CHALLENGE_REQUIRED,
        ),
    ],
)
def test_browser_failure_maps_to_a_distinguishable_error_code(
    monkeypatch, error, expected_code
):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_a, **_k: (
            PAGE_URL,
            "<html><head><title>Loading</title></head><body><p>.</p></body></html>",
        ),
    )
    monkeypatch.setattr(reader, "_read_with_isolated_browser", lambda *_a, **_k: (None, error))
    request = ReadRequest.model_validate({
        "request_id": "browser-failure",
        "source": {"url": "https://example.org/article"},
    })

    response = reader.read_public_page(request)

    assert response.result == ReadResult.BLOCKED
    assert response.errors[0].code == expected_code
    assert response.errors[0].action_required is (
        expected_code == ErrorCode.CHALLENGE_REQUIRED
    )
    assert not any(
        getattr(item, "code", None) == ErrorCode.EMPTY_CONTENT
        for item in response.errors
    )
