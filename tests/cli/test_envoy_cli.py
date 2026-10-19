import json
import sys
from io import BytesIO
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT_DIR / "packages" / "envoy-core" / "src"
CLI_DIR = ROOT_DIR / "packages" / "envoy-cli" / "src"
for path in (CORE_DIR, CLI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_cli_read_returns_protocol_material_for_public_html(monkeypatch, capsys):
    from envoy_cli import cli

    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        url = "https://example.org/article"
        content = b"""
        <html><head><title>Deployment Guide</title></head>
        <body><nav>Menu</nav><main><h1>Deployment Guide</h1>
        <p>Run the worker before starting the API.</p>
        <img src="/diagram.png" alt="Architecture diagram">
        </main><footer>Footer</footer></body></html>
        """
        text = content.decode("utf-8")

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.content

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return StreamContext(Response())

    class StreamContext:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(httpx, "Client", Client)
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-static-1",
        "source": {"url": "https://example.org/article"},
        "goal": "Extract the deployment steps",
    }

    exit_code = cli.main(["read", "--request-json", "-"], stdin=json.dumps(request))

    assert exit_code == 0
    response = json.loads(capsys.readouterr().out)
    assert response["protocol_version"] == "envoy.read/1"
    assert response["result"] == "ready"
    assert response["execution_state"] == "completed"
    assert any("Run the worker before starting the API." in block["content"].get("text", "") for block in response["blocks"])
    assert response["resources"][0]["delivery"]["uri"] == "https://example.org/diagram.png"


def test_goal_ranks_relevant_images_without_changing_source_coordinates(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        "<html><body><p>Deployment guide text remains in source order.</p>"
        '<img src="https://cdn.example.org/diagram.png" alt="Architecture diagram">'
        '<img src="https://cdn.example.org/command.png" alt="Deployment command">'
        "</body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-goal-ranking",
        "source": {"url": "https://example.org/guide"},
        "goal": "Find the deployment command",
        "budget": {"max_visual_inputs": 1},
    }))

    assert response.result.value == "partial"
    assert response.resources[0].delivery.uri.endswith("command.png")
    details = response.coverage.modalities["images"].details
    assert "deployment" in details["goal_terms"]
    assert details["goal_ranked"] is True
    assert details["selected_candidates"][0]["alt"] == "Deployment command"


def test_goal_reports_later_text_evidence_candidates_for_targeted_expansion(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source = (
        "Introduction and context.\n\n"
        "General background that is not the requested detail.\n\n"
        "The deployment command is `envoy read` and must run after setup.\n\n"
        "Final notes."
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        f"<html><body><p>{source.replace(chr(10), '</p><p>')}</p></body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-goal-text",
        "source": {"url": "https://example.org/guide"},
        "goal": "Find the deployment command",
        "budget": {"max_response_chars": 256},
    }))

    candidates = response.coverage.modalities["text"].details["goal_candidates"]
    assert candidates[0]["snippet"].startswith("The deployment command")
    assert candidates[0]["offset"] > 0


def test_goal_candidate_can_be_expanded_without_reading_intervening_text(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/video-guide",
        "<html><body>"
        f"<p>{'Historical overview and surrounding context. ' * 14}</p>"
        "<p>Deployment command: envoy video inspect --frames. "
        "This command is the relevant answer.</p>"
        "</body></html>",
    ))
    initial = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-targeted-expand-read",
        "source": {"url": "https://example.org/video-guide"},
        "goal": "Find the deployment command",
        "budget": {"max_response_chars": 600},
    }))
    candidate = initial.coverage.modalities["text"].details["goal_candidates"][0]

    result = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-targeted-expand",
        "material_id": initial.material_id,
        "offset": candidate["offset"],
        "max_chars": 256,
        "max_total_response_chars": 48_000,
    }))

    assert candidate["offset"] > initial.coverage.modalities["text"].details["returned_chars"]
    assert "envoy video inspect --frames" in result.blocks[0].content.text
    assert result.coverage.modalities["text"].details["offset"] == candidate["offset"]


def test_same_material_source_gets_independent_material_ids_per_session(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/shared",
        "<html><body><p>Shared public information about the deployment process.</p></body></html>",
    ))
    base = {
        "protocol_version": "envoy.read/1",
        "source": {"url": "https://example.org/shared"},
        "budget": {"max_response_chars": 256, "max_total_response_chars": 1024},
    }
    first = reader.read_public_page(ReadRequest.model_validate({
        **base,
        "request_id": "session-a-read",
        "session_id": "agent-session-a",
    }))
    second = reader.read_public_page(ReadRequest.model_validate({
        **base,
        "request_id": "session-b-read",
        "session_id": "agent-session-b",
    }))

    assert first.material_id != second.material_id
    first_material = json.loads((tmp_path / f"{first.material_id}.json").read_text(encoding="utf-8"))
    second_material = json.loads((tmp_path / f"{second.material_id}.json").read_text(encoding="utf-8"))
    assert first_material["session_id"] == "agent-session-a"
    assert second_material["session_id"] == "agent-session-b"
    assert (tmp_path / f"{first.material_id}.json").is_file()
    assert (tmp_path / f"{second.material_id}.json").is_file()


def test_visual_observation_is_persisted_to_existing_material(tmp_path, monkeypatch):
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "b" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "c" * 64,
        "text": "Guide text",
        "visual_resources": [{
            "resource_id": "resource-image",
            "source_block_id": "blk-image-0001",
            "source_url": "https://example.org/chart.png",
        }],
    })

    result = reader.record_visual_observation(
        material_id,
        "https://example.org/chart.png?token=secret",
        "resource-image",
        "blk-image-0001",
        "The chart shows three stages.",
    )

    stored = json.loads((tmp_path / f"{material_id}.json").read_text(encoding="utf-8"))
    assert result == {"persisted": True, "observation_count": 1}
    assert stored["visual_observations"][0]["observation"] == "The chart shows three stages."
    assert "secret" not in json.dumps(stored)
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "c" * 64,
        "text": "Guide text",
        "visual_resources": stored["visual_resources"],
    })
    reread = json.loads((tmp_path / f"{material_id}.json").read_text(encoding="utf-8"))
    assert reread["visual_observations"] == stored["visual_observations"]


def test_short_image_article_creates_material_snapshot_for_visual_followup(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/note",
        '<html><head><title>Chart note</title></head><body>'
        "<p>A short note that explains the chart.</p>"
        '<img src="https://cdn.example.org/chart.png" alt="Three-stage chart">'
        "</body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-short-visual-material",
        "source": {"url": "https://example.org/note"},
        "host_capabilities": {"image_input": "uri"},
    }))

    persisted = reader.record_visual_observation(
        response.material_id,
        "https://cdn.example.org/chart.png",
        response.blocks[2].content.resource_id,
        response.blocks[2].id,
        "The chart has three stages.",
    )
    stored = json.loads((tmp_path / f"{response.material_id}.json").read_text(encoding="utf-8"))

    assert persisted["persisted"] is True
    assert stored["visual_observations"][0]["observation"] == "The chart has three stages."


def test_visual_observation_still_returns_when_retention_is_disabled(tmp_path, monkeypatch):
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    result = reader.record_visual_observation(
        "mat-" + "c" * 24,
        "https://example.org/chart.png",
        "resource-chart",
        "blk-image-0001",
        "A four-stage chart.",
    )

    assert result["persisted"] is False
    assert result["reason"] == "material_unavailable"


def test_visual_observation_rejects_resource_not_owned_by_material(tmp_path, monkeypatch):
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "d" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/note",
        "source_revision": "e" * 64,
        "text": "A note.",
        "visual_resources": [{
            "resource_id": "resource-real",
            "source_block_id": "blk-image-0001",
            "source_url": "https://cdn.example.org/real.png",
        }],
    })

    result = reader.record_visual_observation(
        material_id,
        "https://cdn.example.org/other.png",
        "resource-other",
        "blk-image-0009",
        "An unrelated observation.",
    )

    stored = json.loads((tmp_path / f"{material_id}.json").read_text(encoding="utf-8"))
    assert result == {"persisted": False, "reason": "resource_mismatch"}
    assert stored.get("visual_observations", []) == []


def test_direct_image_read_can_persist_its_visual_observation(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": "https://example.org/chart.png",
        "mime_type": "image/png",
        "size_bytes": 128,
    })

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-direct-image",
        "source": {"url": "https://example.org/chart.png", "kind": "image"},
        "host_capabilities": {"image_input": "uri"},
    }))

    result = reader.record_visual_observation(
        response.material_id,
        "https://example.org/chart.png",
        response.resources[0].resource_id,
        response.blocks[0].id,
        "A three-stage chart.",
    )
    assert result["persisted"] is True


def test_cli_read_returns_structured_blocked_response_for_private_url(capsys):
    from envoy_cli import cli

    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-private-1",
        "source": {"url": "http://127.0.0.1:8140/video"},
    }

    exit_code = cli.main(["read", "--request-json", "-"], stdin=json.dumps(request))

    assert exit_code == 2
    response = json.loads(capsys.readouterr().out)
    assert response["result"] == "blocked"
    assert response["errors"][0]["code"] == "source_unavailable"
    assert response["errors"][0]["action_required"] is False


def test_cli_doctor_is_json_and_does_not_report_secrets(capsys):
    from envoy_cli import cli

    exit_code = cli.main(["doctor", "--json"])

    assert exit_code == 0
    response = json.loads(capsys.readouterr().out)
    assert response["tool"] == "envoy"
    assert response["protocol_version"] == "envoy.read/1"
    assert response["network_reader"] == "public_static_resources"
    assert response["capabilities"]["schema_version"] == "envoy.capabilities/1"
    assert "static_html" in response["capabilities"]["inputs"]["supported"]
    assert "pdf_text_layer" in response["capabilities"]["inputs"]["supported"]
    assert "platform_video_visual_extraction" in response["capabilities"]["inputs"]["unsupported"]
    assert response["capabilities"]["inputs"]["conditional"][1]["id"] == "direct_video_handoff"
    assert response["capabilities"]["inputs"]["conditional"][2]["id"] == "platform_video_subtitles"
    assert "api_key" not in json.dumps(response).lower()


def test_read_respects_response_character_budget_and_reports_partial_coverage(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    html_text = "<html><head><title>Long guide</title></head><body><main>" + (
        "<p>Detailed operational step and supporting explanation.</p>" * 30
    ) + "</main></body></html>"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda _url, max_bytes, _cache=None: ("https://example.org/guide", html_text))
    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-budget-1",
        "source": {"url": "https://example.org/guide"},
        "budget": {"max_response_chars": 256, "max_total_response_chars": 256},
    })

    response = reader.read_public_page(request)
    body = next(block.content.text for block in response.blocks if block.kind.value == "paragraph")

    assert response.result.value == "partial"
    assert len(body) < len(html_text)
    assert response.coverage.modalities["text"].extraction == "partial"
    assert response.coverage.modalities["text"].details["returned_chars"] == len(body)
    assert response.coverage.modalities["text"].details["total_chars"] > len(body)
    assert response.coverage.modalities["text"].details["continuation_available"] is True


def test_fetch_retries_transient_network_errors_and_enforces_streamed_size_limit(monkeypatch):
    from envoy_cli import reader

    calls = []

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body>Small page</body></html>"

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"123456"

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ReadTimeout("temporary timeout")
            return StreamContext(Response())

    class StreamContext:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader.httpx, "Client", Client)
    monkeypatch.setattr(reader.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(reader, "MAX_HTML_BYTES", 5)

    try:
        reader._fetch_html("https://example.org/", 5)
    except reader.PublicPageError as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("Oversized page should be rejected")

    assert len(calls) == 2


def test_read_creates_local_material_and_expand_returns_next_non_overlapping_slice(tmp_path, monkeypatch, capsys):
    from envoy_cli import cli
    from envoy_cli import reader

    html_text = "<html><head><title>Guide</title></head><body><main>" + (
        "<p>Step explains one detailed action and its reason.</p>" * 40
    ) + "</main></body></html>"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    calls = []

    def fetch(_url, _max_bytes, _dns_cache=None):
        calls.append(1)
        return "https://example.org/guide", html_text

    monkeypatch.setattr(reader, "_fetch_html", fetch)
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-expand-1",
        "source": {"url": "https://example.org/guide"},
        "budget": {"max_response_chars": 600, "max_total_response_chars": 2000},
    }

    assert cli.main(["read", "--request-json", "-"], stdin=json.dumps(request)) == 0
    first = json.loads(capsys.readouterr().out)
    material_id = first["material_id"]
    first_body = next(block["content"]["text"] for block in first["blocks"] if block["kind"] == "paragraph")

    assert first["result"] == "partial"
    assert material_id
    expand_request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-expand-2",
        "material_id": material_id,
        "offset": first["coverage"]["modalities"]["text"]["details"]["next_offset"],
        "max_chars": 400,
        "max_total_response_chars": 2000,
    }
    assert cli.main(["expand", "--request-json", "-"], stdin=json.dumps(expand_request)) == 0
    expanded = json.loads(capsys.readouterr().out)
    expanded_text = expanded["blocks"][0]["content"]["text"]

    assert expanded["result"] == "partial"
    assert expanded["blocks"][0]["locator"]["block_id"] == "blk-body-0001"
    assert expanded["coverage"]["modalities"]["text"]["details"]["offset"] == expand_request["offset"]
    assert expanded_text
    assert not first_body.endswith(expanded_text[:40])
    assert len(calls) == 1


def test_expand_enforces_cumulative_response_budget(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "a" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/",
        "source_revision": "b" * 64,
        "session_id": None,
        "text": "x" * 1000,
    })

    response = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-budget-expand",
        "material_id": material_id,
        "offset": 200,
        "max_chars": 500,
        "max_total_response_chars": 300,
    }))

    assert len(response.blocks[0].content.text) == 300
    assert response.coverage.modalities["text"].details["next_offset"] == 500
    assert response.result.value == "partial"


def test_expand_ledger_rejects_repeated_ranges_and_round_limit(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/",
        "<html><body><p>" + ("Useful sentence. " * 120) + "</p></body></html>",
    ))
    initial = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-ledger",
        "source": {"url": "https://example.org/"},
        "budget": {
            "max_response_chars": 600,
            "max_total_response_chars": 900,
            "max_expansion_rounds": 1,
        },
    }))
    details = initial.coverage.modalities["text"].details
    next_offset = details["next_offset"]
    first = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-ledger-expand-1",
        "material_id": initial.material_id,
        "offset": next_offset,
        "max_chars": 200,
        "max_total_response_chars": 900,
    }))
    assert first.coverage.modalities["text"].details["expansion_rounds"] == 1
    assert first.coverage.modalities["text"].details["next_offset"] is not None

    repeated = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-ledger-repeat",
        "material_id": initial.material_id,
        "offset": next_offset,
        "max_chars": 200,
        "max_total_response_chars": 900,
    }))
    assert repeated.result.value == "blocked"
    assert repeated.errors[0].code.value == "budget_exceeded"

    after_round = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-ledger-expand-2",
        "material_id": initial.material_id,
        "offset": first.coverage.modalities["text"].details["next_offset"],
        "max_chars": 200,
        "max_total_response_chars": 900,
    }))
    assert after_round.result.value == "blocked"
    assert after_round.errors[0].code.value == "budget_exceeded"


def test_zero_expansion_round_budget_is_enforced(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/",
        "<html><body><p>" + ("Useful sentence. " * 120) + "</p></body></html>",
    ))
    initial = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-no-expansion",
        "source": {"url": "https://example.org/"},
        "budget": {"max_response_chars": 600, "max_expansion_rounds": 0},
    }))
    next_offset = initial.coverage.modalities["text"].details["next_offset"]
    response = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-no-expansion-expand",
        "material_id": initial.material_id,
        "offset": next_offset,
        "max_chars": 200,
    }))
    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "budget_exceeded"


def test_image_only_page_is_partial_and_exposes_visual_coverage(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/post",
        '<html><head><title>Screenshot guide</title></head><body>'
        '<img src="https://cdn.example.org/guide.png" alt="Guide screenshot">'
        "</body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-image-only",
        "source": {"url": "https://example.org/post"},
        "host_capabilities": {"image_input": "uri"},
    }))

    assert response.result.value == "partial"
    assert response.coverage.modalities["text"].extraction == "failed"
    assert response.coverage.modalities["text"].details["image_only"] is True
    assert response.coverage.modalities["images"].returned == 1


def test_video_page_reports_unread_video_coverage(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/watch",
        '<html><body><p>Watch the following demonstration.</p>'
        '<video src="https://cdn.example.org/demo.mp4"></video></body></html>',
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-video-page",
        "source": {"url": "https://example.org/watch"},
    }))

    assert response.result.value == "partial"
    assert response.coverage.modalities["video"].discovered == 1
    assert response.coverage.modalities["video"].extraction == "failed"
    assert "standalone video extraction" in response.coverage.modalities["video"].unresolved[0]


def test_direct_video_is_handed_to_native_video_host_without_local_model(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": "https://cdn.example.org/lesson.mp4",
            "mime_type": "video/mp4",
        })
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-native-video",
        "source": {
            "url": "https://cdn.example.org/lesson.mp4",
            "kind": "video",
        },
        "goal": "Explain the main idea and the steps shown",
        "host_capabilities": {"video_input": "native"},
    }))

    assert response.result.value == "needs_host", response.model_dump(mode="json")
    assert response.needs[0].type == "inspect_video"
    assert response.resources[0].delivery.uri == "https://cdn.example.org/lesson.mp4"
    assert response.blocks[0].content.type == "video"
    assert response.coverage.modalities["video"].delivered == 1


def test_direct_video_handoff_uses_real_http_redirect_and_content_type(monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/watch":
                self.send_response(302)
                self.send_header("Location", "/files/lesson.mp4")
                self.end_headers()
                return
            if self.path == "/files/lesson.mp4":
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", "64")
                self.end_headers()
                self.wfile.write(b"x" * 64)
                return
            self.send_error(404)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
        response = reader.read_public_page(ReadRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": "req-real-http-video",
            "source": {
                "url": f"http://127.0.0.1:{server.server_port}/watch",
                "kind": "video",
            },
            "host_capabilities": {"video_input": "native"},
        }))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert response.result.value == "needs_host", response.model_dump(mode="json")
    assert response.resources[0].mime_type == "video/mp4"
    assert response.resources[0].delivery.uri.endswith("/files/lesson.mp4")


def test_direct_video_without_native_host_capability_is_reported_as_unsupported(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": "https://cdn.example.org/lesson.mp4",
            "mime_type": "video/mp4",
        })
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-video-no-host",
        "source": {"url": "https://cdn.example.org/lesson.mp4", "kind": "video"},
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "missing_capability"


def test_signed_direct_video_url_is_never_forwarded_to_host(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": "https://cdn.example.org/lesson.mp4?token=%5BREDACTED%5D",
            "mime_type": "video/mp4",
            "sensitive_url": True,
        })
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-signed-video",
        "source": {"url": "https://cdn.example.org/lesson.mp4?token=private"},
        "host_capabilities": {"video_input": "native"},
    }))

    assert response.result.value == "blocked"
    assert "private" not in response.model_dump_json()


def test_unknown_signed_parameter_direct_video_url_is_not_forwarded(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": "https://cdn.example.org/lesson.mp4?edge_credential=private",
            "mime_type": "video/mp4",
        })
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-unknown-signed-video",
        "source": {"url": "https://cdn.example.org/lesson.mp4?edge_credential=private"},
        "host_capabilities": {"video_input": "native"},
    }))

    assert response.result.value == "blocked"
    assert "private" not in response.model_dump_json()


def test_challenge_page_is_not_reported_as_ready(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/check",
        "<html><head><title>Security check</title></head><body>"
        "<p>Checking your browser before accessing the site.</p>"
        "<p>" + ("Please wait. " * 20) + "</p></body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-challenge",
        "source": {"url": "https://example.org/check"},
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "challenge_required"
    assert response.errors[0].action_required is True


def test_local_material_cache_evicts_expired_and_oldest_without_clearing_recent(tmp_path, monkeypatch):
    import os
    import time
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "MAX_CACHED_MATERIALS", 2)
    for suffix in ("1", "2", "3"):
        reader._store_material("mat-" + suffix * 24, {"text": suffix})
        time.sleep(0.01)
    old_path = tmp_path / ("mat-" + "1" * 24 + ".json")
    old_timestamp = time.time() - reader.MATERIAL_TTL_SECONDS - 1
    os.utime(old_path, (old_timestamp, old_timestamp))

    reader._material_root()
    remaining = {path.name for path in tmp_path.glob("mat-*.json")}

    assert "mat-" + "1" * 24 + ".json" not in remaining
    assert len(remaining) <= 2
    assert "mat-" + "3" * 24 + ".json" in remaining


def test_material_expiry_metadata_write_does_not_follow_existing_symlink(tmp_path, monkeypatch):
    from envoy_cli import reader

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    outside_file = tmp_path / "outside"
    outside_file.write_text("unchanged", encoding="utf-8")
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(cache_dir))
    material_id = "mat-" + "d" * 24
    (cache_dir / f"{material_id}.expires").symlink_to(outside_file)

    reader._store_material(material_id, {
        "source_url": "https://example.org/",
        "source_revision": "e" * 64,
        "text": "Local material",
    })

    assert outside_file.read_text(encoding="utf-8") == "unchanged"
    assert not (cache_dir / f"{material_id}.expires").is_symlink()
    assert (cache_dir / f"{material_id}.expires").is_file()


def test_read_redacts_sensitive_query_values_and_omits_signed_image_resources(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    html_text = """
    <html><head><title>Private token page</title></head><body>
    <main><p>Publicly readable article content with enough words.</p>
    <img src="https://cdn.example.org/image.webp?X-Amz-Signature=hidden-signature&amp;size=large">
    </main></body></html>
    """
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda _url, _max_bytes, _dns_cache=None: (
            "https://example.org/article?access_token=hidden-token&amp;page=1",
            html_text,
        ),
    )
    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-redact-1",
        "source": {"url": "https://example.org/article?access_token=hidden-token&page=1"},
    })

    response = reader.read_public_page(request)
    serialized = response.model_dump_json()

    assert response.result.value == "partial"
    assert "hidden-token" not in serialized
    assert "hidden-signature" not in serialized
    assert "access_token=%5BREDACTED%5D" in serialized
    assert response.resources == []
    assert any("credential-like" in item for item in response.coverage.modalities["images"].unresolved)


def test_network_error_does_not_echo_secret_query_value(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    secret_url = "https://example.org/article?api_key=do-not-leak"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)

    def fail_fetch(_url, _max_bytes, _dns_cache=None):
        raise httpx.ConnectError(f"failed to connect to {secret_url}")

    monkeypatch.setattr(reader, "_fetch_html", fail_fetch)
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-network-redact",
        "source": {"url": secret_url},
    }))

    assert "do-not-leak" not in response.model_dump_json()
    assert "api_key=%5BREDACTED%5D" in response.model_dump_json()


def test_source_text_hard_limit_is_reported_as_partial_not_silent_success(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "MAX_TEXT_CHARS", 100)
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda _url, _max_bytes, _dns_cache=None: (
            "https://example.org/long",
            "<html><body><p>" + ("substantial source text " * 20) + "</p></body></html>",
        ),
    )
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-hard-limit",
        "source": {"url": "https://example.org/long"},
    }))

    assert response.result.value == "partial"
    assert response.coverage.modalities["text"].details["source_truncated"] is True
    assert response.coverage.modalities["text"].details["continuation_available"] is False
    assert any("extraction limit" in item for item in response.coverage.modalities["text"].unresolved)


def test_direct_image_link_returns_host_visual_need_without_inlining_image(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(
        reader,
        "_fetch_source",
        lambda _url, _max_bytes, _cache: {
            "kind": "image",
            "url": "https://cdn.example.org/screenshot.png",
            "mime_type": "image/png",
            "size_bytes": 2048,
        },
    )
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-image-1",
        "source": {"url": "https://cdn.example.org/screenshot.png", "kind": "image"},
        "goal": "Read the table in this screenshot",
        "host_capabilities": {"image_input": "uri"},
    }))

    assert response.result.value == "needs_host"
    assert response.execution_state.value == "waiting_for_host"
    assert response.needs[0].type == "inspect_visual"
    assert response.needs[0].resource_ids == ["resource-image"]
    assert response.resources[0].delivery.uri == "https://cdn.example.org/screenshot.png"
    assert response.resources[0].delivery.data_base64 is None


def test_direct_image_link_is_blocked_when_host_cannot_view_images(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(
        reader,
        "_fetch_source",
        lambda _url, _max_bytes, _cache: {
            "kind": "image",
            "url": "https://cdn.example.org/screenshot.png",
            "mime_type": "image/png",
            "size_bytes": 2048,
        },
    )
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-image-2",
        "source": {"url": "https://cdn.example.org/screenshot.png", "kind": "image"},
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "missing_capability"
    assert response.errors[0].action_required is True


def test_direct_image_link_requires_uri_delivery_from_host(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: (_ for _ in ()).throw(
        AssertionError("image should not be fetched without URI delivery support")
    ))
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-image-local-file",
        "source": {"url": "https://example.org/chart.png", "kind": "image"},
        "host_capabilities": {"image_input": "local_file"},
        "goal": "Read the chart",
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "missing_capability"
    assert response.errors[0].action_required is True


def test_pdf_url_extracts_text_with_page_locators(monkeypatch):
    from reportlab.pdfgen import canvas
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, "Install the worker before the API.")
    pdf.showPage()
    pdf.drawString(72, 720, "Verify the health endpoint after startup.")
    pdf.save()
    pdf_bytes = output.getvalue()
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache: {
        "url": "https://example.org/guide.pdf",
        "data": pdf_bytes,
        "mime_type": "application/pdf",
    })

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-pdf-1",
        "source": {"url": "https://example.org/guide.pdf"},
        "goal": "Find installation steps",
    }))

    page_blocks = [block for block in response.blocks if block.kind.value == "paragraph"]
    assert response.result.value == "ready"
    assert len(page_blocks) == 2
    assert page_blocks[0].locator.type == "pdf_page"
    assert page_blocks[0].locator.page_number == 1
    assert page_blocks[1].locator.page_number == 2
    assert "Install the worker" in page_blocks[0].content.text


def test_extensionless_pdf_is_auto_routed_by_response_content_type(monkeypatch):
    from reportlab.pdfgen import canvas
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, "Detected from the PDF response header.")
    pdf.save()
    pdf_data = output.getvalue()
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)

    class Response:
        status_code = 200
        headers = {"content-type": "application/pdf", "content-length": str(len(pdf_data))}
        encoding = "utf-8"

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield pdf_data

    class StreamContext:
        def __enter__(self):
            return Response()

        def __exit__(self, *_args):
            return False

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return StreamContext()

    monkeypatch.setattr(reader.httpx, "Client", Client)

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-pdf-mime",
        "source": {"url": "https://example.org/download?id=123"},
    }))

    assert response.result.value == "ready"
    assert any("Detected from the PDF response header." in block.content.text for block in response.blocks if block.kind.value == "paragraph")


def test_extensionless_image_is_auto_routed_by_response_content_type(monkeypatch):
    from contextlib import contextmanager
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)

    class Response:
        status_code = 200
        headers = {"content-type": "image/png", "content-length": "4"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"PNG!"

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, *_args, **_kwargs):
            yield Response()

    monkeypatch.setattr(reader.httpx, "Client", Client)
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-image-mime",
        "source": {"url": "https://example.org/download?id=image"},
        "host_capabilities": {"image_input": "uri"},
    }))

    assert response.result.value == "needs_host"
    assert response.resources[0].mime_type == "image/png"


def test_text_coverage_reports_token_estimate(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        "<html><body><p>Useful deployment steps for the worker.</p></body></html>",
    ))
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-token-estimate",
        "source": {"url": "https://example.org/guide"},
        "budget": {"max_response_tokens": 256},
    }))

    details = response.coverage.modalities["text"].details
    assert details["estimated_returned_tokens"] >= 1
    assert details["estimated_token_basis"] == "ascii4_nonascii1_v1"


def test_response_token_budget_hard_limits_first_read(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        "<html><body><p>" + ("Useful deployment sentence. " * 80) + "</p></body></html>",
    ))
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-token-budget",
        "source": {"url": "https://example.org/guide"},
        "budget": {
            "max_response_chars": 4000,
            "max_response_tokens": 64,
            "max_total_response_chars": 4000,
        },
    }))

    body = next(block.content.text for block in response.blocks if block.kind.value == "paragraph")
    assert len(body) <= 256
    assert response.coverage.modalities["text"].details["token_budget"] == 64


def test_total_response_token_budget_is_shared_by_expansions(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        "<html><body><p>" + ("Useful deployment sentence. " * 100) + "</p></body></html>",
    ))
    initial = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-total-token",
        "source": {"url": "https://example.org/guide"},
        "budget": {
            "max_response_chars": 2000,
            "max_response_tokens": 64,
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 256,
        },
    }))
    details = initial.coverage.modalities["text"].details
    assert details["total_token_budget"] == 256
    expanded = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-total-token-expand",
        "material_id": initial.material_id,
        "offset": details["next_offset"],
        "max_chars": 400,
        "max_response_tokens": 64,
        "max_total_response_tokens": 256,
    }))
    assert expanded.coverage.modalities["text"].details["remaining_tokens"] >= 0
    assert expanded.coverage.modalities["text"].details["total_token_budget"] == 256


def test_token_estimate_counts_cjk_characters_more_conservatively():
    from envoy_cli.reader import _estimated_tokens

    assert _estimated_tokens("中文链接处理") == 6
    assert _estimated_tokens("مرحبا") == 5
    assert _estimated_tokens("Hello") == 2


def test_response_token_budget_limits_cjk_text_by_estimated_tokens(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_text = "部署命令和配置说明。" * 40
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        f"<html><body><p>{source_text}</p></body></html>",
    ))
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-cjk-token",
        "source": {"url": "https://example.org/guide"},
        "budget": {"max_response_tokens": 64},
    }))
    body = next(block.content.text for block in response.blocks if block.kind.value == "paragraph")

    assert reader._estimated_tokens(body) <= 64


def test_total_token_budget_alone_limits_initial_read_and_stops_expansion(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        "<html><body><p>" + ("Deployment instructions with enough details. " * 100) + "</p></body></html>",
    ))
    initial = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-total-token-only",
        "source": {"url": "https://example.org/guide"},
        "budget": {
            "max_response_chars": 4000,
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 64,
            "max_expansion_rounds": 8,
        },
    }))
    body = next(block.content.text for block in initial.blocks if block.kind.value == "paragraph")
    assert len(body) <= 256
    details = initial.coverage.modalities["text"].details
    assert details["total_token_budget"] == 64
    assert details["remaining_tokens"] == 64 - details["returned_tokens"]

    consumed_chars = len(body)
    consumed_tokens = details["returned_tokens"]
    offset = details["next_offset"]
    for round_index in range(8):
        expanded = reader.expand_material(ExpandRequest.model_validate({
            "request_id": f"req-total-token-only-expand-{round_index}",
            "material_id": initial.material_id,
            "offset": offset,
            "max_chars": 4000,
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 64,
            "max_expansion_rounds": 8,
        }))
        if expanded.result.value == "blocked":
            assert expanded.errors[0].code.value == "budget_exceeded"
            break
        chunk = expanded.blocks[0].content.text
        consumed_chars += len(chunk)
        expansion_details = expanded.coverage.modalities["text"].details
        consumed_tokens = expansion_details["returned_tokens"]
        offset = expansion_details["next_offset"]
    else:
        raise AssertionError("Cumulative token budget should eventually block expansion")
    assert consumed_chars <= 64 * 4
    assert consumed_tokens <= 64


def test_short_material_is_cached_for_goal_directed_followup_search(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/guide",
        "<html><body><p>Overview and background.</p>"
        "<p>Deployment command: envoy start --worker.</p>"
        "<p>Closing notes.</p></body></html>",
    ))
    initial = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-short-search-cache",
        "source": {"url": "https://example.org/guide"},
        "goal": "Summarize the overview",
    }))
    assert initial.result.value == "ready"
    assert (tmp_path / f"{initial.material_id}.json").is_file()

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-short-search",
        "material_id": initial.material_id,
        "query": "deployment command",
    }))

    assert result.result.value == "empty"
    assert result.blocks == []
    assert result.coverage.modalities["text"].details["matches_found"] == 1
    assert result.coverage.modalities["text"].details["already_returned_matches"] == 1


def test_material_search_finds_cjk_query_in_later_paragraph(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "e" * 24
    source_text = (
        "前言和整体背景。\n\n"
        "常见问题说明。\n\n"
        "部署命令是 envoy start --worker，必须先完成配置。\n\n"
        "结尾说明。"
    )
    reader._store_material(material_id, {
        "source_kind": "text",
        "source_url": "https://example.org/guide",
        "source_revision": "f" * 64,
        "title_text": "部署教程",
        "text": source_text,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-cjk-search",
        "material_id": material_id,
        "query": "部署命令",
    }))

    assert "envoy start --worker" in result.blocks[0].content.text
    assert result.blocks[0].order == source_text.index("部署命令")


def test_material_search_and_expand_share_returned_range_ledger(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "1" * 24
    text = "Unrelated introduction.\n\nImportant answer: NEEDLE=42 with context."
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "1" * 64,
        "title_text": "Guide",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 8,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })

    searched = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-search-shared-range",
        "material_id": material_id,
        "query": "NEEDLE=42",
    }))
    expanded = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-expand-shared-range",
        "material_id": material_id,
        "offset": searched.blocks[0].order,
        "max_chars": 256,
    }))

    assert searched.result.value == "ready"
    assert expanded.result.value == "blocked"
    assert "overlaps an earlier response" in expanded.errors[0].message


def test_retained_material_followup_rejects_different_session(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, MaterialSearchRequest, VisualObservationHistoryRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "2" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "2" * 64,
        "session_id": "agent-a",
        "text": "Private session state attached to shared public material.",
        "visual_resources": [],
        "budget_state": {
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 500,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })

    for operation in (
        lambda: reader.expand_material(ExpandRequest.model_validate({
            "request_id": "wrong-session-expand",
            "material_id": material_id,
            "session_id": "agent-b",
        })),
        lambda: reader.search_material(MaterialSearchRequest.model_validate({
            "request_id": "wrong-session-search",
            "material_id": material_id,
            "session_id": "agent-b",
            "query": "session state",
        })),
        lambda: reader.read_visual_observations(VisualObservationHistoryRequest.model_validate({
            "request_id": "wrong-session-history",
            "material_id": material_id,
            "session_id": "agent-b",
        })),
    ):
        try:
            operation()
        except reader.PublicPageError as exc:
            assert exc.code.value == "invalid_request"
            assert "different session" in str(exc)
        else:
            raise AssertionError("Material state must not cross session boundaries")


def test_retained_material_followup_without_session_id_remains_compatible(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "3" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "3" * 64,
        "session_id": "agent-a",
        "text": "A legacy client can continue this material.",
        "budget_state": {
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 500,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })

    response = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "legacy-sessionless-expand",
        "material_id": material_id,
        "offset": 0,
        "max_chars": 128,
    }))

    assert response.result.value == "ready"
    assert response.blocks[0].content.text == "A legacy client can continue this material."


def test_material_search_consumes_shared_cumulative_token_budget(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "e" * 24
    text = "\n\n".join(
        f"Deployment option {index}: configure worker concurrency and retries."
        for index in range(20)
    )
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "f" * 64,
        "title_text": "Deployment guide",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 64,
            "max_expansion_rounds": 8,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })
    request = {
        "protocol_version": "envoy.search/1",
        "material_id": material_id,
        "query": "deployment concurrency retries",
        "max_results": 10,
        "max_chars": 2000,
        "max_response_tokens": 64,
    }
    first = reader.search_material(MaterialSearchRequest.model_validate({
        **request,
        "request_id": "req-search-budget-1",
    }))
    used_tokens = first.coverage.modalities["text"].details["returned_tokens"]
    assert used_tokens > 0
    second = reader.search_material(MaterialSearchRequest.model_validate({
        **request,
        "request_id": "req-search-budget-2",
    }))

    assert second.result.value == "blocked"
    assert second.errors[0].code.value == "budget_exceeded"


def test_material_search_skips_previously_returned_matches(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "d" * 24
    text = (
        "Deployment uses worker queues and bounded retries.\n"
        "Deployment uses a durable job ledger for recovery."
    )
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "a" * 64,
        "title_text": "Deployment guide",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })
    request = {
        "protocol_version": "envoy.search/1",
        "material_id": material_id,
        "query": "deployment",
        "max_results": 5,
    }

    first = reader.search_material(MaterialSearchRequest.model_validate({
        **request,
        "request_id": "req-dedupe-first",
    }))
    repeated = reader.search_material(MaterialSearchRequest.model_validate({
        **request,
        "request_id": "req-dedupe-repeat",
    }))
    assert len(first.blocks) == 2
    assert repeated.result.value == "empty"
    assert repeated.coverage.modalities["text"].details["already_returned_matches"] == 2


def test_material_search_returns_unseen_suffix_of_partially_returned_line(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "c" * 24
    text = "Deployment command: " + "worker concurrency recovery " * 12
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "b" * 64,
        "title_text": "Deployment guide",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })

    first = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-suffix-first",
        "material_id": material_id,
        "query": "deployment command",
        "max_chars": 128,
        "max_response_tokens": 64,
    }))
    second = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-suffix-second",
        "material_id": material_id,
        "query": "deployment command",
    }))

    first_text = first.blocks[0].content.text
    second_text = second.blocks[0].content.text
    assert text.startswith(first_text)
    assert second_text == text.rstrip()[len(first_text):]
    assert first.blocks[0].order + len(first_text) == second.blocks[0].order


def test_material_search_returns_window_around_match_in_long_single_line(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "f" * 24
    text = ("irrelevant background " * 240) + "NEEDLE=42" + (" unrelated tail" * 8)
    reader._store_material(material_id, {
        "source_url": "https://example.org/long",
        "source_revision": "f" * 64,
        "title_text": "Long page",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-long-line-hit",
        "material_id": material_id,
        "query": "NEEDLE=42",
        "max_chars": 128,
        "max_response_tokens": 64,
    }))

    assert len(result.blocks) == 1
    assert "NEEDLE=42" in result.blocks[0].content.text
    assert result.blocks[0].order > 5000


def test_expand_remote_offset_uses_remaining_budget_not_absolute_offset(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "9" * 24
    text = "x" * 64_100 + "REMOTE-TAIL" + "y" * 200
    reader._store_material(material_id, {
        "source_url": "https://example.org/long",
        "source_revision": "9" * 64,
        "title_text": "Long page",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 50_000,
            "max_total_response_tokens": None,
            "max_expansion_rounds": 8,
            "returned_chars": 2376,
            "returned_tokens": 0,
            "expansion_rounds": 1,
            "returned_ranges": [],
        },
    })

    result = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-remote-offset",
        "material_id": material_id,
        "offset": 64_100,
        "max_chars": 128,
        "max_total_response_chars": 50_000,
    }))

    assert result.result.value == "partial"
    assert result.blocks[0].content.text.startswith("REMOTE-TAIL")
    assert result.coverage.modalities["text"].details["offset"] == 64_100


def test_visual_resource_block_id_is_stable_when_candidate_order_changes(monkeypatch):
    from envoy_cli import reader

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    images = [
        {"url": "https://cdn.example.org/a.png", "source_index": 0},
        {"url": "https://cdn.example.org/b.png", "source_index": 1},
    ]
    first, _ = reader._safe_visual_candidates(images, "https://example.org", {})
    reordered, _ = reader._safe_visual_candidates(list(reversed(images)), "https://example.org", {})
    first_ids = {
        item["resource_id"]: item["block_id"]
        for item in first
    }
    reordered_ids = {
        item["resource_id"]: item["block_id"]
        for item in reordered
    }

    assert first_ids == reordered_ids


def test_budgeted_text_prefix_scans_mixed_text_within_estimated_budget():
    from envoy_cli.reader import _estimated_tokens, _take_text_budgeted

    text = "abcd中文efgh"
    content, end = _take_text_budgeted(text, 0, len(text), 3)

    assert end == len(content)
    assert content == "abcd中文"
    assert _estimated_tokens(content) == 3
    assert _estimated_tokens(text[:end + 1]) > 3


def test_text_link_is_auto_routed_by_response_content_type(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedText({
            "url": "https://example.org/download?id=readme",
            "data": b"# Setup\n\nInstall the worker first.\n",
            "mime_type": "text/markdown",
            "encoding": "utf-8",
        })
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-text-mime",
        "source": {"url": "https://example.org/download?id=readme"},
    }))

    assert response.result.value == "ready"
    assert any("Install the worker first." in block.content.text for block in response.blocks if block.kind.value == "paragraph")
    assert response.blocks[1].locator.type == "web"


def test_text_suffix_handles_generic_download_content_type():
    from contextlib import contextmanager
    from unittest.mock import patch
    from envoy_cli import reader

    class Response:
        status_code = 200
        headers = {"content-type": "application/octet-stream"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"Simple text file with enough content."

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, *_args, **_kwargs):
            yield Response()

    with patch.object(reader, "_is_public_http_url", return_value=True), \
         patch.object(reader.httpx, "Client", Client):
        try:
            reader._fetch_html("https://example.org/readme.txt")
        except reader.DetectedText as exc:
            assert exc.source["mime_type"] == "text/plain"
            assert exc.source["data"] == b"Simple text file with enough content."
        else:
            raise AssertionError("Text suffix should route binary content as text")


def test_long_plain_text_uses_incremental_material_cache(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    source_text = "\n".join(f"Section {index}: retain this source sentence." for index in range(400))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedText({
            "url": "https://example.org/archive.txt",
            "data": source_text.encode(),
            "mime_type": "text/plain",
            "encoding": "utf-8",
        })
    ))
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-long-text",
        "source": {"url": "https://example.org/archive.txt"},
        "budget": {"max_response_chars": 900},
    }))

    assert response.result.value == "partial"
    body = next(block.content.text for block in response.blocks if block.id == "blk-body-0001")
    next_offset = response.coverage.modalities["text"].details["next_offset"]
    expanded = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-long-text-expand",
        "material_id": response.material_id,
        "offset": next_offset,
        "max_chars": 800,
        "max_total_response_chars": 5000,
    }))

    assert response.coverage.modalities["text"].details["continuation_available"] is True
    assert expanded.blocks[0].content.text
    assert body != source_text
    assert expanded.coverage.modalities["text"].details["offset"] == next_offset


def test_long_pdf_expands_by_page_without_losing_page_locator(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, "Install the worker before the API.")
    pdf.showPage()
    pdf.drawString(72, 720, "Verify the health endpoint after startup.")
    pdf.save()
    pdf_bytes = output.getvalue()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache: {
        "url": "https://example.org/guide.pdf",
        "data": pdf_bytes,
        "mime_type": "application/pdf",
    })
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-long-pdf",
        "source": {"url": "https://example.org/guide.pdf"},
        "budget": {"max_response_chars": 650},
    }))

    assert response.result.value == "partial"
    next_offset = response.coverage.modalities["text"].details["next_offset"]
    expanded = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "req-expand-pdf",
        "material_id": response.material_id,
        "offset": next_offset,
        "max_chars": 500,
        "max_total_response_chars": 1000,
    }))

    assert expanded.blocks[0].locator.type == "pdf_page"
    assert expanded.blocks[0].locator.page_number == 2
    page_two_first = next(
        block.content.text
        for block in response.blocks
        if getattr(block.locator, "page_number", None) == 2
    )
    assert "Verify the health endpoint" in page_two_first + expanded.blocks[0].content.text


def test_scanned_pdf_reports_ocr_capability_gap(monkeypatch):
    from reportlab.pdfgen import canvas
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.showPage()
    pdf.save()
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache: {
        "url": "https://example.org/scanned.pdf",
        "data": output.getvalue(),
        "mime_type": "application/pdf",
    })
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-scanned-pdf",
        "source": {"url": "https://example.org/scanned.pdf"},
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "missing_capability"
    assert response.errors[0].action_required is True
    assert "OCR" in response.errors[0].message


def test_public_dns_validation_reuses_resolution_only_within_one_read(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    calls = {}

    def resolve(host, port):
        calls[host] = calls.get(host, 0) + 1
        return [(None, None, None, None, ("93.184.216.34", port))]

    monkeypatch.setattr(reader.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda _url, _max_bytes, _dns_cache=None: (
            "https://example.org/page",
            (
                "<html><body><p>Article content with enough text to read.</p>"
                '<img src="https://cdn.example.org/one.webp">'
                '<img src="https://cdn.example.org/two.webp">'
                "</body></html>"
            ),
        ),
    )
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-dns-cache",
        "source": {"url": "https://example.org/page"},
    }))

    assert response.result.value == "ready"
    assert len(response.resources) == 2
    assert calls == {"example.org": 1, "cdn.example.org": 1}


def test_zero_retention_does_not_advertise_expandable_content(tmp_path, monkeypatch, capsys):
    from envoy_cli import cli, reader

    html_text = "<html><head><title>Guide</title></head><body><p>" + ("Useful sentence. " * 80) + "</p></body></html>"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda _url, _max_bytes, _dns_cache=None: ("https://example.org/", html_text))
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-no-retain",
        "source": {"url": "https://example.org/"},
        "budget": {"max_response_chars": 600, "max_total_response_chars": 2000},
        "retention": {"raw_media": "ephemeral", "derived_ttl_seconds": 0},
    }

    assert cli.main(["read", "--request-json", "-"], stdin=json.dumps(request)) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["result"] == "partial"
    assert response["coverage"]["modalities"]["text"]["details"]["continuation_available"] is False
    assert any(
        "retention is disabled" in item
        for item in response["coverage"]["modalities"]["text"]["unresolved"]
    )
    assert list(tmp_path.glob("mat-*.json")) == []
