#!/usr/bin/env python3
"""One page for a whole campaign: the subjects compared, then every episode's interactions.

A campaign runs the same plates against several subjects and models so that they can be
compared, and `generate_report.py` reads one episode at a time: it cannot say which
subject repaired what the other did not. This page does, with the tables the campaign
records already use:

* the per-cell summary of `summarize_campaign.py` (repairs, how the episodes ended,
  turns, output tokens, seconds, mutations, the flags that make cells incomparable);
* the evaluation parameters of `evaluation_parameters.py`;
* repairs by scenario family, and how the episodes ended, as the campaign READMEs set
  them side by side;

plus the table a matrix makes possible: the same plate, the same fault, one column per
subject. Below that, every episode's timeline -- the thinking and the calls in order --
rendered by `generate_report.py` exactly as the episode's own report shows it, with a
link to that report.

Nothing is recomputed differently here: the cell, the termination cause and every rate
come from the two scripts above, so this page and their tables cannot disagree.

    scripts/generate_campaign_report.py reports/campaigns/runs/<campaign>
    scripts/generate_campaign_report.py <directory> -o /tmp/compare/campaign.report -f html

The directory is one the web interface wrote: its `campaign.json` gives the matrix, in
order, and the cells that never produced a result are listed with the reason.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for _path in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _load(name: str) -> Any:
    """A sibling script as a module, registered once: scripts/ is not a package."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses look their module up by name.
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


summarize_campaign = _load("summarize_campaign")
evaluation_parameters = _load("evaluation_parameters")
generate_report = _load("generate_report")

#: Bumped when a consumer would have to change to keep reading these files.
REPORT_VERSION = "1.0"
REPORT_KIND = "campaign_report"
MANIFEST = "campaign.json"
#: Where the page goes inside the campaign directory: beside the episodes' own reports,
#: and never beside the results, which the campaign scripts collect as `*.json`.
DEFAULT_REPORT_DIR = "reports"
DEFAULT_BASENAME = "campaign.report"
#: What a cell is here: one subject run under one condition, model included.
BY = ("subject", "condition")
#: The rates set side by side. The call mix is a table of its own.
PARAMETER_ROWS = tuple(name for name in evaluation_parameters.ORDER if name != "ani_call_type_ratio")


# -- reading the campaign ---------------------------------------------------------


@dataclass
class Episode:
    """One cell of the matrix: its result when it produced one, and its own report page."""

    number: int
    cell: Mapping[str, Any]
    path: Path | None = None
    report: Path | None = None
    payload: Mapping[str, Any] | None = None
    result: Any = None
    contender: str | None = None
    document: Mapping[str, Any] | None = None
    error: str | None = None

    @property
    def anchor(self) -> str:
        return f"episode-{self.number}"


def repository_path(value: Any) -> Path | None:
    """A path as the web interface records it: repository-relative, or absolute when the
    campaign was written outside the repository."""
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else REPO_ROOT / path


def read_manifest(directory: Path) -> Mapping[str, Any]:
    path = directory / MANIFEST
    if not path.is_file():
        raise ValueError(f"{directory} holds no {MANIFEST}: not a campaign the web interface ran")
    return json.loads(path.read_text(encoding="utf-8"))


def contender_key(result: Any) -> str:
    return json.dumps([result.subject, result.condition.key])


def contender_label(result: Any, *, with_condition: bool) -> str:
    """The subject and the model, and the condition when it is what tells two rows apart."""
    label = f"{result.subject} · {summarize_campaign.short_model([str(result.condition.model)])}"
    if with_condition:
        label += f" · {result.condition.label}"
    return label


def read_episodes(manifest: Mapping[str, Any], *, read_trace: bool) -> tuple[list[Episode], list[dict[str, str]]]:
    """Every cell of the matrix, in its order, read from the result it wrote.

    A result is read the way its own report reads it (`generate_report.load_payload`,
    which holds it to the schema); one that does not read is listed, not dropped with the
    rest of the campaign.
    """
    no_trace = None if read_trace else {
        "path": None, "available": False, "reason": "reading the trace was disabled"}
    episodes: list[Episode] = []
    broken: list[dict[str, str]] = []
    for number, cell in enumerate(manifest.get("cells") or [], start=1):
        episode = Episode(number, cell, path=repository_path(cell.get("result_path")),
                          report=repository_path(cell.get("report_path")))
        episodes.append(episode)
        if episode.path is None:
            # The cell wrote no result; its state and error in the manifest say why.
            continue
        try:
            raw = episode.path.read_bytes()
            payload = generate_report.load_payload(raw, episode.path)
            result = summarize_campaign.Result(episode.path, payload)
            document = generate_report.report_document(payload, episode.path, raw, trace=no_trace)
        except Exception as exc:  # noqa: BLE001 - one unreadable record must not hide the rest
            episode.error = f"{type(exc).__name__}: {exc}"
            broken.append({"path": str(episode.path), "error": episode.error})
            continue
        episode.payload, episode.result, episode.document = payload, result, document
        episode.contender = contender_key(result)
    return episodes, broken


# -- comparing the subjects ---------------------------------------------------------


def contenders(episodes: Sequence[Episode]) -> list[dict[str, Any]]:
    """Every subject-and-condition that produced a result, in the order the matrix met them.

    The condition joins the label only when the rows differ by it: a campaign run under one
    condition would otherwise repeat it in every header (the overview still names it).
    """
    firsts: dict[str, Any] = {}
    for episode in episodes:
        if episode.result is not None:
            firsts.setdefault(episode.contender, episode.result)
    varied = len({result.condition.label for result in firsts.values()}) > 1
    return [{
        "key": key,
        "label": contender_label(result, with_condition=varied),
        "subject": result.subject,
        "model": result.condition.model,
        "condition": result.condition.label,
    } for key, result in firsts.items()]


def members_of(episodes: Sequence[Episode], key: str) -> list[Episode]:
    return [episode for episode in episodes if episode.contender == key]


def overview(episodes: Sequence[Episode], order: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`summarize_campaign`'s cell for each contender, with the interval its repairs allow."""
    rows = []
    for contender in order:
        members = [episode.result for episode in members_of(episodes, contender["key"])]
        cell = summarize_campaign.summarize_cell(members, BY)
        low, high = (evaluation_parameters.wilson_interval(cell["repair_passed"], cell["scored"])
                     if cell["scored"] else (None, None))
        rows.append({"contender": contender["key"], **cell, "repair_interval": [low, high]})
    return rows


def plate_of(result: Any) -> tuple[str, Any, Any]:
    """The fault an episode was measured on: the scenario, its seed, and whether it was injected."""
    provenance = result.document.get("provenance") or {}
    metrics = result.document.get("metrics") or {}
    return (result.scenario, provenance.get("scenario_seed"), metrics.get("fault_applicable"))


def family_of(scenario: str) -> str:
    return ".".join(str(scenario).split(".")[:2])


def outcome(members: Sequence[Episode]) -> dict[str, Any]:
    """How one contender did on one plate. With one episode per plate, as a campaign runs
    it, `repaired` is whether that episode repaired; with repeats, whether most did."""
    judged = [episode for episode in members if episode.result.repair_passed is not None]
    repaired = sum(1 for episode in judged if episode.result.repair_passed is True)
    majority = None
    if judged and repaired * 2 != len(judged):
        majority = repaired * 2 > len(judged)
    return {
        "episodes": [episode.number for episode in members],
        "judged": len(judged),
        "repaired_count": repaired,
        "repaired": majority,
        "termination": dict(Counter(episode.result.termination for episode in members)),
        "turns": median(episode.result.measures["model_turns"] for episode in members),
        "elapsed_seconds": median(episode.result.measures["elapsed_seconds"] for episode in members),
    }


def plates(episodes: Sequence[Episode], order: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple, dict[str, list[Episode]]] = {}
    for episode in episodes:
        if episode.result is None:
            continue
        grouped.setdefault(plate_of(episode.result), {}).setdefault(episode.contender, []).append(episode)
    rows = []
    for (scenario, seed, fault), by_contender in sorted(grouped.items(), key=lambda item: tuple(map(str, item[0]))):
        rows.append({
            "scenario": scenario,
            "family": family_of(scenario),
            "seed": seed,
            "fault_applicable": fault,
            "outcomes": {contender["key"]: outcome(by_contender[contender["key"]])
                         for contender in order if contender["key"] in by_contender},
        })
    return rows


def pairs(plate_rows: Sequence[Mapping[str, Any]], order: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """For every two contenders, the plates both were judged on: who repaired which."""
    result = []
    for first_index, first in enumerate(order):
        for second in order[first_index + 1:]:
            tally = Counter()
            for row in plate_rows:
                a = (row["outcomes"].get(first["key"]) or {}).get("repaired")
                b = (row["outcomes"].get(second["key"]) or {}).get("repaired")
                if a is None or b is None:
                    continue
                tally["compared"] += 1
                tally["both" if a and b else "only_a" if a else "only_b" if b else "neither"] += 1
            result.append({"a": first["key"], "b": second["key"],
                           **{name: tally.get(name, 0)
                              for name in ("compared", "both", "only_a", "only_b", "neither")}})
    return result


def families(plate_rows: Sequence[Mapping[str, Any]], episodes: Sequence[Episode],
             order: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Repairs by scenario family, the way the campaign READMEs compare two cells."""
    names = sorted({row["family"] for row in plate_rows})
    rows = []
    for name in names:
        row: dict[str, Any] = {"family": name,
                               "plates": sum(1 for plate in plate_rows if plate["family"] == name),
                               "repairs": {}}
        for contender in order:
            members = [episode.result for episode in members_of(episodes, contender["key"])
                       if family_of(episode.result.scenario) == name]
            if members:
                row["repairs"][contender["key"]] = {
                    "repaired": sum(1 for member in members if member.repair_passed is True),
                    "scored": sum(1 for member in members if member.scored),
                }
        rows.append(row)
    return rows


def parameters(episodes: Sequence[Episode], order: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`evaluation_parameters` over each contender's episodes."""
    found: dict[str, Any] = {}
    for contender in order:
        documents = [episode.payload for episode in members_of(episodes, contender["key"])]
        try:
            found[contender["key"]] = evaluation_parameters.parameters_for(
                [evaluation_parameters.episode_facts(document) for document in documents])
        except Exception as exc:  # noqa: BLE001 - the other tables stand without these
            found[contender["key"]] = {"error": f"{type(exc).__name__}: {exc}"}
    return found


def retrieval(episodes: Sequence[Episode], order: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the retrieval subjects looked up (`execution.rag`); empty when none did."""
    found: dict[str, Any] = {}
    for contender in order:
        logs = [((episode.payload.get("sut_result") or {}).get("execution") or {}).get("rag")
                for episode in members_of(episodes, contender["key"])]
        logs = [log for log in logs if isinstance(log, Mapping)]
        if not logs:
            continue
        searches = [int(log.get("tool_searches") or 0) for log in logs]
        found[contender["key"]] = {
            "episodes": len(logs),
            "context_excerpts": sum(int(log.get("context_excerpts") or 0) for log in logs),
            "tool_searches": sum(searches),
            "tool_searches_median": statistics.median(searches),
            "episodes_that_searched": sum(1 for count in searches if count),
            "failed_searches": sum(1 for log in logs for search in (log.get("searches") or [])
                                   if isinstance(search, Mapping) and search.get("error")),
        }
    return found


def median(values: Any) -> float | None:
    numbers = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return statistics.median(numbers) if numbers else None


# -- the document -------------------------------------------------------------------


def episode_row(episode: Episode, labels: Mapping[str, str], names: Mapping[str, str],
                output_dir: Path) -> dict[str, Any]:
    cell = episode.cell
    row: dict[str, Any] = {
        "number": episode.number,
        "anchor": episode.anchor,
        "state": cell.get("state"),
        "result": str(episode.path) if episode.path else None,
        "report": relative_link(episode.report, output_dir),
        "error": cell.get("error") or episode.error,
    }
    if episode.result is None:
        # A cell that never produced a result: what the matrix asked for, and why not.
        row.update({
            "contender": None,
            "label": (f"{names.get(cell.get('architecture'), cell.get('architecture'))} · "
                      f"{summarize_campaign.short_model([str(cell.get('model'))])}"),
            "scenario": cell.get("scenario_id"),
            "seed": cell.get("seed"),
            "fault_applicable": not cell.get("no_fault"),
        })
        return row
    result = episode.result
    document = episode.payload
    evaluations = document.get("evaluations") or {}
    operations = (document.get("sut_result") or {}).get("ani_operations")
    scenario, seed, fault = plate_of(result)
    row.update({
        "contender": episode.contender,
        "label": labels.get(episode.contender, result.subject),
        "scenario": scenario,
        "seed": seed,
        "fault_applicable": fault,
        "success": (document.get("metrics") or {}).get("success"),
        "judge_status": document.get("status"),
        "repair": (evaluations.get("repair") or {}).get("passed"),
        "preservation": (evaluations.get("preservation") or {}).get("passed"),
        "termination": result.termination,
        "turns": result.measures["model_turns"],
        "elapsed_seconds": result.measures["elapsed_seconds"],
        "output_tokens": result.measures["output_tokens"],
        "ani_calls": len(operations) if isinstance(operations, list) else None,
        "conclusion": conclusion_of(document),
    })
    return row


def conclusion_of(document: Mapping[str, Any]) -> str | None:
    """The subject's own last word, `{status, summary}`, when it concluded."""
    final = (document.get("sut_result") or {}).get("final_response")
    return final.get("summary") if isinstance(final, Mapping) else None


def subject_names(episodes: Sequence[Episode]) -> dict[str, str]:
    """The name each subject gives itself (its agent card), by the key the matrix uses."""
    return {episode.cell.get("architecture"): episode.result.subject
            for episode in episodes if episode.result is not None}


def relative_link(target: Path | None, output_dir: Path) -> str | None:
    if target is None:
        return None
    return Path(os.path.relpath(target.resolve(), output_dir.resolve())).as_posix()


def campaign_document(directory: Path, *, output_dir: Path, manifest: Mapping[str, Any] | None = None,
                      read_trace: bool = True) -> tuple[dict[str, Any], list[Episode]]:
    """The report as data, and the episodes the page renders it from."""
    manifest = manifest if manifest is not None else read_manifest(directory)
    episodes, broken = read_episodes(manifest, read_trace=read_trace)
    order = contenders(episodes)
    labels = {contender["key"]: contender["label"] for contender in order}
    names = subject_names(episodes)
    plate_rows = plates(episodes, order)
    read = [episode for episode in episodes if episode.result is not None]
    document = {
        "report_version": REPORT_VERSION,
        "kind": REPORT_KIND,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "directory": str(directory),
            "episodes": len(episodes),
            "results": len(read),
            "broken": broken,
        },
        "campaign": campaign_block(manifest, directory),
        "totals": {
            "episodes": len(episodes),
            "results": len(read),
            "repair_passed": sum(1 for episode in read if episode.result.repair_passed is True),
            "scored": sum(1 for episode in read if episode.result.scored),
            "success": sum(1 for episode in read if (episode.payload.get("metrics") or {}).get("success") is True),
        },
        "contenders": order,
        "comparison": {
            "overview": overview(episodes, order),
            "families": families(plate_rows, episodes, order),
            "plates": plate_rows,
            "pairs": pairs(plate_rows, order),
            "parameters": parameters(episodes, order),
            "retrieval": retrieval(episodes, order),
        },
        "episodes": [episode_row(episode, labels, names, output_dir) for episode in episodes],
    }
    return document, episodes


def campaign_block(manifest: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    request = manifest.get("request") or {}
    return {
        "campaign_id": manifest.get("campaign_id") or directory.name,
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "error": manifest.get("error"),
        "counts": manifest.get("counts"),
        "subjects": request.get("architectures"),
        "models": request.get("models"),
        "execution_budget_seconds": request.get("execution_budget_seconds"),
        "seed_campaign": request.get("seed_campaign"),
    }


# -- the page ---------------------------------------------------------------------

escaped = generate_report.escaped
mark = generate_report.mark
rows_table = generate_report.rows_table
html_mapping_table = generate_report.html_mapping_table

#: What the campaign page adds to the look of the per-episode report.
CAMPAIGN_STYLE = """    .scroll { overflow-x: auto; }
    .scroll table { min-width: 100%; width: auto; } .scroll th, .scroll td { white-space: nowrap; }
    .cell-note { color: #6b7280; font-size: .85rem; }
    details { border: 1px solid #dbe2ea; border-radius: .4rem; margin: .6rem 0; background: #fbfcfe; }
    details > summary { cursor: pointer; padding: .6rem .8rem; font-weight: 600; }
    details[open] > summary { border-bottom: 1px solid #dbe2ea; }
    details section { border: none; margin: 0; }
    nav.toc a { margin-right: 1rem; }
    .legend { color: #4b5563; font-size: .85rem; }
    .verdict.neutral { background: #334155; }
"""


def number(value: Any, digits: int | None = None) -> str:
    """A count as a whole number, a measure with one decimal, nothing as a dash."""
    if value is None:
        return "–"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return escaped(value)
    if digits is not None:
        return f"{value:.{digits}f}"
    if isinstance(value, int) or value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def fault_text(value: Any) -> str:
    return "injected" if value is True else "false positive" if value is False else "–"


def counter_text(counter: Mapping[str, int]) -> str:
    return escaped(" ".join(f"{key}:{value}" for key, value in sorted(counter.items())) or "–")


def repaired_text(repaired: int, scored: int) -> str:
    return f"{repaired}/{scored}" if scored else "–"


def verdict_html(row: Mapping[str, Any]) -> str:
    if row.get("contender") is None:
        return f'<span class="mark unknown">–</span> {escaped(row.get("state"))}'
    if row.get("judge_status") == "failed":
        return '<span class="mark warn">⚠</span> run error'
    return mark(row.get("success"), "PASS", "FAIL")


def rate_text(entry: Mapping[str, Any] | None) -> str:
    """One evaluation parameter as `evaluation_parameters.render_text` writes it."""
    if not isinstance(entry, Mapping):
        return "–"
    text = evaluation_parameters.format_rate(entry).strip()
    if entry.get("status") == "measured" and entry.get("denominator"):
        low, high = evaluation_parameters.wilson_interval(entry["numerator"], entry["denominator"])
        text += f" [{low:.2f}, {high:.2f}]"
    if entry.get("same_as"):
        text += f" (= {entry['same_as']})"
    if entry.get("proxy_for"):
        text += " (proxy)"
    return escaped(text)


def overview_html(document: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    rows = []
    for row in document["comparison"]["overview"]:
        low, high = row["repair_interval"]
        interval = f" [{low:.2f}, {high:.2f}]" if low is not None else ""
        rate = f"{row['repair_rate']:.2f}{interval}" if row["repair_rate"] is not None else "–"
        rows.append([
            escaped(labels[row["contender"]]), escaped(row["condition"]), escaped(row["n"]),
            escaped(repaired_text(row["repair_passed"], row["scored"])), escaped(rate),
            escaped(summarize_campaign.fmt_spread(row["model_turns"])),
            escaped(summarize_campaign.fmt_spread(row["output_tokens"])),
            escaped(summarize_campaign.fmt_spread(row["elapsed_seconds"])),
            escaped(f"{summarize_campaign.fmt_spread(row['successful_mutations'])} / "
                    f"{summarize_campaign.fmt_spread(row['ani_mutations'])}"),
            counter_text(row["termination"]),
            counter_text(row["flags"]),
        ])
    return ("<h3>Overview</h3>"
            '<p class="legend">One row per subject and condition, as <code>summarize_campaign.py</code> '
            "groups them. Turns, output tokens, seconds and mutations are median/min/max over the row; "
            "the bracket is the 95% Wilson interval of the repair rate.</p>"
            '<div class="scroll">'
            + rows_table(["Subject · model", "Condition", "Episodes", "Repaired", "Repair rate", "Turns",
                          "Output tokens", "Seconds", "Mutations ok / made", "Termination", "Flags"], rows)
            + "</div>")


def families_html(document: Mapping[str, Any], order: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    totals: dict[str, list[int]] = {contender["key"]: [0, 0] for contender in order}
    for row in document["comparison"]["families"]:
        cells = [escaped(row["family"]), escaped(row["plates"])]
        for contender in order:
            found = row["repairs"].get(contender["key"])
            if found:
                totals[contender["key"]][0] += found["repaired"]
                totals[contender["key"]][1] += found["scored"]
            cells.append(escaped(repaired_text(found["repaired"], found["scored"])) if found else "–")
        rows.append(cells)
    rows.append(["<strong>all</strong>", f"<strong>{len(document['comparison']['plates'])}</strong>",
                 *(f"<strong>{escaped(repaired_text(*totals[contender['key']]))}</strong>"
                   for contender in order)])
    return ("<h3>Repairs by scenario family</h3>"
            '<div class="scroll">'
            + rows_table(["Family", "Plates", *(contender["label"] for contender in order)], rows)
            + "</div>")


def plates_html(document: Mapping[str, Any], order: Sequence[Mapping[str, Any]],
                labels: Mapping[str, str]) -> str:
    rows = []
    for plate in document["comparison"]["plates"]:
        cells = [escaped(plate["scenario"]), escaped(plate["seed"]), escaped(fault_text(plate["fault_applicable"]))]
        for contender in order:
            found = plate["outcomes"].get(contender["key"])
            if not found:
                cells.append("–")
                continue
            links = " ".join(f'<a href="#episode-{number}">#{number}</a>' for number in found["episodes"])
            verdict = mark(found["repaired"], "repaired", "not repaired", "not judged")
            if found["judged"] > 1:
                verdict += f" ({found['repaired_count']}/{found['judged']})"
            detail = (f"{counter_text(found['termination'])} · {number(found['turns'])} turns · "
                      f"{number(found['elapsed_seconds'])} s")
            cells.append(f"{verdict} {links}<br><span class=\"cell-note\">{detail}</span>")
        rows.append(cells)
    pair_rows = [[
        escaped(labels[pair["a"]]), escaped(labels[pair["b"]]), escaped(pair["compared"]),
        escaped(pair["both"]), escaped(pair["only_a"]), escaped(pair["only_b"]), escaped(pair["neither"]),
    ] for pair in document["comparison"]["pairs"]]
    pair_table = ("<h3>Who repaired what the other did not</h3>"
                  '<p class="legend">Over the plates both were judged on. A plate one of them repaired and '
                  "the other did not is where they differ; the rest is where they agree.</p>"
                  + rows_table(["A", "B", "Plates compared", "Both", "Only A", "Only B", "Neither"], pair_rows)
                  ) if pair_rows else ""
    return ("<h3>Plate by plate</h3>"
            '<p class="legend">The same scenario, seed and fault for every subject: the one comparison '
            "a matrix exists for. Each verdict links to the episode's interactions below.</p>"
            '<div class="scroll">'
            + rows_table(["Scenario", "Seed", "Fault", *(contender["label"] for contender in order)], rows)
            + "</div>" + pair_table)


def terminations_html(document: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    overview_rows = document["comparison"]["overview"]
    causes = sorted({cause for row in overview_rows for cause in row["termination"]})
    rows = [[escaped(labels[row["contender"]]),
             *(escaped(row["termination"].get(cause, 0)) for cause in causes),
             number(row["elapsed_seconds"]["median"]), number(row["model_turns"]["median"])]
            for row in overview_rows]
    return ("<h3>How the episodes ended</h3>"
            '<p class="legend">The cause each episode ended on, as <code>summarize_campaign.py</code> '
            "names it: the subject's own conclusion, its stop rules, the clock, or the judge.</p>"
            '<div class="scroll">'
            + rows_table(["Subject · model", *causes, "Median seconds", "Median model calls"], rows)
            + "</div>")


def parameters_html(document: Mapping[str, Any], order: Sequence[Mapping[str, Any]]) -> str:
    found = document["comparison"]["parameters"]
    rows = [[escaped(name), *(rate_text((found.get(contender["key"]) or {}).get(name)) for contender in order)]
            for name in PARAMETER_ROWS]
    mix = [[escaped(bucket),
            *(rate_text(((found.get(contender["key"]) or {}).get("ani_call_type_ratio") or {}).get(bucket))
              for contender in order)]
           for bucket in evaluation_parameters.BUCKET_ORDER]
    errors = "".join(f"<p>{escaped(contender['label'])}: {escaped(found[contender['key']]['error'])}</p>"
                     for contender in order if "error" in (found.get(contender["key"]) or {}))
    return ("<h3>Evaluation parameters</h3>"
            '<p class="legend">From <code>evaluation_parameters.py</code>: value, numerator/denominator, '
            "and the 95% Wilson interval. <code>0.000*</code> structurally zero (the surface has no such "
            "operation); <code>-/0</code> no denominator (the campaign never asked the question); "
            "<code>n/a</code> unavailable (the record carries no signal for it).</p>"
            + errors
            + '<div class="scroll">'
            + rows_table(["Parameter", *(contender["label"] for contender in order)], rows)
            + "</div><h3>What the calls were</h3>"
            '<div class="scroll">'
            + rows_table(["Kind of call", *(contender["label"] for contender in order)], mix)
            + "</div>")


def retrieval_html(document: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    found = document["comparison"]["retrieval"]
    if not found:
        return ""
    rows = [[escaped(labels[key]), escaped(value["episodes"]), escaped(value["context_excerpts"]),
             escaped(value["tool_searches"]), number(value["tool_searches_median"], 1),
             escaped(f"{value['episodes_that_searched']}/{value['episodes']}"), escaped(value["failed_searches"])]
            for key, value in found.items()]
    return ("<h3>Document retrieval</h3>"
            '<p class="legend">For the subjects that retrieve from reference documents '
            "(<code>execution.rag</code>). Searches are not ANI calls: they are counted in none of the "
            "tables above, the <code>search</code> row of the call mix included, which is about the ANI.</p>"
            + rows_table(["Subject · model", "Episodes", "Excerpts given with the task", "Searches by the model",
                          "Median searches per episode", "Episodes that searched", "Failed searches"], rows))


def episodes_html(document: Mapping[str, Any]) -> str:
    rows = []
    for row in document["episodes"]:
        report = (f'<a href="{escaped(row["report"])}">report</a>' if row.get("report") else "–")
        interactions = (f'<a href="#{row["anchor"]}">interactions</a>'
                        if row.get("contender") is not None else "–")
        rows.append([
            escaped(row["number"]), escaped(row["label"]), escaped(row["scenario"]), escaped(row["seed"]),
            escaped(fault_text(row["fault_applicable"])), verdict_html(row),
            mark(row.get("repair"), "yes", "no", "–") if row.get("contender") else "–",
            mark(row.get("preservation"), "yes", "no", "–") if row.get("contender") else "–",
            escaped(row.get("termination") or "–"), number(row.get("turns")),
            number(row.get("elapsed_seconds")), number(row.get("ani_calls")),
            f"{interactions} · {report}", escaped(row.get("error") or ""),
        ])
    return ('<section id="episodes"><h2>Episodes</h2><div class="scroll">'
            + rows_table(["#", "Subject · model", "Scenario", "Seed", "Fault", "Verdict", "Repair",
                          "Preservation", "Termination", "Turns", "Seconds", "ANI calls", "Pages", "Error"], rows)
            + "</div></section>")


def interactions_html(document: Mapping[str, Any], episodes: Sequence[Episode]) -> str:
    """Every episode's timeline, as its own report renders it, folded until opened."""
    rows = {row["number"]: row for row in document["episodes"]}
    blocks = []
    for episode in episodes:
        if episode.result is None:
            continue
        row = rows[episode.number]
        summary = (f"#{episode.number} · {escaped(row['label'])} · {escaped(row['scenario'])} · "
                   f"seed {escaped(row['seed'])} · {verdict_html(row)}")
        body = []
        if row.get("report"):
            body.append(f'<p><a href="{escaped(row["report"])}">The episode\'s full report</a> '
                        "(oracles, metrics, device configurations, provenance).</p>")
        if row.get("conclusion"):
            body.append(f"<p><strong>The subject's conclusion:</strong> {escaped(row['conclusion'])}</p>")
        body.append(generate_report.timeline_html(episode.document["timeline"]))
        blocks.append(f'<details id="{episode.anchor}"><summary>{summary}</summary>'
                      f'<div style="padding: 0 .8rem .6rem">{"".join(body)}</div></details>')
    return ('<section id="interactions"><h2>Interactions</h2>'
            '<p class="legend">Each episode in the order of the table above: every turn of the model and '
            "every call it made, with the arguments it chose. The model's words appear when the subject "
            "ran with a trace at level <code>full</code>.</p>"
            + ("".join(blocks) or "<p>No episode produced a result.</p>") + "</section>")


def campaign_html(document: Mapping[str, Any]) -> str:
    campaign = document["campaign"]
    counts = campaign.get("counts")
    values = {
        "campaign": escaped(campaign.get("campaign_id")),
        "directory": escaped(document["source"]["directory"]),
        "started": escaped(campaign.get("started_at") or "–"),
        "finished": escaped(campaign.get("finished_at") or "–"),
        "subjects": escaped(", ".join(campaign.get("subjects") or []) or "–"),
        "models": escaped(", ".join(campaign.get("models") or []) or "–"),
        "budget": escaped(f"{campaign['execution_budget_seconds']} s"
                          if campaign.get("execution_budget_seconds") else "–"),
        "cells": counter_text(counts) if counts else escaped(document["source"]["episodes"]),
        "generated": escaped(document["generated_at"]),
    }
    if campaign.get("error"):
        values["stopped"] = f'<span class="mark bad">✗</span> {escaped(campaign["error"])}'
    broken = document["source"]["broken"]
    unreadable = ("<h3>Unreadable files</h3>"
                  + rows_table(["File", "Error"], [[escaped(item["path"]), escaped(item["error"])]
                                                   for item in broken])) if broken else ""
    return f"<section><h2>Campaign</h2>{html_mapping_table(values)}{unreadable}</section>"


def render_html(document: Mapping[str, Any], episodes: Sequence[Episode]) -> str:
    order = document["contenders"]
    labels = {contender["key"]: contender["label"] for contender in order}
    totals = document["totals"]
    title = f"{document['campaign'].get('campaign_id')} · campaign"
    headline = f"{totals['repair_passed']}/{totals['scored']} repaired"
    comparison = ("<p>No episode produced a result yet.</p>" if not order else
                  overview_html(document, labels)
                  + families_html(document, order)
                  + plates_html(document, order, labels)
                  + terminations_html(document, labels)
                  + parameters_html(document, order)
                  + retrieval_html(document, labels))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped(headline)} · {escaped(title)}</title>
  <style>
{generate_report.STYLE}{CAMPAIGN_STYLE}  </style>
</head>
<body>
<header>
  <div><h1>IBN campaign report</h1><p>{escaped(title)} · {escaped(totals['episodes'])} episodes, {len(order)} subject(s) and model(s)</p></div>
  <div class="verdict neutral">{escaped(headline)}</div>
</header>
<main>
  <nav class="toc"><a href="#comparison">Comparison</a><a href="#episodes">Episodes</a><a href="#interactions">Interactions</a></nav>
  {campaign_html(document)}
  <section id="comparison"><h2>Comparing the subjects</h2>{comparison}</section>
  {episodes_html(document)}
  {interactions_html(document, episodes)}
</main>
</body>
</html>
"""


def render_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n"


def output_base(directory: Path, output: str | Path | None) -> Path:
    base = Path(output) if output else directory / DEFAULT_REPORT_DIR / DEFAULT_BASENAME
    if base.suffix.lower() in {".json", ".html"}:
        base = base.with_suffix("")
    return base


def write_campaign_report(directory: str | Path, *, output: str | Path | None = None,
                          formats: tuple[str, ...] = ("json", "html"),
                          manifest: Mapping[str, Any] | None = None,
                          read_trace: bool = True) -> list[Path]:
    """Derive the campaign page from a campaign directory and write it; return the files.

    `manifest` stands in for the directory's `campaign.json` when the caller holds a newer
    one than the file, as the web interface does while it closes a campaign.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a directory")
    base = output_base(directory, output)
    document, episodes = campaign_document(directory, output_dir=base.parent, manifest=manifest,
                                           read_trace=read_trace)
    renderers = {"json": lambda: render_json(document), "html": lambda: render_html(document, episodes)}
    written = []
    for fmt in formats:
        path = Path(f"{base}.{fmt}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(renderers[fmt](), encoding="utf-8")
        written.append(path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("campaign_dir", type=Path,
                        help="a campaign directory the web interface wrote (it holds campaign.json)")
    parser.add_argument("-o", "--output",
                        help="base path for the report; default <campaign_dir>/reports/campaign.report")
    parser.add_argument("-f", "--format", choices=("both", "json", "html"), default="both")
    parser.add_argument("--no-trace", action="store_true",
                        help="do not read the episodes' traces: timelines keep their timings, not the model's text")
    args = parser.parse_args(argv)
    if not args.campaign_dir.is_dir():
        parser.error(f"{args.campaign_dir} is not a directory")
    formats = ("json", "html") if args.format == "both" else (args.format,)
    try:
        written = write_campaign_report(args.campaign_dir, output=args.output, formats=formats,
                                        read_trace=not args.no_trace)
    except ValueError as exc:
        parser.error(str(exc))
    for path in written:
        print(f"wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
