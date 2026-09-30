"""The two command lines an episode is: the subject's server, then the judge.

A setting the chosen subject has no flag for is refused rather than dropped. A
dropped setting is the worst outcome available here: the run would be recorded
under a condition it never ran with.
"""
from __future__ import annotations

from webui.catalog.architectures import Architecture
from webui.catalog.experiments import ExperimentPreset
from webui.endpoints import Endpoint
from webui.form import EpisodeRequest

#: Subject flags whose value is written out as `--flag value`, by request field.
_VALUE_FLAGS = (
    ("max_tokens", "--max-tokens"),
    ("temperature", "--temperature"),
    ("prompt_variant", "--prompt-variant"),
    ("debug_trace", "--debug-trace"),
    ("ani_call_limit", "--ani-call-limit"),
    ("min_retry_tokens", "--min-retry-tokens"),
    ("tool_result_chars", "--tool-result-chars"),
    ("context_budget_chars", "--context-budget-chars"),
    ("max_context_chars", "--max-context-chars"),
    ("max_consecutive_rejections", "--max-consecutive-rejections"),
    ("recursion_limit", "--recursion-limit"),
    ("artifact_directory", "--artifact-directory"),
)


def resolved_port(request: EpisodeRequest, architecture: Architecture) -> int:
    return request.port or architecture.default_port


def build_sut_argv(
    request: EpisodeRequest,
    architecture: Architecture,
    preset: ExperimentPreset,
) -> list[str]:
    """`python -m <subject server>` arguments for this episode."""
    port = resolved_port(request, architecture)
    argv = [
        "--host", request.host,
        "--port", str(port),
        "--model", request.model,
        # The subject's own cap and the budget the judge enforces are the same
        # number on purpose: two budgets that disagree end episodes for a reason
        # the record cannot name.
        "--max-execution-seconds", str(request.execution_budget_seconds),
        "--scenario-topology", preset.topology_descriptor,
        "--healthy-state", preset.reference_state,
    ]
    for field, flag in _VALUE_FLAGS:
        value = getattr(request, field)
        if value is None:
            continue
        if not architecture.accepts(field):
            raise ValueError(
                f"{architecture.label} has no {flag}; "
                f"clear it or run this on a subject that accepts it"
            )
        argv += [flag, str(value)]
    if request.enable_thinking is not None:
        argv += ["--enable-thinking", "true" if request.enable_thinking else "false"]
    if request.dry_run:
        argv.append("--dry-run")
    return argv


def build_sut_env(
    request: EpisodeRequest,
    architecture: Architecture | None = None,
    endpoint: Endpoint | None = None,
) -> dict[str, str]:
    """Settings the subject reads from its environment rather than from a flag.

    The credentials of a registered endpoint are among them on purpose: a key on
    the command line would show in the run log and in `ps`. They go under the
    subject's own prefix, which its `from_env()` reads before the repository-wide
    `LLM_*`, so the endpoint chosen here wins over whatever `.env` names.
    """
    environment: dict[str, str] = {}
    if request.thinking_style is not None:
        environment["IBN_SUT_THINKING_STYLE"] = request.thinking_style
    if request.thinking_effort is not None:
        environment["IBN_SUT_THINKING_EFFORT"] = request.thinking_effort
    if endpoint is not None and architecture is not None:
        environment[f"{architecture.env_prefix}_API_BASE"] = endpoint.api_base
        if endpoint.api_key:
            environment[f"{architecture.env_prefix}_API_KEY"] = endpoint.api_key
    return environment


def build_benchmark_argv(
    request: EpisodeRequest,
    preset: ExperimentPreset,
    *,
    experiment_id: str,
    sut_url: str,
    result_dir: str,
    report_dir: str,
    judge: Endpoint | None = None,
) -> list[str]:
    """`benchmarks/run.py` arguments for this episode.

    `judge` is the registered endpoint serving `request.diagnosis_judge_model`;
    asking for a judge no endpoint serves is refused here, before anything
    starts, because the judge would otherwise be recorded as configured and
    never reached. Its key never goes on this command line (see
    `build_benchmark_env`).
    """
    argv = [
        "--config", preset.config_path,
        "--sut-url", sut_url,
        "--scenario-id", request.scenario_id,
        "--seed", str(request.seed),
        "--experiment-id", experiment_id,
        "--execution-budget-seconds", str(request.execution_budget_seconds),
        "--result-dir", result_dir,
        "--report-dir", report_dir,
        # What this run meant to measure, checked against what the subject reports.
        "--configured-model", request.model,
    ]
    if request.seed_campaign is not None:
        # Every record of one campaign carries it, so any single record leads
        # back to the campaign it was a cell of.
        argv += ["--seed-campaign", str(request.seed_campaign)]
    if request.no_fault:
        argv.append("--no-fault")
    if request.cleanup is not None:
        argv += ["--cleanup", request.cleanup]
    if request.diagnosis_judge_model:
        if judge is None:
            raise ValueError(
                f"no registered endpoint serves the diagnosis judge "
                f"{request.diagnosis_judge_model!r}; register it on the Models page")
        argv += ["--diagnosis-judge-model", request.diagnosis_judge_model,
                 "--diagnosis-judge-api-base", judge.api_base]
        if request.diagnosis_judge_reading:
            argv += ["--diagnosis-judge-reading", request.diagnosis_judge_reading]
    return argv


def build_benchmark_env(judge: Endpoint | None = None) -> dict[str, str]:
    """Settings the judge reads from its environment: the diagnosis judge's key,
    when the endpoint has one. The run log shows the command line, never this."""
    if judge is not None and judge.api_key:
        return {"IBN_DIAGNOSIS_JUDGE_API_KEY": judge.api_key}
    return {}
