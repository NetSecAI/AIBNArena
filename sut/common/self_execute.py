"""The ANI v0.1 `self_execute` loop, shared by every SUT.

This module is the reason the benchmark can compare two agents at all. The loop,
the completion gate, the way ANI results become `device_changes[]`, and the report shape
are what the judge measures; if two SUTs implemented them separately, a drift
would read as an agent-quality difference instead of a plumbing bug.

The only agent-specific seam is `call_model`. Everything else is fixed, so a new
SUT is a preamble plus a model caller.

`report_extras` is the one seam through which a SUT may add its own fields to the
report; everything else in the shape is fixed.

Malformed provider tool calls are rejected inside the loop and returned to the model
as retry feedback; they never escape the public self-execute report or reach ANI.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from benchmarks.platforms.containerlab import ANIRequestError

from .ani_report import (
    ANI_VERSION,
    SELF_EXECUTE_MODE,
    device_changes_from_ani_result,
    ani_category,
    objective_from_validation,
    operation_category,
    operation_summary,
    public_task_for_model,
    task_summary,
)
from .messages import (
    DIAGNOSIS_REQUEST,
    ModelActionError,
    assistant_tool_message,
    message_content,
    message_to_dict,
    parse_answer,
    parse_diagnosis,
    parse_final_response,
    tool_call_parts,
    tool_calls,
)
from .trace import Tracer

ModelCaller = Callable[[list[dict[str, Any]], float], Any]

#: How long the diagnosis turn may take after the execution budget is spent. The
#: judge waits budget + 120 s for the A2A response (benchmarks/core/sut_client.py),
#: so this fits inside the margin it already allows; a model that cannot state the
#: fault in a minute has its silence recorded, not the episode voided.
DIAGNOSIS_SECONDS = 60.0

# A public success check describes one observed device state, not the rest of the
# episode.  Any later operation which can change that state consumes the check: the
# model must validate again before it may claim completion.
NETWORK_MUTATION_OPERATIONS = frozenset({
    "update_config",
    "update_object",
    "rollback_config",
})

# Sanity questions are observational. Tool schemas are shared with repair episodes,
# so the model can still *ask* for a writer; this positive allowlist is the authority
# boundary that prevents that call reaching a device.
QUESTION_READ_ONLY_OPERATIONS = frozenset({
    "get_topology",
    "get_state",
    "get_running_config",
    "get_object",
    "execute_validation",
})


@dataclass(frozen=True)
class SelfExecuteRuntime:
    """Everything the loop needs, with the model caller injected.

    Both prompts are supplied by the agent rather than defaulted here: prompt text
    is each SUT's own. The loop still depends on the final-JSON shape they declare,
    so `sut/common/tests/test_self_execute_loop.py` asserts the SUTs agree on it.
    """

    ani: Any
    call_model: ModelCaller
    system_prompt: str
    question_system_prompt: str
    max_execution_seconds: float = 300.0
    trace: Tracer = field(default_factory=lambda: Tracer("off"))
    dry_run: bool = False
    self_execute: bool = True
    sut_identity: str = "unknown"
    sut_version: str = "1.0.0"
    configured_model: str | None = None
    model_parameters: dict[str, Any] = field(default_factory=dict)
    # What the model endpoint said about itself: device, driver, engine settings.
    # Empty when the endpoint does not answer /server_info.
    serving: dict[str, Any] = field(default_factory=dict)
    #: How many ANI operations the subject may make, or None for no cap. Enforced by
    #: the ANI, where every operation passes: a call past the cap is refused by name
    #: and recorded, so the episode ends with a report rather than a silent stop, and
    #: the record says the cap was reached.
    ani_call_limit: int | None = None
    #: Transcript bounds. All None by default, under which the loop sends every
    #: tool result whole and the transcript grows until the provider refuses it,
    #: exactly as every result before these fields ran. `tool_result_chars` cuts one
    #: tool result to that many characters and spills the whole result to
    #: `artifact_directory` when one is set; `context_budget_chars` is the most the
    #: serialized transcript may be when the model is called, with the results of
    #: reads a later identical read superseded elided first and the episode ended by
    #: name when that is not enough; `max_consecutive_rejections` ends an episode
    #: after that many completion claims in a row the gate refused.
    tool_result_chars: int | None = None
    context_budget_chars: int | None = None
    artifact_directory: str | None = None
    max_consecutive_rejections: int | None = None

def ani_budget_holder(ani: Any) -> Any | None:
    """The object that owns the ANI budget, reached through any tool router wrapping it.

    A subject's router answers the model and holds the platform's ANI; the budget lives
    on the ANI, because that is where every operation passes.
    """
    seen = set()
    current = ani
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if hasattr(current, "call_budget") and hasattr(current, "budget_state"):
            return current
        current = getattr(current, "ani", None)
    return None


def install_ani_budget(ani: Any, limit: int | None) -> None:
    """Put the run's ANI cap where the operations are counted."""
    holder = ani_budget_holder(ani)
    if holder is not None:
        holder.call_budget = limit


def reset_ani_budget(ani: Any) -> None:
    """Start an episode's count at zero.

    The ANI counts its operations for as long as the process lives, and a subject
    server serves many episodes in a row (a campaign restarts it only when the
    topology changes). Without this reset the record of every episode after the
    first carried the count of all of them, and a call limit would have been
    enforced over the whole run instead of over one episode.
    """
    holder = ani_budget_holder(ani)
    if holder is not None:
        holder.calls_made = 0
        holder.calls_refused = 0


def ani_budget_state(ani: Any, limit: int | None) -> dict[str, Any]:
    holder = ani_budget_holder(ani)
    if holder is None:
        # A test double with no budget of its own: report the request, not a count the
        # loop did not make.
        return {"limit": limit, "used": None, "refused": None, "reached": False}
    return holder.budget_state()


def _drain_nested_operations(ani: Any) -> list[dict[str, Any]]:
    """Take the operations a tool performed on the ANI while answering one call.

    The router leaves them on `nested_ani_operations`, the in-call counterpart of
    `pre_ani_operations`: the same summaries, each naming its `source`, made by the
    subject's own code and not chosen by the model. Read after every dispatch and
    emptied here, so nothing carries into the next call's account. A plain ANI has no
    such list and contributes nothing.
    """
    pending = getattr(ani, "nested_ani_operations", None)
    if not isinstance(pending, list):
        return []
    drained = [dict(item) for item in pending if isinstance(item, dict)]
    del pending[:]
    return drained


def invoke(runtime: SelfExecuteRuntime, task_json: str) -> str:
    task = load_task(task_json)
    # Open this episode's trace file before the first entry, so the record starts
    # with the task the SUT was actually given.
    # The model belongs in the name: several models write into one campaign
    # directory, and scenario plus counter alone would have them overwrite each
    # other's traces. The counter itself is the tracer's -- this runtime is frozen.
    runtime.trace.begin_episode(
        f"{runtime.configured_model or 'model'}-"
        f"{task.get('scenario_id') or 'episode'}"
    )
    runtime.trace("summary", "SUT received public A2A task", task_summary(task))
    if task.get("question"):
        return json.dumps(answer_sanity_question(runtime, task))
    if runtime.dry_run:
        return json.dumps(dry_run_report(task, runtime))
    if not runtime.self_execute:
        return json.dumps({
            "mode": SELF_EXECUTE_MODE,
            "sut_identity": runtime.sut_identity,
            "sut_version": runtime.sut_version,
            "model_reported_by_sut": runtime.configured_model,
            "provider_reported_model": None,
            "status": "failed",
            "verified": False,
            "device_changes": [],
            "error": "ANI v0.1 SUT requires self_execute mode",
        })
    return json.dumps(run_self_execute(runtime, task))

def run_self_execute(runtime: SelfExecuteRuntime, task: dict[str, Any]) -> dict[str, Any]:
    reset_ani_budget(runtime.ani)
    budget_seconds = execution_budget(task, runtime.max_execution_seconds)
    started = time.monotonic()
    deadline = started + budget_seconds
    messages = [
        {"role": "system", "content": runtime.system_prompt},
        {"role": "user", "content": json.dumps(public_task_for_model(task), indent=2)},
    ]
    device_changes: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = [
        dict(item) for item in getattr(runtime.ani, "pre_ani_operations", [])
        if isinstance(item, dict)
    ]
    latest_objective: dict[str, Any] | None = None
    error: str | None = None
    status = "timeout"
    llm_calls = 0
    token_usage: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    provider_model: str | None = None
    # One entry per model turn: how long the model took, and what came out of it. The
    # text does not travel — `sut/common/trace.py` explains why reasoning stays out of
    # the judged report — but the timing and the shape of each turn are telemetry, and
    # without them a report can place a tool call in the episode and not the thinking
    # that led to it.
    turns: list[dict[str, Any]] = []
    final_response: dict[str, Any] | None = None
    # The last completion claim the gate refused. Its `diagnosis` is still the
    # subject's own statement of the fault, so an episode the rejection cap or the
    # clock ends on such claims is not asked again for what it already said.
    rejected_final: dict[str, Any] | None = None
    # Why the loop stopped, written at the site that knows. `status` alone cannot
    # say it: "failed" covers a provider that rejected the context and a model that
    # concluded failure, and "timeout" covers a slow model and one that claimed
    # completion until the clock ran out. Filled at every exit below.
    termination: dict[str, Any] | None = None
    # How far the backend had to shrink its output reservation across the episode.
    # The backend reports it per call; summed here because a result is read per
    # episode, and a single "reduced" flag would hide a run that lived at 62 tokens.
    reservation = _empty_reservation()
    # The transcript's size at each call and what was done to keep it under the
    # configured bound; the limits are recorded even when unset, so a result says
    # which bounds it ran under and not only what happened.
    context = _empty_context(runtime)
    elided_messages: set[int] = set()

    while time.monotonic() < deadline:
        remaining = max(1.0, deadline - time.monotonic())
        turn_started = time.monotonic()
        prompt_chars = _transcript_chars(messages)
        if runtime.context_budget_chars is not None and prompt_chars > runtime.context_budget_chars:
            # Over budget before the provider sees it. Superseded reads go first,
            # because a read the model repeated is one it no longer needs the old copy
            # of; a transcript still over the budget after that ends here by name
            # rather than as a provider error the record cannot tell from a crash.
            elided = _elide_superseded_reads(messages, elided_messages)
            before, prompt_chars = prompt_chars, _transcript_chars(messages)
            context["elided_results"] += elided
            context["events"].append({
                "turn": len(turns) + 1, "kind": "elided_reads", "count": elided,
                "prompt_chars_before": before, "prompt_chars_after": prompt_chars,
            })
            if prompt_chars > runtime.context_budget_chars:
                error = (f"transcript of {prompt_chars} characters exceeds the context "
                         f"budget of {runtime.context_budget_chars}")
                status = "failed"
                termination = {"cause": "context_budget", "detail": error, "turn": len(turns)}
                break
        context["peak_prompt_chars"] = max(context["peak_prompt_chars"], prompt_chars)
        try:
            llm_calls += 1
            message = runtime.call_model(messages, remaining)
            provider_model = _provider_model(message) or provider_model
            _merge_token_usage(token_usage, _message_token_usage(message))
            _merge_reservation(reservation, _message_metadata(message, "_ibn_reservation"))
        except Exception as exc:
            _merge_reservation(reservation, getattr(exc, "_ibn_reservation", None))
            turns.append(_turn(turns, turn_started, kind="error", error=str(exc)))
            error = str(exc)
            status = "failed"
            termination = {
                "cause": exception_cause(exc, deadline, remaining),
                "detail": str(exc)[:200],
                "turn": len(turns),
            }
            break

        calls = tool_calls(message)
        content = message_content(message)
        # Closed here, before the tools run: what follows is execution, not thinking.
        turn = _turn(turns, turn_started, kind="pending", content=content)
        turns.append(turn)
        runtime.trace("full", "SUT raw model message", message_to_dict(message))
        if calls:
            try:
                parsed_calls = [tool_call_parts(tool_call) for tool_call in calls]
                assistant_message = assistant_tool_message(content, calls, parsed_calls)
            except ModelActionError as exc:
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Malformed ANI tool call rejected: {exc}. Issue a valid ANI "
                        "function call with a name and JSON object arguments, or return "
                        "the required final status object."
                    ),
                })
                turn.update(kind="malformed_tool_call", error=str(exc))
                error = str(exc)
                continue
            messages.append(assistant_message)
            turn["kind"] = "tool_calls"
            turn["tool_calls"] = [name for name, _, _ in parsed_calls]
            for tool_name, arguments, tool_call_id in parsed_calls:
                call_started = time.monotonic()
                try:
                    result = runtime.ani.dispatch(tool_name, arguments, task=task)
                except ANIRequestError as exc:
                    result = {"ok": False, "operation": tool_name, "error": str(exc)}
                except Exception as exc:  # noqa: BLE001 - keep provider output on-contract
                    result = {
                        "ok": False,
                        "operation": tool_name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    if tool_name in NETWORK_MUTATION_OPERATIONS:
                        # An unexpected writer exception may happen after the native
                        # device accepted a change. Never describe that as an ordinary
                        # confirmed no-op; force a fresh read/reconciliation instead.
                        result["indeterminate"] = True
                duration = round(time.monotonic() - call_started, 3)
                # Filed before the call's own summary because they happened before its
                # result: a subject tool that reads the devices on its way to a write
                # performs ANI operations the model did not choose, and until they are
                # carried here they reach the judge in no `ani_operations` entry. The
                # platform's own ledger still counts them; this is the report's copy,
                # in the order the lab saw it.
                for nested in _drain_nested_operations(runtime.ani):
                    operations.append(nested)
                    turn["operations"].append(len(operations))
                    runtime.trace(
                        "summary",
                        f"ANI {nested.get('operation')} ({nested.get('source')})", nested)
                summary = operation_summary(tool_name, result, duration, arguments)
                operations.append(summary)
                # By position rather than by a key on the summary: `ani_operations` is a
                # contract several SUTs and their goldens agree on, and the link belongs
                # to the turn that issued the call.
                turn["operations"].append(len(operations))
                runtime.trace("summary", f"ANI {tool_name}", summary)
                runtime.trace("full", f"ANI {tool_name} result", result)
                device_changes.extend(device_changes_from_ani_result(result))
                if tool_name in NETWORK_MUTATION_OPERATIONS:
                    # Invalidate even when the envelope says ``ok: false``. A partial
                    # or indeterminate writer failure is precisely when an older
                    # passing observation is least safe to reuse.
                    latest_objective = None
                objective = objective_from_validation(result)
                if objective is not None:
                    latest_objective = objective
                    # Only credit an action that actually executed. Decorating a failed
                    # one reads as "this change fixed it" when the change never landed,
                    # which is exactly backwards for a report the evaluation relies on.
                    # A passing objective is still reported globally in final_observation.
                    if objective.get("passed") and device_changes and (device_changes[-1].get("result") or {}).get("ok"):
                        device_changes[-1]["verified_after_action"] = True
                        device_changes[-1]["objective_verification"] = objective
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": _bounded_tool_content(runtime, tool_name, result, context, turn["turn"]),
                })
            continue

        final = parse_final_response(content)
        if final is not None:
            if final["status"] == "completed" and not (latest_objective or {}).get("passed"):
                rejected_final = dict(final)
                messages.append({
                    "role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "Completion rejected: call execute_validation with type public_success_criteria before claiming success, or return status failed.",
                })
                turn.update(kind="rejected_final", error=(
                    "completion claimed without a passing public validation"))
                error = "SUT claimed completion without a passing public validation"
                cap = runtime.max_consecutive_rejections
                if cap is not None and _consecutive_rejections(turns) >= cap:
                    # The gate would refuse the next claim as well; the record names
                    # the cap as the reason rather than the clock that never ran out.
                    error = (f"completion claimed {cap} times in a row without a passing "
                             "public validation")
                    status = "failed"
                    termination = {"cause": "completion_rejected", "detail": error,
                                   "turn": turn["turn"]}
                    break
                continue
            status = final["status"]
            final_response = dict(final)
            turn["kind"] = "final"
            error = None if status == "completed" else final.get("summary")
            termination = {"cause": "own_conclusion", "detail": status, "turn": turn["turn"]}
            break

        messages.append({"role": "assistant", "content": content or ""})
        messages.append({
            "role": "user",
            "content": "Use an ANI tool next, or return the required final JSON status object.",
        })
        turn.update(kind="no_action", error=(
            "neither ANI tool calls nor a final status object"))
        error = "model returned neither ANI tool calls nor a final status object"

    elapsed = round(time.monotonic() - started, 3)
    verified = status == "completed" and bool((latest_objective or {}).get("passed"))
    if status == "timeout" and error is None:
        error = f"ANI execution budget of {budget_seconds:g}s elapsed"
    if termination is None:
        # Only the while condition exits without a break, so this is the clock. A
        # model that spent its last turns claiming completion the gate refused did
        # not run out of time so much as of arguments, and the two are read apart.
        tail = turns[-5:]
        stuck = len(tail) == 5 and all(item["kind"] == "rejected_final" for item in tail)
        termination = {
            "cause": "completion_rejected_until_budget" if stuck else "budget",
            "detail": error,
            "turn": len(turns),
        }
    # The intermediate step of evaluation parameter 13: what the subject says the
    # fault was. Taken from its own conclusion when it reached one, asked for in one
    # tool-less turn when the episode ended on it. After the clock, on purpose: the
    # repair is measured under the budget and the diagnosis is measured beside it.
    if final_response is not None:
        diagnosis = _diagnosis_from_conclusion(final_response)
    elif rejected_final is not None and rejected_final.get("diagnosis"):
        diagnosis = dict(_diagnosis_from_conclusion(rejected_final), source="rejected_final_response")
    elif termination["cause"] in {"context_budget", "context_window"}:
        # The transcript is what the provider or the bound refused; sending it once
        # more with a question appended would fail the same way.
        diagnosis = {"text": None, "source": "termination_turn", "requested": False,
                     "error": f"not asked: the episode ended on {termination['cause']}, "
                              "so the transcript cannot be sent again"}
    else:
        diagnosis = _request_diagnosis(runtime, messages)
        # Its tokens are part of what the episode cost; its turn is not one of the
        # loop's (`model_turns` and `llm_calls` describe the repair), so the block
        # carries its own count and timing.
        _merge_token_usage(token_usage, diagnosis.pop("_token_usage", {}))
    return {
        **report_extras(runtime.ani),
        "mode": SELF_EXECUTE_MODE,
        "sut_identity": runtime.sut_identity,
        "sut_version": runtime.sut_version,
        "model_reported_by_sut": runtime.configured_model,
        "provider_reported_model": provider_model,
        "status": status,
        "verified": verified,
        "device_changes": device_changes,
        "ani_operations": operations,
        "execution": {
            "ani_version": ANI_VERSION,
            "budget_seconds": budget_seconds,
            "elapsed_seconds": elapsed,
            "tool_call_count": len(operations),
            "interaction_limits": {
                # The platform's own count, which includes the operations a subject's
                # own tool performed on the way to answering one model call.
                "ani": ani_budget_state(runtime.ani, runtime.ani_call_limit),
            },
            "llm_calls": llm_calls,
            "token_usage": token_usage,
            "provider_model": provider_model,
            "configured_model": runtime.configured_model,
            "model_parameters": dict(runtime.model_parameters),
            "serving": dict(runtime.serving),
            "trace_ref": str(runtime.trace.path) if runtime.trace.path else None,
            "model_turns": turns,
            "model_seconds": round(sum(item["seconds"] for item in turns), 3),
            "termination": termination,
            "reservation": reservation,
            "context": context,
        },
        "final_observation": latest_objective,
        # What the model concluded, in its own words, under the contract its prompt
        # declares. `error` carries it only when the episode failed, so without this a
        # successful run loses the one sentence the model wrote about its own work.
        "final_response": final_response,
        # The fault as the subject identified it, and where that statement came from.
        "diagnosis": diagnosis,
        "error": error,
    }


def _diagnosis_from_conclusion(final_response: dict[str, Any]) -> dict[str, Any]:
    """The diagnosis the subject's own final JSON carried, or the fact that it did not."""
    text = final_response.get("diagnosis")
    return {
        "text": text,
        "source": "final_response",
        "requested": False,
        "error": None if text else "the final response carried no diagnosis",
    }


def _request_diagnosis(runtime: SelfExecuteRuntime,
                       messages: list[dict[str, Any]]) -> dict[str, Any]:
    """One tool-less turn asking what the fault was, after an episode that ended
    without the subject's own conclusion.

    The transcript is sent as it stands, so the answer rests on what the model saw.
    A tool call in reply is refused once and the question repeated; a second one, an
    exception, or the clock leave `text` None with the reason beside it. Nothing here
    changes `status`, `verified` or the operations: the repair was already measured.
    """
    started = time.monotonic()
    deadline = started + DIAGNOSIS_SECONDS
    transcript = list(messages)
    transcript.append({"role": "user", "content": DIAGNOSIS_REQUEST})
    result: dict[str, Any] = {"text": None, "source": "termination_turn", "requested": True,
                              "error": None, "calls": 0, "seconds": 0.0,
                              "_token_usage": {"input_tokens": None, "output_tokens": None,
                                               "total_tokens": None}}
    runtime.trace("summary", "SUT diagnosis requested", {"seconds_allowed": DIAGNOSIS_SECONDS})
    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result["error"] = f"no time left for the diagnosis turn ({DIAGNOSIS_SECONDS:g}s allowed)"
            break
        try:
            result["calls"] += 1
            message = runtime.call_model(transcript, max(1.0, remaining))
        except Exception as exc:  # noqa: BLE001 - the reason is the record, not a crash
            result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            break
        _merge_token_usage(result["_token_usage"], _message_token_usage(message))
        content = message_content(message)
        runtime.trace("full", "SUT raw model message", message_to_dict(message))
        if tool_calls(message):
            if attempt == 1:
                transcript.append({"role": "assistant", "content": content or ""})
                transcript.append({
                    "role": "user",
                    "content": ("No tool will be dispatched. Answer the question only: "
                                "return the JSON diagnosis object."),
                })
                continue
            result["error"] = "the model called a tool instead of stating a diagnosis"
            break
        text = parse_diagnosis(content)
        result["text"] = text
        if text is None:
            result["error"] = "the model returned no diagnosis text"
        break
    result["seconds"] = round(time.monotonic() - started, 3)
    runtime.trace("summary", "SUT diagnosis", {key: value for key, value in result.items()
                                               if key != "_token_usage"})
    return result


# --------------------------------------------------------------------------- #
# Transcript bounds
# --------------------------------------------------------------------------- #

#: Tool names whose result may be elided once the same call was made again: the
#: ANI's own reads, and a subject tool named as a read. A write's result carries the
#: transaction and the precondition the next write is checked against, and a
#: validation's is what the completion gate reads; neither is replaced whatever the
#: transcript costs.
_READ_PREFIXES = ("get_", "list_", "lookup_", "query_", "search_", "describe_", "find_",
                  "read_", "show_")


def _is_read_operation(tool_name: str) -> bool:
    return ani_category(tool_name) == "read" or tool_name.startswith(_READ_PREFIXES)


def _empty_context(runtime: SelfExecuteRuntime) -> dict[str, Any]:
    """The transcript account an episode starts with, naming the bounds it ran under."""
    return {
        "budget_chars": runtime.context_budget_chars,
        "tool_result_chars": runtime.tool_result_chars,
        "max_consecutive_rejections": runtime.max_consecutive_rejections,
        "peak_prompt_chars": 0,
        "truncated_results": 0,
        "elided_results": 0,
        "events": [],
    }


def _transcript_chars(messages: list[dict[str, Any]]) -> int:
    """Size of the transcript as it is sent: the serialized messages.

    Characters rather than tokens: the loop has no tokenizer for the model it drives,
    and a count it makes itself is the same for every provider and every replay. The
    provider's own count arrives after the call, in token_usage.
    """
    return len(json.dumps(messages, default=str))


def _consecutive_rejections(turns: list[dict[str, Any]]) -> int:
    count = 0
    for item in reversed(turns):
        if item.get("kind") != "rejected_final":
            break
        count += 1
    return count


def _bounded_tool_content(
    runtime: SelfExecuteRuntime,
    tool_name: str,
    result: Any,
    context: dict[str, Any],
    turn_number: int,
) -> str:
    """The tool message for a result, cut to the configured size when it has one.

    Unset, the result travels whole. Over the limit, the model receives the head of
    the result inside an envelope that says how much was cut, the digest of the whole
    and where the whole was written when an artifact directory is configured, so a
    narrower call is the model's decision and the record still holds what the tool
    returned. The envelope is a little longer than the limit: the limit bounds the
    data, and the account of the cut travels beside it.
    """
    content = json.dumps(result)
    limit = runtime.tool_result_chars
    if limit is None or len(content) <= limit:
        return content
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    artifact_ref = None
    if runtime.artifact_directory:
        directory = Path(runtime.artifact_directory)
        directory.mkdir(parents=True, exist_ok=True)
        artifact = directory / f"tool-{digest}.json"
        if not artifact.exists():
            artifact.write_text(content, encoding="utf-8")
        artifact_ref = str(artifact)
    envelope = {
        "truncated": True,
        "operation": tool_name,
        "complete_size_chars": len(content),
        "returned_size_chars": limit,
        "sha256": digest,
        "artifact_ref": artifact_ref,
        "data": content[:limit],
        "note": (
            f"{tool_name} returned {len(content)} characters; the first {limit} are shown. "
            "Ask for less next time: scope the call to one device, prefix, name or page."
        ),
    }
    context["truncated_results"] += 1
    context["events"].append({
        "turn": turn_number, "kind": "truncated_result", "operation": tool_name,
        "complete_size_chars": len(content), "sha256": digest, "artifact_ref": artifact_ref,
    })
    return json.dumps(envelope, sort_keys=True)


def _call_arguments(tool_call: Any) -> str:
    function = tool_call.get("function") if isinstance(tool_call, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return arguments
    return json.dumps(arguments, sort_keys=True, default=str)


def _elide_superseded_reads(messages: list[dict[str, Any]], elided: set[int]) -> int:
    """Replace the results of reads a later identical read superseded; how many.

    Only the newest result of each (operation, arguments) read stays whole. An older
    one becomes a stub naming the call whose result replaced it and the digest of what
    was there, so the model still sees that it asked and the record can be matched to
    the trace. Role and tool_call_id are untouched, which is what the provider checks
    a transcript for. Writes and validations are never touched.
    """
    calls: dict[str, tuple[str, str]] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str) and tool_call.get("id") is not None:
                calls[str(tool_call["id"])] = (name, _call_arguments(tool_call))
    latest: dict[tuple[str, str], int] = {}
    positions: list[tuple[int, tuple[str, str], str]] = []
    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id"))
        if call_id not in calls or not _is_read_operation(calls[call_id][0]):
            continue
        latest[calls[call_id]] = index
        positions.append((index, calls[call_id], call_id))
    def newest_is_ok(index: int) -> bool:
        # A failed re-read supersedes nothing: eliding the older, good answer would
        # leave the model only the failure. Content that is not a JSON object with
        # an explicit ok=false counts as an answer.
        content = messages[index].get("content")
        try:
            data = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            return True
        return not (isinstance(data, dict) and data.get("ok") is False)

    count = 0
    for index, key, call_id in positions:
        if index == latest[key] or index in elided or not newest_is_ok(latest[key]):
            continue
        content = messages[index].get("content")
        content = content if isinstance(content, str) else json.dumps(content, default=str)
        replacement = {
            "superseded": True,
            "operation": key[0],
            "superseded_by_call": next(
                cid for pos, k, cid in positions if pos == latest[key] and k == key),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "elided_chars": len(content),
        }
        messages[index] = {**messages[index], "content": json.dumps(replacement, sort_keys=True)}
        elided.add(index)
        count += 1
    return count


def _turn(turns: list[dict[str, Any]], started: float, *, kind: str,
          content: str = "", error: str | None = None) -> dict[str, Any]:
    """One model turn, measured. Never its text: only how much of it there was.

    `content_chars` is what lets a report say a turn produced prose without carrying the
    prose; the text itself is in the trace file, which is where reasoning is allowed to
    live and which the benchmark artifact references by path.
    """
    return {
        "turn": len(turns) + 1,
        "seconds": round(time.monotonic() - started, 3),
        "kind": kind,
        "content_chars": len(content),
        "tool_calls": [],
        "operations": [],
        "error": error,
    }

def _message_metadata(message: Any, key: str) -> Any:
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)

def _provider_model(message: Any) -> str | None:
    value = _message_metadata(message, "_ibn_provider_model")
    return str(value) if value not in (None, "") else None

def _message_token_usage(message: Any) -> dict[str, int | None]:
    value = _message_metadata(message, "_ibn_usage")
    if not isinstance(value, dict):
        return {}
    return {
        key: (int(value[key]) if value.get(key) is not None else None)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }

def _merge_token_usage(total: dict[str, int | None], incoming: dict[str, int | None]) -> None:
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = incoming.get(key)
        if value is not None:
            total[key] = (total[key] or 0) + value

def exception_cause(exc: BaseException, deadline: float, remaining: float) -> str:
    """Name what a model-call exception was, from the text the provider wrote.

    The context test is the one the backend retries on (`_context_retry_tokens`),
    so what it gave up on here is what it retried on there. Budget is read from the
    clock first: a provider timeout raised with a minute still to go was the
    provider's, not the budget's, and lands in model_error with its text.
    """
    text = f"{type(exc).__name__}: {exc}"
    if "ContextWindowExceeded" in text or "context length" in text.lower():
        return "context_window"
    if time.monotonic() >= deadline or ("Timeout" in text and remaining < 60):
        return "budget"
    return "model_error"

def _empty_reservation() -> dict[str, int | None]:
    return {"reductions": 0, "min_max_tokens": None, "calls_reduced": 0}

def _merge_reservation(total: dict[str, int | None], incoming: Any) -> None:
    """Fold one call's `_ibn_reservation` into the episode's.

    `min_max_tokens` is only the reduced calls' minimum: a call that kept its
    configured ceiling says nothing about how low the episode was pushed, and
    counting it would report the ceiling as the floor.
    """
    if not isinstance(incoming, dict):
        return
    reductions = incoming.get("reductions")
    if not isinstance(reductions, int) or reductions <= 0:
        return
    total["reductions"] = (total["reductions"] or 0) + reductions
    total["calls_reduced"] = (total["calls_reduced"] or 0) + 1
    floor = incoming.get("min_max_tokens")
    if isinstance(floor, int):
        current = total["min_max_tokens"]
        total["min_max_tokens"] = floor if current is None else min(current, floor)

def report_extras(ani: Any) -> dict[str, Any]:
    """Report fields a SUT adds about itself, or nothing.

    The judge stores the report verbatim, so a SUT can surface something the shared
    fields cannot express, which no count of ANI operations reveals. Spread first in
    `run_self_execute` so a careless extra can never shadow a contract field.

    Failures are swallowed: an agent that repaired the network must not be reported
    as failed because a diagnostic field could not be computed.
    """
    hook = getattr(ani, "report_extras", None)
    if hook is None:
        return {}
    try:
        extras = hook()
    except Exception:  # noqa: BLE001 - reporting is never worth losing an episode over
        return {}
    return extras if isinstance(extras, dict) else {}

def answer_sanity_question(runtime: SelfExecuteRuntime, task: dict[str, Any]) -> dict[str, Any]:
    budget_seconds = min(60.0, execution_budget(task, runtime.max_execution_seconds))
    deadline = time.monotonic() + budget_seconds
    messages = [
        {"role": "system", "content": runtime.question_system_prompt},
        {"role": "user", "content": json.dumps(public_task_for_model(task), indent=2)},
    ]
    evidence: list[str] = []
    while time.monotonic() < deadline:
        try:
            message = runtime.call_model(
                messages, max(1.0, deadline - time.monotonic()))
        except Exception as exc:  # noqa: BLE001 - provider errors are answer evidence
            return {
                "answer": "Unable to answer because the model provider failed.",
                "evidence": evidence,
                "error": str(exc),
            }
        calls = tool_calls(message)
        content = message_content(message)
        if calls:
            try:
                parsed_calls = [tool_call_parts(tool_call) for tool_call in calls]
                assistant_message = assistant_tool_message(content, calls, parsed_calls)
            except ModelActionError as exc:
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({
                    "role": "user",
                    "content": f"Malformed ANI tool call rejected: {exc}. Retry with a valid call.",
                })
                continue
            messages.append(assistant_message)
            for tool_name, arguments, tool_call_id in parsed_calls:
                if tool_name not in QUESTION_READ_ONLY_OPERATIONS:
                    result = {
                        "ok": False,
                        "operation": tool_name,
                        "error": (
                            "sanity questions are read-only; this operation was not "
                            "dispatched"
                        ),
                    }
                else:
                    try:
                        result = runtime.ani.dispatch(tool_name, arguments, task=task)
                    except (ANIRequestError, ModelActionError) as exc:
                        result = {"ok": False, "operation": tool_name, "error": str(exc)}
                    except Exception as exc:  # noqa: BLE001 - questions stay on-contract
                        result = {
                            "ok": False,
                            "operation": tool_name,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                evidence.append(f"ANI {tool_name}: ok={result.get('ok')}")
                messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result)})
            continue
        answer = parse_answer(content)
        if answer is not None:
            answer["evidence"] = answer.get("evidence") or evidence
            return answer
        messages.append({"role": "assistant", "content": content or ""})
        messages.append({"role": "user", "content": "Use ANI tools for evidence, then return the required answer JSON."})
    return {"answer": "Unable to answer within the ANI execution budget.", "evidence": evidence}

def execution_budget(task: dict[str, Any], max_execution_seconds: float) -> float:
    payload = task.get("execution_budget")
    requested = payload.get("wall_clock_seconds") if isinstance(payload, dict) else None
    try:
        value = float(requested) if requested is not None else max_execution_seconds
    except (TypeError, ValueError):
        value = max_execution_seconds
    if value <= 0:
        value = max_execution_seconds
    return min(value, max_execution_seconds)

def load_task(task_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(task_json)
    except json.JSONDecodeError:
        return {"raw": task_json}
    return payload if isinstance(payload, dict) else {"raw": task_json}

def dry_run_report(task: dict[str, Any], runtime: SelfExecuteRuntime) -> dict[str, Any]:
    return {
        "mode": SELF_EXECUTE_MODE,
        "sut_identity": runtime.sut_identity,
        "sut_version": runtime.sut_version,
        "model_reported_by_sut": runtime.configured_model,
        "provider_reported_model": None,
        "status": "failed",
        "verified": False,
        "device_changes": [],
        "ani_operations": [],
        "execution": {
            "ani_version": ANI_VERSION,
            "dry_run": True,
            "llm_calls": 0,
            "token_usage": {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
            "configured_model": runtime.configured_model,
            "model_parameters": dict(runtime.model_parameters),
            "serving": dict(runtime.serving),
            "trace_ref": str(runtime.trace.path) if runtime.trace.path else None,
            "termination": {
                "cause": "dry_run",
                "detail": "dry-run does not execute ANI operations",
                "turn": 0,
            },
            "reservation": _empty_reservation(),
            "context": _empty_context(runtime),
        },
        "error": "dry-run does not execute ANI operations",
    }
