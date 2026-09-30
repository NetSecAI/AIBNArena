#!/usr/bin/env python
"""Score recorded episodes for evaluation parameter 13 after the fact.

The judge runs inside `benchmarks/run.py` for every new episode. This is for the
episodes already on disk: it replays each record's compiled instance (the same
replay `evaluation_parameters.py` uses for the faulty device), puts the injected
fault into words, takes the subject's statement of the fault from the record, asks
the ParaPLUIE judge, and prints the verdicts. With `--write` the block lands in the
record under `metrics.diagnosis`, marked as scored offline, so the parameter report
and the web verdict read it as if the judge had been there.

A record from before the diagnosis step has no stated diagnosis; its conclusion's
`summary` is scored instead and the block says so (`hypothesis_source: summary`).

    python scripts/score_diagnoses.py ../runs/e8 --judge-model ministral-3-8b-instruct \\
        --judge-api-base http://127.0.0.1:18002/v1 --write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for _path in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import evaluation_parameters  # noqa: E402
import summarize_campaign  # noqa: E402

from benchmarks.core import diagnosis  # noqa: E402


def reference_for_record(document: dict[str, Any], root: Path = REPO_ROOT) -> diagnosis.FaultReference | None:
    instance = evaluation_parameters.compiled_instance(document, root)
    if instance is None:
        return None
    private = instance.get("private") or {}
    method = (private.get("evaluator") or {}).get("selected_method")
    if not isinstance(method, dict):
        return None
    return diagnosis.reference_for_method(method, private.get("bindings") or {})


def score_record(document: dict[str, Any], judge: Any, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    metrics = document.get("metrics") if isinstance(document.get("metrics"), dict) else {}
    applicable = metrics.get("fault_applicable")
    reference = reference_for_record(document, root)
    block = diagnosis.assess_diagnosis(
        reference, document.get("sut_result") or {}, judge,
        fault_applicable=(applicable if isinstance(applicable, bool) else True))
    if reference is None and applicable is not False:
        block["reason"] = ("the compiled instance could not be replayed from the record's "
                           "topology digest and seed, so the fault cannot be described")
    block["offline"] = {"scored_at": datetime.now(timezone.utc).isoformat(),
                        "script": "scripts/score_diagnoses.py"}
    return block


def load_records(root: Path) -> tuple[list[Any], list[dict[str, str]]]:
    """Every **/records/*.json under the directory, as `load_results` reads results/."""
    results, broken = [], []
    for path in sorted(root.glob("**/records/*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            results.append(summarize_campaign.Result(path, document))
        except Exception as exc:  # noqa: BLE001 - listed, the rest still scored
            broken.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return results, broken


def write_back(path: Path, document: dict[str, Any], block: dict[str, Any]) -> None:
    metrics = document.setdefault("metrics", {})
    if not isinstance(metrics, dict):
        raise TypeError(f"{path}: metrics is not an object")
    metrics["diagnosis"] = block
    # The judge's own format (benchmarks/core/reporting.py): sorted keys, two spaces.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs_dir", type=Path, help="directory whose **/results/*.json are scored")
    parser.add_argument("--judge-model", default=os.getenv("IBN_DIAGNOSIS_JUDGE_MODEL"))
    parser.add_argument("--judge-api-base", default=os.getenv("IBN_DIAGNOSIS_JUDGE_API_BASE") or os.getenv("LLM_API_BASE"))
    parser.add_argument("--judge-reading", choices=("prompt_logprobs", "top_logprobs"),
                        default=os.getenv("IBN_DIAGNOSIS_JUDGE_READING") or "prompt_logprobs",
                        help="prompt_logprobs (vLLM, exact) or top_logprobs (OpenAI API candidates)")
    parser.add_argument("--audit-log", type=Path, default=None,
                        help="one JSON line per judge call with the raw candidates; default <runs_dir>/diagnosis-audit.jsonl")
    parser.add_argument("--judge-extra-body", default=os.getenv("IBN_DIAGNOSIS_JUDGE_EXTRA_BODY"),
                        help="JSON object merged into every judge request (OpenRouter: provider pinning, reasoning off)")
    parser.add_argument("--write", action="store_true", help="store the block in each record's metrics.diagnosis")
    parser.add_argument("--rescore", action="store_true", help="score records that already carry a judged block")
    parser.add_argument("--limit", type=int, help="score at most this many records")
    parser.add_argument("--json", type=Path, help="also write every block, keyed by record path, to this file")
    args = parser.parse_args(argv)
    if not (args.judge_model and args.judge_api_base):
        parser.error("--judge-model and --judge-api-base (or IBN_DIAGNOSIS_JUDGE_MODEL / "
                     "IBN_DIAGNOSIS_JUDGE_API_BASE) are required")
    judge = diagnosis.judge_from_config({
        "model": args.judge_model, "api_base": args.judge_api_base,
        # The judge's own key, or the one the subject's provider takes.
        "api_key": os.getenv("IBN_DIAGNOSIS_JUDGE_API_KEY") or os.getenv("LLM_API_KEY"),
        "reading": args.judge_reading,
        "audit_log": str(args.audit_log or (args.runs_dir / "diagnosis-audit.jsonl")),
        "extra_body": (json.loads(args.judge_extra_body) if args.judge_extra_body else None),
    })

    results, broken = summarize_campaign.load_results(args.runs_dir)
    if not results:
        # The exported campaigns keep their episodes under records/, not results/.
        results, broken = load_records(args.runs_dir)
    for item in broken:
        print(f"skipped {item['path']}: {item['error']}", file=sys.stderr)
    blocks: dict[str, Any] = {}
    counts = {"found": 0, "not_found": 0, "unjudged": 0}
    print(f"{'scenario':46} {'source':18} {'found':6} {'score':>8}  hypothesis")
    for result in results:
        if args.limit is not None and len(blocks) >= args.limit:
            break
        document = dict(result.document)
        existing = (document.get("metrics") or {}).get("diagnosis")
        if isinstance(existing, dict) and isinstance(existing.get("found"), bool) and not args.rescore:
            continue
        block = score_record(document, judge)
        blocks[str(result.path)] = block
        found = block.get("found")
        counts["found" if found is True else "not_found" if found is False else "unjudged"] += 1
        score = block.get("score")
        print(f"{result.scenario:46} {str(block.get('hypothesis_source')):18} {str(found):6} "
              f"{(f'{score:+.2f}' if isinstance(score, (int, float)) else '-'):>8}  "
              f"{(block.get('hypothesis') or '')[:70]}")
        if block.get("reason") and found is None:
            print(f"{'':46} reason: {block['reason']}")
        if args.write:
            write_back(result.path, document, block)
    print(f"\n{len(blocks)} scored: {counts['found']} found, {counts['not_found']} not found, "
          f"{counts['unjudged']} unjudged" + (" (written)" if args.write else ""))
    if args.json:
        args.json.write_text(json.dumps(blocks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
