"""LiteLLM-backed model caller.

This is the one piece the self_execute loop does not own: swap it and the same
loop drives a different provider stack. It keeps the caller signature the loop
expects, `(messages, remaining_seconds) -> message`, so a scripted stand-in can
replace it in tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable

from .agent_config import ModelAgentConfig
from .thinking import ThinkingPlan, apply_directive, resolve_thinking
from .trace import Tracer


@dataclass(frozen=True)
class LiteLLMModelCaller:
    model: str
    api_base: str | None
    api_key: str | None
    max_tokens: int
    temperature: float | None
    enable_thinking: bool | None
    thinking_style: str
    thinking_directive: str | None
    max_execution_seconds: float
    tool_schemas: Callable[[], list[dict[str, Any]]]
    #: How hard a reasoning model should think, when its mechanism takes a level.
    thinking_effort: str | None = None
    #: Floor for the output reservation a context rejection may retry with; None
    #: retries down to one token.
    min_retry_tokens: int | None = None
    trace: Tracer = field(default_factory=lambda: Tracer("off"))

    @classmethod
    def from_config(
        cls,
        config: ModelAgentConfig,
        tool_schemas: Callable[[], list[dict[str, Any]]],
        trace: Tracer | None = None,
    ) -> "LiteLLMModelCaller":
        return cls(
            model=config.model,
            api_base=config.api_base,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            enable_thinking=config.enable_thinking,
            thinking_style=config.thinking_style,
            thinking_directive=config.thinking_directive,
            thinking_effort=config.thinking_effort,
            min_retry_tokens=config.min_retry_tokens,
            max_execution_seconds=config.max_execution_seconds,
            tool_schemas=tool_schemas,
            trace=trace or Tracer(config.debug_trace, config.trace_directory),
        )

    @property
    def thinking_plan(self) -> ThinkingPlan:
        """How this model is asked to think, refused by name when it cannot be.

        Computed from the model name and the run's setting rather than assumed:
        a family that has no switch must stop the run, not be sent a flag it
        ignores while the record claims thinking was on.
        """
        return resolve_thinking(
            self.model, self.enable_thinking, self.thinking_style, self.thinking_effort)

    def _apply_thinking_directive(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prepend the configured directive to the system message.

        Applies only when the family expects thinking through the prompt and a
        directive was supplied. With no directive the messages are returned
        untouched, so enabling the style alone changes nothing and cannot quietly
        alter the instructions the agent runs under.
        """
        if not self.thinking_plan.directive_applies:
            return messages
        directive = (self.thinking_directive or "").strip()
        if not directive or not messages:
            return messages
        head, *rest = messages
        if head.get("role") != "system":
            return [{"role": "system", "content": directive}, *messages]
        merged = dict(head)
        # The same merge the LangChain subject applies to its prompt at construction.
        merged["content"] = apply_directive(
            str(head.get("content", "")), self.thinking_plan, self.thinking_directive)
        return [merged, *rest]

    def __call__(self, messages: list[dict[str, Any]], remaining_seconds: float) -> Any:
        try:
            import litellm
            from litellm import completion
        except ImportError as exc:
            raise RuntimeError("litellm is not installed. Run: python -m pip install -r requirements.txt") from exc
        # LiteLLM prints a provider banner straight to stdout on every internal provider
        # lookup that misses, which any proxy-served model id triggers several times per
        # call. It is cosmetic and would otherwise drown the SUT audit trace.
        litellm.suppress_debug_info = True
        model_name = litellm_model_name(self.model, self.api_base)
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "tools": self.tool_schemas(),
            "tool_choice": "auto",
            # LiteLLM validates tool_choice against its own model registry and rejects it for
            # ids it does not know, proxy-served ones included, before any request goes out.
            # Those endpoints do support tool calls, so whitelist the parameter rather than
            # let the whole ReAct loop fail on the first call.
            "allowed_openai_params": ["tool_choice"],
            "max_tokens": self.max_tokens,
            "timeout": max(1.0, min(remaining_seconds, self.max_execution_seconds)),
        }
        if self.api_base is not None:
            kwargs["api_base"] = self.api_base
            kwargs["api_key"] = self.api_key or "EMPTY"
        elif self.api_key is not None:
            kwargs["api_key"] = self.api_key
        # Thinking is requested differently per model family. Only the chat-template
        # route reaches the serving layer; the prompt route is applied to the
        # messages below, because that is the channel those families use.
        plan = self.thinking_plan
        kwargs.update(plan.request_kwargs)
        messages = self._apply_thinking_directive(messages)
        kwargs["messages"] = messages
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        self.trace("summary", "SUT calling model through ANI ReAct loop", {
            "model": self.model,
            "litellm_model": model_name,
            "api_base": self.api_base,
            "max_tokens": self.max_tokens,
            "remaining_budget_seconds": round(remaining_seconds, 3),
            "ani_tools": [item["function"]["name"] for item in self.tool_schemas()],
        })
        self.trace("full", "SUT system prompt", messages[0]["content"])
        self.trace("full", "SUT messages sent to model", messages)
        # Providers count the requested output reservation against the context window.
        # A long, still-valid tool transcript can therefore be rejected even when the
        # next response would be tiny. Keep the configured ceiling on the first call;
        # only a provider-confirmed context overflow may reduce it, and retry the exact
        # same messages so no evidence or CAS precondition is discarded.
        deadline = time.monotonic() + max(1.0, remaining_seconds)
        output_tokens = self.max_tokens
        # How far this call was pushed off its configured reservation. Reported on
        # the message, and on the exception when no message comes back, because a
        # response produced under a 62-token reservation is not the same evidence
        # as one produced under the configured 8000, and the report would otherwise
        # show only the configured number.
        reservation = {"reductions": 0, "min_max_tokens": output_tokens}
        while True:
            kwargs["max_tokens"] = output_tokens
            kwargs["timeout"] = max(
                1.0,
                min(deadline - time.monotonic(), self.max_execution_seconds),
            )
            try:
                response = completion(**kwargs)
            except Exception as exc:
                reduced = _context_retry_tokens(exc, output_tokens)
                # The floor raises the provider's own error rather than a wrapper
                # naming the floor: the loop classifies the episode by that text,
                # and the exception's own fields say how far the call got.
                if (reduced is None or time.monotonic() >= deadline
                        or (self.min_retry_tokens is not None and reduced < self.min_retry_tokens)):
                    _attach_reservation(exc, reservation)
                    raise
                self.trace("summary", "Retrying model call with a smaller output reservation", {
                    "reason": "provider_context_window_exceeded",
                    "previous_max_tokens": output_tokens,
                    "retry_max_tokens": reduced,
                })
                output_tokens = reduced
                reservation["reductions"] += 1
                reservation["min_max_tokens"] = reduced
                continue

            # The loop returns what the judge needs to attribute an episode: the
            # message itself plus what the provider said it spent and which model
            # actually served it. A backend that cannot be dumped to a mapping is
            # handed back untouched rather than guessed at.
            message = response.choices[0].message
            payload = message.model_dump() if hasattr(message, "model_dump") else message
            if not isinstance(payload, dict):
                return message
            usage = getattr(response, "usage", None)
            payload["_ibn_usage"] = {
                "input_tokens": _usage_value(usage, "prompt_tokens"),
                "output_tokens": _usage_value(usage, "completion_tokens"),
                "total_tokens": _usage_value(usage, "total_tokens"),
            }
            payload["_ibn_provider_model"] = getattr(response, "model", None)
            payload["_ibn_reservation"] = dict(reservation)
            return payload


def _attach_reservation(exc: BaseException, reservation: dict[str, int]) -> None:
    """Carry the reduction count out on the exception the loop will record.

    An exception has no payload to hang it on, and a wrapper exception would hide
    the provider's message from the loop's termination classifier.
    """
    try:
        exc._ibn_reservation = dict(reservation)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - an exception type without __dict__ still propagates
        pass


_CONTEXT_LIMIT = re.compile(
    r"maximum context length is\s+(?P<limit>[0-9]+)\s+tokens", re.IGNORECASE)
_PROMPT_TOKENS = re.compile(
    r"prompt contains at least\s+(?P<input>[0-9]+)\s+input tokens", re.IGNORECASE)


def _context_retry_tokens(exc: Exception, requested: int) -> int | None:
    """Return one smaller reservation only for a provider context rejection."""
    text = f"{type(exc).__name__}: {exc}"
    if "ContextWindowExceeded" not in text and "context length" not in text.lower():
        return None
    if requested <= 1:
        return None

    # Halving gives the model useful room without trusting an error's "at least"
    # estimate as an exact tokenizer count. When the provider reports a tighter
    # bound, stay 256 tokens below it as well.
    reduced = max(1, requested // 2)
    limit_match = _CONTEXT_LIMIT.search(text)
    input_match = _PROMPT_TOKENS.search(text)
    if limit_match and input_match:
        available = int(limit_match.group("limit")) - int(input_match.group("input"))
        reduced = min(reduced, max(1, available - 256))
    return reduced if reduced < requested else None


def litellm_model_name(model: str, api_base: str | None) -> str:
    return f"openai/{model}" if api_base and not model.startswith("openai/") else model


def _usage_value(usage: Any, name: str) -> int | None:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return int(value) if value is not None else None
