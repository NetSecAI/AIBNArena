"""The baseline sends past reasoning back the way the LiteLLM loop does.

langchain_litellm returns each past turn's reasoning as `reasoning_content`, which vLLM
(0.19) does not read from a request message, so every past turn reached the chat template
as an empty think block and the baseline stopped reasoning after its first reply, as the
loop did. The subject's model moves it to `reasoning` on the last
REASONING_TURNS_SENT_BACK assistant turns. Checked on the conversion, then on the wire
through a real LangChain graph against a local endpoint that answers as vLLM does.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from sut.common.messages import REASONING_KEY, REASONING_TURNS_SENT_BACK
from sut.langchain_agent.agent import LangChainAgentConfig, LangChainRepairAgent


def _model(tmp_path, api_base: str = "http://127.0.0.1:9/v1"):
    config = LangChainAgentConfig(model="openai/served-model", api_base=api_base, api_key="k",
                                  artifact_directory=str(tmp_path))
    return LangChainRepairAgent(config, ani=object())._model()


def test_reasoning_moves_to_the_key_vllm_reads_on_the_recent_turns_only(tmp_path):
    history = [SystemMessage("system"), HumanMessage("repair")]
    for index in range(1, 6):
        history.append(AIMessage(
            content="", additional_kwargs={"reasoning_content": f"think {index}"} if index != 3 else {},
            tool_calls=[{"name": "get_state", "args": {}, "id": f"call_{index}"}]))
        history.append(ToolMessage("{}", tool_call_id=f"call_{index}"))

    dicts, _params = _model(tmp_path)._create_message_dicts(history, None)

    assert REASONING_TURNS_SENT_BACK == 3
    assert [message.get(REASONING_KEY) for message in dicts if message["role"] == "assistant"] == [
        None, "think 2", None, "think 4", "think 5"], "the turn with none does not count among the three"
    assert not any("reasoning_content" in message for message in dicts)


def _vllm_reply(index: int, last: int) -> dict:
    message = {"role": "assistant", "content": None, "reasoning": f"think {index}",
               "tool_calls": [{"id": f"call_{index}", "type": "function",
                               "function": {"name": "get_state", "arguments": "{}"}}]}
    if index == last:
        message = {"role": "assistant", "content": "done", "reasoning": f"think {index}"}
    return {"id": f"chatcmpl-{index}", "object": "chat.completion", "created": 0, "model": "served-model",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "stop" if index == last else "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def test_a_langchain_graph_sends_the_recent_reasoning_back_on_the_wire(tmp_path):
    bodies: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            body = json.dumps(_vllm_reply(len(bodies), last=5)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    @tool
    def get_state() -> str:
        """Read the state."""
        return "{}"

    try:
        graph = create_agent(model=_model(tmp_path, f"http://127.0.0.1:{server.server_port}/v1"),
                             tools=[get_state], system_prompt="system")
        graph.invoke({"messages": [{"role": "user", "content": "repair"}]})
    finally:
        server.shutdown()

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
