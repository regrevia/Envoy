"""Minimal Anthropic-compatible endpoint that logs what Claude Code sends to the model.

First request: log it, then answer with a tool_use for whichever envoy_read tool
the client declared. Later requests: log them, then answer with plain text. The
second logged request contains the tool_result content, which is exactly what the
model would read.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open("/tmp/claude_mock/requests.jsonl", "a", encoding="utf-8")


def _envoy_read_tool(tools):
    for tool in tools or []:
        name = str(tool.get("name") or "")
        if name.endswith("envoy_read"):
            return name
    return None


def _sse(handler, payloads):
    body = []
    for name, data in payloads:
        body.append(f"event: {name}\ndata: {json.dumps(data)}\n\n")
    raw = "".join(body).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _message(handler, blocks, stop_reason, stream: bool):
    message_id = "msg_mock_" + str(abs(hash(json.dumps(blocks))) % 10**8)
    base = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5-20250929",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }
    if not stream:
        raw = json.dumps({**base, "content": blocks}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        return

    events = [("message_start", {"type": "message_start", "message": {**base, "content": [], "stop_reason": None, "usage": {"input_tokens": 100, "output_tokens": 1}}})]
    for index, block in enumerate(blocks):
        if block["type"] == "tool_use":
            events.append(("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": block["text"]}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 20}}))
    events.append(("message_stop", {"type": "message_stop"}))
    _sse(handler, events)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"_unparsed": raw}
        LOG.write(json.dumps({"path": self.path, "body": payload}, ensure_ascii=False) + "\n")
        LOG.flush()

        messages = payload.get("messages") or []
        has_tool_result = any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for message in messages
            if isinstance(message, dict)
            for block in (message.get("content") or [])
            if isinstance(block, dict)
        )
        stream = bool(payload.get("stream"))
        if has_tool_result:
            _message(self, [{"type": "text", "text": "Got the tool result."}], "end_turn", stream)
            return
        tool = _envoy_read_tool(payload.get("tools"))
        if not tool:
            _message(self, [{"type": "text", "text": "no envoy_read tool declared"}], "end_turn", stream)
            return
        _message(
            self,
            [{"type": "tool_use", "id": "toolu_mock_1", "name": tool, "input": {"source": {"url": "https://example.com"}, "goal": "what does this page say"}}],
            "tool_use",
            stream,
        )


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
