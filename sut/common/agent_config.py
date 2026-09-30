"""Model/endpoint configuration shared by ANI SUTs.

The defaults live here on purpose. Two SUTs that silently disagree on
`max_tokens` or on the execution budget are not comparable, and the drift would
show up as an agent-quality difference in the metrics. Each SUT subclasses this
and picks its own env prefix so both can run in one shell without colliding.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelAgentConfig:
    model: str = "openai/gpt-4o-mini"
    api_base: str | None = None
    api_key: str | None = None
    enable_thinking: bool | None = None
    # How the model family expects thinking to be requested. "chat_template" sends
    # chat_template_kwargs, which is what vLLM exposes for Qwen3; "system_prompt"
    # prepends a directive instead, which is how families that ship their own
    # reasoning instructions expect it. The directive text is deliberately not
    # shipped here: it belongs to the model and is an experiment variable, so it
    # is supplied per run and recorded in the result.
    #: How thinking is requested. "auto" reads it from the model family (see
    #: sut/common/thinking.py); an explicit style overrides that reading.
    thinking_style: str = "auto"
    thinking_directive: str | None = None
    #: Effort level for models whose reasoning is asked for as a level.
    thinking_effort: str | None = None
    temperature: float | None = None
    max_tokens: int = 400
    dry_run: bool = False
    self_execute: bool = True
    max_execution_seconds: float = 300.0
    debug_trace: str = "off"
    #: Which system prompt to run with; "default" is the one the subject ships.
    #: Any other name is a file under the subject's prompts/ directory.
    prompt_variant: str = "default"
    #: How many ANI operations the subject may make in one episode, or None for no
    #: cap. A call past the cap is refused by name, never dispatched.
    ani_call_limit: int | None = None
    #: The smallest output reservation a provider context rejection may retry
    #: with. None keeps the backend halving down to one token, which is what
    #: every result before this field ran under; a floor makes the call fail on
    #: the provider's own error instead of finishing on a reservation too small
    #: to hold a tool call.
    min_retry_tokens: int | None = None
    #: Transcript bounds, all off by default so a result is what the loop always
    #: produced unless a run file sets them. tool_result_chars cuts one tool result
    #: to that many characters and spills the whole to artifact_directory when one
    #: is configured; context_budget_chars is the most the serialized transcript may
    #: be when the model is called, the results of reads a later identical read
    #: superseded being elided first and the episode ending by name when that is not
    #: enough; max_consecutive_rejections ends an episode after that many completion
    #: claims in a row the gate refused. See sut/common/self_execute.py.
    tool_result_chars: int | None = None
    context_budget_chars: int | None = None
    artifact_directory: str | None = None
    max_consecutive_rejections: int | None = None
    # Where per-episode trace files are written. Empty keeps the trace in the
    # server log only, which is the previous behaviour.
    trace_directory: str | None = None

    @classmethod
    def env_fields(cls, prefix: str) -> dict[str, Any]:
        """Read the shared fields from `{prefix}_*`, with repo-wide fallbacks.

        LLM_API_* are the repository-wide endpoint credentials declared in .env and
        name no provider: any OpenAI-compatible endpoint fits. The agent-specific
        prefix still wins so a single run can target another endpoint without
        touching .env. The LITELLM_* reads are kept so an older .env still resolves.
        """
        temperature = os.getenv(f"{prefix}_TEMPERATURE")
        return {
            "model": os.getenv(f"{prefix}_MODEL", cls.model),
            "api_base": (
                os.getenv(f"{prefix}_API_BASE")
                or os.getenv("LLM_API_BASE")
                or os.getenv("LITELLM_BASE_URL")
                or None
            ),
            "api_key": (
                os.getenv(f"{prefix}_API_KEY")
                or os.getenv("LLM_API_KEY")
                or os.getenv("LITELLM_API_KEY")
                or None
            ),
            "enable_thinking": optional_bool(os.getenv(f"{prefix}_ENABLE_THINKING")),
            "thinking_style": os.getenv(f"{prefix}_THINKING_STYLE")
            or os.getenv("IBN_SUT_THINKING_STYLE")
            or cls.thinking_style,
            "thinking_directive": os.getenv(f"{prefix}_THINKING_DIRECTIVE")
            or os.getenv("IBN_SUT_THINKING_DIRECTIVE")
            or cls.thinking_directive,
            "thinking_effort": os.getenv(f"{prefix}_THINKING_EFFORT")
            or os.getenv("IBN_SUT_THINKING_EFFORT")
            or cls.thinking_effort,
            "temperature": float(temperature) if temperature else None,
            "max_tokens": int(os.getenv(f"{prefix}_MAX_TOKENS", cls.max_tokens)),
            "dry_run": os.getenv(f"{prefix}_DRY_RUN", "").lower() in {"1", "true", "yes"},
            "self_execute": os.getenv(f"{prefix}_SELF_EXECUTE", "true").lower() not in {"0", "false", "no"},
            "max_execution_seconds": float(os.getenv(f"{prefix}_MAX_EXECUTION_SECONDS", cls.max_execution_seconds)),
            "debug_trace": os.getenv(f"{prefix}_DEBUG_TRACE", cls.debug_trace),
            "prompt_variant": os.getenv(f"{prefix}_PROMPT_VARIANT")
            or os.getenv("IBN_SUT_PROMPT_VARIANT")
            or cls.prompt_variant,
            "ani_call_limit": optional_int(
                os.getenv(f"{prefix}_ANI_CALL_LIMIT") or os.getenv("IBN_SUT_ANI_CALL_LIMIT")),
            "min_retry_tokens": optional_int(
                os.getenv(f"{prefix}_MIN_RETRY_TOKENS") or os.getenv("IBN_SUT_MIN_RETRY_TOKENS")),
            "tool_result_chars": optional_int(
                os.getenv(f"{prefix}_TOOL_RESULT_CHARS") or os.getenv("IBN_SUT_TOOL_RESULT_CHARS")),
            "context_budget_chars": optional_int(
                os.getenv(f"{prefix}_CONTEXT_BUDGET_CHARS")
                or os.getenv("IBN_SUT_CONTEXT_BUDGET_CHARS")),
            "artifact_directory": (
                os.getenv(f"{prefix}_ARTIFACT_DIRECTORY")
                or os.getenv("IBN_SUT_ARTIFACT_DIRECTORY")
                or cls.artifact_directory
            ),
            "max_consecutive_rejections": optional_int(
                os.getenv(f"{prefix}_MAX_CONSECUTIVE_REJECTIONS")
                or os.getenv("IBN_SUT_MAX_CONSECUTIVE_REJECTIONS")),
            "trace_directory": (
                os.getenv(f"{prefix}_TRACE_DIRECTORY")
                or os.getenv("IBN_SUT_TRACE_DIRECTORY")
                or cls.trace_directory
            ),
        }


def optional_int(value: str | None) -> int | None:
    """An interaction cap read from the environment, or None when it is not set.

    A cap of zero is a real setting (no calls allowed), so emptiness is the only
    thing that means "not set".
    """
    if value is None or not value.strip():
        return None
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"interaction limit must not be negative: {value!r}")
    return parsed


def optional_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def resolve_endpoint(
    arg_api_base: str | None,
    arg_api_key: str | None,
    env_config: ModelAgentConfig,
) -> tuple[str | None, str | None]:
    """Merge CLI endpoint flags over the environment, as a pair.

    An endpoint is a (base, key) pair. `--api-base ""` explicitly discards the one
    from the environment so the provider is reached through its own defaults, and
    must not leave the paired key behind to be sent to that different provider.
    """
    if arg_api_base is not None:
        return (arg_api_base or None), (arg_api_key or None)
    return env_config.api_base, (arg_api_key or env_config.api_key)
