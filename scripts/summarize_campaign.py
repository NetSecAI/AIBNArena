#!/usr/bin/env python3
"""One row per experimental cell, from the result files a campaign directory holds.

A campaign directory is a tree of runs, each with a `results/*.json` the judge wrote.
Reading those one at a time answers "what did this episode do"; the question a
campaign asks is "how did this subject do on this scenario under this condition",
which needs the episodes grouped before any number is taken. This script does the
grouping and nothing else clever.

The cell is (subject, scenario, condition). The subject is `provenance.sut_identity`
and the scenario is `scenario.id`. The condition is derived from
`provenance.model_parameters` alone, never from the run directory or the file name,
so the same parameters land in the same cell whichever campaign they ran in; the
directory layout records when a run happened, not what it was.

Why an episode ended is not one field in an old result: the judge records its own
failure under `error.phase`, the subject reports `status` and an error string, and
the wall clock sits in `execution`. `termination_cause` reads them in a fixed order
and prefers `execution.termination.cause` whenever a result carries it, so a result
written after the subject started naming its own end is read from that name.

    .venv/bin/python scripts/summarize_campaign.py runs/20260905
    .venv/bin/python scripts/summarize_campaign.py runs/20260905 --json
    .venv/bin/python scripts/summarize_campaign.py runs/20260905 --by subject,condition
    .venv/bin/python scripts/summarize_campaign.py runs/e1 --by subject,intent
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: Every field a cell may be keyed by. `intent` is the wording of the task the
#: subject was handed (provenance.intent_variant: low, medium, high), a property of
#: the episode's input rather than of the subject's parameters, which is why it is
#: not a CONDITION_FIELD: the condition says how the subject was run, the intent
#: says what it was asked.
GROUP_FIELDS = ("subject", "scenario", "intent", "condition")
#: The cell as it always was. A directory that mixes records written before the
#: judge named the wording and after would otherwise split one scenario into an
#: "unrecorded" and a "medium" cell by default.
DEFAULT_BY = ("subject", "scenario", "condition")

# The parameters that make a condition, in the order the label renders them.
# Anything the subject records outside this list (temperature, recursion_limit,
# thinking_style, ...) is either derived from one of these or not an experiment
# variable, and letting it into the key would split one condition into several
# cells whenever a subject version adds a field.
CONDITION_FIELDS = (
    "model",
    "prompt_variant",
    "enable_thinking",
    "thinking_mechanism",
    "thinking_request",
    "ani_call_limit",
    "max_tokens",
    "max_execution_seconds",
    "min_retry_tokens",
    "tool_result_chars",
    "context_budget_chars",
    "max_consecutive_rejections",
)

# The values a knob has when a campaign does not vary it. These are not the
# dataclass defaults in sut/common/agent_config.py (400 tokens, 300 seconds): no
# campaign has run with those, every run file sets 8000 and 1800 through the
# environment, and labelling every episode "tok8000+1800s" would hide the one
# capcheck run that actually shortened the budget. A knob at its baseline is left
# out of the label; the full parameter set is still in the cell key and the JSON.
BASELINE = {
    "max_tokens": 8000,
    "max_execution_seconds": 1800.0,
}

# Fraction of the budget after which a provider Timeout is the budget, not the
# provider. The backend asks the provider for min(remaining, max_execution_seconds),
# so a request that starts near the deadline times out at the deadline and litellm
# reports it as its own Timeout; an episode that stops on that error with the clock
# at the budget was stopped by the budget. Below the fraction the same error is a
# provider stall and stays a model error.
DEADLINE_FRACTION = 0.98

REPEAT_SUFFIX = re.compile(r"-r(\d+)$")


def intent_of(provenance: Mapping[str, Any]) -> str:
    """The wording the subject was handed, or why it cannot be named.

    Key absent: the record predates the judge writing intent_variant. Null: the
    scenario declares one wording and the compiler names none (compiler.py returns
    no task_variant for it). The two are different facts and read differently.
    """
    if "intent_variant" not in provenance:
        return "unrecorded"
    value = provenance.get("intent_variant")
    return str(value) if value not in (None, "") else "unnamed"


class Result:
    """One result file, with the fields the summary reads pulled out once."""

    def __init__(self, path: Path, document: Mapping[str, Any]) -> None:
        self.path = path
        self.document = document
        provenance = document.get("provenance") or {}
        scenario = document.get("scenario") or {}
        self.run_id = document.get("run_id")
        self.experiment_id = document.get("experiment_id")
        self.subject = provenance.get("sut_identity") or "unknown"
        self.scenario = scenario.get("id") or "unknown"
        self.intent = intent_of(provenance)
        self.condition = condition_of(provenance.get("model_parameters"))
        self.repeat = repeat_index(self.experiment_id)
        self.judge_status = document.get("status")
        self.termination = termination_cause(document)
        sut = document.get("sut_result") or {}
        # A judge-side failure before the subject ran leaves `sut_result` as an empty
        # object. Such an episode is not one the subject failed to repair, and must
        # not sit in the denominator of a rate about what the subject did.
        self.scored = bool(sut)
        self.execution = sut.get("execution") or {}
        evaluations = document.get("evaluations") or {}
        self.repair_passed = (evaluations.get("repair") or {}).get("passed")
        counts = document.get("operation_counts") or {}
        tokens = self.execution.get("token_usage") or {}
        turns = self.execution.get("model_turns")
        self.measures = {
            "model_turns": len(turns) if isinstance(turns, list) else None,
            "output_tokens": tokens.get("output_tokens"),
            "elapsed_seconds": self.execution.get("elapsed_seconds"),
            "ani_mutations": counts.get("ani_mutations"),
            "successful_mutations": counts.get("successful_mutations"),
        }
        git = provenance.get("git") or {}
        self.git_commit = git.get("commit")
        self.git_dirty = bool(git.get("dirty"))
        self.configured_model = provenance.get("configured_model")
        self.sut_reported_model = provenance.get("sut_reported_model")
        self.provider_reported_model = provenance.get("provider_reported_model")
        self.oracle_versions = json.dumps(provenance.get("oracle_versions"), sort_keys=True)
        # A judge failure before the subject ran leaves no execution at all; that
        # is not a missing trace, there was nothing to trace.
        self.trace_missing = bool(self.execution) and self.execution.get("trace_ref") is None


class Condition:
    def __init__(self, parameters: Mapping[str, Any]) -> None:
        self.parameters = {name: parameters[name] for name in CONDITION_FIELDS if name in parameters}
        # Results written before a subject recorded its prompt variant (every run
        # before 2f54f9a) carry no prompt_variant key; they ran the shipped prompt,
        # and keying on the raw dict would put them in a different cell from a
        # later run of the same prompt that does say "default". The gap is kept
        # visible through the unrecorded_prompt_variant flag on the cell.
        self.prompt_variant_recorded = "prompt_variant" in parameters
        # Every condition field, present or not. "key absent" and "key present with
        # value null" are the same condition -- both mean the knob was not turned --
        # but they produce different JSON, so a result written before a subject
        # version added a knob keyed differently from one written after it and the
        # same cell split in two. `self.parameters` still holds only what was
        # recorded: the label and the unrecorded_* flags are about that, not this.
        keyed = {name: self.parameters.get(name) for name in CONDITION_FIELDS}
        if keyed.get("prompt_variant") is None:
            keyed["prompt_variant"] = "default"
        if keyed.get("thinking_mechanism") is None:
            keyed["thinking_mechanism"] = "unset"
        if not keyed.get("thinking_request"):
            keyed["thinking_request"] = {}
        self.key = json.dumps(keyed, sort_keys=True, default=str) if parameters else ""
        self.model = self.parameters.get("model")
        self.label = condition_label(self.parameters)


def condition_of(parameters: Mapping[str, Any] | None) -> Condition:
    return Condition(parameters or {})


def condition_label(parameters: Mapping[str, Any]) -> str:
    """Render the knobs a run turned as a short name, "default" when it turned none.

    The model is left out: it is a column of its own, and a label that had to
    carry a provider path would not be short. Two conditions that differ only in
    the model have the same label and different keys, which the table shows by
    the model column.
    """
    if not parameters:
        return "unrecorded"
    tokens: list[str] = []
    variant = parameters.get("prompt_variant")
    if variant not in (None, "default"):
        tokens.append(f"p-{variant}")
    tokens.extend(thinking_tokens(parameters))
    if parameters.get("ani_call_limit") is not None:
        tokens.append(f"ani{parameters['ani_call_limit']}")
    max_tokens = parameters.get("max_tokens")
    if max_tokens is not None and max_tokens != BASELINE["max_tokens"]:
        tokens.append(f"tok{max_tokens}")
    seconds = parameters.get("max_execution_seconds")
    if seconds is not None and float(seconds) != BASELINE["max_execution_seconds"]:
        tokens.append(f"{seconds:g}s")
    for name, prefix in (
        ("min_retry_tokens", "retry"),
        ("tool_result_chars", "trc"),
        ("context_budget_chars", "ctx"),
    ):
        if parameters.get(name) is not None:
            tokens.append(f"{prefix}{parameters[name]}")
    return "+".join(tokens) if tokens else "default"


def thinking_tokens(parameters: Mapping[str, Any]) -> list[str]:
    enabled = parameters.get("enable_thinking")
    mechanism = parameters.get("thinking_mechanism")
    request = parameters.get("thinking_request") or {}
    tokens: list[str] = []
    if enabled is not None:
        token = "thinkon" if enabled else "thinkoff"
        # chat_template is how enable_thinking reaches a Qwen model; naming it
        # would say nothing the on/off token does not. Any other mechanism is
        # a different experiment and is named.
        if mechanism not in (None, "unset", "chat_template"):
            token += f"-{mechanism}"
        tokens.append(token)
        implied = {"extra_body": {"chat_template_kwargs": {"enable_thinking": bool(enabled)}}}
        if request and request != implied:
            tokens.append(f"req-{digest(request)}")
    else:
        if mechanism not in (None, "unset"):
            tokens.append(f"think-{mechanism}")
        if request:
            tokens.append(f"req-{digest(request)}")
    return tokens


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:6]


def repeat_index(experiment_id: Any) -> int | None:
    if not isinstance(experiment_id, str):
        return None
    match = REPEAT_SUFFIX.search(experiment_id)
    return int(match.group(1)) if match else None


def termination_cause(document: Mapping[str, Any]) -> str:
    sut = document.get("sut_result") or {}
    execution = sut.get("execution") or {}
    termination = execution.get("termination") or {}
    if isinstance(termination, Mapping) and termination.get("cause"):
        return str(termination["cause"])
    judge_error = document.get("error")
    if isinstance(judge_error, Mapping) and judge_error.get("phase"):
        return f"judge_{judge_error['phase']}"
    status = sut.get("status")
    if status == "timeout":
        return "budget"
    error = sut.get("error")
    error = error if isinstance(error, str) else ""
    if "ContextWindowExceeded" in error:
        return "context_window"
    # The clock, not the wording. Requiring "Timeout" in the error meant a subject
    # that says it differently -- sut/langchain_agent says "execution budget
    # exceeded" -- fell past every branch to "unclassified", and an episode the wall
    # clock ended was then counted as one it did not. `at_deadline` is the fact;
    # `context_window` stays ahead of it because running out of context near the
    # deadline is still a context failure.
    if at_deadline(execution):
        return "budget"
    turns = execution.get("model_turns") or []
    if turns and isinstance(turns[-1], Mapping) and turns[-1].get("kind") == "error":
        return "model_error"
    if status == "failed" and sut.get("final_response"):
        return "own_conclusion"
    if status == "completed":
        return "completed"
    return "unclassified"


def at_deadline(execution: Mapping[str, Any]) -> bool:
    elapsed = execution.get("elapsed_seconds")
    budget = execution.get("budget_seconds")
    if not isinstance(elapsed, (int, float)) or not isinstance(budget, (int, float)) or budget <= 0:
        return False
    return elapsed >= DEADLINE_FRACTION * budget


def load_results(runs_dir: Path, paths: Iterable[Path] | None = None
                 ) -> tuple[list[Result], list[dict[str, str]]]:
    """Every results/*.json under the directory, and the files that would not parse.

    A campaign directory is written while it runs; a file the judge is still
    writing, or one a crashed run left truncated, must not take the table down
    with it. It is listed by name and the rest is summarised.

    `paths` names the files instead, for a campaign that does not keep them under
    `results/`: the web interface writes a campaign's records flat, subjects mixed.
    """
    results: list[Result] = []
    broken: list[dict[str, str]] = []
    for path in sorted(runs_dir.glob("**/results/*.json")) if paths is None else paths:
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, Mapping):
                raise TypeError(f"top level is {type(document).__name__}, not an object")
        except (OSError, ValueError, TypeError) as exc:
            broken.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        # A file that parses but carries the wrong shape (a scenario that is a
        # string, model_parameters that is a list) is as broken as one that does
        # not parse: it is listed, and the rest of the campaign is still summarised.
        try:
            results.append(Result(path, document))
        except Exception as exc:  # noqa: BLE001 - any shape error is a broken file
            broken.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return results, broken


def group_key(result: Result, by: Sequence[str]) -> tuple[str, ...]:
    parts = {
        "subject": result.subject,
        "scenario": result.scenario,
        "intent": result.intent,
        "condition": result.condition.key,
    }
    return tuple(parts[field] for field in by)


def spread(values: Iterable[Any]) -> dict[str, float | None]:
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return {"median": None, "min": None, "max": None, "n": 0}
    return {
        "median": statistics.median(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "n": len(numbers),
    }


def summarize(results: Sequence[Result], by: Sequence[str] = DEFAULT_BY) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[Result]] = {}
    for result in results:
        groups.setdefault(group_key(result, by), []).append(result)
    cells = [summarize_cell(members, by) for members in groups.values()]
    cells.sort(key=lambda cell: tuple(str(cell[field]) for field in GROUP_FIELDS))
    return cells


def summarize_cell(members: Sequence[Result], by: Sequence[str]) -> dict[str, Any]:
    first = members[0]
    conditions = {m.condition.key: m.condition for m in members}
    cell: dict[str, Any] = {
        "subject": first.subject if "subject" in by else sorted({m.subject for m in members}),
        "scenario": first.scenario if "scenario" in by else sorted({m.scenario for m in members}),
        "intent": first.intent if "intent" in by else sorted({m.intent for m in members}),
        "condition": first.condition.label if "condition" in by
        else sorted({c.label for c in conditions.values()}),
        "model": sorted({str(c.model) for c in conditions.values()}),
        "parameters": first.condition.parameters if "condition" in by
        else [c.parameters for c in conditions.values()],
        "n": len(members),
    }
    scored = sum(1 for m in members if m.scored)
    repaired = sum(1 for m in members if m.repair_passed is True)
    cell["scored"] = scored
    cell["repair_passed"] = repaired
    # Over the episodes the subject actually ran. Dividing by every member counted a
    # testbed that never came up as an episode the agent failed to repair.
    cell["repair_rate"] = (repaired / scored) if scored else None
    cell["judge_status"] = dict(Counter(str(m.judge_status) for m in members))
    for name in first.measures:
        cell[name] = spread(m.measures[name] for m in members)
    cell["termination"] = dict(Counter(m.termination for m in members))
    repeats = sorted(m.repeat for m in members if m.repeat is not None)
    cell["repeats"] = repeats
    cell["flags"] = cell_flags(members)
    cell["runs"] = [m.run_id for m in members]
    return cell


def model_disagreement(member: "Result") -> bool:
    """Whether this episode's three model names contradict one another.

    `configured_model` is what the judge was told, `sut_reported_model` what the
    subject says it asked for, `provider_reported_model` what answered. A name absent
    is not a disagreement: a judge that was not told has nothing to contradict, and a
    provider that reported no model said nothing to contradict either.
    """
    known = [name for name in (member.configured_model, member.sut_reported_model,
                               member.provider_reported_model) if name]
    return len(known) > 1 and len(set(known)) > 1


def cell_flags(members: Sequence[Result]) -> dict[str, int]:
    """What makes a cell's numbers not comparable with each other or with a neighbour.

    Each entry is a count: how many members carry the flag, or how many distinct
    values a field that should have one takes. A count reads in the table where
    a boolean would need a footnote.
    """
    flags: dict[str, int] = {}
    dirty = sum(1 for m in members if m.git_dirty)
    if dirty:
        flags["git_dirty"] = dirty
    commits = {m.git_commit for m in members}
    if len(commits) > 1:
        flags["mixed_commit"] = len(commits)
    # Only a judge that was told a model can disagree with the subject; a result
    # recorded before the judge was told (configured_model null) is not a mismatch,
    # and neither is a provider that reported no model of its own. The provider's is
    # the one worth having: the other two are what was asked for, echoed, while this
    # is what answered. A gateway that aliases a name instead of refusing it disagrees
    # here and nowhere else.
    mismatched = sum(1 for m in members if model_disagreement(m))
    if mismatched:
        flags["model_mismatch"] = mismatched
    untraced = sum(1 for m in members if m.trace_missing)
    if untraced:
        flags["no_trace_ref"] = untraced
    oracles = {m.oracle_versions for m in members}
    if len(oracles) > 1:
        flags["mixed_oracle_versions"] = len(oracles)
    unrecorded = sum(1 for m in members
                     if m.condition.parameters and not m.condition.prompt_variant_recorded)
    if unrecorded:
        flags["unrecorded_prompt_variant"] = unrecorded
    return flags


def totals(results: Sequence[Result], broken: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    return {
        "results": len(results),
        "broken": len(broken),
        "repair_passed": sum(1 for r in results if r.repair_passed is True),
        "scored": sum(1 for r in results if r.scored),
        "judge_status": dict(Counter(str(r.judge_status) for r in results)),
        "termination": dict(Counter(r.termination for r in results)),
    }


def build_summary(runs_dir: Path, by: Sequence[str] = DEFAULT_BY,
                  paths: Iterable[Path] | None = None) -> dict[str, Any]:
    results, broken = load_results(runs_dir, paths)
    return {
        "runs_dir": str(runs_dir),
        "by": list(by),
        "cells": summarize(results, by),
        "totals": totals(results, broken),
        "broken": list(broken),
    }


def short_subject(name: Any) -> str:
    if isinstance(name, list):
        return ",".join(short_subject(n) for n in name)
    return str(name).replace("A2A ", "").replace(" Agentic AI", "")


def short_model(models: Sequence[str]) -> str:
    return ",".join(m.rsplit("/", 1)[-1] for m in models if m != "None") or "-"


def grouped_or_count(value: Any, noun: str) -> str:
    if isinstance(value, list):
        return value[0] if len(value) == 1 else f"{len(value)} {noun}"
    return str(value)


def fmt_spread(stat: Mapping[str, Any], digits: int = 0) -> str:
    if stat["n"] == 0:
        return "-"
    return "/".join(f"{stat[k]:.{digits}f}" for k in ("median", "min", "max"))


def fmt_counter(counter: Mapping[str, int]) -> str:
    return " ".join(f"{k}:{v}" for k, v in sorted(counter.items()))


def render_text(summary: Mapping[str, Any]) -> str:
    show_repeats = any(cell["repeats"] for cell in summary["cells"])
    head = ["subject", "scenario", "intent", "condition", "model", "n", "repair", "judge",
            "turns", "out_tok", "secs", "mut_ok/mut", "termination", "flags"]
    if show_repeats:
        head.insert(6, "repeats")
    rows = []
    for cell in summary["cells"]:
        row = [
            short_subject(cell["subject"]),
            grouped_or_count(cell["scenario"], "scenarios"),
            grouped_or_count(cell["intent"], "intents"),
            grouped_or_count(cell["condition"], "conditions"),
            short_model(cell["model"]),
            str(cell["n"]),
            f"{cell['repair_passed']}/{cell['scored']}",
            fmt_counter(cell["judge_status"]),
            fmt_spread(cell["model_turns"]),
            fmt_spread(cell["output_tokens"]),
            fmt_spread(cell["elapsed_seconds"]),
            f"{fmt_spread(cell['successful_mutations'])} / {fmt_spread(cell['ani_mutations'])}",
            fmt_counter(cell["termination"]),
            fmt_counter(cell["flags"]) or "-",
        ]
        if show_repeats:
            row.insert(6, ",".join(str(r) for r in cell["repeats"]) or "-")
        rows.append(row)
    widths = [max(len(head[i]), *(len(r[i]) for r in rows)) if rows else len(head[i])
              for i in range(len(head))]
    lines = ["  ".join(h.ljust(w) for h, w in zip(head, widths)),
             "  ".join("-" * w for w in widths)]
    lines.extend("  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in rows)
    t = summary["totals"]
    lines.append("")
    lines.append(f"results {t['results']}, cells {len(summary['cells'])}, "
                 f"repair passed {t['repair_passed']}/{t['scored']}"
                 + (f", broken files {t['broken']}" if t["broken"] else ""))
    lines.append(f"  judge: {fmt_counter(t['judge_status'])}")
    lines.append(f"  termination: {fmt_counter(t['termination'])}")
    lines.append("  turns/out_tok/secs/mut columns are median/min/max over the cell")
    for entry in summary["broken"]:
        lines.append(f"  broken: {entry['path']} ({entry['error']})")
    return "\n".join(lines)


def parse_by(value: str) -> tuple[str, ...]:
    fields = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [f for f in fields if f not in GROUP_FIELDS]
    if unknown or not fields:
        raise argparse.ArgumentTypeError(
            f"--by takes a comma list of {', '.join(GROUP_FIELDS)}; got {value!r}")
    return fields


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    parser.add_argument("--by", type=parse_by, default=DEFAULT_BY,
                        help="cell fields, comma separated (default: subject,scenario,condition; "
                             "intent is the wording of the task, low/medium/high)")
    args = parser.parse_args(argv)
    if not args.runs_dir.is_dir():
        parser.error(f"{args.runs_dir} is not a directory")
    summary = build_summary(args.runs_dir, args.by)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=False, default=str))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
