#!/usr/bin/env python
"""Does every record of a campaign say what actually held, about thinking and identity?

A record carries a label (the experiment id ends in -thinkon, model_parameters says
enable_thinking true under the chat_template mechanism with the exact request) and,
separately, evidence: the trace the subject wrote holds the model's raw messages, and
a model that thought left reasoning_content on them. A thinkon record with none is a
lie, whatever caused it (the relay served a non-thinking checkpoint, the flag was
dropped on the way), and it must be found before the number it feeds is read.

Two trace shapes are read. The shared loop writes "SUT raw model message" entries with
payload.reasoning_content; the LangChain subject writes "SUT model response" entries
with payload.generations[].reasoning_content.

    thinking_audit.py <results dir>            # per-record problems, then a summary
    thinking_audit.py <results dir> --json

Exit status 1 when any record lies about its label; 2 when a record's trace could not
be read (that is a gap, not a lie, but it must not pass silently).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REQUEST = {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}
#: Below this many output tokens per model call a thinkon episode is suspect: D1's
#: thinkon medians were 681 and 770 with a tenth percentile near 540. With a readable
#: trace the count of messages carrying reasoning is the verdict and this is only a
#: warning: Qwen3.5-9B under "thinking permitted" mostly answers a tool result without
#: reasoning, which is the model's choice per turn, not a label lying. Without a trace
#: the label rests on this heuristic alone, so it stays a problem there.
SUSPECT_TOKENS_PER_CALL = 300


def trace_reasoning(trace_ref: str | None) -> tuple[int, int] | None:
    """(model messages, messages carrying reasoning) from the trace, or None if unreadable."""
    if not trace_ref:
        return None
    path = Path(trace_ref)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return None
    messages = with_reasoning = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        title = str(entry.get("title") or "")
        payload = entry.get("payload") or {}
        if title == "SUT raw model message" and isinstance(payload, dict):
            messages += 1
            with_reasoning += bool(str(payload.get("reasoning_content") or "").strip())
        elif title == "SUT model response" and isinstance(payload, dict):
            for generation in payload.get("generations") or []:
                messages += 1
                with_reasoning += bool(str((generation or {}).get("reasoning_content") or "").strip())
    return messages, with_reasoning


def audit(document: dict) -> dict:
    provenance = document.get("provenance") or {}
    sut = document.get("sut_result") or {}
    execution = sut.get("execution") or {}
    parameters = provenance.get("model_parameters") or {}
    experiment_id = str(document.get("experiment_id") or "")
    problems: list[str] = []
    labelled_on = experiment_id.endswith("-thinkon") or "-thinkon-" in experiment_id
    if labelled_on:
        if parameters.get("enable_thinking") is not True:
            problems.append("label thinkon but model_parameters.enable_thinking is not true")
        if parameters.get("thinking_mechanism") != "chat_template":
            problems.append(f"thinking_mechanism is {parameters.get('thinking_mechanism')!r}, not chat_template")
        if parameters.get("thinking_request") != EXPECTED_REQUEST:
            problems.append(f"thinking_request is {parameters.get('thinking_request')!r}")
    configured = provenance.get("configured_model")
    for key, value in (("provenance.sut_reported_model", provenance.get("sut_reported_model")),
                       ("sut_result.provider_reported_model", sut.get("provider_reported_model"))):
        if value and configured and value != configured:
            problems.append(f"{key} {value!r} differs from configured {configured!r}")
    git = provenance.get("git") or {}
    warnings: list[str] = []
    if git.get("dirty"):
        # A fact about the checkout, not about thinking: reported apart so the exit
        # status stays a verdict on the label, and a dirty tree does not hide one.
        warnings.append("provenance.git.dirty is true")
    evidence = trace_reasoning(execution.get("trace_ref")) if sut else None
    gap = sut and evidence is None
    reasoning_share = None
    if labelled_on and evidence is not None:
        messages, with_reasoning = evidence
        if messages and with_reasoning == 0:
            problems.append(f"no reasoning_content on any of {messages} model messages")
        if messages:
            reasoning_share = with_reasoning / messages
    usage = execution.get("token_usage") or {}
    calls = execution.get("llm_calls") or 0
    per_call = (usage.get("output_tokens") or 0) / calls if calls else None
    if labelled_on and per_call is not None and per_call < SUSPECT_TOKENS_PER_CALL and calls >= 3:
        low = f"only {per_call:.0f} output tokens per call over {calls} calls"
        if evidence is None:
            problems.append(low)
        else:
            # The trace has the verdict; this only says the permission went mostly unused.
            warnings.append(f"{low}; reasoning on {evidence[1]} of {evidence[0]} model messages")
    return {"run_id": document.get("run_id"), "experiment_id": experiment_id,
            "subject": provenance.get("sut_identity"), "scenario": (document.get("scenario") or {}).get("id"),
            "commit": git.get("commit"), "evidence": evidence, "trace_gap": bool(gap),
            "tokens_per_call": per_call, "reasoning_share": reasoning_share,
            "problems": problems, "warnings": warnings}


def audit_files(paths: Iterable[Path]) -> list[dict]:
    """The audit of every record among `paths` whose subject ran."""
    records = []
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            records.append({"run_id": path.name, "problems": [f"unreadable: {exc}"],
                            "warnings": [], "trace_gap": True})
            continue
        if not (document.get("sut_result") or {}):
            continue  # the subject never ran; there is nothing to lie about
        records.append(audit(document))
    return records


def summarize(records: list[dict]) -> dict:
    return {"records": len(records),
            "lying": sum(1 for r in records if r["problems"]),
            "warnings": sum(1 for r in records if r.get("warnings")),
            "trace_gaps": sum(1 for r in records if r.get("trace_gap")),
            "commits": dict(Counter(r.get("commit") for r in records))}


def summary_line(summary: dict) -> str:
    return (f"records {summary['records']}, lying {summary['lying']}, warnings {summary['warnings']}, "
            f"trace gaps {summary['trace_gaps']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.runs_dir.is_dir():
        parser.error(f"{args.runs_dir} is not a directory")
    records = audit_files(sorted(args.runs_dir.glob("**/results/*.json")))
    lying = [r for r in records if r["problems"]]
    warned = [r for r in records if r.get("warnings")]
    gaps = [r for r in records if r.get("trace_gap")]
    summary = summarize(records)
    commits = summary["commits"]
    if args.json:
        print(json.dumps({"summary": summary, "records": records}, indent=1, default=str))
    else:
        for r in lying:
            print(f"LIE  {r.get('experiment_id')} {r.get('scenario')} {r.get('run_id')}: " + "; ".join(r["problems"]))
        for r in warned:
            print(f"WARN {r.get('experiment_id')} {r.get('scenario')} {r.get('run_id')}: " + "; ".join(r["warnings"]))
        for r in gaps:
            print(f"GAP  {r.get('experiment_id')} {r.get('scenario')} {r.get('run_id')}: trace unreadable")
        if len(commits) > 1:
            print(f"NOTE more than one commit in the corpus: {commits}")
        print(summary_line(summary))
    return 1 if lying else (2 if gaps else 0)


if __name__ == "__main__":
    sys.exit(main())
