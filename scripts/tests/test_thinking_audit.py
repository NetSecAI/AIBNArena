"""A thinkon label is checked against the reasoning the trace actually holds."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("thinking_audit", REPO / "scripts" / "thinking_audit.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AU = _load()
REQUEST = {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}


def _record(trace_ref, *, experiment_id="connectivity-smoke-a-thinkon", dirty=False, calls=4, out=3000):
    return {"experiment_id": experiment_id, "run_id": "r", "scenario": {"id": "connectivity.a.m1"},
            "provenance": {"sut_identity": "x", "configured_model": "openai/qwen3-8b-think",
                           "sut_reported_model": "openai/qwen3-8b-think", "git": {"commit": "abc", "dirty": dirty},
                           "model_parameters": {"enable_thinking": True, "thinking_mechanism": "chat_template",
                                                "thinking_request": REQUEST}},
            "sut_result": {"provider_reported_model": "openai/qwen3-8b-think",
                           "execution": {"trace_ref": str(trace_ref), "llm_calls": calls,
                                         "token_usage": {"output_tokens": out}}}}


def _trace(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return path


def test_both_trace_shapes_are_read(tmp_path):
    loop = _trace(tmp_path / "loop.jsonl", [
        {"title": "SUT raw model message", "payload": {"reasoning_content": "because", "content": ""}},
        {"title": "SUT raw model message", "payload": {"reasoning_content": "", "content": "x"}},
        {"title": "ANI get_node", "payload": {}},
    ])
    assert AU.trace_reasoning(str(loop)) == (2, 1)
    langchain = _trace(tmp_path / "lc.jsonl", [
        {"title": "SUT model response", "payload": {"turn": 1, "generations": [
            {"text": "a", "reasoning_content": "hmm"}, {"text": "b"}]}},
    ])
    assert AU.trace_reasoning(str(langchain)) == (2, 1)
    assert AU.trace_reasoning(str(tmp_path / "missing.jsonl")) is None


def test_a_thinkon_label_with_no_reasoning_is_a_lie_and_a_dirty_tree_a_warning(tmp_path):
    silent = _trace(tmp_path / "silent.jsonl", [
        {"title": "SUT raw model message", "payload": {"content": "x"}}] * 3)
    verdict = AU.audit(_record(silent))
    assert any("no reasoning_content" in p for p in verdict["problems"])

    thinking = _trace(tmp_path / "thinking.jsonl", [
        {"title": "SUT raw model message", "payload": {"reasoning_content": "because"}}] * 3)
    verdict = AU.audit(_record(thinking))
    assert verdict["problems"] == []

    verdict = AU.audit(_record(thinking, dirty=True))
    assert verdict["problems"] == [] and verdict["warnings"] == ["provenance.git.dirty is true"]

    verdict = AU.audit(_record(thinking, experiment_id="connectivity-smoke-a-thinkoff"))
    assert verdict["problems"] == [], "an episode not labelled thinkon claims nothing"


def test_the_label_is_checked_field_by_field(tmp_path):
    thinking = _trace(tmp_path / "t.jsonl", [
        {"title": "SUT raw model message", "payload": {"reasoning_content": "because"}}])
    record = _record(thinking)
    record["provenance"]["model_parameters"]["thinking_request"] = {}
    verdict = AU.audit(record)
    assert any("thinking_request" in p for p in verdict["problems"])


def test_low_output_per_call_is_a_warning_with_a_trace_and_a_lie_without(tmp_path):
    """Qwen3.5-9B under "thinking permitted" answers most tool results without reasoning:
    few output tokens per call, reasoning on a handful of messages. The trace decides,
    so that reads as a warning with the share; the token heuristic alone (no trace)
    still convicts."""
    sparse = _trace(tmp_path / "sparse.jsonl", [
        {"title": "SUT raw model message", "payload": {"reasoning_content": "hmm", "content": ""}}]
        + [{"title": "SUT raw model message", "payload": {"content": "x"}}] * 9)
    verdict = AU.audit(_record(sparse, calls=10, out=2000))
    assert verdict["problems"] == []
    assert any("200 output tokens per call" in w and "reasoning on 1 of 10" in w for w in verdict["warnings"])
    assert verdict["reasoning_share"] == 0.1
    verdict = AU.audit(_record(tmp_path / "missing.jsonl", calls=10, out=2000))
    assert any("output tokens per call" in p for p in verdict["problems"])
    assert verdict["reasoning_share"] is None
