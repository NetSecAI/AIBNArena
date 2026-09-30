from __future__ import annotations

import math
import sys
from types import ModuleType, SimpleNamespace

import pytest

from sut.common.litellm_backend import LiteLLMModelCaller


def _caller(*, max_tokens: int = 8_000, min_retry_tokens: int | None = None) -> LiteLLMModelCaller:
    return LiteLLMModelCaller(
        model="test-model",
        api_base="http://provider.invalid/v1",
        api_key="test-key",
        max_tokens=max_tokens,
        temperature=None,
        enable_thinking=None,
        thinking_style="chat_template",
        thinking_directive=None,
        max_execution_seconds=30.0,
        tool_schemas=lambda: [],
        min_retry_tokens=min_retry_tokens,
    )


def _fake_litellm(monkeypatch: pytest.MonkeyPatch, completion) -> None:
    module = ModuleType("litellm")
    module.completion = completion
    module.suppress_debug_info = False
    monkeypatch.setitem(sys.modules, "litellm", module)


def test_context_overflow_retries_same_messages_with_smaller_reservation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError(
                "ContextWindowExceededError: This model's maximum context length is "
                "40960 tokens. However, you requested 8000 output tokens and your "
                "prompt contains at least 32961 input tokens."
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message={"content": "ok"})],
            model="provider/served-model",
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        )

    _fake_litellm(monkeypatch, completion)
    messages = [{"role": "user", "content": "preserve me"}]

    result = _caller()(messages, 10.0)

    # The retried call is the one whose telemetry is reported: the attempt that
    # raised spent no tokens the judge should attribute to the episode.
    assert result["content"] == "ok"
    assert result["_ibn_provider_model"] == "provider/served-model"
    assert result["_ibn_usage"] == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
    assert [call["max_tokens"] for call in calls] == [8_000, 4_000]
    assert calls[0]["messages"] is messages
    assert calls[1]["messages"] is messages
    # The reservation the answer was produced under travels with it.
    assert result["_ibn_reservation"] == {"reductions": 1, "min_max_tokens": 4_000}


def test_non_context_provider_error_is_not_retried(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("connection failed")

    _fake_litellm(monkeypatch, completion)

    with pytest.raises(RuntimeError, match="connection failed"):
        _caller()([{"role": "user", "content": "x"}], 10.0)

    assert len(calls) == 1


CONTEXT_ERROR = RuntimeError(
    "ContextWindowExceededError: This model's maximum context length is 40960 tokens.")


def _always_overflowing(monkeypatch: pytest.MonkeyPatch) -> list:
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        raise CONTEXT_ERROR

    _fake_litellm(monkeypatch, completion)
    return calls


def test_without_a_floor_the_reservation_halves_to_one_token(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The default is unchanged: every result before the floor existed ran this way."""
    calls = _always_overflowing(monkeypatch)
    expected = [8_000]
    while expected[-1] > 1:
        expected.append(expected[-1] // 2)

    with pytest.raises(RuntimeError, match="ContextWindowExceeded") as raised:
        _caller()([{"role": "user", "content": "x"}], 30.0)

    assert [call["max_tokens"] for call in calls] == expected
    assert len(calls) == 13
    assert raised.value._ibn_reservation == {"reductions": 12, "min_max_tokens": 1}


def test_a_floor_stops_the_retries_on_the_providers_own_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _always_overflowing(monkeypatch)

    with pytest.raises(RuntimeError, match="ContextWindowExceeded") as raised:
        _caller(min_retry_tokens=1_024)([{"role": "user", "content": "x"}], 30.0)

    # 8000 -> 4000 -> 2000 are allowed; the next halving would land under the floor.
    assert len(calls) <= math.ceil(math.log2(8_000 / 1_024)) + 1
    assert [call["max_tokens"] for call in calls] == [8_000, 4_000, 2_000]
    assert min(call["max_tokens"] for call in calls) >= 1_024
    assert raised.value is CONTEXT_ERROR
    assert raised.value._ibn_reservation == {"reductions": 2, "min_max_tokens": 2_000}


def test_a_floor_does_not_touch_a_call_that_never_overflowed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message={"content": "ok"})],
            model="provider/served-model",
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        )

    _fake_litellm(monkeypatch, completion)
    result = _caller(min_retry_tokens=1_024)([{"role": "user", "content": "x"}], 10.0)

    assert [call["max_tokens"] for call in calls] == [8_000]
    assert result["_ibn_reservation"] == {"reductions": 0, "min_max_tokens": 8_000}


def test_the_floor_is_a_config_field_settable_from_the_environment(monkeypatch) -> None:
    from sut.common.agent_config import ModelAgentConfig

    assert ModelAgentConfig().min_retry_tokens is None
    assert LiteLLMModelCaller.from_config(ModelAgentConfig(), tool_schemas=lambda: []).min_retry_tokens is None

    monkeypatch.setenv("IBN_SUT_MIN_RETRY_TOKENS", "512")
    assert ModelAgentConfig.env_fields("TEST_SUT")["min_retry_tokens"] == 512

    # The subject's own prefix wins over the shared one, as for the other knobs.
    monkeypatch.setenv("TEST_SUT_MIN_RETRY_TOKENS", "256")
    config = ModelAgentConfig(**ModelAgentConfig.env_fields("TEST_SUT"))
    assert config.min_retry_tokens == 256
    assert LiteLLMModelCaller.from_config(config, tool_schemas=lambda: []).min_retry_tokens == 256
