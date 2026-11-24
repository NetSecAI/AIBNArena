"""Stable LangChain `create_agent` baseline over the independent ANI API."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, after_model, before_model
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage
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
from sut.common.ani_report import SUBMISSION_OPERATION, public_task_for_model
from sut.common.repeated_requests import RepeatedRequests
from sut.common.messages import (
    REASONING_KEY,
    REASONING_TURNS_SENT_BACK,
    keep_recent_reasoning,
    no_action_nudge,
    unparsed_tool_call,
)
from sut.common.self_execute import ani_budget_state, exception_cause
from sut.common.agent_config import request_max_tokens
from sut.common.serving_info import probe_serving
from sut.common.thinking import apply_directive, resolve_thinking
from sut.common.tool_schema_transport import TOOL_ARGUMENT_TRANSPORT, declare_nullable_types
from sut.common.trace import Tracer
from .ani_tools import LangChainANIAdapter


class StructuredConclusion(BaseModel):
    """The JSON conclusion a model may write as text; execution evidence is derived from ANI.

    The other end is the `submit_result` tool, whose arguments the ANI checks itself.
    """

    status: Literal["completed", "failed"]
    summary: str = Field(description="Concise outcome based only on observed ANI evidence")


class NoConclusion(ValueError):
    """The graph ended without a conclusion: no submit_result, no JSON conclusion.

    A model that stops in prose ends the graph without either, which is a different
    end from a call that failed and must not be filed as one.
    """


def termination_cause(exc: BaseException, deadline: float) -> str:
    """Name how an episode ended, in the vocabulary the shared loop uses.

    The two ends this subject has and the loop does not are named for what they are:
    a graph that ended without a conclusion, and LangGraph's step cap. Filing either
    under model_error or budget would record a cause that did not hold. Everything
    else is read the way the loop reads it: from the clock first, the provider's text
    second. Until 2026-10-30 a third, `repeated_failed_requests`, ended the episode on
    the third identical failed request; the request is now answered with a note
    (`sut.common.repeated_requests`), as the shared loop's are, and nothing ends.
    """
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
            # ChatLiteLLM puts the provider's finish_reason in generation_info; other
            # chat models put it in the message's response_metadata.
            for source in (getattr(generation, "generation_info", None),
                           getattr(message, "response_metadata", None)):
                if isinstance(source, dict) and source.get("finish_reason"):
                    record["finish_reason"] = str(source["finish_reason"])
                    break
            records.append(record)
    return records


def _conclusion_in_text(state: Any) -> dict[str, Any] | None:
    """The conclusion the prompt asks for, when the model wrote it as text.

    The prompt offers two ends: the submit_result tool, or "return only this JSON:
    {status, summary}". A model that takes the second answers in its message content,
    and that answer is the conclusion the contract asks for, so it is read as one: the last message
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
    finish_reason = next((record["finish_reason"] for record in generations
                          if record.get("finish_reason")), None)
    return {
        "turn": number,
        "seconds": round(time.monotonic() - started, 3) if started is not None else None,
        "kind": "tool_calls" if names else "text",
        "content_chars": len(text),
        "reasoning_chars": reasoning,
        "finish_reason": finish_reason,
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

    def close_unanswered_turn(self, error: str) -> None:
        """Record the model call the episode ended in, as the shared loop records it: a
        call made (`llm_calls`), its seconds, and `kind` "error" with why no answer came.

        `asyncio.wait_for` cancels that call at the deadline, and this callback files a turn
        only when a call answers, so until 2026-11-15 the record lost it: in main03 up to
        490 s of a budget end were in no turn of this subject, where the shared loop
        files the same call (`self_execute._turn`). A call that answered leaves no turn
        open, and then nothing happens.
        """
        if self._turn_started is None:
            return
        self._attribute_operations()
        self.llm_calls += 1
        turn = _model_turn(self.llm_calls, self._turn_started, [])
        turn.update(kind="error", error=error)
        self.model_turns.append(turn)
        self._turn_started = None
        self._operations_at_end = self._count_operations()

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
    #: LangGraph steps, four per turn with this subject's two middlewares: 1 000 was 250
    #: turns, fewer than a 3 600 s episode holds at 6 to 9 s a turn (one E20
    #: wrong_routing_table.m3 episode took 379), so it would end an episode the budget had
    #: not. A runaway guard only: the budget bounds the episode, as it does the shared loop's.
    recursion_limit: int = 10_000
    #: What the tools are bound with. "auto" lets the server's own tool parser read the
    #: model's call, see `TOOL_CHOICE_NOTE`; "required" forces a tool call every turn,
    #: and the only end left is then submit_result.
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
    "A forced tool choice ('any', 'required') means every turn must end in a tool call. "
    "vLLM implements it as a JSON grammar that engages after the reasoning block; a model "
    "that answers a tool result without closing its <think> block (Qwen3.5-9B does, most "
    "turns) never reaches the grammar, and the reply is finish_reason=tool_calls with an "
    "empty tool_calls list and no content, which ends the graph on the second turn. Bound "
    "with 'auto', the server's tool parser reads the model's own call, and a turn without "
    "one ends the graph on its text: a JSON conclusion, or a missing conclusion the record "
    "names. Until 2026-10-07 langchain's ToolStrategy forced 'any' on every turn; "
    "submit_result has replaced it.")


class ForcedToolChoiceRelaxed(ChatLiteLLM):
    """ChatLiteLLM that binds the tools the way the served parser needs them.

    No choice or a forced one ("any", "required") is bound as the configured one
    instead; a named tool, "none" or "auto" pass through (`TOOL_CHOICE_NOTE` says why). And
    every optional container parameter is offered with an explicit type rather than
    pydantic's anyOf, so an XML tool-call parser hands a list over as JSON and not
    as a quoted string (sut.common.tool_schema_transport).
    """

    tool_choice_override: str | None = None

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        if self.tool_choice_override is not None and tool_choice in (None, "any", "required"):
            tool_choice = self.tool_choice_override
        tools = [declare_nullable_types(convert_to_openai_tool(tool)) for tool in tools]
        # LiteLLM validates tool_choice against its own model registry and rejects it for
        # ids it does not know, proxy-served ones included, before any request goes out.
        # Those endpoints do support tool calls, so whitelist the parameter where it is
        # sent rather than let the first turn fail (as sut/common/litellm_backend.py does).
        kwargs.setdefault("allowed_openai_params", ["tool_choice"])
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _create_message_dicts(self, messages, stop):
        """The request messages, with past reasoning sent back the way the server reads it.

        langchain_litellm returns every past turn with its reasoning as `reasoning_content`.
        vLLM reads only `reasoning` from a request message, so that reasoning never reached
        the chat template, and the model saw a run of empty think blocks as the LiteLLM loop
        did (sut.common.messages.REASONING_KEY). Here the reasoning moves to that key and
        stays on the last REASONING_TURNS_SENT_BACK assistant turns only, as in the loop.
        """
        message_dicts, params = super()._create_message_dicts(messages, stop)
        for message in message_dicts:
            reasoning = message.pop("reasoning_content", None)
            if message.get("role") == "assistant" and reasoning:
                message[REASONING_KEY] = reasoning
        keep_recent_reasoning(message_dicts, REASONING_TURNS_SENT_BACK)
        return message_dicts, params


class LangChainRepairAgent:
    """One model/tool loop built with LangChain's stable agent primitive."""

    identity = "langchain_agent"
    version = "1.0.0"
    #: Where this subject's `prompts/` directory is. A subject built on this one
    #: ships its own prompts and points this at its own package.
    subject_dir = Path(__file__).resolve().parent
    #: What an episode's ANI calls go through. A subject built on this one may name its own.
    adapter_class: ClassVar[type[LangChainANIAdapter]] = LangChainANIAdapter

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
            # None (no limit) leaves max_tokens out of the request, see request_max_tokens.
            "max_tokens": request_max_tokens(self.config.max_tokens),
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
        adapter = self.adapter_class(
            self.ani,
            task=task,
            artifact_directory=Path(self.config.artifact_directory),
            max_context_chars=self.config.max_context_chars,
            deadline=deadline,
            budget_seconds=budget,
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
            tools = self._tools(adapter)
            graph = self.agent_factory(
                model=self._model(request_timeout=remaining),
                tools=tools,
                system_prompt=self.system_prompt,
                middleware=self._middleware(adapter),
            )
            # Built before the clock is read again, so whatever building it costs
            # is spent inside the budget the graph is then given.
            message = self._task_message(
                task, deadline, [name for tool in tools if (name := getattr(tool, "name", None))])
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
            # The submission first: once the ANI accepted it the graph stopped there,
            # whatever the last message says. Otherwise the JSON the prompt allows as text.
            conclusion = adapter.trace.submission
            conclusion_source = "submit_result" if conclusion is not None else None
            if conclusion is None:
                conclusion = _conclusion_in_text(state)
                conclusion_source = "final_message" if isinstance(conclusion, dict) else None
            if not isinstance(conclusion, dict):
                raise NoConclusion("LangChain agent ended without submit_result or a JSON conclusion")
            report = self._report(adapter, conclusion, budget, time.monotonic() - started, None,
                                  _best_telemetry(state, usage_callback), conclusion_source=conclusion_source)
        except TimeoutError:
            # ExecutionBudgetExceeded from a tool, the two pre-checks and wait_for all
            # land here: the clock ended the episode.
            usage_callback.close_unanswered_turn("execution budget exceeded")
            report = self._report(
                adapter, None, budget, time.monotonic() - started, "execution budget exceeded",
                usage_callback.snapshot(),
                termination={"cause": "budget", "detail": "execution budget exceeded",
                             "turn": usage_callback.llm_calls})
        except Exception as exc:
            # A model call under way when the graph raised is a turn, as in the shared loop;
            # any other end leaves no call open.
            usage_callback.close_unanswered_turn(str(exc))
            termination = {"cause": termination_cause(exc, deadline),
                           "detail": str(exc)[:200], "turn": usage_callback.llm_calls}
            report = self._report(
                adapter, None, budget, time.monotonic() - started, str(exc),
                usage_callback.snapshot(), termination=termination)
        return json.dumps(report)

    def _middleware(self, adapter: LangChainANIAdapter) -> list[Any]:
        """What runs around the model's turns in one episode."""
        return [_stop_after_submission(adapter), _nudge_on_no_action(adapter), SubmissionInListedOrder()]

    def _tools(self, adapter: LangChainANIAdapter) -> list[Any]:
        """The tools one episode is given: the ANI's, and nothing else here."""
        return adapter.tools()

    def _task_message(self, task: dict[str, Any], deadline: float, surface: list[str] | None = None) -> str:
        """The first user message: the public task, its `tools` naming the episode's
        tools (`surface`), and nothing else here."""
        return json.dumps(public_task_for_model(task, surface), sort_keys=True)

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
        submitted = conclusion_source == "submit_result"
        # A submission is recorded as declared: submitting ends the episode whatever it
        # claims, and `verified` says whether a validation passed. The JSON written as
        # text keeps its gate: a completion without a passing validation is a failure.
        if submitted:
            status = requested_status
        else:
            status = "completed" if requested_status == "completed" and verified else "failed"
        if requested_status == "completed" and not verified and error is None and not submitted:
            error = "SUT claimed completion without a passing public validation"
        if conclusion is not None and requested_status == "failed" and error is None:
            # The loop files a self-declared failure under `error` (self_execute.py);
            # a record whose subject gave up but whose error reads null says nothing.
            error = str(conclusion.get("summary") or "") or None
        if conclusion is not None and termination is None:
            # The two ends a conclusion can have, named as the shared loop names them:
            # the gate refusing a completion claim, or the subject's own conclusion.
            turn = int(telemetry.get("llm_calls") or 0)
            if requested_status == "completed" and not verified and not submitted:
                termination = {"cause": "completion_rejected", "detail": error, "turn": turn}
            else:
                termination = {"cause": "own_conclusion", "detail": requested_status, "turn": turn}
                if submitted:
                    termination["via"] = "submit_result"
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
                # Identical failed requests (`sut.common.repeated_requests`): from the
                # third with no applied write between them, the answer carries a note and
                # the episode goes on; `notes` names each, `resets` each write that started
                # the count again. Records from 2026-10-27 to 2026-10-29 have `stop_rule`
                # instead, when the third ended the episode (`repeated_failed_requests`).
                "repeated_requests": (trace.repeated_requests.record() if trace
                                      else RepeatedRequests().record()),
                # The one `failed` sent back with more than half the budget left, or
                # None. Records before 2026-10-30 have no key.
                "early_failure_pushback": trace.early_failure_pushback if trace else None,
                # The one `completed` sent back for want of a passing public validation
                # since the last write, or None. Records before 2026-10-29 have no key.
                "unverified_completion_pushback": trace.unverified_completion_pushback if trace else None,
                # Each reply with neither a tool call nor a conclusion sent back to the
                # model with the shared loop's nudge. Records before 2026-10-29 have no
                # key: such a reply ended the episode with no conclusion.
                "no_action_nudges": list(trace.no_action_nudges if trace else []),
                # How the conclusion reached the harness: submit_result, or the JSON
                # text the prompt allows (None when there was none). Records before
                # 2026-10-07 say structured_response, langchain's conclusion tool.
                "conclusion_source": conclusion_source,
                "interaction_limits": {
                    "ani": ani_budget_state(self.ani, self.config.ani_call_limit),
                },
                "configured_model": self.config.model,
                "llm_calls": telemetry["llm_calls"],
                "model_parameters": {
                    "model": self.config.model,
                    # null when no output limit was sent (--max-tokens 0, none or unlimited)
                    "max_tokens": request_max_tokens(self.config.max_tokens),
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
                    # How many past turns went back with their reasoning; unknown for a
                    # supplied model, which builds its own requests.
                    "reasoning_turns_sent_back": REASONING_TURNS_SENT_BACK if self.model is None else None,
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
                {"status": requested_status, "summary": str(conclusion.get("summary") or ""),
                 **({"diagnosis": conclusion.get("diagnosis")} if submitted else {})}
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


class SubmissionInListedOrder(AgentMiddleware):
    """A model message that carries `submit_result` beside other calls runs its calls one
    at a time, in the order the model listed them.

    `create_agent` runs the calls of one message at the same time. The adapter refuses a
    call once a submission exists (`LangChainANIAdapter._invoke`), so with a submission in
    the message, which calls reached the ANI was a matter of timing: a call listed after
    the submission was sent in about half of the runs, and one listed before it was refused
    in about half. For a write that decided whether the network changed after the agent's
    own end. In the listed order a call before the submission is sent and one after it is
    not, as in the shared loop (`sut.common.self_execute`), which runs every message in order.

    A message with no submission, or with a submission alone, is left as it is.
    """

    #: A call waits this long for the one listed before it, then runs anyway: a call that
    #: never returned must not hold the episode.
    WAIT_SECONDS = 900.0

    def __init__(self) -> None:
        super().__init__()
        self._finished: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _turn(self, request: Any) -> tuple[threading.Event | None, threading.Event | None]:
        """The event of the call listed before this one, and this call's own."""
        messages = (request.state or {}).get("messages") if isinstance(request.state, dict) else None
        calls = list(getattr(messages[-1], "tool_calls", None) or []) if messages else []
        identifiers = [str(call.get("id")) for call in calls]
        mine = str((request.tool_call or {}).get("id"))
        if (len(calls) < 2 or mine not in identifiers
                or not any(call.get("name") == SUBMISSION_OPERATION for call in calls)):
            return None, None
        with self._lock:
            events = [self._finished.setdefault(identifier, threading.Event()) for identifier in identifiers]
        position = identifiers.index(mine)
        return (events[position - 1] if position else None), events[position]

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        before, own = self._turn(request)
        if before is not None:
            before.wait(self.WAIT_SECONDS)
        try:
            return handler(request)
        finally:
            if own is not None:
                own.set()

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        before, own = self._turn(request)
        if before is not None:
            await asyncio.to_thread(before.wait, self.WAIT_SECONDS)
        try:
            return await handler(request)
        finally:
            if own is not None:
                own.set()


def _stop_after_submission(adapter: LangChainANIAdapter) -> Any:
    """Middleware ending the graph before the next model call once a submission exists.

    `return_direct` would not do it: it stops only when every call of the turn asked
    for it, and a model may send submit_result alongside a read.
    """
    @before_model(can_jump_to=["end"])
    def stop_after_submission(state: Any, runtime: Any) -> dict[str, Any] | None:
        return {"jump_to": "end"} if adapter.trace.submission is not None else None

    return stop_after_submission


#: What the model is told after a reply with neither a tool call nor a conclusion: the
#: shared loop's own nudge, with this subject's two ends named.
NO_ACTION_NUDGE = ("Use an ANI tool next, or conclude with submit_result or the required final JSON "
                   "status object.")


def _nudge_on_no_action(adapter: LangChainANIAdapter) -> Any:
    """Middleware sending a reply with no tool call and no conclusion back to the model.

    `create_agent` ends the graph on a model message with no tool call, and the episode
    then ended with no conclusion: three in E19 (disable_routing.m1, drop_traffic.m3,
    wrong_routing_table.m3), each a reply of 915 to 3 226 characters that thought in its
    content and stopped. The shared loop nudges such a reply and goes on; so does this,
    every time, bounded by the budget and the recursion limit as the loop is by its
    budget. A JSON conclusion written as text is a conclusion and ends the graph as before.
    """
    @after_model(can_jump_to=["model"])
    def nudge_on_no_action(state: Any, runtime: Any) -> dict[str, Any] | None:
        if adapter.trace.submission is not None:
            return None
        messages = state.get("messages") if isinstance(state, dict) else None
        if not messages:
            return None
        last = messages[-1]
        if getattr(last, "type", None) != "ai" or getattr(last, "tool_calls", None):
            return None
        if _conclusion_in_text({"messages": [last]}) is not None:
            return None
        content = getattr(last, "content", "") or ""
        adapter.trace.no_action_nudges.append({
            "operations_before": len(adapter.trace.operations),
            "reply_chars": len(content if isinstance(content, str) else json.dumps(content, default=str)),
            # A tool call written as text, which the nudge then names (sut.common.messages).
            "unparsed_tool_call": unparsed_tool_call(content),
        })
        return {"messages": [HumanMessage(content=no_action_nudge(content, NO_ACTION_NUDGE))],
                "jump_to": "model"}

    return nudge_on_no_action


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
