"""One episode, from a submitted form to a judged result.

The order is the one the campaign script established: compile the configuration
first so a typo costs nothing, then start the subject, prove it is the process
and model just started, and only then point the judge at it. The subject is
stopped in a `finally`, so an episode that fails anywhere still leaves no
listener behind for the next one to be measured against.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from benchmarks.run import validate_bundle

from webui import catalog, endpoints
from webui.argv import (
    build_benchmark_argv,
    build_benchmark_env,
    build_sut_argv,
    build_sut_env,
    resolved_port,
)
from webui.catalog.experiments import ExperimentPreset
from webui.config import REPOSITORY, WebUIConfig
from webui.bundle import compile_bundle
from webui.form import EpisodeRequest

from . import history
from .process import StreamedProcess, release_port
from .results import collect
from .seed_guard import settle
from .readiness import wait_for_runtime_identity
from .run_state import LogSource, RunManager, RunRecord, RunState


def diagnosis_judge(request: EpisodeRequest) -> endpoints.Endpoint | None:
    """The endpoint the judge this episode names runs on, or None when it names none.

    A judge registered on its own uses its endpoint. One that is not is run on the
    subject's endpoint, with the subject's key: a judge on the same provider as the
    generator needs no second secret. When neither is registered the episode is
    refused by name, because the judge would otherwise be recorded as configured
    and never reached."""
    if not request.diagnosis_judge_model:
        return None
    judge = endpoints.credentials(request.diagnosis_judge_model)
    if judge is None:
        judge = endpoints.credentials(request.model)
    if judge is None:
        raise ValueError(
            f"no registered endpoint serves the diagnosis judge "
            f"{request.diagnosis_judge_model!r}, and the subject's model "
            f"{request.model!r} is not registered either; register one on the Models page")
    return judge


def validate(request: EpisodeRequest, preset: ExperimentPreset) -> dict[str, Any]:
    """Compile the configuration, the scenario and its oracles. Touches nothing live."""
    config, scenario, oracles = compile_bundle(request, preset)
    judge = diagnosis_judge(request)
    return {
        "diagnosis_judge": (
            {"model": request.diagnosis_judge_model, "api_base": judge.api_base}
            if judge is not None else None),
        "experiment": config.experiment_id,
        "testbed": config.testbed.get("id"),
        "cleanup": config.cleanup,
        "execution_budget_seconds": config.execution_budget_seconds,
        "scenario": {
            "id": scenario.scenario_id,
            "domain": scenario.domain,
            "intent": scenario.intent,
            "intent_variant": scenario.intent_variant,
            "seed": scenario.seed,
            "fault_applicable": scenario.fault_applicable,
        },
        "oracles": {phase: f"{doc['oracle_id']}@{doc['version']}"
                    for phase, doc in oracles.items()},
    }


async def run_episode(manager: RunManager, config: WebUIConfig, record: RunRecord) -> None:
    request = record.request
    architecture = catalog.architecture(request.architecture)
    preset = catalog.preset(request.experiment)
    port = resolved_port(request, architecture)
    logs = config.absolute(config.log_dir) / record.id

    def voice(source: LogSource):
        """A sink that says who is speaking, so the page can follow each apart."""
        return lambda line: manager.log(record, line, source)

    # The launcher's own narration. The two processes get their own below, and
    # the page shows the judge and the subject side by side.
    sink = voice(LogSource.LAUNCHER)

    # Written now and again at the end, so a server that stops mid-run still
    # leaves the run to be listed, as failed, by the next one.
    if not history.save(record, logs):
        sink(f"could not write {logs / history.RECORD}: this run will not be listed after a restart")

    subject: StreamedProcess | None = None
    error: str | None = None
    try:
        manager.set_state(record, RunState.VALIDATING)
        entry = await asyncio.to_thread(settle, request, preset, run=record.id)
        sink(f"configuration compiles: {request.scenario_id} on {preset.topology}")
        sink(f"seed {request.seed}: {_instance(entry)}")

        manager.set_state(record, RunState.STARTING_SUT)
        await asyncio.to_thread(
            release_port, port, timeout=config.port_release_timeout_seconds, sink=sink)
        argv = [config.python, "-u", "-m", architecture.module,
                *build_sut_argv(request, architecture, preset)]
        endpoint = endpoints.credentials(request.model)
        # A model the endpoint refuses would only fail at the first model
        # call, after the testbed is deployed; asking now costs one request.
        if endpoint is not None:
            sink(await asyncio.to_thread(endpoints.probe_model, request.model, endpoint))
        subject = StreamedProcess(
            "sut", argv,
            cwd=REPOSITORY,
            env={**os.environ, **build_sut_env(request, architecture, endpoint)},
            log_path=logs / "sut.log",
            sink=voice(LogSource.SUT),
        )
        # The command line is logged, so it must never carry the key: the
        # endpoint is named here and its credentials travel in the environment.
        sink(f"endpoint: {endpoint.name} ({endpoint.api_base})" if endpoint
             else "endpoint: none registered for this model; the environment decides")
        sink("starting the subject: " + " ".join(argv))
        await subject.start()

        manager.set_state(record, RunState.WAITING_READY)
        await wait_for_runtime_identity(
            request.sut_url(port),
            expected={
                "schema_version": "1.0",
                "sut_identity": architecture.identity,
                "configured_model": request.model,
                "process_id": subject.pid,
            },
            timeout=config.readiness_timeout_seconds,
            interval=config.readiness_interval_seconds,
            is_running=subject.is_running,
        )
        sink(f"subject ready on port {port} (pid {subject.pid}, model {request.model})")

        manager.set_state(record, RunState.RUNNING_BENCHMARK)
        judge_endpoint = diagnosis_judge(request)
        judge_argv = [config.python, "-u", "benchmarks/run.py", *build_benchmark_argv(
            request, preset,
            experiment_id=record.experiment_id,
            sut_url=request.sut_url(port),
            result_dir=config.result_dir,
            report_dir=config.report_dir,
            judge=judge_endpoint,
        )]
        sink(f"diagnosis judge: {request.diagnosis_judge_model} at {judge_endpoint.api_base}"
             f" ({request.diagnosis_judge_reading or 'prompt_logprobs'} reading)"
             if judge_endpoint is not None
             else "diagnosis judge: none; the diagnosis is recorded, not judged")
        judge = StreamedProcess(
            "benchmark", judge_argv,
            cwd=REPOSITORY,
            env={**os.environ, **build_benchmark_env(judge_endpoint)},
            log_path=logs / "benchmark.log",
            sink=voice(LogSource.BENCHMARK),
        )
        sink("running the judge: " + " ".join(judge_argv))
        await judge.start()
        record.exit_code = await judge.wait()
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the page
        error = f"{type(exc).__name__}: {exc}"
        sink(error)
    finally:
        if subject is not None:
            manager.set_state(record, RunState.STOPPING)
            await subject.stop(timeout=config.port_release_timeout_seconds)
            sink("subject stopped")

    # A lifecycle that fails late still writes the episode it judged, so the
    # result is collected before the run is called a failure.
    _collect(config, record, sink)
    if error is None and record.exit_code:
        error = f"the judge exited with code {record.exit_code}"
    manager.finish(record, error=error)
    history.save(record, logs)


def _collect(config: WebUIConfig, record: RunRecord, sink) -> None:
    found = collect(
        config.absolute(config.result_dir),
        config.absolute(config.report_dir),
        record.experiment_id,
        sink,
    )
    record.result_path = found["result_path"]
    record.verdict = found["verdict"]
    record.report_path = found["report_path"]


def _instance(entry: dict[str, Any]) -> str:
    """The fault this seed stands for, in one line for the run log."""
    bindings = entry.get("bindings") or {}
    named = [f"{field}={bindings[field]!r}"
             for field in ("target", "interface", "segment", "destination")
             if bindings.get(field) is not None]
    affected = bindings.get("affected_nodes")
    if affected:
        named.append(f"affects {affected}")
    seen = "first recorded" if len(entry.get("runs") or []) <= 1 else \
        f"run {len(entry['runs'])} of this seed"
    return f"{', '.join(named) or 'no bindings'} ({seen})"
