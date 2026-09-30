"""Stable LangChain `create_agent` baseline over the independent ANI API."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_litellm import ChatLiteLLM
import litellm
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field, ValidationError

from benchmarks.platforms.containerlab import ANI_VERSION, ContainerLabANI, ContainerLabEnv
from sut.common import (
    ModelAgentConfig,
    install_ani_budget,
    reset_ani_budget,
    resolve_system_prompt,
    thinking_record,
)
from sut.common.ani_report import public_task_for_model
from sut.common.self_execute import ani_budget_state, exception_cause
from sut.common.serving_info import probe_serving
from sut.common.thinking import apply_directive, resolve_thinking
from sut.common.tool_schema_transport import TOOL_ARGUMENT_TRANSPORT, declare_nullable_types
from sut.common.trace import Tracer
from .ani_tools import LangChainANIAdapter, RepeatedANIRequestError


class StructuredConclusion(BaseModel):
    """Model-produced terminal decision; execution evidence is derived from ANI."""

    status: Literal["completed", "failed"]
    summary: str = Field(description="Concise outcome based only on observed ANI evidence")


class NoConclusion(ValueError):
    """The graph ended without the structured conclusion the prompt requires.

    A model that stops in prose leaves `structured_response` empty (langchain's
    factory returns None rather than raising), which is a different end from a call
    that failed and must not be filed as one.
    """


def termination_cause(exc: BaseException, deadline: float) -> str:
    """Name how an episode ended, in the vocabulary the shared loop uses.

    The three ends this subject has and the loop does not are named for what they
    are: its own stop rule on repeated failed requests, a graph that ended without a
    conclusion, and LangGraph's step cap. Filing any of them under model_error or
    budget would record a cause that did not hold. Everything else is read the way
    the loop reads it: from the clock first, the provider's text second.
    """
    if isinstance(exc, RepeatedANIRequestError):
        return "repeated_failed_requests"
    if isinstance(exc, NoConclusion):
        return "no_conclusion"
    if isinstance(exc, GraphRecursionError):
        return "recursion_limit"
    return exception_cause(exc, deadline, deadline - time.monotonic())


def _generation_records(response: Any) -> list[dict[str, Any]]:
    """Flatten an LLMResult into text and tool calls, tolerating any shape."""
    records: list[dict[str, Any]] = []
    for batch in getattr(response, "generations", None) or []:
        for generation in batch or []:
            message = getattr(generation, "message", None)
            record: dict[str, Any] = {"text": getattr(generation, "text", None)}
            if message is not None:
                record["content"] = getattr(message, "content", None)
                calls = getattr(message, "tool_calls", None)
                if calls:
                    record["tool_calls"] = calls
                extra = getattr(message, "additional_kwargs", None)
                # Where a provider returns reasoning separately it lands here.
                if isinstance(extra, dict) and extra.get("reasoning_content"):
                    record["reasoning_content"] = extra["reasoning_content"]
            records.append(record)
    return records


def _conclusion_in_text(state: Any) -> dict[str, Any] | None:
    """The conclusion the prompt asks for, when the model wrote it as text.

    The prompt ends with "return only JSON: {status, summary}". A model that does
    exactly that answers in its message content rather than through the structured
    output tool, and langchain then leaves `structured_response` empty. That answer
    is the conclusion the contract asks for, so it is read as one: the last message
    must be the model's and its content must validate as StructuredConclusion (a
    ```json fence or surrounding prose around the object is tolerated). Anything
    else is still no conclusion.
    """
    messages = state.get("messages") if isinstance(state, dict) else None
    if not messages:
        return None
    last = messages[-1]
    if isinstance(last, dict):
        if last.get("role") not in (None, "assistant", "ai"):
            return None
        content = last.get("content")
    else:
        if getattr(last, "type", None) not in (None, "ai"):
            return None
        content = getattr(last, "content", None)
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    if not isinstance(content, str):
        return None
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return StructuredConclusion.model_validate(payload).model_dump()
    except ValidationError:
        return None


def _model_turn(number: int, started: float | None, generations: list[dict[str, Any]]) -> dict[str, Any]:
    """One model turn as the shared loop records it (`self_execute._turn`): measured,
    never its text. The words are in the trace; the report carries how long the turn
    took, what it asked for and how much it wrote."""
    names = [str(call.get("name") or "") for record in generations
             for call in (record.get("tool_calls") or []) if isinstance(call, dict)]
    text = "".join(str(record.get("text") or "") for record in generations)
    reasoning = sum(len(str(record.get("reasoning_content") or "")) for record in generations)
    return {
        "turn": number,
        "seconds": round(time.monotonic() - started, 3) if started is not None else None,
        "kind": "tool_calls" if names else "text",
        "content_chars": len(text),
        "reasoning_chars": reasoning,
        "tool_calls": names,
        "operations": [],
        "error": None,
    }


class RunUsageCallback(UsageMetadataCallbackHandler):
    """Keep usage telemetry even when LangGraph exits by exception, and measure every
    model turn (`model_turns`, `model_seconds`) the way the shared loop does, so a
    report can split this subject's time into thinking and tools too."""

    def __init__(self, trace: Tracer | None = None, operations: Callable[[], int] | None = None) -> None:
        super().__init__()
        self.llm_calls = 0
        self.trace = trace
        self.model_turns: list[dict[str, Any]] = []
        # How many ANI operations exist right now; the calls a turn asked for run
        # between its end and the next turn's start, which is how they are attributed.
        self._count_operations = operations or (lambda: 0)
        self._turn_started: float | None = None
        self._operations_at_end = 0

    def _attribute_operations(self) -> None:
        """Link the operations made since the last turn ended to that turn, as 1-based
        positions in `ani_operations`. A tool call refused before the ANI leaves no
        operation, so a turn can name more calls than it has operations."""
        count = self._count_operations()
        if self.model_turns and not self.model_turns[-1]["operations"]:
            self.model_turns[-1]["operations"] = list(range(self._operations_at_end + 1, count + 1))
        self._operations_at_end = count

    def on_llm_start(self, serialized: Any, prompts: list[str], **kwargs: Any) -> None:
        # The base handler only implements on_llm_end; its on_llm_start is the
        # inherited no-op and requires a keyword-only run_id, so forwarding here
        # would couple this to a signature we gain nothing from.
        self._attribute_operations()
        self._turn_started = time.monotonic()
        if self.trace is not None:
            self.trace("full", "SUT prompt to model", {"turn": self.llm_calls + 1, "prompts": prompts})

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.llm_calls += 1
        generations = _generation_records(response)
        self.model_turns.append(_model_turn(self.llm_calls, self._turn_started, generations))
        self._turn_started = None
        self._operations_at_end = self._count_operations()
        if self.trace is not None:
            # The generations carry the prose and the tool calls; this is the half
            # of the interaction the A2A report is not allowed to return. Pull the
            # fields out rather than dumping the object, whose repr says nothing.
            self.trace("full", "SUT model response",
                       {"turn": self.llm_calls, "generations": generations})
        super().on_llm_end(response, **kwargs)

    def snapshot(self) -> dict[str, Any]:
        self._attribute_operations()
        usage = {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
        provider_model: str | None = None
        for model_name, values in self.usage_metadata.items():
            provider_model = str(model_name)
            for key in usage:
                value = values.get(key)
                if value is not None:
                    usage[key] = (usage[key] or 0) + int(value)
        return {
            "llm_calls": self.llm_calls,
            "token_usage": usage,
            "provider_model": provider_model,
            "model_turns": [dict(turn) for turn in self.model_turns],
            "model_seconds": round(sum(turn["seconds"] or 0.0 for turn in self.model_turns), 3),
        }

@dataclass(frozen=True)
class LangChainAgentConfig(ModelAgentConfig):
    max_context_chars: int = 12_000
    artifact_directory: str = "reports/manual/results/artifacts"
    recursion_limit: int = 1_000
    #: What the model is bound with when langchain would force a tool call. Its
    #: ToolStrategy binds every turn with tool_choice="any" (a tool or the conclusion
    #: tool, nothing else); "auto" lets the server's own tool parser read the model's
    #: call, see `TOOL_CHOICE_NOTE`. "required" keeps langchain's forcing.
    tool_choice: str = "auto"
    #: Extra fields for the request body, as a JSON object: what an OpenAI-compatible
    #: gateway reads beyond the standard fields, such as OpenRouter's provider pin
    #: ({"provider": {"order": ["deepinfra"], "quantizations": ["bf16"]}}). Merged
    #: into `extra_body` next to the thinking request and recorded with the result.
    request_extra_body: str | None = None
    #: The prefix `from_env` reads. A subject built on this config names its own, so
    #: two subjects started from one shell never read each other's settings.
    ENV_PREFIX: ClassVar[str] = "LANGCHAIN_AGENT"

    @classmethod
    def from_env(cls) -> "LangChainAgentConfig":
        import os

        prefix = cls.ENV_PREFIX
        fields = cls.env_fields(prefix)
        fields.update({
            "max_context_chars": int(os.getenv(f"{prefix}_MAX_CONTEXT_CHARS", cls.max_context_chars)),
            "artifact_directory": os.getenv(f"{prefix}_ARTIFACT_DIRECTORY", cls.artifact_directory),
            "recursion_limit": int(os.getenv(f"{prefix}_RECURSION_LIMIT", cls.recursion_limit)),
            "tool_choice": os.getenv(f"{prefix}_TOOL_CHOICE", cls.tool_choice),
            "request_extra_body": os.getenv(f"{prefix}_REQUEST_EXTRA_BODY") or cls.request_extra_body,
        })
        return cls(**fields)


def parse_extra_body(text: str | None) -> dict[str, Any]:
    """The JSON object `request_extra_body` holds, or {} when unset; refused by name otherwise."""
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"request_extra_body is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("request_extra_body must be a JSON object")
    return value


TOOL_CHOICE_NOTE = (
    "langchain's ToolStrategy binds the tools with tool_choice='any', so every turn must "
    "end in a tool call. vLLM implements a forced choice as a JSON grammar that engages "
    "after the reasoning block; a model that answers a tool result without closing its "
    "<think> block (Qwen3.5-9B does, most turns) never reaches the grammar, and the reply "
    "is finish_reason=tool_calls with an empty tool_calls list and no content, which ends "
    "the graph on the second turn. Bound with 'auto', the server's tool parser reads the "
    "model's own call, and a turn without one ends the graph as a missing conclusion, "
    "which the record names.")


class ForcedToolChoiceRelaxed(ChatLiteLLM):
    """ChatLiteLLM that binds the tools the way the served parser needs them.

    A forced tool choice ("any", "required") is bound as the configured one instead;
    a named tool, "none" or "auto" pass through (`TOOL_CHOICE_NOTE` says why). And
    every optional container parameter is offered with an explicit type rather than
    pydantic's anyOf, so an XML tool-call parser hands a list over as JSON and not
    as a quoted string (sut.common.tool_schema_transport).
    """

    tool_choice_override: str | None = None

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        if self.tool_choice_override is not None and tool_choice in ("any", "required"):
            tool_choice = self.tool_choice_override
        tools = [declare_nullable_types(convert_to_openai_tool(tool)) for tool in tools]
        # LiteLLM validates tool_choice against its own model registry and rejects it for
        # ids it does not know, proxy-served ones included, before any request goes out.
        # Those endpoints do support tool calls, so whitelist the parameter where it is
        # sent rather than let the first turn fail (as sut/common/litellm_backend.py does).
        kwargs.setdefault("allowed_openai_params", ["tool_choice"])
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)


class LangChainRepairAgent:
    """One model/tool loop built with LangChain's stable agent primitive."""

    identity = "langchain_agent"
    version = "1.0.0"
    #: Where this subject's `prompts/` directory is. A subject built on this one
    #: ships its own prompts and points this at its own package.
    subject_dir = Path(__file__).resolve().parent

    def __init__(
        self,
        config: LangChainAgentConfig | None = None,
        ani: ContainerLabANI | None = None,
        *,
        model: Any | None = None,
        agent_factory: Any = create_agent,
    ) -> None:
        self.config = config or LangChainAgentConfig.from_env()
        self.ani = ani or ContainerLabANI(ContainerLabEnv())
        self.model = model
        self.agent_factory = agent_factory
        # Resolved once, here: a family this subject cannot ask to think stops the
        # server at startup by name, and the request the model is built with and the
        # record thinking_record() writes come from this one plan, so the two cannot
        # disagree. Before this the record said chat_template while nothing was sent.
        self._thinking = resolve_thinking(
            self.config.model, self.config.enable_thinking,
            self.config.thinking_style, self.config.thinking_effort)
        if model is not None and self.config.enable_thinking is not None:
            # A supplied model is called as it is; nothing here can attach the request
            # to it, and the record would then claim a condition that was never sent.
            raise ValueError("a supplied model cannot run under an enable_thinking condition")
        # Ask the endpoint what it is running on once, at construction: the answer
        # is a property of the server, not of the episode, and a per-episode probe
        # would spend the budget on it.
        self._serving = probe_serving(self.config.api_base) or {}
        self._trace = Tracer(self.config.debug_trace, self.config.trace_directory)
        # Resolved at construction so a run that asked for a prompt this subject does
        # not ship stops the server at startup, not halfway through a campaign.
        # Every prompt this subject has, the shipped one included, is a file under
        # prompts/, so there is no prompt text in this module to pass as a fallback.
        # The cap belongs on the ANI, which every tool call here passes through.
        install_ani_budget(self.ani, self.config.ani_call_limit)
        self.system_prompt = apply_directive(
            resolve_system_prompt(self.subject_dir, self.config.prompt_variant),
            self._thinking, self.config.thinking_directive)

    def _model_kwargs(self) -> dict[str, Any]:
        """The thinking request plus the run's extra body, the latter merged into
        `extra_body` so a provider pin and a chat-template flag travel together."""
        kwargs = dict(self._thinking.request_kwargs)
        extra = parse_extra_body(self.config.request_extra_body)
        if extra:
            kwargs["extra_body"] = {**dict(kwargs.get("extra_body") or {}), **extra}
        return kwargs

    def _model(self, *, request_timeout: float | None = None) -> Any:
        if self.model is not None:
            return self.model
        # The banner litellm_backend silences, for the same reason: LiteLLM prints
        # "Provider List" each time it looks a proxy-served id up in its own catalog
        # and misses -- seven times per call for fr-gpt-5.4. The lookup still runs and
        # the request is built the same; only the print goes, which drowned the log.
        litellm.suppress_debug_info = True
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            # ChatLiteLLM forwards its declared fields and `model_kwargs` to
            # litellm.completion; an unknown keyword such as extra_body= is dropped by
            # pydantic without a word. So the thinking request travels inside
            # model_kwargs, which carries a chat-template flag and an effort level alike.
            "model_kwargs": self._model_kwargs(),
        }
        if request_timeout is not None:
            kwargs["request_timeout"] = request_timeout
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if self.config.tool_choice not in ("any", "required"):
            kwargs["tool_choice_override"] = self.config.tool_choice
        return ForcedToolChoiceRelaxed(**kwargs)

    def invoke(self, task_json: str) -> str:
        started = time.monotonic()
        task = json.loads(task_json)
        if not isinstance(task, dict):
            raise ValueError("A2A task must be a JSON object")
        # One episode, one count: the ANI is this process's and outlives the episode.
        reset_ani_budget(self.ani)
        budget = self._execution_budget(task)
        if self.config.dry_run or not self.config.self_execute:
            return json.dumps(self._report(
                None, None, budget, time.monotonic() - started,
                "dry-run does not execute ANI operations",
                termination={"cause": "dry_run",
                             "detail": "dry-run does not execute ANI operations", "turn": 0}))
        deadline = started + budget
        adapter = LangChainANIAdapter(
            self.ani,
            task=task,
            artifact_directory=Path(self.config.artifact_directory),
            max_context_chars=self.config.max_context_chars,
            deadline=deadline,
        )
        self._trace.begin_episode(
            f"{self.config.model}-{task.get('scenario_id') or 'episode'}"
        )
        self._trace("summary", "SUT received public A2A task", {"scenario_id": task.get("scenario_id"), "intent": task.get("intent")})
        usage_callback = RunUsageCallback(self._trace, operations=lambda: len(adapter.trace.operations))
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("execution budget exhausted before agent creation")
            graph = self.agent_factory(
                model=self._model(request_timeout=remaining),
                tools=self._tools(adapter),
                system_prompt=self.system_prompt,
                response_format=ToolStrategy(StructuredConclusion),
            )
            # Built before the clock is read again, so whatever building it costs
            # is spent inside the budget the graph is then given.
            message = self._task_message(task, deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("execution budget exhausted before graph invocation")
            state = asyncio.run(asyncio.wait_for(
                graph.ainvoke(
                    {"messages": [{"role": "user", "content": message}]},
                    config={
                        "recursion_limit": self.config.recursion_limit,
                        "callbacks": [usage_callback],
                    },
                ),
                timeout=remaining,
            ))
            conclusion = state.get("structured_response") if isinstance(state, dict) else None
            if hasattr(conclusion, "model_dump"):
                conclusion = conclusion.model_dump()
            conclusion_source = "structured_response" if isinstance(conclusion, dict) else None
            if not isinstance(conclusion, dict):
                # The prompt asks for the conclusion as JSON text; a model that obeys
                # it never calls the structured-output tool (_conclusion_in_text).
                conclusion = _conclusion_in_text(state)
                conclusion_source = "final_message" if isinstance(conclusion, dict) else None
            if not isinstance(conclusion, dict):
                raise NoConclusion("LangChain agent returned no structured_response")
            report = self._report(adapter, conclusion, budget, time.monotonic() - started, None,
                                  _best_telemetry(state, usage_callback), conclusion_source=conclusion_source)
        except TimeoutError:
            # ExecutionBudgetExceeded from a tool, the two pre-checks and wait_for all
            # land here: the clock ended the episode.
            report = self._report(
                adapter, None, budget, time.monotonic() - started, "execution budget exceeded",
                usage_callback.snapshot(),
                termination={"cause": "budget", "detail": "execution budget exceeded",
                             "turn": usage_callback.llm_calls})
        except Exception as exc:
            report = self._report(
                adapter, None, budget, time.monotonic() - started, str(exc),
                usage_callback.snapshot(),
                termination={"cause": termination_cause(exc, deadline),
                             "detail": str(exc)[:200], "turn": usage_callback.llm_calls})
        return json.dumps(report)

    def _tools(self, adapter: LangChainANIAdapter) -> list[Any]:
        """The tools one episode is given: the ANI's, and nothing else here."""
        return adapter.tools()

    def _task_message(self, task: dict[str, Any], deadline: float) -> str:
        """The first user message: the public task, and nothing else here."""
        return json.dumps(public_task_for_model(task), sort_keys=True)

    def _report(self, adapter: LangChainANIAdapter | None, conclusion: dict[str, Any] | None, budget: float, elapsed: float, error: str | None, telemetry: dict[str, Any] | None = None, *, termination: dict[str, Any] | None = None, conclusion_source: str | None = None) -> dict[str, Any]:
        trace = adapter.trace if adapter else None
        telemetry = telemetry or {
            "llm_calls": 0,
            "token_usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
            "provider_model": None,
        }
        validation = trace.final_validation if trace else None
        verified = bool(validation and validation.get("passed"))
        requested_status = str((conclusion or {}).get("status") or "failed")
        status = "completed" if requested_status == "completed" and verified else "failed"
        if requested_status == "completed" and not verified and error is None:
            error = "SUT claimed completion without a passing public validation"
        if conclusion is not None and requested_status == "failed" and error is None:
            # The loop files a self-declared failure under `error` (self_execute.py);
            # a record whose subject gave up but whose error reads null says nothing.
            error = str(conclusion.get("summary") or "") or None
        if conclusion is not None and termination is None:
            # The two ends a conclusion can have, named as the shared loop names them:
            # the gate refusing a completion claim, or the subject's own conclusion.
            turn = int(telemetry.get("llm_calls") or 0)
            if requested_status == "completed" and not verified:
                termination = {"cause": "completion_rejected", "detail": error, "turn": turn}
            else:
                termination = {"cause": "own_conclusion", "detail": requested_status, "turn": turn}
        device_changes = list(trace.device_changes if trace else [])
        if verified:
            for action in device_changes:
                if (action.get("result") or {}).get("ok") is True:
                    action["verified_after_action"] = True
                    action["objective_verification"] = validation
        return {
            "sut_identity": self.identity,
            "sut_version": self.version,
            "model_reported_by_sut": self.config.model,
            "provider_reported_model": telemetry["provider_model"],
            "mode": "self_execute",
            "status": status,
            "verified": verified,
            "device_changes": device_changes,
            "ani_operations": list(trace.operations if trace else []),
            "execution": {
                "ani_version": ANI_VERSION,
                "budget_seconds": budget,
                "elapsed_seconds": elapsed,
                "tool_call_count": len(trace.operations if trace else []),
                "reads": sum(item["category"] == "read" for item in (trace.operations if trace else [])),
                "mutations": sum(item["category"] == "mutation" for item in (trace.operations if trace else [])),
                "validations": sum(item["category"] == "validation" for item in (trace.operations if trace else [])),
                "successful_mutations": sum(item["category"] == "mutation" and item.get("ok") is True for item in (trace.operations if trace else [])),
                "failed_operations": trace.failed_operations if trace else 0,
                "unsafe_operations": trace.unsafe_operations if trace else 0,
                # The same two keys the shared loop writes, so a reader of the record
                # (summarize_campaign, evaluation_parameters) reads this subject's end
                # from the name it gives rather than guessing from status and error.
                "termination": termination,
                # How the conclusion reached the harness: the structured-output tool,
                # or the JSON text the prompt asks for (None when there was none).
                "conclusion_source": conclusion_source,
                "interaction_limits": {
                    "ani": ani_budget_state(self.ani, self.config.ani_call_limit),
                },
                "configured_model": self.config.model,
                "llm_calls": telemetry["llm_calls"],
                "model_parameters": {
                    "model": self.config.model,
                    "max_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                    "max_context_chars": self.config.max_context_chars,
                    "max_execution_seconds": self.config.max_execution_seconds,
                    "recursion_limit": self.config.recursion_limit,
                    # How the tools were bound: "auto" relaxes langchain's forced
                    # call (TOOL_CHOICE_NOTE), "required" keeps it.
                    "tool_choice": self.config.tool_choice,
                    # How tool arguments travelled: typed optional containers in the
                    # bound schema and JSON strings decoded (sut.common.tool_schema_transport).
                    "tool_argument_transport": TOOL_ARGUMENT_TRANSPORT,
                    # Which prompt was in force, so a result is readable without
                    # knowing what the subject shipped that day.
                    "prompt_variant": self.config.prompt_variant,
                    "ani_call_limit": self.config.ani_call_limit,
                    # The floor under the context-overflow retry, so a result can say whether
                    # a small reservation was allowed to finish the call.
                    "min_retry_tokens": self.config.min_retry_tokens,
                    # Recorded for the shared parameter contract; this subject bounds
                    # its context through max_context_chars instead.
                    "tool_result_chars": self.config.tool_result_chars,
                    "context_budget_chars": self.config.context_budget_chars,
                    "max_consecutive_rejections": self.config.max_consecutive_rejections,
                    # Extra request fields the run sent (a gateway's provider pin), so a
                    # result says which provider served it, not only which model.
                    "request_extra_body": parse_extra_body(self.config.request_extra_body) or None,
                    # How thinking was requested and which mechanism the model
                    # family resolved to, so a result can be read without
                    # inferring it from the model name.
                    **thinking_record(self.config),
                },
                "serving": dict(self._serving),
                "trace_ref": str(self._trace.path) if self._trace.path else None,
                "token_usage": telemetry["token_usage"],
                "provider_model": telemetry["provider_model"],
                # Measured per turn by the callback (2026-09-16); the state fallback has
                # no turns, so an episode it describes reads as unmeasured, not as empty.
                "model_turns": list(telemetry.get("model_turns") or []),
                "model_seconds": telemetry.get("model_seconds"),
            },
            "final_observation": validation,
            # The loop's shape for the subject's own conclusion, which the summary reads
            # to tell a self-concluded episode from one the clock or an error ended.
            # `summary` stays: earlier records of this subject carry it.
            "final_response": (
                {"status": requested_status, "summary": str(conclusion.get("summary") or "")}
                if conclusion is not None else None),
            "summary": (conclusion or {}).get("summary"),
            "error": error,
        }

    def _execution_budget(self, task: dict[str, Any]) -> float:
        supplied = (task.get("execution_budget") or {}).get("wall_clock_seconds")
        try:
            value = float(supplied)
        except (TypeError, ValueError):
            value = self.config.max_execution_seconds
        if value <= 0:
            value = self.config.max_execution_seconds
        return min(value, self.config.max_execution_seconds)


def _state_telemetry(state: Any) -> dict[str, Any]:
    messages = state.get("messages", []) if isinstance(state, dict) else []

    llm_calls = 0
    provider_model: str | None = None
    token_usage: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    for message in messages:
        message_type = message.get("role") if isinstance(message, dict) else getattr(message, "type", None)
        usage = message.get("usage_metadata") if isinstance(message, dict) else getattr(message, "usage_metadata", None)
        metadata = message.get("response_metadata", {}) if isinstance(message, dict) else getattr(message, "response_metadata", {})
        if message_type in {"ai", "assistant"} or usage:
            llm_calls += 1
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if value is not None:
                    token_usage[key] = (token_usage[key] or 0) + int(value)
        if isinstance(metadata, dict):
            value = metadata.get("model_name") or metadata.get("model")
            if value not in (None, ""):
                provider_model = str(value)
    return {"llm_calls": llm_calls, "token_usage": token_usage, "provider_model": provider_model}


def _best_telemetry(state: Any, callback: RunUsageCallback) -> dict[str, Any]:
    captured = callback.snapshot()
    if captured["llm_calls"]:
        return captured
    return _state_telemetry(state)
