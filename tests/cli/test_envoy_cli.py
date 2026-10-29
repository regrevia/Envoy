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
    assert response["session_id"] is None
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


def test_goal_image_budget_skips_obvious_ui_backgrounds_without_goal_match(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/video",
        "<html><body><p>Video page.</p>"
        '<img src="https://cdn.example.org/jxWeakBtnBgV1.png" alt="">'
        '<img src="https://cdn.example.org/poster.jpg" alt="Frame">'
        "</body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-ui-image-filter",
        "source": {"url": "https://example.org/video"},
        "goal": "Inspect the visual layout",
        "budget": {"max_visual_inputs": 1},
    }))

    assert len(response.resources) == 1
    assert response.resources[0].delivery.uri.endswith("poster.jpg")


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


def test_material_transaction_serializes_concurrent_budget_updates(tmp_path, monkeypatch):
    from threading import Event, Thread
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "d" * 24
    acquired = Event()

    def acquire_second():
        with reader._material_transaction(material_id):
            acquired.set()

    with reader._material_transaction(material_id):
        thread = Thread(target=acquire_second)
        thread.start()
        assert not acquired.wait(0.05)
    assert acquired.wait(1)
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_material_file_lock_serializes_across_processes(tmp_path):
    import os
    import subprocess
    import sys
    from envoy_cli.media import interprocess_file_lock

    lock_path = tmp_path / ".mat-lock-test.lock"
    script = (
        "import sys; "
        "from envoy_cli.media import interprocess_file_lock; "
        "\ntry:\n"
        " with interprocess_file_lock(sys.argv[1], max_wait_seconds=0.1, busy_message='busy'):\n"
        "  print('acquired')\n"
        "except TimeoutError:\n"
        " print('busy')\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(path)
            for path in (
                Path(__file__).resolve().parents[2] / "packages" / "envoy-cli" / "src",
                Path(__file__).resolve().parents[2] / "packages" / "envoy-core" / "src",
            )
        ),
    }

    with interprocess_file_lock(
        lock_path,
        max_wait_seconds=1,
        busy_message="parent lock acquisition timed out",
    ):
        result = subprocess.run(
            [sys.executable, "-c", script, str(lock_path)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "busy"


def test_material_cleanup_never_unlinks_an_active_lock_inode(tmp_path, monkeypatch):
    import os
    from envoy_cli import reader
    from envoy_cli.media import interprocess_file_lock

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    stale_lock = tmp_path / ".material-aa.lock"
    stale_lock.touch()
    old_time = stale_lock.stat().st_mtime - (8 * 24 * 60 * 60)
    os.utime(stale_lock, (old_time, old_time))

    with interprocess_file_lock(
        stale_lock,
        max_wait_seconds=1,
        busy_message="test lock timed out",
    ):
        reader._material_root()
        assert stale_lock.exists()


def test_material_cleanup_waits_for_atomic_material_publication(tmp_path, monkeypatch):
    from threading import Event, Thread
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "c" * 24
    json_published = Event()
    allow_writer = Event()
    cleanup_finished = Event()
    original_replace = reader.os.replace
    errors = []

    def pause_after_material_json(source, destination):
        original_replace(source, destination)
        if Path(destination).name == f"{material_id}.json":
            json_published.set()
            if not allow_writer.wait(timeout=5):
                raise TimeoutError("test did not release material writer")

    monkeypatch.setattr(reader.os, "replace", pause_after_material_json)

    def write_material():
        try:
            reader._store_material(material_id, {
                "source_kind": "text",
                "source_url": "https://example.org/concurrent",
                "source_revision": "c" * 64,
                "text": "Newly published material must survive concurrent cleanup.",
            })
        except BaseException as exc:
            errors.append(exc)

    def cleanup_materials():
        reader._material_root()
        cleanup_finished.set()

    writer = Thread(target=write_material)
    cleaner = Thread(target=cleanup_materials)
    writer.start()
    assert json_published.wait(timeout=5)
    cleaner.start()
    try:
        assert not cleanup_finished.wait(timeout=0.1)
    finally:
        allow_writer.set()
        writer.join(timeout=5)
        cleaner.join(timeout=5)

    assert not writer.is_alive()
    assert not cleaner.is_alive()
    assert not errors
    assert (tmp_path / f"{material_id}.json").is_file()
    assert (tmp_path / f"{material_id}.expires").is_file()


def test_concurrent_material_expansions_share_one_cumulative_budget(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import json
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "e" * 24
    text = "x" * 1000
    reader._store_material(material_id, {
        "source_kind": "text",
        "source_url": "https://example.org/long-text",
        "source_revision": "f" * 64,
        "session_id": "expand-budget-session",
        "text": text,
        "title_text": "Long text",
        "budget_state": {
            "max_total_response_chars": 256,
            "max_total_response_tokens": None,
            "max_expansion_rounds": 10,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })
    requests = [
        ExpandRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": f"parallel-expand-{offset}",
            "session_id": "expand-budget-session",
            "material_id": material_id,
            "offset": offset,
            "max_chars": 200,
        })
        for offset in (0, 300)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(reader.expand_material, requests))

    returned_chars = sum(
        len(block.content.text)
        for response in responses
        for block in response.blocks
    )
    stored = json.loads((tmp_path / f"{material_id}.json").read_text(encoding="utf-8"))
    assert returned_chars == 256
    assert stored["budget_state"]["returned_chars"] == 256
    ranges = stored["budget_state"]["returned_ranges"]
    assert sum(end - start for start, end in ranges) == 256
    assert len(ranges) == 2
    assert ranges[0][1] <= ranges[1][0]


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
    assert result == {
        "persisted": True,
        "observation_count": 1,
        "source_url": "https://example.org/chart.png",
    }
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


def test_html_reread_keeps_full_image_catalog_and_prior_observations(tmp_path, monkeypatch):
    import json
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest, VisualObservationHistoryRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (
        "https://example.org/note",
        '<html><head><title>Images</title></head><body><p>Shared note content.</p>'
        '<img src="https://cdn.example.org/alpha.png" alt="Alpha chart">'
        '<img src="https://cdn.example.org/beta.png" alt="Beta chart">'
        "</body></html>",
    ))
    first = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "image-catalog-alpha",
        "session_id": "image-catalog-session",
        "source": {"url": "https://example.org/note"},
        "goal": "Alpha chart",
        "host_capabilities": {"image_input": "uri"},
        "budget": {"max_visual_inputs": 1},
    }))
    first_image = next(block for block in first.blocks if block.kind.value == "image")
    assert "alpha.png" in first.resources[0].delivery.uri
    assert reader.record_visual_observation(
        first.material_id,
        first.resources[0].delivery.uri,
        first_image.content.resource_id,
        first_image.id,
        "Alpha chart has three bars.",
        session_id="image-catalog-session",
    )["persisted"] is True

    second = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "image-catalog-beta",
        "session_id": "image-catalog-session",
        "source": {"url": "https://example.org/note"},
        "goal": "Beta chart",
        "host_capabilities": {"image_input": "uri"},
        "budget": {"max_visual_inputs": 1},
    }))
    history = reader.read_visual_observations(VisualObservationHistoryRequest.model_validate({
        "request_id": "image-catalog-history",
        "material_id": first.material_id,
        "session_id": "image-catalog-session",
        "max_items": 4,
        "max_chars": 1000,
    }))
    stored = json.loads((tmp_path / f"{first.material_id}.json").read_text(encoding="utf-8"))

    assert second.material_id == first.material_id
    assert "beta.png" in second.resources[0].delivery.uri
    assert {item["source_block_id"] for item in stored["visual_resources"]} == {
        "blk-image-0001",
        "blk-image-0002",
    }
    assert "Alpha chart has three bars." in history.blocks[0].content.text
    assert "Some source images were not included in this response" in history.coverage.unresolved


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
    assert response["build"]["channel"] == "source-checkout"
    assert response["build"]["version"] == response["cli_version"]
    assert response["build"]["source_revision"] == "working-tree"
    assert response["protocol_version"] == "envoy.read/1"
    assert response["network_reader"] == "public_static_and_supported_media_extractors"
    assert response["capabilities"]["schema_version"] == "envoy.capabilities/1"
    assert "static_html" in response["capabilities"]["inputs"]["supported"]
    assert "pdf_text_layer" in response["capabilities"]["inputs"]["supported"]
    assert "standalone_audio" in response["capabilities"]["inputs"]["unsupported"]
    assert response["capabilities"]["inputs"]["conditional"][1]["id"] == "direct_video_handoff"
    assert response["capabilities"]["inputs"]["conditional"][2]["id"] == "platform_video_subtitles"
    assert response["capabilities"]["inputs"]["conditional"][3]["id"] == "local_video_asr"
    assert response["capabilities"]["inputs"]["conditional"][4]["id"] == "platform_video_frames"
    assert response["capabilities"]["inputs"]["conditional"][5]["id"] == "browser_captured_video_evidence"
    assert "timed_video_transcript" in response["capabilities"]["outputs"]
    assert "leased_source_timed_video_frames" in response["capabilities"]["outputs"]
    assert response["runtime"]["yt_dlp"]
    assert "yt_dlp_supported" in response["runtime"]
    assert "ffmpeg" in response["runtime"]
    assert "asr_installed" in response["runtime"]
    assert "api_key" not in json.dumps(response).lower()


def test_cli_asr_setup_prepares_model_and_reports_only_local_model_status(monkeypatch, capsys):
    from envoy_cli import cli

    monkeypatch.setattr(cli, "prepare_asr_model", lambda model: {
        "model": model,
        "cache_dir": "/private/model-cache",
        "available": True,
    }, raising=False)

    exit_code = cli.main(["asr", "setup", "--model", "tiny.en"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "ready",
        "model": "tiny.en",
        "cache_dir": "/private/model-cache",
        "available": True,
    }
    assert "api_key" not in json.dumps(result).lower()


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


def test_direct_ogv_video_uses_native_handoff_without_html_fetch(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://media.example.org/talk.ogv"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("direct OGV must enter the media pipeline")
        ),
    )

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "direct-ogv-native",
        "source": {"url": source_url, "kind": "video"},
        "host_capabilities": {"video_input": "native"},
    }))

    assert response.result.value == "needs_host"
    assert response.resources[0].mime_type == "video/ogg"
    assert response.resources[0].delivery.uri == source_url


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
    from envoy_cli import browser

    monkeypatch.setattr(
        reader,
        "_read_with_isolated_browser",
        lambda *_args, **_kwargs: (
            None,
            browser.BrowserChallengeRequired("login or verification required"),
        ),
    )

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
    expired_frames = tmp_path / ("frames-mat-" + "1" * 24)
    expired_frames.mkdir()
    (expired_frames / "frame-001.jpg").write_bytes(b"frame")
    old_timestamp = time.time() - reader.MATERIAL_TTL_SECONDS - 1
    os.utime(old_path, (old_timestamp, old_timestamp))

    reader._material_root()
    remaining = {path.name for path in tmp_path.glob("mat-*.json")}

    assert "mat-" + "1" * 24 + ".json" not in remaining
    assert not expired_frames.exists()
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
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache, **_kwargs: {
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


def test_material_search_ignores_english_stopwords_for_goal_queries(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "b" * 24
    text = "\n".join([
        "We think visually, in sound, and kinesthetically.",
        "Students learn arithmetic and grammar in schools.",
        "Creativity is as important as literacy, and schools should treat it equally.",
    ])
    reader._store_material(material_id, {
        "source_kind": "video",
        "source_url": "https://www.youtube.com/watch?v=public-demo",
        "source_revision": "c" * 64,
        "title_text": "Education",
        "text": text,
        "video_spans": [
            {"start_offset": 0, "end_offset": len(text.splitlines()[0]), "start_seconds": 796, "end_seconds": 799},
            {"start_offset": len(text.splitlines()[0]) + 1, "end_offset": len("\n".join(text.splitlines()[:2])), "start_seconds": 400, "end_seconds": 404},
            {"start_offset": len("\n".join(text.splitlines()[:2])) + 1, "end_offset": len("\n".join(text.splitlines()[:3])), "start_seconds": 600, "end_seconds": 605},
        ],
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
        "request_id": "req-stopword-search",
        "material_id": material_id,
        "query": "What is his central argument about creativity in schools?",
        "max_results": 3,
        "max_chars": 1200,
    }))

    assert result.blocks[0].content.text == (
        "Creativity is as important as literacy, and schools should treat it equally."
    )
    assert result.blocks[0].locator.start_seconds == 600
    assert result.coverage.modalities["text"].details["query_terms"] == [
        "central",
        "argument",
        "creativity",
        "schools",
    ]


def test_material_search_does_not_lock_the_same_range_out_of_expand(tmp_path, monkeypatch):
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
    repeated = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-expand-repeat-range",
        "material_id": material_id,
        "offset": searched.blocks[0].order,
        "max_chars": 256,
    }))

    assert searched.result.value == "ready"
    # A search ships one short window per hit, so it must not close the region to
    # a later expand: treating it as full delivery deadlocked the model into
    # re-reading the URL.
    assert expanded.result.value in {"ready", "partial"}
    assert expanded.blocks
    # A range an expand already delivered stays closed.
    assert repeated.result.value == "blocked"
    blocked = repeated.errors[0]
    assert "overlaps an earlier response" in blocked.message
    # The refusal is machine-readable, and the place to resume is in details so
    # the caller does not have to parse the sentence.
    assert blocked.code.value == "range_already_delivered"
    assert blocked.details["reason"] == "range_already_delivered"
    assert blocked.details["next_offset"] == int(
        blocked.message.rsplit("character ", 1)[1]
    )


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
            "request_id": "missing-session-expand",
            "material_id": material_id,
        })),
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


def test_material_replay_returns_cached_evidence_to_a_new_session_without_reusing_ranges(
    tmp_path,
    monkeypatch,
):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, MaterialReplayRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "4" * 24
    source_revision = "4" * 64
    stored_text = (
        "First source paragraph. " * 18
        + "\n\n"
        + "Second source paragraph. " * 18
    )
    reader._store_material(material_id, {
        "source_url": "https://example.org/replay",
        "source_revision": source_revision,
        "session_id": "old-agent",
        "text": stored_text,
        "title_text": "Replayable material",
        "budget_state": {
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 500,
            "max_expansion_rounds": 3,
            "returned_chars": len(stored_text),
            "returned_tokens": 20,
            "expansion_rounds": 1,
            "returned_ranges": [[0, len(stored_text)]],
        },
        "source_coverage_unresolved": [],
    })

    response = reader.replay_material(MaterialReplayRequest.model_validate({
        "request_id": "replay-new-session",
        "material_id": material_id,
        "source_revision": source_revision,
        "source_session_id": "old-agent",
        "session_id": "new-agent",
        "max_chars": 256,
        "max_total_response_chars": 1000,
    }))

    assert response.result.value == "partial"
    assert response.session_id == "new-agent"
    assert response.material_id != material_id
    assert any(
        block.content.type == "text" and block.content.text
        for block in response.blocks
    )
    text_coverage = response.coverage.modalities["text"]
    assert text_coverage.details["continuation_available"] is True
    assert text_coverage.details["next_offset"] > 0
    original = reader._load_matching_material(material_id, source_revision, "old-agent")
    replayed = reader._load_matching_material(
        response.material_id,
        source_revision,
        "new-agent",
    )
    assert original is not None
    assert replayed is not None
    assert original["budget_state"]["returned_ranges"] == [[0, len(stored_text)]]
    assert replayed["budget_state"]["returned_ranges"] != original["budget_state"]["returned_ranges"]
    continuation = reader.expand_material(ExpandRequest.model_validate({
        "request_id": "replay-new-session-expand",
        "material_id": response.material_id,
        "session_id": "new-agent",
        "offset": text_coverage.details["next_offset"],
        "max_chars": 512,
        "max_total_response_chars": 1000,
    }))
    assert continuation.result.value in {"ready", "partial"}
    assert any(
        block.content.type == "text" and "Second source paragraph." in block.content.text
        for block in continuation.blocks
    )


def test_material_replay_cli_accepts_versioned_replay_request(monkeypatch, capsys):
    import json
    from envoy_cli import cli
    from envoy_core.schemas.read_v1 import ExecutionState, ReadResponse, ReadResult

    observed = {}

    def replay(request):
        observed.update(request.model_dump())
        return ReadResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            material_id="mat-" + "a" * 24,
            source_revision=request.source_revision,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        )

    monkeypatch.setattr(cli, "replay_material", replay)
    code = cli.main(
        ["replay", "--request-json", "-"],
        stdin=json.dumps({
            "protocol_version": "envoy.read/1",
            "request_id": "cli-replay-1",
            "material_id": "mat-" + "b" * 24,
            "source_revision": "c" * 64,
            "source_session_id": "session-old",
            "session_id": "session-new",
            "max_chars": 1200,
        }),
    )

    response = json.loads(capsys.readouterr().out)
    assert code == 0
    assert response["result"] == "partial"
    assert observed["session_id"] == "session-new"
    assert observed["source_session_id"] == "session-old"


def test_material_replay_rejects_wrong_source_session_and_repeated_target_session(
    tmp_path,
    monkeypatch,
):
    import pytest
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialReplayRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "5" * 24
    source_revision = "5" * 64
    reader._store_material(material_id, {
        "source_url": "https://example.org/replay-session",
        "source_revision": source_revision,
        "session_id": "source-session",
        "text": "Evidence bound to the source session.",
        "title_text": "Session-bound source",
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
    request = {
        "request_id": "replay-session-check",
        "material_id": material_id,
        "source_revision": source_revision,
        "source_session_id": "wrong-session",
        "session_id": "target-session",
    }

    with pytest.raises(reader.PublicPageError, match="source session does not match"):
        reader.replay_material(MaterialReplayRequest.model_validate(request))

    request["source_session_id"] = "source-session"
    first = reader.replay_material(MaterialReplayRequest.model_validate(request))
    replayed_material = reader._load_matching_material(
        first.material_id,
        source_revision,
        "target-session",
    )
    assert replayed_material is not None
    replayed_material["budget_state"]["returned_ranges"] = [[0, 8]]
    reader._store_material(first.material_id, replayed_material, merge=False)

    with pytest.raises(reader.PublicPageError, match="already replayed") as exc_info:
        reader.replay_material(MaterialReplayRequest.model_validate({
            **request,
            "request_id": "replay-session-check-again",
        }))
    assert exc_info.value.details["material_id"] == first.material_id


def test_replay_refusals_name_the_remedy_in_details(tmp_path, monkeypatch):
    """A refused replay must say what to pass next, not only that it is wrong."""
    import pytest
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialReplayRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "9" * 24
    source_revision = "9" * 64
    reader._store_material(material_id, {
        "source_url": "https://example.org/replay-remedy",
        "source_revision": source_revision,
        "session_id": "remedy-source",
        "text": "Remedy text.",
        "budget_state": {"returned_ranges": []},
    })

    with pytest.raises(reader.PublicPageError, match="revision does not match") as exc_info:
        reader.replay_material(MaterialReplayRequest.model_validate({
            "request_id": "replay-remedy-short-revision",
            "material_id": material_id,
            "source_revision": "a" * 64,
            "session_id": "remedy-target",
        }))
    details = exc_info.value.details
    assert details["expected_source_revision"] == source_revision
    assert "source_revision printed by the read" in details["next_action"]

    with pytest.raises(reader.PublicPageError, match="new session_id") as exc_info:
        reader.replay_material(MaterialReplayRequest.model_validate({
            "request_id": "replay-remedy-same-session",
            "material_id": material_id,
            "source_revision": source_revision,
            "session_id": "remedy-source",
        }))
    assert "different session" in exc_info.value.details["next_action"]


def test_material_replay_honors_minimum_token_budget_without_expand_schema_error(
    tmp_path,
    monkeypatch,
):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialReplayRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "7" * 24
    source_revision = "7" * 64
    reader._store_material(material_id, {
        "source_url": "https://example.org/replay-budget",
        "source_revision": source_revision,
        "session_id": "budget-source",
        "text": "技术内容 " * 80,
        "title_text": "A very long technical title " * 20,
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

    response = reader.replay_material(MaterialReplayRequest.model_validate({
        "request_id": "replay-budget-target",
        "material_id": material_id,
        "source_revision": source_revision,
        "source_session_id": "budget-source",
        "session_id": "budget-target",
        "max_chars": 256,
        "max_total_response_chars": 256,
        "max_response_tokens": 64,
        "max_total_response_tokens": 64,
    }))

    returned_text = " ".join(
        block.content.text
        for block in response.blocks
        if block.content.type == "text"
    )
    assert reader._estimated_tokens(returned_text) <= 64
    assert response.result.value == "partial"


def test_material_replay_copies_leased_frames_and_preserves_host_observations(
    tmp_path,
    monkeypatch,
):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialReplayRequest, VisualObservationHistoryRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "6" * 24
    source_revision = "6" * 64
    source_url = "https://example.org/video"
    source_frame = tmp_path / f"frames-{material_id}" / "frame-001.jpg"
    second_source_frame = tmp_path / f"frames-{material_id}" / "frame-002.jpg"
    reader._store_material(material_id, {
        "source_url": source_url,
        "source_revision": source_revision,
        "session_id": "video-session-a",
        "source_kind": "video",
        "text": "[00:01.000] A speaker describes the diagram.",
        "title_text": "Technical talk",
        "budget_state": {
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 500,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
        "visual_resources": [],
    })
    source_frame.parent.mkdir()
    source_frame.write_bytes(b"jpeg-frame")
    second_source_frame.write_bytes(b"jpeg-frame-2")
    reader._store_material(material_id, {
        "source_url": source_url,
        "source_revision": source_revision,
        "session_id": "video-session-a",
        "source_kind": "video",
        "text": "[00:01.000] A speaker describes the diagram.",
        "title_text": "Technical talk",
        "budget_state": {
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 500,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
        "visual_resources": [
            {
                "resource_id": "resource-frame-001",
                "source_block_id": "blk-video-frame-001",
                "source_url": source_url,
                "frame_path": str(source_frame),
                "timestamp": 1.0,
            },
            {
                "resource_id": "resource-frame-002",
                "source_block_id": "blk-video-frame-002",
                "source_url": source_url,
                "frame_path": str(second_source_frame),
                "timestamp": 2.0,
            },
        ],
    })
    observation = reader.record_visual_observation(
        material_id,
        None,
        "resource-frame-001",
        "blk-video-frame-001",
        "The slide shows a three-column architecture diagram.",
        "00:01.000",
        "video-session-a",
    )
    assert observation["persisted"] is True

    replay = reader.replay_material(MaterialReplayRequest.model_validate({
        "request_id": "replay-video-session-b",
        "material_id": material_id,
        "source_revision": source_revision,
        "source_session_id": "video-session-a",
        "session_id": "video-session-b",
        "include_visual": True,
        "host_capabilities": {"image_input": "local_file"},
    }))

    assert replay.resources, replay.model_dump()
    assert replay.result.value == "needs_host"
    assert len(replay.resources) == 1
    assert replay.resources[0].resource_id == "resource-frame-002"
    assert replay.coverage.modalities["replay"].details["stored_visual_observation_count"] == 1
    replay_frame = Path(replay.resources[0].delivery.path)
    assert replay_frame != second_source_frame
    assert replay_frame.read_bytes() == second_source_frame.read_bytes()
    history = reader.read_visual_observations(VisualObservationHistoryRequest.model_validate({
        "request_id": "replayed-observation-history",
        "material_id": replay.material_id,
        "session_id": "video-session-b",
        "resource_id": "resource-frame-001",
    }))
    assert any(
        block.content.type == "text"
        and "three-column architecture" in block.content.text
        for block in history.blocks
    )

    (tmp_path / f"{material_id}.json").unlink()
    (tmp_path / f"{material_id}.expires").unlink()
    reader._material_root()
    assert not source_frame.exists()
    assert not second_source_frame.exists()
    assert replay_frame.is_file()


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
        "query": "deployment command worker",
        "max_chars": 128,
        "max_response_tokens": 64,
    }))
    second = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "req-suffix-second",
        "material_id": material_id,
        "query": "deployment command worker",
    }))

    first_text = first.blocks[0].content.text
    second_text = second.blocks[0].content.text
    assert text.startswith(first_text)
    assert second_text == text.rstrip()[len(first_text):]
    assert first.blocks[0].order + len(first_text) == second.blocks[0].order


def test_material_search_does_not_return_irrelevant_suffix_after_hit_was_returned(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "a" * 24
    text = "Deployment command: " + ("unrelated background context " * 40)
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "d" * 64,
        "title_text": "Guide",
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
        "request_id": "search-first-deployment",
        "material_id": material_id,
        "query": "deployment command",
        "max_results": 1,
        "max_chars": 128,
    }))
    second = reader.search_material(MaterialSearchRequest.model_validate({
        "request_id": "search-second-deployment",
        "material_id": material_id,
        "query": "deployment command",
        "max_results": 1,
        "max_chars": 128,
    }))

    assert "Deployment command" in first.blocks[0].content.text
    assert second.result.value == "empty"
    assert second.blocks == []


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


def test_material_search_scans_all_unseen_segments_of_a_long_line(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "7" * 24
    text = ("background " * 20) + "NEEDLE=first " + ("middle " * 30) + "NEEDLE=second " + ("tail " * 10)
    first_hit = text.index("NEEDLE=first")
    second_hit = text.index("NEEDLE=second")
    returned_ranges = [
        [0, first_hit + len("NEEDLE=first")],
        [first_hit + len("NEEDLE=first") + 1, second_hit],
    ]
    reader._store_material(material_id, {
        "source_url": "https://example.org/long-line",
        "source_revision": "7" * 64,
        "title_text": "Long material",
        "text": text,
        "budget_state": {
            "max_total_response_chars": 20_000,
            "max_total_response_tokens": 5_000,
            "max_expansion_rounds": 8,
            "returned_chars": sum(end - start for start, end in returned_ranges),
            "returned_tokens": sum(reader._estimated_tokens(text[start:end]) for start, end in returned_ranges),
            "expansion_rounds": 1,
            "returned_ranges": returned_ranges,
        },
    })

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "request_id": "search-later-unseen-hit",
        "material_id": material_id,
        "query": "NEEDLE",
        "max_results": 5,
        "max_chars": 2_000,
    }))

    assert result.blocks
    assert any("NEEDLE=second" in block.content.text for block in result.blocks)
    assert any(block.order <= second_hit < block.order + len(block.content.text) for block in result.blocks)


def test_material_search_counts_repeated_context_against_response_token_budget(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "8" * 24
    lines = [
        f"[00:{index:02d}.000] " + ("previously read context " * 7) + f" NEEDLE={index}"
        for index in range(20)
    ]
    text = "\n".join(lines)
    ranges = []
    import re
    for start, line in zip([match.start() for match in re.finditer(r"(?m)^", text)], lines):
        end = start + line.index("NEEDLE")
        ranges.append([start, end])
    reader._store_material(material_id, {
        "source_kind": "video",
        "source_url": "https://example.org/video",
        "source_revision": "8" * 64,
        "title_text": "Video",
        "text": text,
        "transcript_source": "manual_subtitles",
        "budget_state": {
            "max_total_response_chars": 20_000,
            "max_total_response_tokens": 5_000,
            "max_expansion_rounds": 8,
            "returned_chars": sum(end - start for start, end in ranges),
            "returned_tokens": sum(reader._estimated_tokens(text[start:end]) for start, end in ranges),
            "expansion_rounds": 1,
            "returned_ranges": ranges,
        },
    })

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "request_id": "search-repeated-context-budget",
        "material_id": material_id,
        "query": "NEEDLE",
        "max_results": 20,
        "max_chars": 10_000,
        "max_response_tokens": 64,
    }))
    returned_text_tokens = sum(
        reader._estimated_tokens(block.content.text)
        for block in result.blocks
    )

    assert len(result.blocks) > 1
    assert returned_text_tokens <= 64
    assert result.coverage.modalities["text"].details["returned_tokens"] == returned_text_tokens


def test_material_search_keeps_timestamp_context_when_match_line_was_partially_returned(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "c" * 24
    text = "[08:44.918] Every education system has the same hierarchy of subjects."
    reader._store_material(material_id, {
        "source_kind": "video",
        "source_url": "https://example.org/video",
        "source_revision": "c" * 64,
        "text": text,
        "title_text": "Video",
        "transcript_source": "manual_subtitles",
        "video_spans": [{
            "start_offset": 0,
            "end_offset": len(text),
            "start_seconds": 524.918,
            "end_seconds": 528.565,
        }],
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 3,
            "returned_chars": 12,
            "returned_tokens": 3,
            "expansion_rounds": 0,
            "returned_ranges": [[0, 12]],
        },
    })

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "request_id": "timestamp-context-search",
        "material_id": material_id,
        "query": "hierarchy subjects",
        "max_results": 1,
        "max_chars": 300,
    }))

    assert result.blocks[0].content.text.startswith("[08:44.918]")
    assert "hierarchy of subjects" in result.blocks[0].content.text
    assert result.blocks[0].locator.start_seconds == 524.918


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
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache, **_kwargs: {
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
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda _url, _max_bytes, _cache, **_kwargs: {
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
