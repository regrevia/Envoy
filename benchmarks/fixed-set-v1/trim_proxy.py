#!/usr/bin/env python3
"""Keep every request under the upstream per-request image cap.

The shared inference gateway rejects any request carrying more than ten image
blocks ("request has 12 images; maximum is 10"). A link-heavy task makes an
agent download and read a dozen page images, so the whole conversation then
fails on every following turn with the same 400 — the failure is an upstream
quota, not the arm's reading quality.

This proxy sits between the Claude Code child and the gateway. It forwards
requests untouched unless a request carries more than ``--keep`` image blocks;
then it drops the oldest images and leaves a text block in their place, so the
most recent visual evidence (usually the page under study) survives and the
conversation can continue. Both arms go through the same proxy.

    python3 trim_proxy.py --listen 14051 --upstream http://127.0.0.1:14050

The proxy never reads or stores answer text; it only counts image blocks and
logs the count per request to ``--log``.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

LOG_LOCK = threading.Lock()
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


def log(path: str, record: dict) -> None:
    line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}, ensure_ascii=False)
    with LOG_LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


PLACEHOLDER = "[earlier image dropped to stay under the upstream per-request image cap]"


def count_images(content) -> int:
    """Count image blocks at any depth.

    Claude Code wraps tool output in ``{"type": "tool_result", "content": [...]}``,
    so a page image read by a tool is *nested*, not a top-level block. Counting
    only top-level blocks reports zero images for a request the gateway rejects
    for having twelve.
    """
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image":
            total += 1
        if isinstance(block.get("content"), list):
            total += count_images(block["content"])
    return total


def trim_images(body: dict, keep: int) -> tuple[dict, int, int]:
    """Drop the oldest image blocks so at most ``keep`` remain.

    Returns the possibly rewritten body, the image count seen, and how many
    images were removed. Only ``messages`` is touched: a system prompt or tool
    definition never carries page images.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, 0, 0
    seen = sum(count_images(message.get("content")) for message in messages if isinstance(message, dict))
    if seen <= keep:
        return body, seen, 0

    budget = seen - keep  # how many still have to go, oldest first
    removed = 0

    def visit(content):
        nonlocal budget, removed
        if not isinstance(content, list):
            return content
        kept = []
        for block in content:
            if budget > 0 and isinstance(block, dict) and block.get("type") == "image":
                kept.append({"type": "text", "text": PLACEHOLDER})
                budget -= 1
                removed += 1
                continue
            if isinstance(block, dict) and isinstance(block.get("content"), list):
                block["content"] = visit(block["content"])
            kept.append(block)
        return kept

    for message in messages:
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            message["content"] = visit(message["content"])
    return body, seen, removed


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = "http://127.0.0.1:14050"
    keep = 10
    log_path = "/tmp/trim-proxy.jsonl"

    def log_message(self, fmt: str, *args) -> None:  # silence the default access log
        return

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        seen = removed = 0
        outbound = raw
        if method == "POST" and raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            if isinstance(body, dict):
                body, seen, removed = trim_images(body, self.keep)
                if removed:
                    outbound = json.dumps(body, ensure_ascii=False).encode("utf-8")

        target = urlparse(self.upstream)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["content-length"] = str(len(outbound))
        # The upstream gateway answers 503 in bursts under load. Forwarding that
        # straight to Claude Code makes the CLI retry with its own backoff and the
        # turn burns its 900 s wall-clock budget on retries, so transient upstream
        # failures are absorbed here: the request body is buffered already, which
        # makes a retry cheap and keeps the turn's clock for real work.
        attempt = 0
        while True:
            connection = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=900)
            try:
                connection.request(method, self.path, body=outbound or None, headers=headers)
                response = connection.getresponse()
            except Exception as error:  # upstream down or dropped the socket
                connection.close()
                if attempt < 3:
                    attempt += 1
                    log(self.log_path, {"path": self.path, "retry": attempt, "error": str(error)})
                    time.sleep(1.5 * attempt)
                    continue
                payload = json.dumps({"type": "error", "error": {"type": "proxy_error", "message": str(error)}}).encode()
                self.send_response(502)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                log(self.log_path, {"path": self.path, "images": seen, "removed": removed, "error": str(error)})
                return
            if response.status in (429, 500, 502, 503, 504) and attempt < 3:
                response.read()
                connection.close()
                attempt += 1
                log(self.log_path, {"path": self.path, "retry": attempt, "upstream_status": response.status})
                time.sleep(1.5 * attempt)
                continue
            break

        streaming = "text/event-stream" in (response.getheader("content-type") or "")
        log(self.log_path, {"path": self.path, "status": response.status, "images": seen, "removed": removed})
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in HOP_HEADERS:
                continue
            self.send_header(key, value)
        if streaming:
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            streamed = 0
            clean = True
            try:
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    streamed += len(chunk)
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except Exception as error:  # upstream or client vanished mid-stream
                clean = False
                log(
                    self.log_path,
                    {
                        "path": self.path,
                        "status": response.status,
                        "streamed_bytes": streamed,
                        "stream_error": f"{type(error).__name__}: {error}",
                    },
                )
            if clean:
                log(self.log_path, {"path": self.path, "status": response.status, "streamed_bytes": streamed})
        else:
            data = response.read()
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        self.wfile.flush()
        connection.close()

    def do_POST(self) -> None:
        self._guarded("POST")

    def do_GET(self) -> None:
        self._guarded("GET")

    def _guarded(self, method: str) -> None:
        # Claude Code aborts in-flight streams when a turn is killed at its
        # wall-clock cap, and the socket is already gone by the time the last
        # headers are written. That raised BrokenPipeError out of the request
        # handler and killed the whole proxy mid-sweep, which then looks like a
        # model outage to every shard. One dead client must only cost that
        # client's request.
        try:
            self._forward(method)
        except (BrokenPipeError, ConnectionResetError) as error:
            log(self.log_path, {"path": self.path, "client_gone": type(error).__name__})
        except Exception as error:  # never let one request take the proxy down
            log(self.log_path, {"path": self.path, "handler_error": f"{type(error).__name__}: {error}"})
            try:
                self.close_connection = True
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", type=int, default=14051)
    parser.add_argument("--upstream", default="http://127.0.0.1:14050")
    parser.add_argument("--keep", type=int, default=10)
    parser.add_argument("--log", default="/tmp/trim-proxy.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="print the trimmed body instead of forwarding")
    args = parser.parse_args(argv)

    if args.dry_run:
        body = json.load(sys.stdin)
        trimmed, seen, removed = trim_images(body, args.keep)
        json.dump({"images": seen, "removed": removed, "body": trimmed}, sys.stdout, ensure_ascii=False)
        return 0

    Handler.upstream = args.upstream
    Handler.keep = args.keep
    Handler.log_path = args.log
    server = ThreadingHTTPServer(("127.0.0.1", args.listen), Handler)
    server.daemon_threads = True
    print(f"trim-proxy {args.listen} -> {args.upstream} (keep {args.keep} images)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
