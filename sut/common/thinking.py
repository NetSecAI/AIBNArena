"""How a subject asks a model to think, per model family.

Reasoning is not one switch. A vLLM-served Qwen takes a chat-template flag, the
hosted reasoning models take an effort level that LiteLLM normalises per
provider, and some open-weight families have no switch at all: their reasoning
variant is a different checkpoint, so asking one of them to think is a request
the run cannot honour.

Nothing here guesses. A model whose family is not listed, or a family whose
mechanism cannot express the request, is refused by name at startup: a condition
recorded in a result must be one that actually held, and a silently ignored
`enable_thinking` is exactly the failure this module exists to prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CHAT_TEMPLATE = "chat_template"
REASONING_EFFORT = "reasoning_effort"
SYSTEM_PROMPT = "system_prompt"
NO_SWITCH = "no_switch"
#: A model that does not reason at all: asking it to is refused, asking it not to
#: sends nothing, since that is the only mode it has.
NO_REASONING = "no_reasoning"
AUTO = "auto"

#: Model family -> how thinking is requested. Matched against the model name with
#: the provider prefix stripped, first match wins, so the order is the specific
#: before the general.
FAMILY_MECHANISMS: tuple[tuple[str, str], ...] = (
    # OpenAI's GPT-4 and GPT-3.5 generations do not reason (gpt-4o, gpt-4o-mini, gpt-4.1):
    # before 2026-11-18 they fell under `gpt-\d` below, so a run asking gpt-4o-mini to
    # think was started and sent a reasoning_effort it has no use for.
    (r"(?<![a-z0-9])gpt-(?:3\.5|4)(?![0-9])", NO_REASONING),
    # Qwen3 exposes it in its chat template; vLLM forwards chat_template_kwargs.
    (r"qwen", CHAT_TEMPLATE),
    # Hosted reasoning models take an effort level, which LiteLLM translates into
    # each provider's own field (OpenAI reasoning_effort, Anthropic thinking
    # budget, Gemini thinking config).
    (r"gpt-\d", REASONING_EFFORT),
    (r"^o\d", REASONING_EFFORT),
    (r"claude", REASONING_EFFORT),
    (r"gemini", REASONING_EFFORT),
    # Gemma and Ministral ship reasoning as a separate checkpoint rather than a
    # switch: "thinking" is chosen by which model is served, not by a parameter.
    (r"gemma", NO_SWITCH),
    (r"ministral|mistral", NO_SWITCH),
)

#: Effort asked for when thinking is on and the run names no level. Deliberately
#: the provider-neutral middle: a run that cares states its own.
DEFAULT_EFFORT = "medium"
#: Every effort level any family below takes, lowest first.
EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")

#: The levels a reasoning-effort family takes, and the one that stands for thinking
#: off (None when the family has none: it always reasons). Matched like
#: FAMILY_MECHANISMS, specific first. Only what a model page states is listed
#: (checked 2026-11-18): GPT-5, GPT-5 mini and nano take minimal to high; GPT-5.2 and
#: GPT-5.4 take none to xhigh, without minimal; the o-series low to high. The other
#: GPT-5.x get the levels every documented one takes, and no "off": which word turns
#: their reasoning off is not documented for each, and sending the wrong one fails
#: the first call of the run.
EFFORT_FAMILIES: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    (r"gpt-5\.[24](?![0-9])", ("none", "low", "medium", "high", "xhigh"), "none"),
    (r"gpt-5(?![.0-9])", ("minimal", "low", "medium", "high"), "minimal"),
    (r"gpt-5\.[0-9]", ("low", "medium", "high"), None),
    (r"^o[0-9]", ("low", "medium", "high"), None),
)
#: The other effort families (Claude, Gemini through LiteLLM), and a model whose run
#: names the reasoning_effort style itself: the levels they took before the table.
DEFAULT_EFFORT_FAMILY: tuple[tuple[str, ...], str | None] = (("minimal", "low", "medium", "high"), "minimal")


class ThinkingUnsupported(ValueError):
    """The run asked for a reasoning setting this model cannot be given."""


@dataclass(frozen=True)
class ThinkingPlan:
    """What will actually be sent, and under which mechanism."""

    mechanism: str
    request_kwargs: dict[str, Any] = field(default_factory=dict)
    directive_applies: bool = False

    def as_record(self) -> dict[str, Any]:
        """What the result should say about how thinking was requested."""
        return {
            "thinking_mechanism": self.mechanism,
            "thinking_request": dict(self.request_kwargs),
            "thinking_directive_applied": self.directive_applies,
        }


def family_mechanism(model: str) -> str | None:
    """The mechanism for this model, or None when the family is not known here."""
    name = str(model).rsplit("/", 1)[-1].lower()
    for pattern, mechanism in FAMILY_MECHANISMS:
        if re.search(pattern, name):
            return mechanism
    return None


def effort_family(model: str) -> tuple[tuple[str, ...], str | None]:
    """The effort levels this model takes, and its level for thinking off (or None)."""
    name = str(model).rsplit("/", 1)[-1].lower()
    for pattern, levels, off in EFFORT_FAMILIES:
        if re.search(pattern, name):
            return levels, off
    return DEFAULT_EFFORT_FAMILY


def resolve_thinking(
    model: str,
    enable_thinking: bool | None,
    style: str = AUTO,
    effort: str | None = None,
) -> ThinkingPlan:
    """Decide how to ask this model to think, or refuse the request by name.

    `enable_thinking` None means the run says nothing, and nothing is sent: the
    model runs in whatever mode it serves by default.
    """
    if enable_thinking is None:
        if effort:
            raise ThinkingUnsupported(
                "thinking_effort was set without enable_thinking; say whether thinking is on")
        return ThinkingPlan(mechanism="unset")

    mechanism = style
    if style in (AUTO, "", None):
        mechanism = family_mechanism(model)
        if mechanism is None:
            raise ThinkingUnsupported(
                f"no reasoning mechanism is known for model {model!r}; set thinking_style "
                f"explicitly to one of {CHAT_TEMPLATE!r}, {REASONING_EFFORT!r}, {SYSTEM_PROMPT!r}")

    if mechanism == NO_REASONING:
        if enable_thinking:
            raise ThinkingUnsupported(
                f"model {model!r} does not reason: it has no thinking to turn on. Run a "
                "reasoning model (gpt-5-mini, say) or leave thinking unset")
        if effort:
            raise ThinkingUnsupported(
                f"model {model!r} does not reason, so it takes no thinking_effort")
        return ThinkingPlan(mechanism=NO_REASONING)

    if mechanism == NO_SWITCH:
        raise ThinkingUnsupported(
            f"model {model!r} has no reasoning switch: its family ships reasoning as a "
            "separate checkpoint, so serve the reasoning model instead of setting "
            "enable_thinking")

    if mechanism == CHAT_TEMPLATE:
        if effort:
            raise ThinkingUnsupported(
                f"model {model!r} takes a chat-template flag, not an effort level")
        return ThinkingPlan(
            mechanism=CHAT_TEMPLATE,
            request_kwargs={"extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}},
        )

    if mechanism == REASONING_EFFORT:
        levels, off = effort_family(model)
        if not enable_thinking:
            # The family's own word for off: "none" where it exists, else "minimal", the
            # lowest it takes -- no level turns those into non-reasoning models, and
            # pretending otherwise would record a condition that did not hold. A family
            # with neither always reasons, and is refused rather than sent a guess.
            if off is None:
                raise ThinkingUnsupported(
                    f"model {model!r} has no documented level that turns its reasoning off; "
                    f"leave thinking unset, or turn it on with one of {list(levels)}")
            level = off
        else:
            level = (effort or DEFAULT_EFFORT).lower()
        if level not in EFFORT_LEVELS:
            raise ThinkingUnsupported(
                f"unknown thinking_effort {level!r}; use one of {list(EFFORT_LEVELS)}")
        if level not in levels:
            raise ThinkingUnsupported(
                f"model {model!r} takes thinking_effort {list(levels)}, not {level!r}")
        return ThinkingPlan(mechanism=REASONING_EFFORT, request_kwargs={"reasoning_effort": level})

    if mechanism == SYSTEM_PROMPT:
        if effort:
            raise ThinkingUnsupported(
                "thinking_effort has no meaning for the system-prompt mechanism")
        return ThinkingPlan(mechanism=SYSTEM_PROMPT, directive_applies=bool(enable_thinking))

    raise ThinkingUnsupported(
        f"unknown thinking_style {style!r}; use {AUTO!r}, {CHAT_TEMPLATE!r}, "
        f"{REASONING_EFFORT!r} or {SYSTEM_PROMPT!r}")


def apply_directive(system_prompt: str, plan: ThinkingPlan, directive: str | None) -> str:
    """The system prompt with the run's directive in front, when the plan calls for it.

    Only the prompt mechanism carries thinking through the instructions, and only a
    non-empty directive changes anything: enabling the style alone must not quietly
    alter the prompt the agent runs under. One implementation for every subject, so
    the LiteLLM loop and the LangChain agent cannot merge the directive differently.
    """
    if not plan.directive_applies:
        return system_prompt
    text = (directive or "").strip()
    return f"{text}\n\n{system_prompt}" if text else system_prompt


def thinking_record(config: Any) -> dict[str, Any]:
    """What a result should say about how thinking was asked for.

    Reports the mechanism the model family resolved to and what was sent under it,
    not only the request: a run whose model has no switch is refused before it
    starts, so a record that names a mechanism is a record of what happened.
    """
    plan = resolve_thinking(
        config.model,
        config.enable_thinking,
        getattr(config, "thinking_style", AUTO),
        getattr(config, "thinking_effort", None),
    )
    return {
        "enable_thinking": config.enable_thinking,
        "thinking_style": getattr(config, "thinking_style", AUTO),
        "thinking_effort": getattr(config, "thinking_effort", None),
        **plan.as_record(),
    }
