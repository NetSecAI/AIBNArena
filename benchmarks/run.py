#!/usr/bin/env python3
"""Run every benchmark domain through the same fourteen-phase lifecycle."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from benchmarks.configs import load_experiment, load_scenario  # noqa: E402
from benchmarks.core import (  # noqa: E402
    A2ASUTClient,
    BenchmarkLifecycle,
    LifecycleDependencies,
    LifecycleError,
    OracleEvaluator,
    ResultWriter,
)
from benchmarks.core.diagnosis import judge_from_config  # noqa: E402
from benchmarks.domains.metrics import DOMAIN_PREFIXES, for_domain  # noqa: E402
from benchmarks.platforms.containerlab import ContainerlabPlatform  # noqa: E402
from scenarios.oracle_loader import load_oracle, resolve_oracle  # noqa: E402

import importlib.util  # noqa: E402

# scripts/ is not a package; load the report generator the way export_results.py does.
_report_spec = importlib.util.spec_from_file_location(
    "generate_report", REPOSITORY / "scripts" / "generate_report.py")
generate_report = importlib.util.module_from_spec(_report_spec)
_report_spec.loader.exec_module(generate_report)


DEFAULT_CONFIG = "benchmarks/configs/experiments/connectivity-smoke.toml"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", "-c", default=DEFAULT_CONFIG, help="Experiment/suite TOML")
    result.add_argument("--sut-url", default=os.getenv("IBN_SUT_URL"), help="Running SUT A2A URL (or IBN_SUT_URL)")
    result.add_argument("--sut-timeout-seconds", type=float, help="A2A timeout; defaults to budget + 120 seconds")
    result.add_argument("--scenario-id", help="Override the scenario selected by the experiment TOML")
    result.add_argument("--seed", type=int, help="Override the deterministic scenario seed")
    result.add_argument("--experiment-id", help="Override the result experiment identifier")
    result.add_argument("--execution-budget-seconds", type=float, help="Override the benchmark-owned SUT wall-clock budget")
    result.add_argument("--cleanup", choices=("restore", "destroy"), help="Override what the experiment does with the lab once the episode is judged")
    result.add_argument("--result-dir", help="Override the output directory for this campaign")
    result.add_argument("--report-dir", help="Override where the report derived from each result is written")
    result.add_argument("--no-fault", action="store_true", help="Run the selected instance on a healthy lab without injecting its fault: the false-positive episode, where any attempted mutation is the failure")
    result.add_argument("--configured-model", help="The model this campaign meant to run; recorded as provenance.configured_model and checked against what the SUT reports")
    result.add_argument("--seed-campaign", type=int, help="The campaign this episode is one cell of; recorded as provenance.seed_campaign, the same in every record of one campaign")
    result.add_argument("--diagnosis-judge-model", default=os.getenv("IBN_DIAGNOSIS_JUDGE_MODEL"),
                        help="The model that judges the subject's stated diagnosis against the injected fault (evaluation parameter 13, ParaPLUIE); or IBN_DIAGNOSIS_JUDGE_MODEL. Overrides the experiment's [diagnosis_judge] table; without either, the diagnosis is recorded unjudged")
    result.add_argument("--diagnosis-judge-api-base", default=os.getenv("IBN_DIAGNOSIS_JUDGE_API_BASE") or os.getenv("LLM_API_BASE"),
                        help="OpenAI-compatible base URL of the diagnosis judge; or IBN_DIAGNOSIS_JUDGE_API_BASE, or the subject's own LLM_API_BASE. Its key is read from IBN_DIAGNOSIS_JUDGE_API_KEY, or the subject's LLM_API_KEY, never from the command line")
    result.add_argument("--diagnosis-judge-reading", choices=("prompt_logprobs", "top_logprobs"),
                        default=os.getenv("IBN_DIAGNOSIS_JUDGE_READING"),
                        help="How the judge reads the Yes/No log-probabilities: prompt_logprobs (vLLM, exact, the default) or top_logprobs (OpenAI API: the candidates of the generated token); or IBN_DIAGNOSIS_JUDGE_READING")
    result.add_argument("--diagnosis-judge-audit-log", default=os.getenv("IBN_DIAGNOSIS_JUDGE_AUDIT_LOG"),
                        help="Where every judge call is appended as one JSON line with its raw candidates; default <report-dir>/diagnosis-audit.jsonl")
    result.add_argument("--diagnosis-judge-extra-body", default=os.getenv("IBN_DIAGNOSIS_JUDGE_EXTRA_BODY"),
                        help="JSON object merged into every judge request, for a gateway's own fields (OpenRouter: provider pinning, reasoning off); or IBN_DIAGNOSIS_JUDGE_EXTRA_BODY")
    result.add_argument("--validate-only", action="store_true", help="Compile and validate configuration/scenario/oracles without touching Containerlab or A2A")
    result.add_argument("--show-config", action="store_true", help="Print the composed configuration and selected scenario")
    result.add_argument("--list-benchmarks", action="store_true", help="List domains and exit")
    return result


def validate_bundle(
    config_path: str | Path,
    *,
    scenario_id: str | None = None,
    seed: int | None = None,
    experiment_id: str | None = None,
    execution_budget_seconds: float | None = None,
    result_dir: str | Path | None = None,
    report_dir: str | Path | None = None,
    configured_model: str | None = None,
    seed_campaign: int | None = None,
    fault_applicable: bool | None = None,
    cleanup: str | None = None,
    diagnosis_judge: dict | None = None,
):
    config = load_experiment(
        config_path,
        experiment_id=experiment_id,
        execution_budget_seconds=execution_budget_seconds,
        result_dir=result_dir,
        configured_model=configured_model,
        seed_campaign=seed_campaign,
        cleanup=cleanup,
        diagnosis_judge=diagnosis_judge,
    )
    scenario = load_scenario(config.scenario_path, scenario_id=scenario_id, seed=seed,
                             fault_applicable=fault_applicable)
    oracles = {
        phase: resolve_oracle(
            load_oracle(reference.path, expected_version=reference.version),
            scenario.bindings,
        )
        for phase, reference in scenario.oracles.items()
    }
    return config, scenario, oracles


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.list_benchmarks:
        print("\n".join(sorted(DOMAIN_PREFIXES)))
        return 0

    judge_override = diagnosis_judge_override(args)
    config, scenario, oracles = validate_bundle(
        args.config,
        scenario_id=args.scenario_id,
        seed=args.seed,
        experiment_id=args.experiment_id,
        execution_budget_seconds=args.execution_budget_seconds,
        result_dir=args.result_dir,
        report_dir=args.report_dir,
        configured_model=args.configured_model,
        seed_campaign=args.seed_campaign,
        fault_applicable=False if args.no_fault else None,
        cleanup=args.cleanup,
        diagnosis_judge=judge_override,
    )
    if args.show_config or args.validate_only:
        print(json.dumps({
            "experiment": config.experiment_id,
            "scenario": {"id": scenario.scenario_id, "version": scenario.version, "domain": scenario.domain, "seed": scenario.seed},
            "testbed": dict(config.testbed),
            "execution_budget_seconds": config.execution_budget_seconds,
            "fault_applicable": scenario.fault_applicable,
            "oracles": {phase: f"{doc['oracle_id']}@{doc['version']}" for phase, doc in oracles.items()},
            "diagnosis_judge": ({key: value for key, value in config.diagnosis_judge.items() if key != "api_key"}
                                if config.diagnosis_judge else None),
        }, indent=2, sort_keys=True))
        if args.validate_only:
            return 0

    if not args.sut_url:
        argument_parser.error("--sut-url or IBN_SUT_URL is required for a live run")

    platform = ContainerlabPlatform()
    dependencies = LifecycleDependencies(
        config_loader=lambda path: load_experiment(
            path,
            experiment_id=args.experiment_id,
            execution_budget_seconds=args.execution_budget_seconds,
            result_dir=args.result_dir,
            report_dir=args.report_dir,
            configured_model=args.configured_model,
            seed_campaign=args.seed_campaign,
            cleanup=args.cleanup,
            diagnosis_judge=judge_override,
        ),
        scenario_loader=lambda path: load_scenario(
            path, scenario_id=args.scenario_id, seed=args.seed,
            fault_applicable=False if args.no_fault else None,
        ),
        oracle_loader=lambda path, version: load_oracle(path, expected_version=version),
        platform=platform,
        evaluator=OracleEvaluator(platform),
        sut_client=A2ASUTClient(
            args.sut_url,
            execution_budget_seconds=config.execution_budget_seconds,
            timeout_seconds=args.sut_timeout_seconds,
        ),
        result_sink=ResultWriter(),
        trace_sink=_print_phase,
        repository=REPOSITORY,
        domain_metrics=for_domain(scenario.domain),
        diagnosis_judge=judge_from_config(diagnosis_judge_settings(config, args)),
    )
    try:
        output = BenchmarkLifecycle(dependencies).run(args.config)
    except LifecycleError as exc:
        if exc.result_path is not None:
            _print_verdict(exc.result_path)
            _write_report(exc.result_path, config.report_dir)
        print(str(exc), file=sys.stderr)
        return 1
    _print_verdict(output)
    _write_report(output, config.report_dir)
    return 0


def diagnosis_judge_override(args: argparse.Namespace) -> dict | None:
    """The judge the command line (or its environment) names, or None to let the
    experiment file decide. Naming one without the other is refused: a judge with no
    endpoint would be recorded as configured and never reached."""
    model = getattr(args, "diagnosis_judge_model", None)
    api_base = getattr(args, "diagnosis_judge_api_base", None)
    if not model:
        return None
    if not api_base:
        raise SystemExit("--diagnosis-judge-model needs an endpoint: --diagnosis-judge-api-base, "
                         "IBN_DIAGNOSIS_JUDGE_API_BASE, or the subject's LLM_API_BASE")
    override = {"model": str(model), "api_base": str(api_base)}
    # The judge's key: its own when one is set, otherwise the one the subject's
    # provider takes, so a judge on the same provider needs no second secret.
    key = os.getenv("IBN_DIAGNOSIS_JUDGE_API_KEY") or os.getenv("LLM_API_KEY")
    if key:
        override["api_key"] = key
    reading = getattr(args, "diagnosis_judge_reading", None)
    if reading:
        override["reading"] = str(reading)
    audit = getattr(args, "diagnosis_judge_audit_log", None)
    if audit:
        override["audit_log"] = str(audit)
    extra = _extra_body(args)
    if extra:
        override["extra_body"] = extra
    return override


def _extra_body(args: argparse.Namespace) -> dict | None:
    raw = getattr(args, "diagnosis_judge_extra_body", None)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SystemExit(f"--diagnosis-judge-extra-body is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--diagnosis-judge-extra-body must be a JSON object")
    return parsed


def diagnosis_judge_settings(config, args: argparse.Namespace) -> dict | None:
    """The judge the lifecycle runs with: the experiment's, completed with the
    command line's reading and audit log, and an audit log beside the reports when
    none was named."""
    judge = dict(config.diagnosis_judge or {})
    if not judge:
        return None
    reading = getattr(args, "diagnosis_judge_reading", None)
    if reading:
        judge["reading"] = str(reading)
    audit = getattr(args, "diagnosis_judge_audit_log", None)
    if audit:
        judge["audit_log"] = str(audit)
    extra = _extra_body(args)
    if extra:
        judge["extra_body"] = extra
    if not judge.get("audit_log"):
        directory = Path(config.report_dir)
        if not directory.is_absolute():
            directory = REPOSITORY / directory
        judge["audit_log"] = str(directory / "diagnosis-audit.jsonl")
    return judge


def _write_report(result_path: Path, report_dir: str | Path) -> None:
    """Report the episode in the experiment's `report_dir`. The result is the record and
    is already on disk, so a report that cannot be derived is said, never turned into a
    failed run."""
    directory = Path(report_dir)
    if not directory.is_absolute():
        directory = REPOSITORY / directory
    try:
        written = generate_report.write_report(
            result_path, output=directory / f"{result_path.stem}.report")
    except Exception as exc:  # noqa: BLE001 - reporting must not void a judged episode
        print(f"report: not written ({type(exc).__name__}: {exc})", file=sys.stderr)
        return
    for path in written:
        print(f"report: {path}")


def _print_phase(event: str, payload: Mapping[str, Any]) -> None:
    """Say each lifecycle phase as it starts and ends. The verdict is only printed
    at the end, and deploying the testbed alone takes about a minute; without this
    a judge at work reads the same as a judge that hung."""
    phase = payload.get("phase")
    if event == "phase_started":
        print(f"phase {phase}: started", flush=True)
    elif event == "phase_finished":
        print(f"phase {phase}: finished in {payload.get('duration_seconds', 0.0):.1f}s", flush=True)
    elif event == "phase_failed":
        print(f"phase {phase}: failed: {payload.get('error')}", flush=True)


def _print_verdict(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sut_result = payload.get("sut_result") or {}
    metrics = payload.get("metrics") or {}
    print("\nBenchmark result")
    print("================")
    print(f"benchmark_success: {metrics.get('success')}")
    print(f"environment_success: {metrics.get('environment_success')}")
    print(f"converged: {metrics.get('converged')}")
    print(f"lifecycle_status: {str(payload.get('status', 'unknown')).upper()}")
    print(f"sut_status: {str(sut_result.get('status', 'unknown')).upper()}")
    print(f"sut_verified: {sut_result.get('verified')}")
    print(f"scenario: {payload.get('scenario', {}).get('id')}")
    print(f"seed: {payload.get('provenance', {}).get('scenario_seed')}")
    diagnosis = metrics.get("diagnosis") if isinstance(metrics.get("diagnosis"), dict) else {}
    # Evaluation parameter 13 on this one episode: whether the subject's stated
    # diagnosis names the injected fault, the judge's margin for it, and which
    # model judged.
    print(f"root_cause_identified: {diagnosis.get('found')}")
    print(f"diagnosis_score: {diagnosis.get('score')}")
    judge = diagnosis.get("judge") if isinstance(diagnosis.get("judge"), dict) else {}
    print(f"diagnosis_judge: {judge.get('model')}" + (f" ({judge.get('reading')})" if judge.get('reading') else ""))
    print(f"result: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
