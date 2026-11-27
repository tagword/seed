"""End-to-end: LLMAPIExecutor against a mock OpenAI Responses endpoint.

Spins up a local HTTP server that speaks the Responses protocol and drives the
real executor through it, so the request body we send and the response we
parse are both verified end to end (not just the pure conversion helpers).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from seed.core.llm_exec import LLMAPIExecutor

RECEIVED: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        RECEIVED.append({"path": self.path, "body": body})

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                {"type": "response.created"},
                {"type": "response.reasoning_summary_text.delta", "delta": "thinking..."},
                {"type": "response.output_text.delta", "delta": "Hello"},
                {"type": "response.output_text.delta", "delta": " world"},
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 3,
                            "total_tokens": 8,
                        },
                    },
                },
            ]
            for ev in events:
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = {
            "id": "resp_test",
            "object": "response",
            "model": body.get("model"),
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "let me think"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hi there"}],
                },
            ],
            "usage": {
                "input_tokens": 7,
                "output_tokens": 4,
                "total_tokens": 11,
            },
        }
        self.wfile.write(json.dumps(payload).encode())


@pytest.fixture()
def server():
    RECEIVED.clear()
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}/v1"
    httpd.shutdown()


def _executor(base_url: str) -> LLMAPIExecutor:
    return LLMAPIExecutor(
        baseURL=base_url,
        model="gpt-5.1",
        api_key="sk-test",
        maxOutputTokens=256,
        provider="openai_responses",
    )


def test_endpoint_path_is_responses(server: str) -> None:
    ex = _executor(server)
    assert ex._get_completion_url() == f"{server}/responses"


def test_non_stream_request_body_and_parsed_response(server: str) -> None:
    ex = _executor(server)
    content, meta = ex.generate(
        [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert content == "Hi there"
    assert meta.get("tool_calls") == []

    req = RECEIVED[-1]
    assert req["path"] == "/v1/responses"
    body = req["body"]
    # Responses body shape, not Chat Completions.
    assert "messages" not in body
    assert body["instructions"] == "be nice"
    assert body["input"][0]["type"] == "message"
    assert body["input"][0]["content"][0] == {"type": "input_text", "text": "hi"}
    # ``SEED_LLM_MAX_TOKENS`` (when set) intentionally overrides the
    # constructor value — that is the pre-existing env-first contract.
    assert body["max_output_tokens"] == ex.maxOutputTokens
    assert body["max_output_tokens"] > 0
    assert body["store"] is False
    assert body["model"] == "gpt-5.1"


def test_stream_produces_deltas_and_done(server: str) -> None:
    ex = _executor(server)
    events = list(
        ex.generate_stream(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        )
    )
    deltas = [e for e in events if e["type"] == "delta"]
    assert "".join(e["text"] for e in deltas) == "Hello world"

    reasoning = [e for e in events if e["type"] == "reasoning_delta"]
    assert "".join(e["text"] for e in reasoning) == "thinking..."

    done = events[-1]
    assert done["type"] == "done"
    assert done["content"] == "Hello world"
    assert done["metadata"]["usage"]["total_tokens"] == 8

    body = RECEIVED[-1]["body"]
    assert body["stream"] is True
    assert "stream_options" not in body


def test_tool_call_roundtrip(server: str) -> None:
    """Assistant tool_calls -> function_call item -> function_call_output item."""
    ex = _executor(server)
    ex.generate(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"c":"SF"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ]
    )
    body = RECEIVED[-1]["body"]
    kinds = [i["type"] for i in body["input"]]
    assert "function_call" in kinds
    assert "function_call_output" in kinds
    fc = next(i for i in body["input"] if i["type"] == "function_call")
    assert fc["call_id"] == "call_1"
    assert fc["name"] == "get_weather"
    fo = next(i for i in body["input"] if i["type"] == "function_call_output")
    assert fo["call_id"] == "call_1"
    assert fo["output"] == "sunny"


def test_tools_are_flattened(server: str) -> None:
    ex = _executor(server)
    ex.generate(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    body = RECEIVED[-1]["body"]
    assert body["tools"][0]["name"] == "f"
    assert "function" not in body["tools"][0]
    assert body["tool_choice"] == "auto"
