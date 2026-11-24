"""--max-tokens 0, none or unlimited, under which a model call sends no output limit.

A reply cut at max_tokens carries no tool call, and the episode then ends with no conclusion. In
main07 it was 17 of the 40 episodes of one subject on Qwen3.8-27B at 16000 tokens. The limit can be
lifted. The request then carries no max_tokens, and vLLM 0.19 lets the reply run to what the
context window leaves after the prompt (unless the model's generation_config sets max_new_tokens).
A fixed large limit would not do. vLLM refuses a request whose prompt and limit together exceed
the window.
"""
from __future__ import annotations

import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType, SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from sut.common.agent_config import ModelAgentConfig, parse_max_tokens, request_max_tokens
from sut.common.litellm_backend import LiteLLMModelCaller
from sut.langchain_agent.agent import LangChainAgentConfig, LangChainRepairAgent


@pytest.mark.parametrize("value, expected", [
    ("16000", 16000), (8000, 8000), ("0", 0), (0, 0), ("none", 0), ("None", 0), ("UNLIMITED", 0), (" unlimited ", 0),
])
def test_a_limit_or_a_word_for_none_is_read(value, expected):
    assert parse_max_tokens(value) == expected


@pytest.mark.parametrize("value", ["-1", "abc", ""])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        parse_max_tokens(value)


def test_zero_is_sent_as_no_limit_and_a_limit_as_itself():
    assert request_max_tokens(0) is None
    assert request_max_tokens(16000) == 16000


def test_the_environment_takes_the_same_words(monkeypatch):
    monkeypatch.setenv("MAXTOKTEST_MAX_TOKENS", "unlimited")
    assert ModelAgentConfig.env_fields("MAXTOKTEST")["max_tokens"] == 0
    monkeypatch.setenv("MAXTOKTEST_MAX_TOKENS", "16000")
    assert ModelAgentConfig.env_fields("MAXTOKTEST")["max_tokens"] == 16000


def _caller(max_tokens: int) -> LiteLLMModelCaller:
    return LiteLLMModelCaller(
        model="test-model", api_base="http://provider.invalid/v1", api_key="test-key", max_tokens=max_tokens,
        temperature=None, enable_thinking=None, thinking_style="chat_template", thinking_directive=None,
        max_execution_seconds=30.0, tool_schemas=lambda: [])


def _fake_litellm(monkeypatch, completion) -> None:
    module = ModuleType("litellm")
    module.completion = completion
    module.suppress_debug_info = False
    monkeypatch.setitem(sys.modules, "litellm", module)


def _answer():
    return SimpleNamespace(choices=[SimpleNamespace(message={"content": "ok"}, finish_reason="stop")],
                           model="provider/served-model",
                           usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7))


def test_the_litellm_loop_sends_no_max_tokens_without_a_limit(monkeypatch):
    calls = []
    _fake_litellm(monkeypatch, lambda **kwargs: calls.append(kwargs) or _answer())
    _caller(0)([{"role": "user", "content": "repair"}], 10.0)
    _caller(16000)([{"role": "user", "content": "repair"}], 10.0)
    assert "max_tokens" not in calls[0]
    assert calls[1]["max_tokens"] == 16000


def test_a_context_rejection_without_a_limit_is_not_retried_with_one(monkeypatch):
    """With no reservation the prompt itself is too long, and a smaller reservation cannot help."""
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("ContextWindowExceededError: This model's maximum context length is 40960 tokens. "
                           "However, you requested 41000 tokens.")

    _fake_litellm(monkeypatch, completion)
    with pytest.raises(RuntimeError, match="ContextWindowExceededError"):
        _caller(0)([{"role": "user", "content": "repair"}], 10.0)
    assert len(calls) == 1 and "max_tokens" not in calls[0]


class _Endpoint(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self):  # noqa: N802 - the http.server interface
        self.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        out = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "served-model",
                          "choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"role": "assistant", "content": "ok"}}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):  # noqa: D102 - silence the test server
        pass


def test_the_langchain_subjects_send_no_max_tokens_on_the_wire_without_a_limit(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Endpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        for limit in (0, 16000):
            config = LangChainAgentConfig(model="openai/served-model", api_base=base, api_key="k",
                                          max_tokens=limit, artifact_directory=str(tmp_path))
            LangChainRepairAgent(config, ani=object())._model().invoke([HumanMessage("repair")])
    finally:
        server.shutdown()
    unlimited, limited = _Endpoint.bodies[-2:]
    assert "max_tokens" not in unlimited
    assert limited["max_tokens"] == 16000


@pytest.mark.parametrize("module", ["sut.step-back.full_baseline.a2a_server",
                                    "sut.step-back.dumb_baseline.a2a_server",
                                    "sut.step-back.object_baseline.a2a_server",
                                    "sut.langchain_agent_v2.a2a_server"])
@pytest.mark.parametrize("word", ["none", "unlimited", "0"])
def test_the_servers_take_the_words_on_the_command_line(monkeypatch, module, word):
    monkeypatch.setattr(sys, "argv", ["a2a_server.py", "--max-tokens", word])
    assert importlib.import_module(module).parse_args().max_tokens == 0
