"""The model call ends with the budget, against a real HTTP endpoint.

The OpenAI client under LiteLLM retries a timed-out request with the same timeout,
which is invisible to a stubbed `completion`; these tests serve the call from a local
server so the client's own behaviour is what is measured.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import pytest

from sut.common.litellm_backend import LiteLLMModelCaller

ANSWER = {
    "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "served-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture
def endpoint():
    """A chat endpoint whose answer to the n-th request (1-based) a test chooses."""
    hits: list[float] = []
    behaviour: dict[str, Callable[[int], tuple[int, float]]] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            hits.append(time.monotonic())
            status, delay = behaviour["answer"](len(hits))
            time.sleep(delay)
            payload = ANSWER if status == 200 else {"error": {"message": "rate limited", "code": status}}
            body = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", hits, behaviour
    server.shutdown()


def _caller(api_base: str) -> LiteLLMModelCaller:
    return LiteLLMModelCaller(
        model="served-model", api_base=api_base, api_key="test-key", max_tokens=16,
        temperature=None, enable_thinking=None, thinking_style="chat_template",
        thinking_directive=None, max_execution_seconds=1800.0, tool_schemas=lambda: [],
    )


def test_a_call_that_outlives_the_budget_is_sent_once(endpoint) -> None:
    api_base, hits, behaviour = endpoint
    behaviour["answer"] = lambda n: (200, 4.0)

    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        _caller(api_base)([{"role": "user", "content": "hi"}], 1.0)
    elapsed = time.monotonic() - started

    assert "Timeout" in type(raised.value).__name__
    assert len(hits) == 1
    # One attempt plus LiteLLM's own setup; the two retries it used to make took 6 s.
    assert elapsed < 4.0


def test_a_rate_limit_is_retried_while_the_budget_lasts(endpoint) -> None:
    api_base, hits, behaviour = endpoint
    behaviour["answer"] = lambda n: (429, 0.0) if n == 1 else (200, 0.0)

    message = _caller(api_base)([{"role": "user", "content": "hi"}], 20.0)

    assert message["content"] == "ok"
    assert len(hits) == 2


def test_a_rate_limit_is_not_retried_past_the_budget(endpoint) -> None:
    api_base, hits, behaviour = endpoint
    behaviour["answer"] = lambda n: (429, 0.0)

    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        _caller(api_base)([{"role": "user", "content": "hi"}], 2.5)

    assert "RateLimit" in type(raised.value).__name__
    assert len(hits) == 1
    assert time.monotonic() - started < 2.5
