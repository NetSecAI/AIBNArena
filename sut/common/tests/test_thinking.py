"""Reasoning is requested per model family, and a request that cannot be honoured stops the run.

The failure this guards against is silence: sending a Qwen chat-template flag to a
model that ignores it, while the result records that thinking was on.
"""
from __future__ import annotations

import pytest

from sut.common.thinking import (
    CHAT_TEMPLATE,
    DEFAULT_EFFORT,
    NO_SWITCH,
    REASONING_EFFORT,
    SYSTEM_PROMPT,
    ThinkingUnsupported,
    family_mechanism,
    resolve_thinking,
)


@pytest.mark.parametrize("model,mechanism", [
    ("openai/qwen3-8b-think", CHAT_TEMPLATE),
    ("Qwen3-32B", CHAT_TEMPLATE),
    ("gpt-5.6-sol", REASONING_EFFORT),
    ("openai/gpt-5", REASONING_EFFORT),
    ("claude-5-opus", REASONING_EFFORT),
    ("gemini/gemini-3.1-pro", REASONING_EFFORT),
    ("gemma-4-31b", NO_SWITCH),
    ("ministral-3-14b", NO_SWITCH),
    ("some-unlisted-model", None),
])
def test_the_family_decides_the_mechanism(model: str, mechanism: str | None) -> None:
    assert family_mechanism(model) == mechanism


def test_a_chat_template_family_gets_the_serving_flag() -> None:
    plan = resolve_thinking("openai/qwen3-8b-think", True)
    assert plan.mechanism == CHAT_TEMPLATE
    assert plan.request_kwargs == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}
    assert resolve_thinking("openai/qwen3-8b-think", False).request_kwargs == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}


def test_a_reasoning_family_gets_an_effort_level() -> None:
    assert resolve_thinking("gpt-5.6-sol", True).request_kwargs == {
        "reasoning_effort": DEFAULT_EFFORT}
    assert resolve_thinking("claude-5-opus", True, effort="high").request_kwargs == {
        "reasoning_effort": "high"}
    # There is no level that makes a reasoning model stop reasoning; the lowest the
    # shared vocabulary has is what "off" can honestly mean.
    assert resolve_thinking("gemini/gemini-3.1-pro", False).request_kwargs == {
        "reasoning_effort": "minimal"}


def test_a_family_without_a_switch_is_refused_by_name() -> None:
    for model in ("gemma-4-31b", "ministral-3-14b"):
        with pytest.raises(ThinkingUnsupported, match="no reasoning switch"):
            resolve_thinking(model, True)


def test_an_unknown_family_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ThinkingUnsupported, match="no reasoning mechanism is known"):
        resolve_thinking("some-unlisted-model", True)
    # An explicit style is still honoured: the refusal is about guessing, not about
    # running a model the table has not met.
    assert resolve_thinking("some-unlisted-model", True, style=CHAT_TEMPLATE).mechanism == CHAT_TEMPLATE


def test_saying_nothing_sends_nothing() -> None:
    plan = resolve_thinking("openai/qwen3-8b-think", None)
    assert plan.mechanism == "unset" and plan.request_kwargs == {}
    with pytest.raises(ThinkingUnsupported, match="without enable_thinking"):
        resolve_thinking("gpt-5.6-sol", None, effort="high")


def test_effort_is_refused_where_it_has_no_meaning() -> None:
    with pytest.raises(ThinkingUnsupported, match="chat-template flag"):
        resolve_thinking("openai/qwen3-8b-think", True, effort="high")
    with pytest.raises(ThinkingUnsupported, match="no meaning"):
        resolve_thinking("any-model", True, style=SYSTEM_PROMPT, effort="high")
    with pytest.raises(ThinkingUnsupported, match="unknown thinking_effort"):
        resolve_thinking("gpt-5.6-sol", True, effort="enormous")


def test_the_prompt_mechanism_only_marks_the_directive() -> None:
    plan = resolve_thinking("any-model", True, style=SYSTEM_PROMPT)
    assert plan.request_kwargs == {} and plan.directive_applies is True
    assert resolve_thinking("any-model", False, style=SYSTEM_PROMPT).directive_applies is False


def test_the_directive_goes_in_front_only_on_the_prompt_route() -> None:
    """One merge for every subject; enabling the style alone must not alter the prompt."""
    from sut.common.thinking import apply_directive

    prompt_route = resolve_thinking("openai/gpt-5.4", True, "system_prompt")
    assert apply_directive("Repair the lab.", prompt_route, "Think first.") == \
        "Think first.\n\nRepair the lab."
    assert apply_directive("Repair the lab.", prompt_route, "   ") == "Repair the lab."
    assert apply_directive("Repair the lab.", prompt_route, None) == "Repair the lab."

    off = resolve_thinking("openai/gpt-5.4", False, "system_prompt")
    assert apply_directive("Repair the lab.", off, "Think first.") == "Repair the lab."

    chat_template = resolve_thinking("openai/qwen3-8b-think", True)
    assert apply_directive("Repair the lab.", chat_template, "Think first.") == "Repair the lab.", \
        "the chat-template route carries thinking in the request, never in the prompt"


# --- families that do not reason, and the levels each reasoning family takes -----------

from sut.common.thinking import NO_REASONING, effort_family  # noqa: E402


@pytest.mark.parametrize("model", [
    "openai/gpt-4o-mini", "gpt-4o", "openai/gpt-4.1-mini", "gpt-4", "gpt-3.5-turbo", "fr-gpt-4o"])
def test_the_gpt_4_and_3_5_generations_do_not_reason(model: str) -> None:
    """gpt-4o-mini fell under `gpt-\\d` and was sent a reasoning_effort before 2026-11-18."""
    assert family_mechanism(model) == NO_REASONING
    with pytest.raises(ThinkingUnsupported, match="does not reason"):
        resolve_thinking(model, True)
    with pytest.raises(ThinkingUnsupported, match="takes no thinking_effort"):
        resolve_thinking(model, False, effort="low")
    # Off is the only mode it has: accepted, and nothing is sent.
    plan = resolve_thinking(model, False)
    assert plan.mechanism == NO_REASONING and plan.request_kwargs == {}


@pytest.mark.parametrize("model", ["openai/gpt-5-mini", "gpt-5", "gpt-5-nano", "openai/gpt-5.4-mini", "o4-mini"])
def test_the_gpt_5_and_o_series_still_reason(model: str) -> None:
    assert family_mechanism(model) == REASONING_EFFORT


def test_each_family_takes_its_own_levels() -> None:
    # GPT-5, GPT-5 mini and nano: minimal to high; off is minimal.
    assert effort_family("openai/gpt-5-mini") == (("minimal", "low", "medium", "high"), "minimal")
    assert resolve_thinking("openai/gpt-5-mini", False).request_kwargs == {"reasoning_effort": "minimal"}
    with pytest.raises(ThinkingUnsupported, match="not 'xhigh'"):
        resolve_thinking("openai/gpt-5-mini", True, effort="xhigh")
    # GPT-5.2 and GPT-5.4: none to xhigh, no minimal; off is none.
    for model in ("openai/gpt-5.4-mini", "gpt-5.2", "openai/gpt-5.4"):
        assert resolve_thinking(model, False).request_kwargs == {"reasoning_effort": "none"}
        assert resolve_thinking(model, True, effort="xhigh").request_kwargs == {"reasoning_effort": "xhigh"}
        with pytest.raises(ThinkingUnsupported, match="not 'minimal'"):
            resolve_thinking(model, True, effort="minimal")


def test_a_family_with_no_documented_off_level_is_not_sent_a_guess() -> None:
    for model in ("gpt-5.6-sol", "o4-mini", "o3"):
        assert resolve_thinking(model, True, effort="high").request_kwargs == {"reasoning_effort": "high"}
        with pytest.raises(ThinkingUnsupported, match="no documented level that turns its reasoning off"):
            resolve_thinking(model, False)
    with pytest.raises(ThinkingUnsupported, match="not 'xhigh'"):
        resolve_thinking("o4-mini", True, effort="xhigh")
