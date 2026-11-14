"""Two callers asking for the same source at the same time read it once.

The read path serializes per source and scope, so a second caller that had to
wait for the first is handed the finished evidence instead of fetching the
source again. A caller that starts after that read finished must still see the
source as it is now, so the hand-off is bounded by the instant the waiting
began; the second test pins that boundary on the operation ledger itself.
"""

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (ROOT_DIR / "packages" / "envoy-core" / "src", ROOT_DIR / "packages" / "envoy-cli" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


PAGE = b"""<!doctype html>
<html><head><title>Deployment Guide</title></head>
<body><main><h1>Deployment Guide</h1>
<p>Run the worker before starting the API.</p>
</main></body></html>
"""


class _Source:
    def __init__(self, delay_seconds=0.4):
        self.delay_seconds = delay_seconds
        self.fetches = 0
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/robots.txt":
                    self.send_response(404)
                    self.end_headers()
                    return
                with outer.lock:
                    outer.fetches += 1
                time.sleep(outer.delay_seconds)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/guide"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def count(self):
        with self.lock:
            return self.fetches


def _request(request_id, url, session_id="session-shared"):
    from envoy_core.schemas.read_v1 import ReadRequest

    return ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": request_id,
        "session_id": session_id,
        "source": {"url": url, "kind": "article"},
    })


def test_two_waiting_callers_share_one_fetch(monkeypatch):
    from envoy_cli import reader

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    with _Source() as source:
        results = {}
        errors = []

        def read(request_id):
            try:
                results[request_id] = reader.read_public_page(_request(request_id, source.url))
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        threads = [
            threading.Thread(target=read, args=("req-share-1",)),
            threading.Thread(target=read, args=("req-share-2",)),
        ]
        for thread in threads:
            thread.start()
            time.sleep(0.05)
        for thread in threads:
            thread.join(timeout=60)

    assert not errors, errors
    assert len(results) == 2, results
    assert source.count() == 1, f"the source was fetched {source.count()} times"

    first = results["req-share-1"]
    second = results["req-share-2"]
    assert first.result.value == "ready", first.model_dump(mode="json")
    assert second.result.value == "ready", second.model_dump(mode="json")
    assert first.request_id == "req-share-1"
    assert second.request_id == "req-share-2"
    # The second caller is answered with the same material the first read, and
    # its own request id, so the answer is still addressed to it.
    assert second.material_id == first.material_id
    assert second.source_revision == first.source_revision


def test_a_result_completed_before_the_caller_started_waiting_is_not_handed_over():
    from envoy_cli.operations import run_idempotent

    calls = []

    def execute():
        calls.append(1)
        return {"answer": len(calls)}

    def run(share_window_start):
        return run_idempotent(
            "envoy-share:freshness",
            fingerprint="fingerprint-freshness",
            execute=execute,
            share_window_start=share_window_start,
        )

    assert run(None) == {"answer": 1}
    # A caller that was already waiting when the read finished picks it up.
    assert run(0.0) == {"answer": 1}
    assert len(calls) == 1
    # A caller that starts waiting only now must not be answered with work that
    # was already finished before it asked, so the read runs again.
    assert run(time.time()) == {"answer": 2}
    assert len(calls) == 2
