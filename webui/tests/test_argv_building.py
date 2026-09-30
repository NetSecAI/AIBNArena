from dataclasses import replace

import pytest

from webui.argv import (
    build_benchmark_argv,
    build_sut_argv,
    build_sut_env,
    resolved_port,
)
from webui.catalog import architecture, preset
from webui.endpoints import Endpoint
from webui.form import EpisodeRequest

LANGCHAIN = architecture("langchain_agent")
SMOKE = preset("connectivity-smoke")


def episode(**overrides) -> EpisodeRequest:
    return EpisodeRequest(**{
        "architecture": "langchain_agent",
        "experiment": "connectivity-smoke",
        "scenario_id": "connectivity.disable_interface.m1",
        "model": "openai/gpt-4o-mini",
        "seed": 9,
        "execution_budget_seconds": 400,
        **overrides,
    })


def pairs(argv: list[str]) -> dict[str, str]:
    return {argv[index]: argv[index + 1] for index in range(0, len(argv) - 1)
            if argv[index].startswith("--")}


def test_the_subject_is_started_on_the_experiment_topology_and_healthy_state():
    argv = build_sut_argv(episode(), LANGCHAIN, SMOKE)
    values = pairs(argv)
    assert values["--model"] == "openai/gpt-4o-mini"
    assert values["--scenario-topology"] == SMOKE.topology_descriptor
    assert values["--healthy-state"] == SMOKE.reference_state
    assert values["--port"] == str(LANGCHAIN.default_port)


def test_the_one_budget_reaches_both_the_subject_and_the_judge():
    request = episode(execution_budget_seconds=250)
    subject = pairs(build_sut_argv(request, LANGCHAIN, SMOKE))
    judge = pairs(build_benchmark_argv(
        request, SMOKE, experiment_id="webui-x", sut_url="http://127.0.0.1:8003",
        result_dir="reports/manual/results", report_dir="reports/manual"))
    assert subject["--max-execution-seconds"] == judge["--execution-budget-seconds"] == "250.0"


def test_a_setting_the_chosen_subject_has_no_flag_for_is_refused():
    # A subject without the flag, rather than one that ignores it: the setting would
    # otherwise be recorded as applied when it never reached the subject.
    without = replace(LANGCHAIN, flags=LANGCHAIN.flags - {"max_context_chars"})
    with pytest.raises(ValueError, match="--max-context-chars"):
        build_sut_argv(episode(max_context_chars=4000), without, SMOKE)


def test_a_registered_key_travels_in_the_environment_and_never_on_the_command_line():
    endpoint = Endpoint(id="proxy", name="Internal proxy", api_base="http://127.0.0.1:4000/v1",
                        models=("openai/gpt-4o-mini",), api_key="secret-key")
    argv = build_sut_argv(episode(), LANGCHAIN, SMOKE)
    # The run log prints this argv and `ps` shows it to every local user.
    assert "secret-key" not in " ".join(argv)
    assert "--api-key" not in argv and "--api-base" not in argv
    environment = build_sut_env(episode(), LANGCHAIN, endpoint)
    # Under the subject's own prefix, which its from_env() reads before LLM_*.
    assert environment["LANGCHAIN_AGENT_API_KEY"] == "secret-key"
    assert environment["LANGCHAIN_AGENT_API_BASE"] == "http://127.0.0.1:4000/v1"


def test_an_endpoint_without_a_key_still_names_its_base():
    endpoint = Endpoint(id="local", name="Local vLLM", api_base="http://127.0.0.1:8000/v1",
                        models=("qwen3-8b",), api_key=None)
    environment = build_sut_env(episode(), LANGCHAIN, endpoint)
    assert environment["LANGCHAIN_AGENT_API_BASE"] == "http://127.0.0.1:8000/v1"
    assert "LANGCHAIN_AGENT_API_KEY" not in environment


def test_thinking_is_sent_as_a_flag_and_its_style_as_the_environment():
    assert "--enable-thinking" not in build_sut_argv(episode(), LANGCHAIN, SMOKE)
    argv = build_sut_argv(episode(enable_thinking=False), LANGCHAIN, SMOKE)
    assert pairs(argv)["--enable-thinking"] == "false"
    assert build_sut_env(episode()) == {}
    assert build_sut_env(episode(thinking_effort="high")) == {"IBN_SUT_THINKING_EFFORT": "high"}


def test_dry_run_is_a_flag_without_a_value():
    argv = build_sut_argv(episode(dry_run=True), LANGCHAIN, SMOKE)
    assert argv[-1] == "--dry-run"


def test_the_judge_is_told_the_model_the_run_means_to_measure():
    values = pairs(build_benchmark_argv(
        episode(), SMOKE, experiment_id="webui-run", sut_url="http://127.0.0.1:8003",
        result_dir="reports/manual/results", report_dir="reports/manual"))
    assert values["--configured-model"] == "openai/gpt-4o-mini"
    assert values["--config"] == SMOKE.config_path
    assert values["--scenario-id"] == "connectivity.disable_interface.m1"
    assert values["--experiment-id"] == "webui-run"
    assert values["--seed"] == "9"


def test_the_false_positive_and_cleanup_overrides_are_only_sent_when_asked_for():
    plain = build_benchmark_argv(
        episode(), SMOKE, experiment_id="webui-run", sut_url="http://127.0.0.1:8003",
        result_dir="reports/manual/results", report_dir="reports/manual")
    assert "--no-fault" not in plain and "--cleanup" not in plain
    asked = build_benchmark_argv(
        episode(no_fault=True, cleanup="destroy"), SMOKE, experiment_id="webui-run",
        sut_url="http://127.0.0.1:8003", result_dir="reports/manual/results",
        report_dir="reports/manual")
    assert "--no-fault" in asked
    assert pairs(asked)["--cleanup"] == "destroy"


def test_an_explicit_port_wins_over_the_subject_default():
    assert resolved_port(episode(port=9100), LANGCHAIN) == 9100
    assert resolved_port(episode(), LANGCHAIN) == LANGCHAIN.default_port
