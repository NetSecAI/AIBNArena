"""A reply's reasoning reaches the server again, under the key the server reads.

vLLM (0.19) returns a reply's reasoning as `reasoning`, LiteLLM hands it over as
`reasoning_content`, and on a request message vLLM reads only `reasoning`. These tests
serve the model from a local endpoint that answers the way vLLM does and look at the
request bodies LiteLLM actually sends, so the key is checked where it matters, on the wire.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sut.common.litellm_backend import LiteLLMModelCaller
from sut.common.messages import (
    REASONING_KEY,
    assistant_tool_message,
    keep_recent_reasoning,
    message_content,
    message_reasoning,
    tool_call_parts,
    tool_calls,
)


def _vllm_reply(index: int) -> dict:
    """A tool-call reply as vLLM 0.19 serializes it, reasoning under `reasoning`."""
    return {
        "id": f"chatcmpl-{index}", "object": "chat.completion", "created": 0, "model": "served-model",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning": f"think {index}",
            "tool_calls": [{"id": f"call_{index}", "type": "function",
                            "function": {"name": "get_state", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture
def endpoint():
    bodies: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            body = json.dumps(_vllm_reply(len(bodies))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", bodies
    server.shutdown()


def test_the_loop_sends_the_reasoning_back_under_the_key_vllm_reads(endpoint) -> None:
    """The loop's own steps, in its order: read the reply, replay it, cut, call again."""
    api_base, bodies = endpoint
    caller = LiteLLMModelCaller(
        model="openai/served-model", api_base=api_base, api_key="test-key", max_tokens=16,
        temperature=None, enable_thinking=None, thinking_style="chat_template",
        thinking_directive=None, max_execution_seconds=1800.0, tool_schemas=lambda: [],
    )
    messages = [{"role": "user", "content": "repair"}]
    for _ in range(5):
        keep_recent_reasoning(messages)
        reply = caller(messages, 60.0)
        calls = tool_calls(reply)
        parts = [tool_call_parts(call) for call in calls]
        messages.append(assistant_tool_message(message_content(reply), calls, parts, message_reasoning(reply)))
        messages.append({"role": "tool", "tool_call_id": parts[0][2], "content": "{}"})

    assert message_reasoning(reply) == "think 5", "the reasoning vLLM returned is read from the reply"
    sent = [[message.get(REASONING_KEY) for message in body["messages"] if message["role"] == "assistant"]
            for body in bodies]
    assert sent == [
        [],
        ["think 1"],
        ["think 1", "think 2"],
        ["think 1", "think 2", "think 3"],
        [None, "think 2", "think 3", "think 4"],
    ]
    assert not any("reasoning_content" in message for body in bodies for message in body["messages"])
