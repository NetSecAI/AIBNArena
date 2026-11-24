#!/usr/bin/env python3
"""Check the combined scenarios' oracles on a live lab, with the reference restore as the repair.

For each combined task (base wording; the fault does not depend on the wording):

  healthy            must pass (else the reference state is re-applied and it is retried)
  inject             must succeed
  degradation        must pass, every component group (re-injected once if unseen)
  repair, faulted    must FAIL for every component group: an unrepaired fault is seen
  preservation, faulted  must pass: no fault reaches a path outside the union of blast radii
  restore + converge (the fault's own reference restore, DHCP renewed)
  repair, restored   must pass, every group
  preservation, restored must pass

Results go to a JSONL file (default reports/manual/combined_lab_check/<topology>-seed<seed>.jsonl),
one line per task with the time of each phase, flushed as they come; --resume skips the ids
already there. Tasks run m1 of every combination first, then m2, then m3, so a run stopped
early still covers every combination. The lab is deployed once (or reused with --reuse) and
re-settled to the reference state whenever a task leaves it unhealthy.

    .venv/bin/python scripts/check_combined_on_lab.py                    # deploy, check every task
    .venv/bin/python scripts/check_combined_on_lab.py --reuse --resume   # lab up: carry on
    .venv/bin/python scripts/check_combined_on_lab.py --reuse --methods 1 --only vlan_misconfiguration
    .venv/bin/python scripts/check_combined_on_lab.py --reuse --destroy  # destroy the lab at the end
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)  # the testbed and descriptor paths are relative to the repository

from benchmarks.configs.loader import _oracle_bindings, load_experiment, _read_toml, _root_path  # noqa: E402
from benchmarks.core.oracle_evaluator import OracleEvaluator, _combine  # noqa: E402
from benchmarks.platforms.containerlab import ContainerlabPlatform  # noqa: E402
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology  # noqa: E402
from scenarios.access import CompiledScenarioSuite  # noqa: E402
from scenarios.compiler.compiler import ScenarioCompiler  # noqa: E402
from scenarios.oracle_loader import load_oracle, resolve_oracle  # noqa: E402


def groups_of(oracle, evaluation):
    """{group: passed} under each group's own rule; {'*': passed} for a plain oracle."""
    rules = (oracle["success"].get("groups") or {}) if oracle["success"]["mode"] == "all_groups" else {}
    if not rules:
        return {"*": evaluation.passed}
    out = {}
    for group, rule in rules.items():
        values = tuple(item.passed for probe, item in zip(oracle["probes"], evaluation.probes)
                       if group in (probe.get("groups") or ()))
        out[group] = bool(values) and _combine(values, rule)
    return out


def summary_of(oracle, evaluation):
    return {
        "passed": evaluation.passed,
        "groups": groups_of(oracle, evaluation),
        "probes": {item.probe_id: {"passed": item.passed,
                                   **{k: v for k, v in item.metrics.items()
                                      if k in ("failed_checks", "passed_checks", "total_checks",
                                               "leaked_checks", "answer_count", "success")}}
                   for item in evaluation.probes},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", default="benchmarks/configs/experiments/campus_small-combined.toml")
    parser.add_argument("--seed", type=int, default=None, help="default: the experiment's")
    parser.add_argument("--methods", default="all", help="'all' or N: the first N methods of each combination")
    parser.add_argument("--only", action="append", default=[], help="substring of the ids to check")
    parser.add_argument("--out", type=Path, default=None,
                        help="default: reports/manual/combined_lab_check/<topology>-seed<seed>.jsonl")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reuse", action="store_true", help="the lab is already up: do not redeploy")
    parser.add_argument("--destroy", action="store_true", help="destroy the lab at the end")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cli-timeout", type=int, default=600,
                        help="VyOS and EOS batch timeout in seconds (the platform's default is 120); "
                             "a loaded host commits the reference state slower than that")
    args = parser.parse_args()

    config = load_experiment(args.experiment)
    suite_toml = _read_toml(_root_path(args.experiment))
    seed = args.seed if args.seed is not None else int(suite_toml.get("seed", 0))
    topology_path = _root_path(str(suite_toml["topology_descriptor"]))
    compiler = ScenarioCompiler.from_topology_file(topology_path, seed=seed)
    suite = CompiledScenarioSuite(compiler.compile_scenarios(_root_path("scenarios"), domains={"combined"}))
    materializer = CompiledContainerLabTopology.from_file(topology_path)

    if args.out is None:
        args.out = (REPO / "reports" / "manual" / "combined_lab_check"
                    / f"{materializer.document['topology']['id']}-seed{seed}.jsonl")
    # m1 of every combination, then m2, then m3: a run stopped early still covers them all.
    instances = sorted((i for i in suite.instances() if i.private.get("task_variant") == "medium"),
                       key=lambda i: (int(i.id.rsplit(".m", 1)[1]), i.id))
    if args.methods != "all":
        keep = int(args.methods)
        instances = [i for i in instances if int(i.id.rsplit(".m", 1)[1]) <= keep]
    if args.only:
        instances = [i for i in instances if any(s in i.id for s in args.only)]
    done = set()
    if args.resume and args.out.exists():
        done = {json.loads(line)["id"] for line in args.out.read_text().splitlines() if line.strip()}
    instances = [i for i in instances if i.id not in done]
    if args.limit:
        instances = instances[: args.limit]
    print(f"{len(instances)} task(s) to check on {materializer.document['topology']['id']} (seed {seed}), "
          f"{len(done)} already done", flush=True)

    platform = ContainerlabPlatform()

    from benchmarks.platforms.containerlab import injector as injector_module
    from benchmarks.platforms.containerlab.executor import ContainerLabExecutor

    def patch_timeouts():
        """Give every VyOS and EOS batch --cli-timeout seconds, for injection and settling alike."""
        for mode, name in (("vyos_cli", "run_vyos_cli_batch"), ("eos_cli", "run_eos_cli_batch")):
            original = getattr(ContainerLabExecutor, name)

            def runner(executor, node, commands, timeout_seconds=None, _original=original):
                return _original(executor, node, commands, timeout_seconds or args.cli_timeout)
            injector_module._BATCH_RUNNERS[mode] = runner
            setattr(platform.env.executor, name,
                    lambda node, commands, timeout_seconds=None, _r=runner: _r(platform.env.executor, node, commands, timeout_seconds))
    evaluator = OracleEvaluator(platform)
    testbed = dict(config.testbed)
    reference = str(testbed["reference_state"])

    def settle(reason: str) -> bool:
        # A node already in its reference state answers "no change", which only counts as
        # a proven write inside idempotent_writes (as the judge's own restoration does).
        print(f"  settle ({reason})", flush=True)
        try:
            with platform.env.executor.idempotent_writes():
                outcome = platform.apply_reference_state(reference)
        except Exception as exc:  # noqa: BLE001
            print(f"  settle failed: {type(exc).__name__}: {exc}", flush=True)
            return False
        if not outcome.get("ok"):
            print(f"  settle not ok: {str(outcome.get('error'))[:300]}", flush=True)
        return bool(outcome.get("ok"))

    if args.reuse:
        # Bind the platform to the running lab without redeploying it.
        from benchmarks.platforms.containerlab import ContainerLabEnv
        platform.topology_document = materializer.document
        platform._reference_state = reference
        platform._healthy_timeout_seconds = float(testbed.get("healthy_timeout_seconds", 15))
        platform._repair_convergence_timeout_seconds = float(testbed.get("repair_convergence_timeout_seconds", 30))
        platform.env = ContainerLabEnv(materializer.env_config(healthy_state_path=reference))
        patch_timeouts()
    else:
        started = time.monotonic()
        outcome = platform.deploy_or_reset(testbed)
        print(f"deploy: ok={outcome.get('ok')} in {time.monotonic() - started:.0f}s", flush=True)
        if not outcome.get("ok"):
            print(json.dumps(outcome)[:2000])
            return 2
        patch_timeouts()
    if not settle("initial reference state"):
        print("the reference state did not converge; stopping")
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.out.open("a", encoding="utf-8") as sink:
            for index, instance in enumerate(instances, start=1):
                started = time.monotonic()
                timings: dict[str, float] = {}
                clock = [time.monotonic()]

                def lap(name: str) -> None:
                    now = time.monotonic()
                    timings[name] = round(now - clock[0], 1)
                    clock[0] = now
                parts = instance.private["bindings"]["components"]
                record = {
                    "id": instance.id,
                    "size": len(parts),
                    "components": [{"id": p["id"], "scenario": p["scenario_definition"],
                                    "method": p["method"]["name"], "target": p["bindings"].get("target"),
                                    "affected": p["bindings"]["affected_nodes"]} for p in parts],
                }
                try:
                    fault = asdict(materializer.materialize(instance))
                    bindings = _oracle_bindings(instance.private["bindings"], materializer.document, "combined")
                    oracles = {phase: resolve_oracle(load_oracle(ref["path"], expected_version=ref["version"]), bindings)
                               for phase, ref in instance.document["oracles"].items()}

                    healthy = evaluator.evaluate(oracles["healthy"])
                    if not healthy.passed:
                        settle("healthy failed before injection")
                        healthy = evaluator.evaluate(oracles["healthy"])
                    record["healthy"] = summary_of(oracles["healthy"], healthy)
                    lap("healthy")
                    if not healthy.passed:
                        record["verdict"] = "lab_unhealthy"
                        raise StopIteration
                    baseline = {p.probe_id: dict(p.metrics) for p in healthy.probes}

                    injected = platform.inject_fault(fault)
                    record["inject"] = {"ok": injected.get("ok"), "error": injected.get("error")}
                    lap("inject")
                    if not injected.get("ok"):
                        record["verdict"] = "injection_failed"
                        raise StopIteration

                    degraded = evaluator.evaluate(oracles["expected_degradation"], baseline=baseline)
                    attempts = 1
                    if not degraded.passed:
                        restored = platform.restore_fault()
                        if restored.get("ok") and platform.inject_fault(fault).get("ok"):
                            degraded = evaluator.evaluate(oracles["expected_degradation"], baseline=baseline)
                            attempts = 2
                    record["degradation"] = {**summary_of(oracles["expected_degradation"], degraded), "attempts": attempts}
                    lap("degradation")
                    repair_faulted = evaluator.evaluate(oracles["repair"], baseline=baseline)
                    record["repair_faulted"] = summary_of(oracles["repair"], repair_faulted)
                    lap("repair_faulted")
                    preservation_faulted = evaluator.evaluate(oracles["preservation"], baseline=baseline)
                    record["preservation_faulted"] = summary_of(oracles["preservation"], preservation_faulted)
                    lap("preservation_faulted")

                    restored = platform.restore_fault()
                    record["restore"] = {"ok": restored.get("ok"), "error": restored.get("error")}
                    lap("restore")
                    converged = platform.wait_for_convergence()
                    record["converged"] = converged.get("converged")
                    lap("converge")
                    repair_after = evaluator.evaluate(oracles["repair"], baseline=baseline)
                    record["repair_restored"] = summary_of(oracles["repair"], repair_after)
                    lap("repair_restored")
                    preservation_after = evaluator.evaluate(oracles["preservation"], baseline=baseline)
                    record["preservation_restored"] = summary_of(oracles["preservation"], preservation_after)
                    lap("preservation_restored")

                    problems = []
                    if not degraded.passed:
                        problems.append("degradation_unseen:" + ",".join(
                            g for g, ok in record["degradation"]["groups"].items() if not ok))
                    still = [g for g, ok in record["repair_faulted"]["groups"].items() if ok]
                    if still:
                        problems.append("repair_passes_while_faulted:" + ",".join(still))
                    if not preservation_faulted.passed:
                        problems.append("fault_leaks_outside_blast_radius")
                    if not restored.get("ok"):
                        problems.append("restore_failed")
                    if not repair_after.passed:
                        problems.append("repair_fails_after_restore:" + ",".join(
                            g for g, ok in record["repair_restored"]["groups"].items() if not ok))
                    if not preservation_after.passed:
                        problems.append("preservation_fails_after_restore")
                    record["problems"] = problems
                    record["verdict"] = "coherent" if not problems else "incoherent"
                except StopIteration:
                    pass
                except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
                    record["verdict"] = "error"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["traceback"] = traceback.format_exc()[-3000:]
                record["seconds"] = round(time.monotonic() - started, 1)
                record["timings"] = timings
                sink.write(json.dumps(record, default=str) + "\n")
                sink.flush()
                print(f"[{index}/{len(instances)}] {record['verdict']:<15} {record['seconds']:>6}s "
                      f"{instance.id} {record.get('problems') or ''}", flush=True)
                # Leave the lab healthy for the next task whatever happened here.
                if record["verdict"] != "coherent":
                    try:
                        platform.restore_fault()
                    except Exception:  # noqa: BLE001
                        pass
                    if not settle("after a non-coherent task"):
                        print("  reference state did not converge; redeploying", flush=True)
                        outcome = platform.deploy_or_reset(testbed)
                        if outcome.get("ok"):
                            patch_timeouts()
                        if not outcome.get("ok") or not settle("after redeploy"):
                            print("lab lost; stopping", flush=True)
                            return 3
    finally:
        if args.destroy:
            print("destroy:", platform.restore_or_destroy("destroy").get("ok"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
