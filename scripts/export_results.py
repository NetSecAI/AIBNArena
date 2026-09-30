#!/usr/bin/env python
"""Lay the campaign records out as reports/campaigns/<model>/<experiment>/<subject>/<intent>/.

    export_results.py --out reports/campaigns --model qwen35-9b-think \\
        --experiment e5_langchain_ani_v0_2 --cell langchain high ../runs/e1/langchain_high
    export_results.py --out reports/campaigns --campaign reports/campaigns/runs/<campaign>

A campaign the launcher wrote is one cell per `--cell`, named by hand. A campaign the web
interface wrote (`--campaign`) holds every subject and model of its matrix in one
directory; it is cut into one cell per model, subject and intent, the model named without
its provider (`openai/qwen35-9b-think` -> `qwen35-9b-think`), the subject by its
architecture without `_agent` (`langchain_agent` -> `langchain`), the intent by the
wording the record names, and the experiment by the campaign's id unless `--experiment`
names it. The web interface runs this itself when a campaign ends.

Each cell is laid out as records/ (the raw per-episode judge records, copied unchanged;
the record is the measurement and nothing here rewrites it), logs/traces/<plate>/ (the
episode's trace split by kind: the task, the prompts, the model messages with their
reasoning, the ANI operations), logs/judge/ (the judge's console output per episode),
reports/tasks/<plate>/ (the per-task report in the shared format of
scripts/generate_report.py, as report.html and report.json, cut into summary.json,
timeline.json, tool_calls.json and evaluation_parameters.json, plus summary.md) and reports/ (the per-scenario summary table and the evaluation-parameter
report for the whole cell, produced by the same scripts the operator runs on the campaign
directory). Small files rather than one big one per episode, as Hugo asked (2026-09-16).
The subject's stdout log stays behind: it repeats every prompt in full and everything in
it is in the trace.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402


def _script(name: str):
    """A sibling script as a module, registered once: scripts/ is not a package, and its
    dataclasses look their module up by name."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_evaluation_parameters = _script("evaluation_parameters")
episode_facts = _evaluation_parameters.episode_facts
generate_report = _script("generate_report")
summarize_campaign = _script("summarize_campaign")
thinking_audit = _script("thinking_audit")


#: Which testbed a plate runs on, by scenario family (the experiment files bind them).
_TESTBED_OF_FAMILY = (
    ("connectivity.", "sme01-small"),
    ("qos.link_impairment.", "sme01-small"),
    ("dhcp_dns.", "sme01-dns"),
    ("filtering.", "sme01-fw"),
    ("qos.wan_shaping_policy_repair.", "sme01-qos"),
    ("qos.assured_bandwidth.", "sme01-qos-greenfield"),
)


def testbed_of(plate: str) -> str:
    return next((testbed for prefix, testbed in _TESTBED_OF_FAMILY if plate.startswith(prefix)), "")


#: The trace is one JSONL per episode; it is split by the kind of entry so the part a
#: reader wants (the model's messages, the operations) is not buried under the prompts,
#: which repeat the whole context every turn and are most of the bytes.
_TRACE_FILES = (
    ("task.jsonl", lambda title: title == "SUT received public A2A task"),
    ("prompts.jsonl", lambda title: title.startswith("SUT prompt")),
    ("model_messages.jsonl", lambda title: title.startswith("SUT ")),
    ("ani_operations.jsonl", lambda title: title.startswith("ANI ")),
)
_TRACE_KINDS = {
    "task.jsonl": "the A2A task the subject received",
    "prompts.jsonl": "every prompt sent to the model; the first in full, each later one as what it appended to "
                     "the previous prompt (continues_previous, appended, prompt_chars), because a turn's prompt is "
                     "the previous prompt plus the new messages; the prompt of turn n is the first prompt followed "
                     "by every appended up to n",
    "model_messages.jsonl": "every model message: its reasoning, its text and its tool calls",
    "ani_operations.jsonl": "every ANI call and its result",
    "other.jsonl": "entries of no other kind",
}


def _prompt_delta(payload: Any, previous: str) -> tuple[Any, str]:
    """Store a prompt as what it appended to the previous one.

    The baseline logs the whole rendered context on every turn (`prompts: [text]`), and a
    turn's prompt is the previous turn's prompt followed by the new messages: across the
    35 LangChain traces of E3 every prompt started with the one before it, 124 MB of
    prompt text of which 6 MB was new. The first prompt (and any that does not continue
    the previous one) is kept in full; the rest carry `appended`, so the prompt of turn n
    is the first prompt followed by every `appended` up to n. Lossless.
    """
    prompts = payload.get("prompts") if isinstance(payload, dict) else None
    if not (isinstance(prompts, list) and len(prompts) == 1 and isinstance(prompts[0], str)):
        return payload, ""
    text = prompts[0]
    if previous and text.startswith(previous):
        slim = {key: value for key, value in payload.items() if key != "prompts"}
        slim.update({"prompt_chars": len(text), "continues_previous": True, "appended": text[len(previous):]})
        return slim, text
    return payload, text


def split_trace(source: Path, target: Path) -> dict:
    """Write the trace at `source` into `target/` as one JSONL per kind of entry.

    Every written line carries `seq`, its line number in the original, so the files
    merge back into the episode's order. `index.json` says what each file holds.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    handles: dict[str, Any] = {}
    counts: dict[str, int] = {}
    previous_prompt = ""
    try:
        for seq, line in enumerate(source.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                title = str(entry.get("title") or "") if isinstance(entry, dict) else ""
                name = next((name for name, matches in _TRACE_FILES if matches(title)), "other.jsonl")
                if not isinstance(entry, dict):
                    entry = {"entry": entry}
            except json.JSONDecodeError:
                name, entry = "other.jsonl", {"raw": line}
            if name == "prompts.jsonl":
                entry["payload"], previous_prompt = _prompt_delta(entry.get("payload"), previous_prompt)
            if name not in handles:
                handles[name] = (target / name).open("w", encoding="utf-8")
            handles[name].write(json.dumps({"seq": seq, **entry}, ensure_ascii=False) + "\n")
            counts[name] = counts.get(name, 0) + 1
    finally:
        for handle in handles.values():
            handle.close()
    index = {"source": str(source), "lines": sum(counts.values()), "files": counts,
             "order": "seq is the line's position in the original trace; merge the files on seq to read the episode in order",
             "kinds": {name: _TRACE_KINDS[name] for name in counts}}
    (target / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


#: What the per-task folder holds, in the shared report format plus the cuts.
_TASK_FILES = {
    "report.html": "the per-task report of scripts/generate_report.py, readable",
    "report.json": "the same report as data (report_version 1.0 plus time_by_phase, tokens, validation, "
                   "evaluation_parameters, report_information, files); the record's own sections are "
                   "pointers to the record instead of copies",
    "summary.json": "the numbers: run, evaluations, metrics, operation counts, time by phase, tokens, "
                    "validation, how the episode counts in the evaluation parameters, the sheet items",
    "timeline.json": "the model's turns and every call in order, with the reasoning spans from the trace",
    "tool_calls.json": "every ANI operation with its arguments and result status",
    "evaluation_parameters.json": "how this episode counts in each of the evaluation parameters",
    "provenance.json": "what ran: model, subject, prompt, thinking, git commit, scenario version",
    "summary.md": "the short Markdown report: verdict, outcome, execution, operations, the subject's conclusion",
}


def write_task_folder(document: dict, record: Path, plate: str, task_dir: Path, links: dict[str, str]) -> None:
    """One folder per task, in the shared report format, cut into small files."""
    report = generate_report.report_document(document, record, record.read_bytes())
    report["files"] = links
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)
    (task_dir / "report.html").write_text(generate_report.render_html(report), encoding="utf-8")
    slim = dict(report)
    for key in ("sut_result", "convergence_result", "cleanup_result"):
        slim[key] = {"see": f"{links['record']}", "key": key,
                     "note": "verbatim in the record; not copied into the report"}
    (task_dir / "report.json").write_text(generate_report.render_json(slim), encoding="utf-8")
    summary_keys = ("report_version", "source", "run", "error", "evaluations", "metrics", "operation_counts",
                    "time_by_phase", "tokens", "validation", "evaluation_parameters", "report_information",
                    "phase_durations", "files")
    cuts = {
        "summary.json": {key: report.get(key) for key in summary_keys},
        "timeline.json": report.get("timeline"),
        "tool_calls.json": {
            "note": "ani_operations is the subject's own log of every call it made through the ANI, "
                    "in order, with the arguments it sent",
            "ani_operations": (document.get("sut_result") or {}).get("ani_operations"),
        },
        "evaluation_parameters.json": report.get("evaluation_parameters"),
        "provenance.json": report.get("provenance"),
    }
    for name, content in cuts.items():
        (task_dir / name).write_text(json.dumps(content, indent=2, ensure_ascii=False, default=str) + "\n",
                                     encoding="utf-8")
    (task_dir / "summary.md").write_text(task_report(document, plate, links), encoding="utf-8")


#: The launcher names a group directory after the experiment file it ran, and those
#: files still carry their historical "-smoke" suffix (connectivity-smoke.toml binds the
#: connectivity family to the sme01-small testbed; it is not a smoke test). The copies
#: are named by the scenario family instead, which is what a reader is looking for.
_EXPERIMENT_FAMILY = {
    "connectivity-smoke": "connectivity",
    "dhcp_dns-smoke": "dhcp_dns",
    "filtering-smoke": "filtering",
    "qos-smoke": "qos_link_impairment",
    "qos-shaping-repair": "qos_wan_shaping_policy_repair",
    "qos-assured-bandwidth": "qos_assured_bandwidth",
}


def _judge_log_name(group_dir: str) -> str:
    """`connectivity-smoke_20260915T065040` -> `connectivity_20260915T065040`."""
    stem, _, stamp = group_dir.rpartition("_")
    return f"{_EXPERIMENT_FAMILY.get(stem, stem)}_{stamp}"


_PLATE_START = re.compile(r"^---- (\S+)\s+\(\d\d:\d\d:\d\d\)")
_PLATE_END = re.compile(r"^\s+judge rc=\d+")


def _split_judge_log(text: str) -> dict[str, str]:
    """The judge's output per plate, cut on the launcher's start and end markers."""
    parts: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines(keepends=True):
        start = _PLATE_START.match(line)
        if start:
            current = start.group(1)
            parts.setdefault(current, []).append(line)
            continue
        if current is not None:
            parts[current].append(line)
            if _PLATE_END.match(line):
                current = None
    return {plate: "".join(lines) for plate, lines in parts.items()}


def _yes(value: Any) -> str:
    return {True: "yes", False: "no"}.get(value, "n/a")


def _seconds(value: Any) -> str:
    try:
        return f"{float(value):.0f} s"
    except (TypeError, ValueError):
        return "n/a"


def task_report(document: dict, plate: str, links: dict[str, str]) -> str:
    """One Markdown report for one task, read from the record and nothing else.

    Hugo asked for a report per task rather than one cell file (2026-09-16). Every
    number here is also in the record; the report lays them out in reading order
    and says how the episode counts in the evaluation parameters.
    """
    metrics = document.get("metrics") or {}
    phases = metrics.get("phase_passed") or {}
    sut = document.get("sut_result") or {}
    execution = sut.get("execution") or {}
    termination = execution.get("termination") or {}
    counts = document.get("operation_counts") or {}
    final = sut.get("final_response") if isinstance(sut.get("final_response"), dict) else {}
    provenance = document.get("provenance") or {}
    error = document.get("error") if isinstance(document.get("error"), dict) else None
    facts = episode_facts(document)
    repaired = metrics.get("repair_score") == 1.0 or phases.get("repair") is True
    verdict = ("repaired and concluded" if metrics.get("success")
               else "repaired by the oracle's reading, but the subject did not conclude" if repaired
               else "not repaired")
    if document.get("status") != "completed":
        verdict = f"lifecycle {document.get('status')}: no verdict" + (
            f" (failed in {error.get('phase')})" if error else "")
    lines = [f"# {plate}", "", f"**Verdict: {verdict}.**", "",
             f"Intent wording `{provenance.get('intent_variant') or 'unnamed'}`, seed "
             f"{provenance.get('scenario_seed')}, run `{document.get('run_id')}`, "
             f"recorded {str(document.get('created_at') or '')[:19]} UTC.", "",
             "## Outcome", "", "| question | answer |", "|---|---|",
             f"| repair oracle passed | {_yes(phases.get('repair'))} |",
             f"| full success (oracle, converged, subject completed and verified) | {_yes(metrics.get('success'))} |",
             f"| lab healthy before the fault | {_yes(phases.get('healthy'))} |",
             f"| fault degraded the lab as expected | {_yes(phases.get('expected_degradation'))} |",
             f"| unaffected paths preserved | {_yes(phases.get('preservation'))} (score {metrics.get('preservation_score')}) |",
             f"| non-regression | {_yes(metrics.get('non_regression_passed'))} |",
             f"| network converged to the reference | {_yes(metrics.get('converged'))} |",
             f"| repair score | {metrics.get('repair_score')} |",
             f"| subject's own status | {sut.get('status')} (verified {_yes(sut.get('verified'))}) |",
             f"| judge lifecycle | {document.get('status')}" + (f", error in {error.get('phase')}: {error.get('message')}" if error else "") + " |",
             "", "## Execution", "", "| measure | value |", "|---|---|",
             f"| termination | {termination.get('cause')}: {termination.get('detail')} (turn {termination.get('turn')}) |",
             f"| model calls | {execution.get('llm_calls')} (tool calls {execution.get('tool_call_count')}) |",
             f"| wall clock | {_seconds(execution.get('elapsed_seconds'))} of a {_seconds(execution.get('budget_seconds'))} budget |",
             f"| time in the model | {_seconds(execution.get('model_seconds'))} |",
             f"| tokens in / out | {counts.get('input_tokens')} / {counts.get('output_tokens')} |",
             "", "## Operations", "", "| operation | count |", "|---|---|",
             f"| ANI reads (successful) | {counts.get('ani_reads')} ({counts.get('successful_reads')}) |",
             f"| device mutations (successful) | {counts.get('ani_mutations')} ({counts.get('successful_mutations')}) |",
             f"| validations | {counts.get('validations')} |",
             f"| failed operations | {counts.get('failed_operations')} |",
             f"| unsafe operations | {counts.get('unsafe_operations')} |",
             "", "## The subject's conclusion", ""]
    if final:
        lines += [f"Status `{final.get('status')}`.", "", "> " + str(final.get("summary") or "")[:1200].replace("\n", " "), ""]
    else:
        lines += ["No conclusion was returned.", ""]
    if sut.get("error"):
        lines += [f"Subject error: {str(sut.get('error'))[:400]}", ""]
    lines += ["## How this episode counts in the evaluation parameters", "",
              "| parameter | this episode |", "|---|---|",
              f"| pass_rate | {'passed' if facts.repair_passed else 'failed'}; oracle verdict {'reached' if facts.repair_judged else 'not reached'} |",
              f"| tool_call_success_rate | {facts.accepted} accepted of {facts.actions} tool calls |",
              f"| repeat_action_rate | {facts.repeats} repeated of {facts.actions} tool calls |",
              f"| time_limit_rate | {'hit the time limit' if facts.hit_time_limit else 'within the budget'} |",
              f"| interaction_limit_rate | {'hit the interaction limit' if facts.hit_interaction_limit else 'no limit hit' + (' (uncapped)' if not facts.capped else '')} |",
              f"| early_submission_rate | {'early submission' if facts.early_submission else 'no'}; concluded by itself: {_yes(facts.submitted)}, declared completed: {_yes(facts.declared_completed)} |",
              f"| error_submission_rate | {'submission with the repair not passed' if facts.submitted and facts.repair_judged and facts.repair_passed is not True else 'no'} |",
              f"| llm_found_problem_rate | fault located {_yes(facts.fault_located)}; a change reached the faulty device {_yes(facts.fault_device_written)} |",
              f"| false_positive_rate | {'no-fault episode, false positive ' + _yes(facts.made_false_positive) if facts.fault_applicable is False else 'not a no-fault episode'} |",
              f"| ani_call_type_ratio | {dict(facts.buckets) or '{}'} |",
              "", "## Files", "",
              "* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, "
              "`timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`",
              f"* record: [`records/{plate}.json`]({links['record']})",
              f"* trace: [`logs/traces/{plate}/`]({links['trace']})" if links.get("trace") else "* trace: not recorded",
              f"* judge log: [`logs/judge/{plate}.judge.log`]({links['judge_log']})"]
    lines.append("")
    return "\n".join(lines)


@dataclass
class Episode:
    """One episode to lay out: the judge's record, and the judge's console output for it."""

    record: Path
    judge_log: str = ""


def _plate(document: dict, record: Path) -> str:
    return str((document.get("scenario") or {}).get("id") or record.stem)


def episode_names(documents: list[dict], records: list[Path]) -> list[str]:
    """What each episode's files are named in its cell.

    By the plate (scenario id), not by the run id the launcher minted, so a reader can
    open connectivity.remove_ip.high.m2.json without a lookup; the run id stays inside
    the record. A plate the cell ran more than once -- on several seeds, with and
    without the fault, or repeated on one seed -- adds what tells the runs apart, so no
    episode's files overwrite another's.
    """
    plates = [_plate(document, record) for document, record in zip(documents, records)]
    names = []
    for document, plate in zip(documents, plates):
        if plates.count(plate) == 1:
            names.append(plate)
            continue
        name = f"{plate}.seed{(document.get('provenance') or {}).get('scenario_seed')}"
        if (document.get("metrics") or {}).get("fault_applicable") is False:
            name += ".nofault"
        names.append(name)
    repeated = [name for name in names if names.count(name) > 1]
    return [f"{name}.{document.get('run_id')}" if name in repeated else name
            for name, document in zip(names, documents)]


def lay_out_cell(cell: Path, episodes: list[Episode], *, source: Path, model: str, subject: str,
                 intent: str, experiment: str | None = None) -> dict:
    """One cell: records/, logs/, reports/ and index.md, from the episodes given.

    `source` is the campaign directory the episodes were read from; the whole-cell
    tables name it, and read only these episodes, never the rest of that directory.
    """
    records_dir = cell / "records"
    traces_dir = cell / "logs" / "traces"
    judge_dir = cell / "logs" / "judge"
    reports_dir = cell / "reports"
    tasks_dir = reports_dir / "tasks"
    for directory in (records_dir, traces_dir, judge_dir, reports_dir, tasks_dir):
        directory.mkdir(parents=True, exist_ok=True)
    documents = [json.loads(episode.record.read_text(encoding="utf-8")) for episode in episodes]
    names = episode_names(documents, [episode.record for episode in episodes])
    copied_traces = 0
    index: list[dict] = []
    for episode, document, plate in zip(episodes, documents, names):
        record = episode.record
        shutil.copy2(record, records_dir / f"{plate}.json")
        # The layout before 2026-09-16 kept one trace file and one Markdown per plate;
        # a re-export must not leave those next to the folders that replaced them.
        for stale in (traces_dir / f"{plate}.trace.jsonl", tasks_dir / f"{plate}.md"):
            if stale.is_file():
                stale.unlink()
        execution = (document.get("sut_result") or {}).get("execution") or {}
        trace = execution.get("trace_ref")
        trace_split: dict = {}
        if trace and Path(trace).exists():
            trace_split = split_trace(Path(trace), traces_dir / plate)
            copied_traces += 1
        metrics = document.get("metrics") or {}
        # The judge's console output (deploy, fault injection, oracle phases, the verdict
        # block). Written whole rather than appended, so re-exporting a cell rewrites
        # each file instead of adding a second copy.
        if episode.judge_log:
            (judge_dir / f"{plate}.judge.log").write_text(episode.judge_log, encoding="utf-8")
        # The judge's per-device configuration snapshots (records from 2026-09-16 on):
        # healthy, before and after the subject, one text file per device and moment,
        # plus the diffs; copied whole so the report's links resolve.
        devices_dir = ""
        manifest = document.get("device_configurations")
        devices = Path(str(manifest.get("directory"))) if isinstance(manifest, dict) and manifest.get("directory") else None
        if devices is not None and devices.is_dir():
            shutil.copytree(devices, cell / "logs" / "devices" / plate, dirs_exist_ok=True)
            devices_dir = f"logs/devices/{plate}/"
        # Links as seen from reports/tasks/<plate>/, where the report files live.
        up = "../../.."
        links = {"record": f"{up}/records/{plate}.json",
                 "trace": f"{up}/logs/traces/{plate}/" if trace_split else "",
                 "judge_log": f"{up}/logs/judge/{plate}.judge.log",
                 "devices": f"{up}/logs/devices/{plate}/" if devices_dir else "",
                 "note": "relative to this folder"}
        write_task_folder(document, record, plate, tasks_dir / plate, links)
        index.append({
            "plate": plate, "record": f"records/{plate}.json",
            "trace": f"logs/traces/{plate}/" if trace_split else "",
            "judge_log": f"logs/judge/{plate}.judge.log",
            "report": f"reports/tasks/{plate}/",
            "devices": devices_dir,
            "repair": metrics.get("success"),
            "termination": (execution.get("termination") or {}).get("cause"),
            "llm_calls": execution.get("llm_calls"),
            "run_id": document.get("run_id"),
        })
    with_devices = any(row["devices"] for row in index)
    title = " / ".join(part for part in (model, experiment, subject, intent) if part)
    lines = [f"# {title}", ""]
    lines += [
        "Per task: `reports/tasks/<plate>/` holds `report.html` (the shared per-task report format), "
        "`summary.md` and the report cut into `summary.json`, `timeline.json`, `tool_calls.json`, "
        "`evaluation_parameters.json`; `logs/traces/<plate>/` holds the trace "
        "split into the task, the prompts, the model messages and the operations.", "",
        "| plate | repair | termination | model calls | report | record | trace | judge log |"
        + (" devices |" if with_devices else ""),
        "|---|---|---|---|---|---|---|---|" + ("---|" if with_devices else "")]
    for row in sorted(index, key=lambda r: r["plate"]):
        lines.append(f"| {row['plate']} | {row['repair']} | {row['termination']} | {row['llm_calls']} | "
                     f"[html]({row['report']}report.html) [md]({row['report']}summary.md) [json]({row['report']}summary.json) | "
                     f"[{row['plate']}.json]({row['record']}) | "
                     + (f"[split]({row['trace']}) | " if row['trace'] else "not recorded | ")
                     + f"[{row['judge_log'].split('/')[-1]}]({row['judge_log']}) |"
                     + ((f" [before/after]({row['devices']}) |" if row["devices"] else " |") if with_devices else ""))
    (cell / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # The whole-cell tables, from the scripts the operator runs on a campaign directory,
    # read over this cell's records only: a web campaign's directory mixes subjects.
    paths = [episode.record for episode in episodes]
    summary = summarize_campaign.build_summary(source, ("subject", "scenario", "condition"), paths)
    (reports_dir / "summary.txt").write_text(summarize_campaign.render_text(summary) + "\n", encoding="utf-8")
    (reports_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    _evaluation_parameters.write_report(
        _evaluation_parameters.build_parameters(source, ("subject", "intent"), paths),
        reports_dir / "evaluation_parameters.json")
    audit = thinking_audit.summary_line(thinking_audit.summarize(thinking_audit.audit_files(paths)))
    info = {"model": model, **({"experiment": experiment} if experiment else {}),
            "subject": subject, "intent": intent, "source": str(source),
            "records": len(episodes), "traces": copied_traces,
            "thinking_audit": audit,
            "layout": {"index.md": "plate -> report, record, trace, judge log, repair, termination",
                       "records/": "one judge record per episode, named by plate, unchanged",
                       "logs/traces/<plate>/": "the episode's trace split by kind (task.jsonl, prompts.jsonl, model_messages.jsonl, ani_operations.jsonl; index.json says the counts; seq orders them)",
                       "logs/judge/": "the judge's console output per episode, named by plate",
                       "logs/devices/<plate>/": "records from 2026-09-16 on: the judge's per-device running configuration at healthy, before_sut and after_sut (one text file per device and moment) and the diffs between them; manifest.json lists what changed",
                       "reports/tasks/<plate>/": _TASK_FILES,
                       "reports/": "summary.txt/json per scenario, evaluation_parameters.json for the whole cell, this file"}}
    (reports_dir / "cell.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    info["path"] = str(cell)
    return info


def export_cell(out: Path, model: str, subject: str, intent: str, runs_dir: Path,
                experiment: str | None = None) -> dict:
    """One cell of a campaign the launcher wrote: `<group>/results/*.json` and a run.log per group."""
    cell = out / model / experiment / subject / intent if experiment else out / model / subject / intent
    # The launcher appends every judge run of a group to one run.log with "---- <plate>"
    # before each run and "judge rc=" after it, so the group file is cut on those
    # markers. The subject's stdout log is left out: it repeats every prompt in full and
    # everything it says is in the trace.
    judge_parts: dict[str, list[str]] = {}
    for run_log in sorted(runs_dir.glob("*/run.log")):
        for plate, text in _split_judge_log(run_log.read_text(encoding="utf-8", errors="replace")).items():
            judge_parts.setdefault(plate, []).append(text)
    judge = {plate: "".join(parts) for plate, parts in judge_parts.items()}
    episodes = []
    for record in sorted(runs_dir.glob("*/results/*.json")):
        plate = _plate(json.loads(record.read_text(encoding="utf-8")), record)
        episodes.append(Episode(record, judge.get(plate, "")))
    info = lay_out_cell(cell, episodes, source=runs_dir, model=model, subject=subject, intent=intent,
                        experiment=experiment)
    # A judge run that wrote no record still said why in its output; that is kept too.
    judge_dir = cell / "logs" / "judge"
    for plate, text in judge.items():
        if not (judge_dir / f"{plate}.judge.log").exists():
            (judge_dir / f"{plate}.judge.log").write_text(text, encoding="utf-8")
    return info


def model_folder(model: str) -> str:
    """`openai/qwen35-9b-think` -> `qwen35-9b-think`: the provider prefix is how the
    subject reaches the model, not which model it is."""
    return model.rsplit("/", 1)[-1]


def subject_folder(architecture: str) -> str:
    """`langchain_agent` -> `langchain`, `langchain_rag_agent` -> `langchain_rag`."""
    return architecture.removesuffix("_agent")


def campaign_cells(campaign_dir: Path, manifest: dict) -> dict[tuple[str, str, str], list[Episode]]:
    """The episodes of a campaign the web interface ran, by (model, subject, intent).

    The interface writes every record of the matrix into one directory, subjects and
    models mixed; `campaign.json` names the record each cell wrote, and the judge's
    output is `judge-logs/<experiment_id>.log`, one file per cell. The intent is read
    from the record: the matrix does not choose it, the scenario's wording does. A cell
    that wrote no record is left out here and listed on the campaign's own page, and so
    is a record that names another model than its cell: the campaign stopped on it,
    and filed under the cell's model it would be a measurement of a model that did not run.
    """
    cells: dict[tuple[str, str, str], list[Episode]] = {}
    for cell in manifest.get("cells") or []:
        if not cell.get("result_path"):
            continue
        record = Path(str(cell["result_path"]))
        record = record if record.is_absolute() else ROOT / record
        if not record.is_file():
            continue
        document = json.loads(record.read_text(encoding="utf-8"))
        provenance = document.get("provenance") or {}
        if any(provenance.get(field) != cell["model"] for field in ("configured_model", "sut_reported_model")):
            continue
        intent = summarize_campaign.intent_of(provenance)
        log = campaign_dir / "judge-logs" / f"{cell['experiment_id']}.log"
        judge_log = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        key = (model_folder(str(cell["model"])), subject_folder(str(cell["architecture"])), intent)
        cells.setdefault(key, []).append(Episode(record, judge_log))
    return cells


def export_campaign(out: Path, campaign_dir: Path, experiment: str | None = None,
                    manifest: dict | None = None) -> list[dict]:
    """Every cell of a campaign the web interface ran, under out/<model>/<experiment>/<subject>/<intent>/.

    The experiment is the campaign's id unless named: two campaigns never share a cell
    folder by accident, and a campaign re-exported rewrites its own. `manifest` stands
    for its `campaign.json`, for the runner, which holds a newer one than the file.
    """
    if manifest is None:
        manifest = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))
    experiment = experiment or str(manifest.get("campaign_id") or campaign_dir.name)
    return [lay_out_cell(out / model / experiment / subject / intent, episodes, source=campaign_dir,
                         model=model, subject=subject, intent=intent, experiment=experiment)
            for (model, subject, intent), episodes in sorted(campaign_cells(campaign_dir, manifest).items())]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--experiment",
                        help="the folder between the model and the subject; for --campaign, its id by default")
    parser.add_argument("--model", help="the model folder of every --cell")
    parser.add_argument("--cell", nargs=3, action="append", default=[],
                        metavar=("SUBJECT", "INTENT", "RUNS_DIR"), help="a campaign directory the launcher wrote")
    parser.add_argument("--campaign", type=Path, action="append", default=[],
                        help="a campaign directory the web interface wrote (it holds campaign.json)")
    args = parser.parse_args(argv)
    if not args.cell and not args.campaign:
        parser.error("give at least one --cell or --campaign")
    if args.cell and not args.model:
        parser.error("--cell needs --model")
    infos = [export_cell(args.out, args.model, subject, intent, Path(runs_dir).resolve(), args.experiment)
             for subject, intent, runs_dir in args.cell]
    for campaign in args.campaign:
        if not (campaign / "campaign.json").is_file():
            parser.error(f"{campaign} holds no campaign.json: not a campaign the web interface ran")
        infos += export_campaign(args.out, campaign.resolve(), args.experiment)
    for info in infos:
        print(f"{info['path']}: {info['records']} records, {info['traces']} traces, {info['thinking_audit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
