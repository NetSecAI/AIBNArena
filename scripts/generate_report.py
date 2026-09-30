#!/usr/bin/env python3
"""Turn one unified benchmark result into a report meant to be processed.

The result JSON the runner writes is a record, not a report: what an episode did is
spread across `sut_result.ani_operations`, and the arguments the model chose are one
long list away from the counts that summarise them.

This script derives the report from it: every call in the order the episode made it,
each with the parameters the model passed and the error it got back, alongside totals
computed once here rather than in every consumer.

Both renderings are written for every result: the JSON is the report as data, for
whatever reads it next, and the HTML is the same document as a page for a human.
`--format json` or `--format html` restricts a run to one of them.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.core.reporting import device_changes_of, validate_result

DEFAULT_OUTPUT_DIR = Path("reports/manual")

#: Bumped when a consumer would have to change to keep reading these files.
REPORT_VERSION = "1.0"

def load_payload(raw: bytes, path: Path) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    validate_result(payload)
    return payload


# -- extraction -----------------------------------------------------------------


#: The two device-facing families, named the way `sut/common/ani_report.py` categorises
#: them. They are matched by category first and by name second, so an operation recorded
#: by an older SUT that predates the categories still lands in the right column.
DEVICE_READ_OPERATIONS = ("get_topology", "get_state", "get_running_config")
DEVICE_MUTATION_OPERATIONS = ("update_config", "rollback_config")

def containerlab_counts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """What the episode did to the lab itself, and how it split across the tool surface.

    `sut_validations` counts the agent's own `execute_validation` calls, and is named
    apart from the result's `validations` because that one is a different quantity: the
    lifecycle adds its own oracle runs — healthy, every degradation draw that measured,
    repair, preservation — to the same field.
    """
    operations = ((payload.get("sut_result") or {}).get("ani_operations")) or []
    counts = {
        "containerlab_reads": 0,
        "containerlab_mutations": 0,
        "containerlab_mutations_ok": 0,
        "sut_validations": 0,
        "failed_operations": 0,
    }
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        name = str(operation.get("operation") or "")
        category = operation.get("category")
        ok = operation.get("ok") is True
        counts["failed_operations"] += int(not ok)
        if operation.get("dispatched") is False:
            # Refused by an interaction cap before anything ran: an attempt the subject
            # made and a failed operation, but not a read, a write or a validation that
            # happened, so no category counter may claim it.
            continue
        if category == "read" or name in DEVICE_READ_OPERATIONS:
            counts["containerlab_reads"] += 1
        elif category == "mutation" or name in DEVICE_MUTATION_OPERATIONS:
            counts["containerlab_mutations"] += 1
            counts["containerlab_mutations_ok"] += int(ok)
        elif category == "validation" or name == "execute_validation":
            counts["sut_validations"] += 1
    return counts


def operation_count_block(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The counts a reader of this report asks for, derived from the ledger.

    The result's own `operation_counts` is kept whole under `recorded` rather than merged
    into these. It is the judge's accounting, it feeds the metrics, and it still counts
    some things differently — `validations` there includes the lifecycle's own oracle
    runs, not only the agent's calls. Two numbers that disagree must be readable side by
    side rather than silently reconciled.
    """
    recorded = payload.get("operation_counts") or {}
    counts: dict[str, Any] = {"llm_calls": recorded.get("llm_calls")}
    counts.update(containerlab_counts(payload))
    counts.update({
        "unsafe_operations": recorded.get("unsafe_operations"),
        "input_tokens": recorded.get("input_tokens"),
        "output_tokens": recorded.get("output_tokens"),
        "total_tokens": recorded.get("total_tokens"),
        "recorded": dict(recorded),
    })
    return counts


#: Where the trace keeps a model message, and where a provider keeps the thinking inside
#: one. `content` is the answer; the rest are the fields the providers seen here use for
#: reasoning returned apart from it.
TRACE_MODEL_TITLE = "SUT raw model message"
#: The two shapes a model message has in a trace: the shared loop logs the provider's
#: raw message (`content`, `reasoning_content`, `tool_calls`); the LangChain baseline
#: logs the callback's generations (`text`, `content` blocks, `tool_calls`,
#: `reasoning_content`) under "SUT model response".
TRACE_MODEL_TITLES = (TRACE_MODEL_TITLE, "SUT model response")
REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")


def _call_names(calls: Any) -> list[str]:
    names = []
    for call in calls or []:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
        name = call.get("name") or function.get("name")
        if name:
            names.append(str(name))
    return names


def trace_message(title: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """One model message in the report's shape, whichever subject logged it."""
    if title == "SUT model response":
        generations = [item for item in (payload.get("generations") or []) if isinstance(item, Mapping)]
        first = generations[0] if generations else {}
        blocks = [block for block in (first.get("content") or []) if isinstance(block, Mapping)] \
            if isinstance(first.get("content"), list) else []
        reasoning = first.get("reasoning_content") if isinstance(first.get("reasoning_content"), str) else None
        if not (reasoning and reasoning.strip()):
            reasoning = "\n".join(str(block.get("thinking") or "") for block in blocks
                                  if block.get("type") == "thinking" and block.get("thinking")) or None
        text = first.get("text") if isinstance(first.get("text"), str) else ""
        if not text.strip():
            text = "".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")
        return {"text": text.strip(), "reasoning": reasoning,
                "tool_calls": _call_names(first.get("tool_calls")), "turn": payload.get("turn")}
    reasoning = next(
        (str(payload[field]) for field in REASONING_FIELDS
         if isinstance(payload.get(field), str) and payload[field].strip()),
        None,
    )
    return {"text": str(payload.get("content") or "").strip(), "reasoning": reasoning,
            "tool_calls": _call_names(payload.get("tool_calls")), "turn": payload.get("turn")}


def read_trace(reference: Any) -> dict[str, Any]:
    """The model messages of this episode, from the trace file the result points at.

    Reasoning never travels in the benchmark result — `sut/common/trace.py` sets that
    boundary and the SUT keeps it — so the only place the model's own words exist is the
    trace, which the result references by path. Reading it here is what the reference is
    for; a report is read by people and by analysis, not by the scorer.

    A missing or unreadable trace is reported as such. It is the normal case for a run
    made with tracing off, and the timeline still has every turn's timing without it.
    """
    record: dict[str, Any] = {"path": str(reference) if reference else None,
                              "available": False, "messages": []}
    if not reference:
        record["reason"] = "the result records no trace file"
        return record
    path = Path(str(reference))
    if not path.is_absolute() and not path.exists():
        # Written by the SUT process, whose working directory was its own.
        path = REPO_ROOT / path
    if not path.is_file():
        record["reason"] = "the trace file no longer exists"
        return record
    messages = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, Mapping) or entry.get("title") not in TRACE_MODEL_TITLES:
                continue
            payload = entry.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            messages.append(trace_message(entry.get("title"), payload))
    except OSError as exc:
        record["reason"] = f"{type(exc).__name__}: {exc}"
        return record
    record["available"] = True
    record["path"] = str(path)
    record["messages"] = messages
    return record


def inferred_turns(trace: Mapping[str, Any], operations: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turns for a subject that recorded none: one per model message in the trace.

    The LangChain baseline recorded no `model_turns` before 2026-09-16, but its trace has
    every model message with the tool calls it asked for. A turn's operations are matched
    to its tool calls by name, in order: a call the subject refused before the ANI (a
    repeat of a failed request, a malformed argument) has no operation, so a turn can
    name more calls than it has operations. No timing: the trace has none.
    """
    messages = [message for message in (trace.get("messages") or []) if message.get("turn") is not None]
    if not messages:
        return []
    names = [str(operation.get("operation") or "") for operation in operations]
    position = 0
    turns = []
    for index, message in enumerate(messages, start=1):
        calls = list(message.get("tool_calls") or [])
        positions = []
        for name in calls:
            if position < len(names) and names[position] == name:
                position += 1
                positions.append(position)
        turns.append({
            "turn": index, "seconds": None, "kind": "tool_calls" if calls else "text",
            "content_chars": len(message.get("text") or ""), "tool_calls": calls,
            "operations": positions, "error": None, "inferred": True,
        })
    return turns


def thinking_step(turn: Mapping[str, Any], trace: Mapping[str, Any]) -> dict[str, Any]:
    """One turn of the model, with its words when the trace kept them."""
    messages = trace.get("messages") or []
    index = int(turn.get("turn") or 0) - 1
    message = messages[index] if 0 <= index < len(messages) else {}
    return {
        "type": "thinking",
        "turn": turn.get("turn"),
        "seconds": turn.get("seconds"),
        "kind": turn.get("kind"),
        "tool_calls": list(turn.get("tool_calls") or []),
        "content_chars": turn.get("content_chars"),
        "text": message.get("text"),
        "reasoning": message.get("reasoning"),
        "error": turn.get("error"),
    }


def call_step(position: int, operation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "call",
        "position": position,
        "tool": str(operation.get("operation") or ""),
        "category": operation.get("category"),
        "ok": operation.get("ok") is True,
        "seconds": operation.get("duration_seconds"),
        "arguments": operation.get("arguments") if "arguments" in operation else None,
        "error": operation.get("error"),
    }


def budget_block(payload: Mapping[str, Any]) -> dict[str, Any]:
    """What the agent was allowed, what it used, and whether it went over.

    Two different things are reported rather than one, because they answer different
    questions. `exhausted` is the loop's own verdict: it stops opening a turn once the
    deadline has passed and reports the episode as `timeout`, so an exhausted budget means
    the agent was cut off with work still to do. `exceeded` is arithmetic: elapsed beyond
    the budget, which can happen by a little even on an episode that finished, because a
    model turn or a tool call already in flight when the deadline passes is not
    interrupted — the loop waits for it.

    `invoke_sut_seconds` is the same episode measured on the judge's side of the A2A
    boundary; the difference is transport. The judge's client waits `budget + 120 s`
    before giving up, so an episode far past its budget shows up there as a failed phase
    rather than as a long one.
    """
    sut_result = payload.get("sut_result")
    sut_result = sut_result if isinstance(sut_result, Mapping) else {}
    execution = sut_result.get("execution") if isinstance(sut_result.get("execution"), Mapping) else {}
    budget = execution.get("budget_seconds")
    used = execution.get("elapsed_seconds")
    numeric = isinstance(budget, (int, float)) and isinstance(used, (int, float))
    exhausted = sut_result.get("status") == "timeout"
    return {
        "granted_seconds": budget,
        "used_seconds": used,
        "remaining_seconds": round(budget - used, 3) if numeric else None,
        "used_ratio": round(used / budget, 4) if numeric and budget else None,
        # The two verdicts the docstring separates. Null rather than false when the
        # numbers to decide are not in the result.
        "exhausted": exhausted,
        "exceeded": (used > budget) if numeric else None,
        "overrun_seconds": round(max(0.0, used - budget), 3) if numeric else None,
        "sut_status": sut_result.get("status"),
        "invoke_sut_seconds": (payload.get("phase_durations") or {}).get("invoke_sut"),
    }


def timeline(payload: Mapping[str, Any], trace: Mapping[str, Any]) -> dict[str, Any]:
    """The episode in order: each turn of thinking, then the calls that turn issued.

    Turns claim their calls by position, so an operation no turn claims — a compensating
    rollback, or evidence a SUT staged before the model's first turn — is emitted where it
    happened rather than dropped.

    `unaccounted` is what the episode's elapsed time holds beyond thinking and tools:
    transport, the loop's own bookkeeping, and anything that happened between them. It is
    reported rather than distributed, because nothing here measured it.
    """
    sut_result = payload.get("sut_result")
    sut_result = sut_result if isinstance(sut_result, Mapping) else {}
    execution = sut_result.get("execution") if isinstance(sut_result.get("execution"), Mapping) else {}
    operations = [item if isinstance(item, Mapping) else {} for item in (sut_result.get("ani_operations") or [])]
    turns = [item for item in (execution.get("model_turns") or []) if isinstance(item, Mapping)]
    turns_source = "record (execution.model_turns)"
    if not turns:
        turns = inferred_turns(trace, operations)
        turns_source = ("trace (inferred: one turn per model message, operations matched to its tool "
                        "calls by name; no per-turn timing)" if turns else "none")

    steps: list[dict[str, Any]] = []
    emitted: set[int] = set()

    def emit_calls_through(last: int) -> None:
        for position in range(1, min(last, len(operations)) + 1):
            if position not in emitted:
                emitted.add(position)
                steps.append(call_step(position, operations[position - 1]))

    for turn in turns:
        positions = [int(item) for item in (turn.get("operations") or []) if isinstance(item, int)]
        # Anything that happened before this turn's first call belongs before the turn.
        if positions:
            emit_calls_through(min(positions) - 1)
        steps.append(thinking_step(turn, trace))
        if positions:
            emit_calls_through(max(positions))
    emit_calls_through(len(operations))
    for index, step in enumerate(steps, start=1):
        step["step"] = index

    timed = [turn for turn in turns if isinstance(turn.get("seconds"), (int, float))]
    thinking_seconds = round(sum(turn["seconds"] for turn in timed), 3) if timed else None
    tool_seconds = round(sum(
        operation.get("duration_seconds") or 0.0 for operation in operations
        if isinstance(operation.get("duration_seconds"), (int, float))), 3)
    episode_seconds = execution.get("elapsed_seconds")
    return {
        "trace": {key: trace[key] for key in ("path", "available", "reason") if key in trace},
        "budget": budget_block(payload),
        "turns": len(turns),
        "turns_source": turns_source,
        "seconds": {
            # None, not 0, when the subject recorded no turn timing (the baseline before
            # 2026-09-16): an unmeasured quantity must not read as an absent one.
            "thinking": thinking_seconds,
            "tools": tool_seconds,
            "episode": episode_seconds,
            "unaccounted": (
                round(episode_seconds - (thinking_seconds or 0.0) - tool_seconds, 3)
                if isinstance(episode_seconds, (int, float)) else None
            ),
        },
        "final_response": sut_result.get("final_response"),
        "steps": steps,
    }


def evaluation_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Oracle verdicts as a list of records rather than a mapping keyed by phase.

    The probes stay in `sut_result`-sized detail in the source; what a report needs is
    the verdict, the oracle that gave it, and how much it measured.
    """
    rows = []
    for phase, evaluation in (payload.get("evaluations") or {}).items():
        evaluation = evaluation if isinstance(evaluation, Mapping) else {}
        rows.append({
            "phase": phase,
            "oracle_id": evaluation.get("oracle_id"),
            "version": evaluation.get("version"),
            "passed": evaluation.get("passed"),
            "probe_count": len(evaluation.get("probes") or []),
        })
    return rows


def run_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The identifying fields, gathered from the three places they currently live in."""
    provenance = payload.get("provenance") or {}
    sut_result = payload.get("sut_result") or {}
    execution = sut_result.get("execution") if isinstance(sut_result.get("execution"), Mapping) else {}
    metrics = payload.get("metrics") or {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status"),
        "experiment_id": payload.get("experiment_id"),
        "scenario": dict(payload.get("scenario") or {}),
        "sut_identity": provenance.get("sut_identity"),
        "sut_version": provenance.get("sut_version"),
        "configured_model": provenance.get("configured_model"),
        "sut_reported_model": provenance.get("sut_reported_model"),
        "provider_reported_model": provenance.get("provider_reported_model"),
        "model_agreement": model_agreement(provenance),
        "sut_status": sut_result.get("status"),
        "sut_verified": sut_result.get("verified"),
        "final_summary": (sut_result.get("final_response") or {}).get("summary"),
        # Evaluation parameter 13 on this episode: the injected fault in words, the
        # subject's stated diagnosis, and the judge's verdict on the two.
        "diagnosis": (dict(metrics["diagnosis"])
                      if isinstance(metrics.get("diagnosis"), Mapping) else None),
        "success": metrics.get("success"),
        "budget_seconds": execution.get("budget_seconds"),
        "elapsed_seconds": execution.get("elapsed_seconds"),
        "budget_exhausted": sut_result.get("status") == "timeout",
        "trace_ref": execution.get("trace_ref"),
    }


def report_document(payload: Mapping[str, Any], source_path: Path, source_bytes: bytes,
                    *, trace: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The report itself: everything derived, plus the blocks it was derived from.

    The source is named and digested rather than only referenced, so a report can be
    matched back to the exact result it came from after either has been moved.
    """
    trace = trace if trace is not None else read_trace(
        ((payload.get("sut_result") or {}).get("execution") or {}).get("trace_ref"))
    steps = timeline(payload, trace)
    document = {
        "report_version": REPORT_VERSION,
        "source": {
            "path": str(source_path),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "schema_version": payload.get("schema_version"),
        },
        "run": run_summary(payload),
        "error": payload.get("error"),
        "evaluations": evaluation_rows(payload),
        "metrics": payload.get("metrics"),
        "operation_counts": operation_count_block(payload),
        "time_by_phase": time_by_phase(payload, steps),
        "tokens": tokens_block(payload),
        "validation": validation_block(payload),
        "evaluation_parameters": evaluation_parameter_facts(payload),
        "device_configurations": device_configuration_summary(payload),
        "phase_durations": payload.get("phase_durations"),
        "timeline": steps,
        "provenance": payload.get("provenance"),
        "sut_result": payload.get("sut_result"),
        "convergence_result": payload.get("convergence_result"),
        "cleanup_result": payload.get("cleanup_result"),
    }
    document["report_information"] = report_information(document)
    return document


# -- the "report information" items of the parameter sheet ----------------------------
#
# The shared sheet (Testbed parameters.xlsx, "report information") lists what a report
# has to say about one task. Most of it is already in the document above; the blocks
# below add the three it did not have (time split by phase, tokens, the validators'
# verdicts), the per-episode facts of the thirteen evaluation parameters, and an index
# that names where each sheet item lives in this document.

def time_by_phase(payload: Mapping[str, Any], steps: Mapping[str, Any]) -> dict[str, Any]:
    """Sheet item 6: the episode's seconds split into reflection, action and validation.

    Reflection is the model's own time (the turns). Action is what changed the network:
    device mutations. Reading is reported on its own rather than folded into either.
    Validation is the subject's public validations. Unaccounted is transport and the loop's bookkeeping, as in the
    timeline.
    """
    sut_result = payload.get("sut_result")
    sut_result = sut_result if isinstance(sut_result, Mapping) else {}
    execution = sut_result.get("execution") if isinstance(sut_result.get("execution"), Mapping) else {}
    operations = [item for item in (sut_result.get("ani_operations") or []) if isinstance(item, Mapping)]

    def seconds(predicate) -> float:
        return round(sum(float(item.get("duration_seconds") or 0.0) for item in operations
                         if isinstance(item.get("duration_seconds"), (int, float)) and predicate(item)), 3)

    thinking = steps["seconds"]["thinking"]
    reflection = float(thinking) if isinstance(thinking, (int, float)) else None
    action = seconds(lambda item: item.get("category") == "mutation")
    reading = seconds(lambda item: item.get("category") == "read")
    validation = seconds(lambda item: item.get("category") == "validation")
    episode = execution.get("elapsed_seconds")
    accounted = (reflection or 0.0) + action + reading + validation
    return {
        "reflection_seconds": round(reflection, 3) if reflection is not None else None,
        "action_seconds": action,
        "reading_seconds": reading,
        "validation_seconds": validation,
        "episode_seconds": episode,
        "unaccounted_seconds": (round(float(episode) - accounted, 3)
                                if isinstance(episode, (int, float)) else None),
        "note": ("the same clock as the timeline: reflection is the model's turns, the rest are the tool "
                 "calls' own durations; unaccounted is transport and the loop's bookkeeping"
                 + ("; reflection is null because this subject recorded no turn timing, so its share "
                    "sits in unaccounted" if reflection is None else "")),
    }


def tokens_block(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Sheet item 5: what the model consumed, as the subject counted it."""
    recorded = payload.get("operation_counts") or {}
    sut_result = payload.get("sut_result")
    sut_result = sut_result if isinstance(sut_result, Mapping) else {}
    execution = sut_result.get("execution") if isinstance(sut_result.get("execution"), Mapping) else {}
    calls = recorded.get("llm_calls") or execution.get("llm_calls")
    output = recorded.get("output_tokens")
    return {
        "input_tokens": recorded.get("input_tokens"),
        "output_tokens": output,
        "total_tokens": recorded.get("total_tokens"),
        "llm_calls": calls,
        "output_tokens_per_call": (round(output / calls, 1) if isinstance(output, (int, float)) and calls else None),
        "recorded": recorded.get("input_tokens") is not None or recorded.get("output_tokens") is not None,
        "note": "counted by the subject from the provider's usage fields; depends on the model's tokenizer",
    }


def validation_block(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Sheet item 9: what the validators found: the public validations the subject ran."""
    sut_result = payload.get("sut_result")
    sut_result = sut_result if isinstance(sut_result, Mapping) else {}
    operations = [item for item in (sut_result.get("ani_operations") or []) if isinstance(item, Mapping)]
    public = [item for item in operations if item.get("category") == "validation"]
    failed = [item for item in public if item.get("ok") is not True]
    final = sut_result.get("final_observation") if isinstance(sut_result.get("final_observation"), Mapping) else {}
    return {
        "public_validations": len(public),
        "public_validations_failed": len(failed),
        "last_public_validation_passed": final.get("passed") if final else None,
    }


def evaluation_parameter_facts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """How this one episode counts in the thirteen evaluation parameters.

    Derived by the same code that computes the cell-level sheet
    (scripts/evaluation_parameters.py), so a per-task report and the numerical report
    cannot disagree about an episode.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "evaluation_parameters", REPO_ROOT / "scripts" / "evaluation_parameters.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("evaluation_parameters", module)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        facts = module.episode_facts(payload)
    except Exception as exc:  # noqa: BLE001 - the report must render without the scorer
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "pass_rate": {"repair_passed": facts.repair_passed, "oracle_reached_a_verdict": facts.repair_judged},
        "tool_call_success_rate": {"accepted": facts.accepted, "calls": facts.actions},
        "repeat_action_rate": {"repeated": facts.repeats, "calls": facts.actions},
        "time_limit_rate": {"hit_time_limit": facts.hit_time_limit},
        "interaction_limit_rate": {"hit_interaction_limit": facts.hit_interaction_limit, "limits_set": facts.capped},
        "early_submission_rate": {"submitted": facts.submitted, "early_submission": facts.early_submission,
                                  "declared_completed": facts.declared_completed},
        "error_submission_rate": {"submitted_and_judged": bool(facts.submitted and facts.repair_judged),
                                  "wrong_submission": bool(facts.submitted and facts.repair_judged
                                                            and facts.repair_passed is not True)},
        "llm_found_problem_rate": {"fault_located": facts.fault_located,
                                   "change_reached_the_faulty_device": facts.fault_device_written,
                                   "edited_any_device": facts.edited_any_device},
        "false_positive_rate": {"no_fault_episode": facts.fault_applicable is False,
                                "made_false_positive": facts.made_false_positive},
        "ani_call_type_ratio": dict(facts.buckets),
    }


def device_configuration_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Sheet item 12 from the judge's side: every device's running configuration
    before and after the subject, as the judge snapshotted it (records from
    2026-09-16 on; `device_configurations` in the record points at the files)."""
    manifest = payload.get("device_configurations")
    if not isinstance(manifest, Mapping):
        return {"available": False, "reason": "the judge took no device snapshots for this record"}
    snapshots = {label: snapshot for label, snapshot in (manifest.get("snapshots") or {}).items()
                 if isinstance(snapshot, Mapping)}
    changes = manifest.get("changes") if isinstance(manifest.get("changes"), Mapping) else {}
    names = sorted({str(name) for snapshot in snapshots.values() for name in (snapshot.get("devices") or {})})
    rows = []
    for name in names:
        entries = {label: (snapshot.get("devices") or {}).get(name) for label, snapshot in snapshots.items()}
        entries = {label: entry for label, entry in entries.items() if isinstance(entry, Mapping)}
        fault = (changes.get("healthy_to_before_sut") or {}).get(name)
        sut = (changes.get("before_sut_to_after_sut") or {}).get(name)
        rows.append({
            "device": name,
            "kind": next((entry.get("kind") for entry in entries.values() if entry.get("kind")), None),
            "snapshots": {label: entry.get("ok") is True for label, entry in entries.items()},
            "changed_by_fault": fault is not None,
            "changed_by_sut": sut is not None,
            "fault_change": dict(fault) if isinstance(fault, Mapping) else None,
            "sut_change": dict(sut) if isinstance(sut, Mapping) else None,
        })
    return {
        "available": True,
        "directory": manifest.get("directory"),
        "snapshots_taken": sorted(snapshots),
        "snapshots_failed": sorted(label for label, snapshot in snapshots.items() if snapshot.get("ok") is not True),
        "changed_by_sut": [str(name) for name in (manifest.get("changed_by_sut") or [])],
        "devices": rows,
        "note": manifest.get("note"),
    }


#: The sheet's items, in its order, and where each one is read in this document.
REPORT_INFORMATION_ITEMS = (
    ("each ANI tool called by the agent, with the parameters",
     "timeline.steps[type=call] (tool, arguments, ok, error); sut_result.ani_operations"),
    ("whether each tool call succeeded", "timeline.steps[type=call].ok; operation_counts"),
    ("whether the agent ran out of time", "timeline.budget.exhausted, time_by_phase, run.budget_exhausted"),
    ("number of tokens used by the LLM", "tokens"),
    ("time, split into reflection, action and validation", "time_by_phase"),
    ("number of interactions with the ANI", "operation_counts (containerlab_*), evaluation_parameters.tool_call_success_rate.calls"),
    ("number of errors detected by the validators", "validation"),
    ("whether the task is a success", "run.success, evaluations, metrics"),
    ("information on the state of the network", "device_configurations (every device's running configuration healthy, before and "
                                                "after the subject, with what changed); evaluations (probes); "
                                                "files.devices when exported"),
    ("the model's reasoning", "timeline.steps[type=thinking].reasoning (from the trace)"),
)


def report_information(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    tokens = document.get("tokens") or {}
    validation = document.get("validation") or {}
    counts = document.get("operation_counts") or {}
    steps = (document.get("timeline") or {}).get("steps") or []
    calls = [step for step in steps if step.get("type") == "call"]
    values: list[Any] = [
        len(calls),
        f"{sum(1 for step in calls if step.get('ok'))} of {len(calls)} succeeded",
        (document.get("timeline") or {}).get("budget", {}).get("exhausted"),
        tokens.get("total_tokens") if tokens.get("recorded") else "not recorded by this subject",
        {key: value for key, value in (document.get("time_by_phase") or {}).items() if key.endswith("_seconds")},
        counts.get("containerlab_reads", 0) + counts.get("containerlab_mutations", 0) + counts.get("sut_validations", 0)
        if isinstance(counts.get("containerlab_reads"), int) else None,
        validation.get("public_validations_failed"),
        (document.get("run") or {}).get("success"),
        (f"{len(devices['changed_by_sut'])} device(s) changed by the subject: {', '.join(devices['changed_by_sut']) or 'none'}"
         if (devices := document.get("device_configurations") or {}).get("available")
         else "no device snapshots in this record; see evaluations"),
        f"{sum(1 for step in steps if step.get('type') == 'thinking' and step.get('reasoning'))} turns carry a reasoning span",
    ]
    return [{"item": item, "where": where, "value": value}
            for (item, where), value in zip(REPORT_INFORMATION_ITEMS, values)]


# -- html rendering, from the same document -------------------------------------


def escaped(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def json_block(value: Any) -> str:
    rendered = json.dumps(value, indent=2, sort_keys=True, default=str)
    return f"<pre>{html.escape(rendered)}</pre>"


def mapping_table(values: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><th>{escaped(key)}</th><td>{escaped(value)}</td></tr>"
        for key, value in values.items()
    )
    return f"<table><tbody>{rows}</tbody></table>"


def rows_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{escaped(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def seconds_cell(value: Any) -> str:
    return escaped(f"{value:.3f}" if isinstance(value, (int, float)) else value)


def arguments_cell(call: Mapping[str, Any]) -> str:
    """The parameters the model chose for this call, verbatim.

    Null and empty are shown differently on purpose: a tool that takes none is not a
    call whose arguments were never written down.
    """
    arguments = call.get("arguments")
    if arguments is None:
        return "<em>not recorded</em>"
    if arguments in ({}, []):
        return "<em>none</em>"
    rendered = json.dumps(arguments, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    return f'<pre class="args">{html.escape(rendered)}</pre>'


def operation_counts_html(counts: Mapping[str, Any]) -> str:
    """The derived counts, then the judge's own underneath."""
    display = {key: value for key, value in counts.items() if key != "recorded"}
    return ("<section><h2>Operation counts</h2>"
            + mapping_table(display)
            + "<h3>As recorded by the judge</h3>"
            + mapping_table(counts.get("recorded") or {})
            + "</section>")


#: The verdict a reader looks for first. Three states, not two: a run that never reached
#: a verdict has not failed the test, and colouring it red would say it did.
VERDICT_STATES = {
    "pass": ("✓", "PASS"),
    "fail": ("✗", "FAIL"),
    "error": ("⚠", "RUN ERROR"),
}


def run_verdict(document: Mapping[str, Any]) -> dict[str, Any]:
    """Passed, failed, or never concluded — and what decided it.

    `metrics.success` is the test verdict: the environment phases, convergence, and the
    SUT's own completion and verification, all four. A lifecycle failure is reported
    apart, because an episode whose testbed never deployed says nothing about the agent.
    """
    metrics = document.get("metrics") or {}
    if document["run"].get("status") == "failed":
        state = "error"
    else:
        state = "pass" if metrics.get("success") is True else "fail"
    symbol, label = VERDICT_STATES[state]
    error = document.get("error") or {}
    return {
        "state": state,
        "symbol": symbol,
        "label": label,
        "detail": (
            f"the run failed in {error.get('phase')}: {error.get('message')}"
            if state == "error" else None
        ),
        # What `success` was made of, so a failure says which part gave way.
        "conditions": {
            "environment (healthy, degradation, repair, preservation)":
                metrics.get("environment_success"),
            "converged": metrics.get("converged"),
            "SUT completed": metrics.get("sut_completed"),
            "SUT verified": metrics.get("sut_verified"),
        },
    }


def mark(value: Any, yes: str = "passed", no: str = "failed", unknown: str = "unknown") -> str:
    """A verdict as a symbol and a word.

    Never the symbol alone: colour and glyph are the fast read, the word is the one that
    survives a black-and-white print, a screen reader and a copy-paste into a mail.
    """
    if value is True:
        return f'<span class="mark ok">✓</span> {escaped(yes)}'
    if value is False:
        return f'<span class="mark bad">✗</span> {escaped(no)}'
    return f'<span class="mark unknown">–</span> {escaped(unknown)}'


def html_mapping_table(values: Mapping[str, str]) -> str:
    """`mapping_table` for values that are already HTML fragments."""
    rows = "".join(f"<tr><th>{escaped(key)}</th><td>{value}</td></tr>" for key, value in values.items())
    return f"<table><tbody>{rows}</tbody></table>"


def seconds_text(value: Any) -> str | None:
    return f"{value:.3f} s" if isinstance(value, (int, float)) else None


def budget_verdict(budget: Mapping[str, Any]) -> str:
    """The answer to \"did it run out of time?\", in words.

    Exhausted is said first: an episode cut off at the deadline is the case a reader is
    looking for, and it stays true whether or not the arithmetic overran by a second.
    """
    if budget.get("exhausted"):
        return ('<span class="mark bad">✗</span> budget exhausted - the episode was '
                f"cut off at {seconds_text(budget['granted_seconds'])}")
    if budget.get("exceeded"):
        return ('<span class="mark warn">⚠</span> budget exceeded by '
                f"{seconds_text(budget['overrun_seconds'])} - a turn or a call already "
                "running at the deadline was waited for")
    if budget.get("exceeded") is None:
        return ('<span class="mark unknown">–</span> unknown - the result carries '
                "no budget or no elapsed time")
    return '<span class="mark ok">✓</span> within budget'


def thinking_cell(step: Mapping[str, Any]) -> str:
    """What the model said on that turn, reasoning first when it came back apart."""
    blocks = []
    if step.get("reasoning"):
        blocks.append("<p><em>reasoning</em></p>"
                      f'<pre class="args">{html.escape(str(step["reasoning"]))}</pre>')
    if step.get("text"):
        blocks.append(f'<pre class="args">{html.escape(str(step["text"]))}</pre>')
    if blocks:
        return "".join(blocks)
    chars = step.get("content_chars")
    if chars:
        # The turn produced prose the trace did not keep. Saying how much there was is
        # not the same as saying there was none.
        return f"<em>{escaped(chars)} characters, not in the trace</em>"
    return "<em>no text</em>"


def diagnosis_html(block: Mapping[str, Any] | None) -> str:
    """Parameter 13 on this episode, readable without the JSON: what was injected,
    what the subject said, and whether the judge found them to be the same fault."""
    if not isinstance(block, Mapping):
        return ""
    found = block.get("found")
    verdict = mark(found, yes="found the problem", no="did not find the problem",
                   unknown=str(block.get("reason") or "not judged"))
    judge = block.get("judge") if isinstance(block.get("judge"), Mapping) else {}
    score = block.get("score")
    rows = {
        "verdict": verdict,
        "injected fault": escaped(block.get("reference")),
        "stated diagnosis": escaped(block.get("hypothesis")),
        "diagnosis source": escaped(block.get("hypothesis_source")),
        "ParaPLUIE score (log p(Yes) - log p(No))": escaped(
            f"{score:+.3f}" if isinstance(score, (int, float)) else score),
        "judge": escaped(
            f"{judge.get('model')} at {judge.get('api_base')} ({judge.get('prompt_id')}, {judge.get('method')})"
            if judge else None),
        "reason": escaped(block.get("reason")),
    }
    return ("<section><h2>Diagnosis (LLM found the problem)</h2>"
            + html_mapping_table(rows) + "</section>")


def timeline_html(steps: Mapping[str, Any]) -> str:
    """The episode in order, thinking and calls in the same table.

    One table rather than two: the point of the timeline is where a thought sits relative
    to the calls around it, which two tables side by side would lose.
    """
    rows = []
    for step in steps["steps"]:
        if step["type"] == "thinking":
            label = f"turn {escaped(step.get('turn'))}"
            what = escaped(step.get("kind"))
            detail = ", ".join(step.get("tool_calls") or [])
            rows.append([
                escaped(step["step"]), "thinking",
                f"{label} · {what}" + (f" · {escaped(detail)}" if detail else ""),
                seconds_cell(step.get("seconds")), thinking_cell(step),
                escaped(step.get("error")),
            ])
        else:
            rows.append([
                escaped(step["step"]), "call",
                f"<code>{escaped(step['tool'])}</code> · {escaped(step.get('category'))}",
                seconds_cell(step.get("seconds")),
                mark(step["ok"], "ok", "failed") + " " + arguments_cell(step),
                escaped(step.get("error")),
            ])
    seconds = steps["seconds"]
    budget = steps["budget"]
    summary = {
        "granted": seconds_text(budget["granted_seconds"]),
        "used": seconds_text(budget["used_seconds"]),
        "remaining": seconds_text(budget["remaining_seconds"]),
        "budget verdict": budget_verdict(budget),
        "turns": steps["turns"],
        "thinking": seconds_text(seconds["thinking"]),
        "tools": seconds_text(seconds["tools"]),
        "unaccounted": seconds_text(seconds["unaccounted"]),
        "invoke_sut (judge side)": seconds_text(budget["invoke_sut_seconds"]),
        "trace": steps["trace"].get("path") if steps["trace"].get("available") else (
            steps["trace"].get("reason") or "unavailable"),
    }
    conclusion = ""
    if steps.get("final_response"):
        conclusion = ("<h3>The model's conclusion</h3>"
                      + mapping_table(steps["final_response"]))
    # The verdict carries its own mark, so the table must not escape it.
    return ("<section><h2>Timeline</h2>"
            + html_mapping_table({
                key: value if key == "budget verdict" else escaped(value)
                for key, value in summary.items()})
            + conclusion
            + "<h3>Thinking and calls, in order</h3>"
            + rows_table(["#", "Step", "What", "Seconds", "Detail", "Error"], rows)
            + "</section>")


def device_configurations_html(block: Mapping[str, Any] | None, files: Mapping[str, Any] | None = None) -> str:
    """Every device: which snapshots the judge has and what the fault and the subject changed.
    When the exporter placed the files next to the report (`files.devices`), that link is
    shown instead of the judge's own directory."""
    if not isinstance(block, Mapping):
        return ""
    if not block.get("available"):
        return f"<section><h2>Device configurations</h2><p>{escaped(str(block.get('reason') or 'not available'))}</p></section>"
    rows = []
    for row in block.get("devices") or []:
        taken = ", ".join(f"{label}{'' if ok else ' (failed)'}" for label, ok in (row.get("snapshots") or {}).items())
        fault = row.get("fault_change") or {}
        sut = row.get("sut_change") or {}
        rows.append([
            escaped(row.get("device")), escaped(row.get("kind")), escaped(taken),
            escaped(f"yes (+{fault.get('lines_added')} -{fault.get('lines_removed')})" if row.get("changed_by_fault") else "no"),
            escaped(f"yes (+{sut.get('lines_added')} -{sut.get('lines_removed')})" if row.get("changed_by_sut") else "no"),
        ])
    failed = block.get("snapshots_failed") or []
    note = (f"<p>Snapshots that failed: {escaped(', '.join(failed))}.</p>" if failed else "")
    exported = files.get("devices") if isinstance(files, Mapping) else None
    where = (f'<a href="{escaped(str(exported))}">{escaped(str(exported))}</a>' if exported
             else escaped(str(block.get("directory"))))
    return (f"<section><h2>Device configurations</h2>"
            f"<p>Files under {where} (one text file per device and moment, "
            f"a .diff per device that changed).</p>{note}"
            + rows_table(["Device", "Kind", "Snapshots", "Changed by the fault", "Changed by the subject"], rows)
            + "</section>")


def files_html(files: Mapping[str, Any] | None) -> str:
    """Links to the files the report was exported next to (set by the exporter)."""
    if not isinstance(files, Mapping):
        return ""
    items = "".join(f'<li><a href="{escaped(str(value))}">{escaped(key)}</a>: {escaped(str(value))}</li>'
                    for key, value in files.items() if value and key != "note")
    return f"<section><h2>Files</h2><ul>{items}</ul></section>" if items else ""


def model_agreement_html(run: Mapping[str, Any]) -> str:
    """The run table's model row: the name, and whether anything confirmed it.

    A report that shows the declared name alone cannot be read for what actually
    served the episode, which is the whole point of keeping three names.
    """
    verdict = run.get("model_agreement") or model_agreement(run)
    known = verdict.get("known") or {}
    agree = verdict.get("agree")
    if agree is True:
        confirmations = [role for role in ("subject", "provider") if role in known]
        name = known.get("declared") or next(iter(known.values()))
        detail = ("confirmed by " + " and ".join(f"the {role}" for role in confirmations)
                  if confirmations else "the only name recorded")
        return f"{escaped(name)} <small>— {escaped(detail)}</small>"
    if agree is False:
        parts = " · ".join(f"{role} {escaped(name)}" for role, name in known.items())
        return (f'<strong style="color:#a4161a">✗ DISAGREEMENT</strong> — {parts}'
                "<br><small>the provider's name is what answered; the others are what was "
                "asked for</small>")
    name = next(iter(known.values()), None)
    missing = " or ".join(verdict.get("missing") or [])
    return (f"{escaped(name)} <small>— unconfirmed: no {escaped(missing)} model was "
            "recorded to check it against</small>"
            if name else "<small>no model recorded</small>")


def model_agreement(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the three model names agree, and which of them were known.

    `configured_model` is what the judge was told the campaign runs, `sut_reported_model`
    what the subject says it asked for, `provider_reported_model` what answered -- taken
    from the response, not from the request, so it is the only one that is evidence
    rather than intent. A name that is absent contradicts nothing: a judge that was not
    told has nothing to disagree with, and a provider that reported no model said
    nothing. `agree` is null when fewer than two names are known and there is nothing to
    check.
    """
    names = {
        "declared": provenance.get("configured_model"),
        "subject": provenance.get("sut_reported_model"),
        "provider": provenance.get("provider_reported_model"),
    }
    known = {role: str(name) for role, name in names.items() if name}
    return {
        "agree": None if len(known) < 2 else len(set(known.values())) == 1,
        "known": known,
        "missing": sorted(role for role in names if role not in known),
    }


def subject_activity(document: Mapping[str, Any]) -> dict[str, str]:
    """Two rows a reader cannot mistake for one another: what the subject called, and
    what it changed on the lab.

    They are different counts of different things, and a report that shows only one of
    them reads as if it hid the other. One `update_object` naming two devices is one
    call and two changes, so neither number can be derived from the other.
    """
    counts = document.get("operation_counts") or {}
    sut_result = document.get("sut_result") or {}
    operations = sut_result.get("ani_operations")
    total = len(operations) if isinstance(operations, list) else None
    reads = counts.get("containerlab_reads") or 0
    mutations = counts.get("containerlab_mutations") or 0
    validations = counts.get("sut_validations") or 0
    parts = [f"{reads} read", f"{mutations} configuration change", f"{validations} validation"]
    parts = [part if part.startswith("1 ") else part + "s" for part in parts]
    counted = reads + mutations + validations
    if total is not None and total > counted:
        parts.append(f"{total - counted} other")
    calls = f"{total if total is not None else counted} — " + ", ".join(parts)

    actions = device_changes_of(sut_result)
    if not isinstance(actions, list):
        changes = "not recorded"
    elif not actions:
        changes = "0 — the subject left every device as it found it"
    else:
        accepted = sum(1 for action in actions
                       if isinstance(action, Mapping)
                       and (action.get("result") or {}).get("ok") is True)
        devices = sorted({str((action.get("action") or {}).get("machine"))
                          for action in actions if isinstance(action, Mapping)})
        changes = (f"{len(actions)} on {', '.join(devices)}"
                   f" ({accepted} of {len(actions)} accepted by the ANI)")
    return {"ani calls": escaped(calls), "device changes": escaped(changes)}


#: The page's look, shared with every page built from these reports (the campaign page
#: of scripts/generate_campaign_report.py), so an episode reads the same in either.
STYLE = """    :root { font-family: system-ui, sans-serif; color: #17202a; background: #f5f7fa; }
    body { margin: 0; } header { padding: 2rem; color: white; background: #17202a; }
    main { max-width: 1100px; margin: auto; padding: 1.5rem; }
    section { background: white; border: 1px solid #dbe2ea; border-radius: .5rem; margin: 1rem 0; padding: 1rem; }
    table { width: 100%; border-collapse: collapse; } th, td { padding: .55rem; border-bottom: 1px solid #e5e9ef; text-align: left; vertical-align: top; }
    pre { overflow: auto; padding: 1rem; color: #e5e7eb; background: #111827; border-radius: .35rem; }
    code { font-family: ui-monospace, monospace; }
    h3 { margin: 1.4rem 0 .4rem; font-size: 1rem; color: #3c4a5a; }
    /* A model's arguments can be one word or a full expected_current term list, so the
       cell scrolls rather than pushing the ledger off the page. */
    pre.args { margin: 0; padding: .6rem; max-width: 46ch; max-height: 16rem; font-size: .78rem; white-space: pre-wrap; overflow-wrap: anywhere; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
    header h1 { margin: 0 0 .35rem; } header p { margin: 0; opacity: .8; }
    /* The one thing to be readable across the room. Colour is the fast signal, the word
       is what survives a greyscale print or a screen reader. */
    .verdict { display: flex; align-items: center; gap: .6rem; padding: .7rem 1.3rem; border-radius: .5rem; font-size: 1.2rem; font-weight: 700; letter-spacing: .03em; }
    .verdict .glyph { font-size: 1.7rem; line-height: 1; }
    .verdict.pass { background: #0f7b3f; } .verdict.fail { background: #b42318; }
    .verdict.error { background: #b45309; }
    .mark { font-weight: 700; } .mark.ok { color: #0f7b3f; } .mark.bad { color: #b42318; }
    .mark.warn { color: #b45309; } .mark.unknown { color: #6b7280; }
"""


def render_html(document: Mapping[str, Any]) -> str:
    run = document["run"]
    verdict = run_verdict(document)
    title = f"{run['scenario'].get('id')} · {run['run_id']}"
    run_table = html_mapping_table({
        "verdict": (f'<strong>{verdict["symbol"]} {escaped(verdict["label"])}</strong>'
                    + (f" — {escaped(verdict['detail'])}" if verdict["detail"] else "")),
        **{key: mark(value) for key, value in verdict["conditions"].items()},
        "source": escaped(document["source"]["path"]),
        "status": escaped(run["status"]),
        "created_at": escaped(run["created_at"]),
        "experiment": escaped(run["experiment_id"]),
        "scenario": escaped(run["scenario"].get("id")),
        "scenario_version": escaped(run["scenario"].get("version")),
        "domain": escaped(run["scenario"].get("domain")),
        "sut": escaped(run["sut_identity"]),
        "model": model_agreement_html(run),
        **subject_activity(document),
        "conclusion": escaped(run.get("final_summary")),
    })
    evaluations = rows_table(
        ["Phase", "Oracle", "Version", "Passed", "Probes"],
        [[escaped(row["phase"]), escaped(row["oracle_id"]), escaped(row["version"]),
          mark(row["passed"]), escaped(row["probe_count"])]
         for row in document["evaluations"]],
    )
    durations = mapping_table({
        key: f"{value:.6f} s" for key, value in (document["phase_durations"] or {}).items()
    })
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{verdict["symbol"]} {escaped(title)}</title>
  <style>
{STYLE}  </style>
</head>
<body>
<header>
  <div><h1>IBN benchmark result</h1><p>{escaped(title)}</p></div>
  <div class="verdict {verdict["state"]}"><span class="glyph">{verdict["symbol"]}</span>{escaped(verdict["label"])}</div>
</header>
<main>
  <section><h2>Run</h2>{run_table}</section>
  <section><h2>Error</h2>{json_block(document["error"])}</section>
  <section><h2>Oracle evaluations</h2>{evaluations}</section>
  <section><h2>Metrics</h2>{json_block(document["metrics"])}</section>
  {operation_counts_html(document["operation_counts"])}
  {files_html(document.get("files"))}
  <section><h2>Report information (parameter sheet items)</h2>{rows_table(["Item", "Value", "Where in this report"], [[escaped(row["item"]), escaped(row["value"]), escaped(row["where"])] for row in document.get("report_information", [])])}</section>
  <section><h2>Time by phase</h2>{mapping_table({key: (f"{value:.3f} s" if isinstance(value, (int, float)) else str(value)) for key, value in (document.get("time_by_phase") or {}).items() if key != "note"})}</section>
  <section><h2>Tokens</h2>{mapping_table({key: str(value) for key, value in (document.get("tokens") or {}).items() if key != "note"})}</section>
  <section><h2>Validation</h2>{json_block(document.get("validation"))}</section>
  {device_configurations_html(document.get("device_configurations"), document.get("files"))}
  <section><h2>Evaluation parameters, this episode</h2>{json_block(document.get("evaluation_parameters"))}</section>
  {timeline_html(document["timeline"])}
  {diagnosis_html(run.get("diagnosis"))}
  <section><h2>Phase durations</h2>{durations}</section>
  <section><h2>Provenance</h2>{json_block(document["provenance"])}</section>
  <section><h2>SUT result</h2>{json_block(document["sut_result"])}</section>
  <section><h2>Cleanup</h2>{json_block(document["cleanup_result"])}</section>
</main>
</body>
</html>
"""


def render_json(document: Mapping[str, Any]) -> str:
    # Not sorted: the top-level order is the reading order, and `calls` must stay in the
    # order the episode made them.
    return json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n"


RENDERERS = {"json": render_json, "html": render_html}


def output_base(source: Path, output: str | None) -> Path:
    """The name every output of this report is built from.

    The two renderings are one report, so they share a base name and differ only in the
    extension appended — `<run>.report.json`, `<run>.report.html`. An explicit `--output`
    names that base; an extension on it is dropped rather than honoured, because it can
    only name one of them.
    """
    base = Path(output) if output else DEFAULT_OUTPUT_DIR / f"{source.stem}.report"
    if base.suffix.lower() in {".json", ".html"}:
        base = base.with_suffix("")
    return base


def output_paths(base: Path, formats: tuple[str, ...]) -> dict[str, Path]:
    # Appended rather than set with `with_suffix`, which would eat the `.report` part.
    return {fmt: Path(f"{base}.{fmt}") for fmt in formats}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a processable report from one unified benchmark result JSON file.")
    parser.add_argument("result_json")
    parser.add_argument(
        "-o", "--output",
        help="base path for the report; the format extensions are appended to it.")
    parser.add_argument(
        "-f", "--format", choices=("both", "json", "html"), default="both",
        help="both (default): the report as data and as a page. json: data only. html: page only.")
    parser.add_argument(
        "--no-trace", action="store_true",
        help="do not read the episode's trace file, so the timeline carries timings without the model's text.")
    return parser.parse_args()


def write_report(source: Path, *, output: str | Path | None = None,
                 formats: tuple[str, ...] = ("json", "html"),
                 read_trace: bool = True) -> list[Path]:
    """Derive the report from one result file and write it; return the files written.

    The command line below and `benchmarks/run.py`, which reports every episode as soon
    as its result is written, both come through here.
    """
    source_bytes = source.read_bytes()
    payload = load_payload(source_bytes, source)
    trace = (None if read_trace
             else {"path": None, "available": False, "reason": "reading the trace was disabled"})
    document = report_document(payload, source, source_bytes, trace=trace)
    base = output_base(source, None if output is None else str(output))
    written = []
    for fmt, path in output_paths(base, formats).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(RENDERERS[fmt](document), encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    args = parse_args()
    formats = ("json", "html") if args.format == "both" else (args.format,)
    for path in write_report(Path(args.result_json), output=args.output, formats=formats,
                             read_trace=not args.no_trace):
        print(f"wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
