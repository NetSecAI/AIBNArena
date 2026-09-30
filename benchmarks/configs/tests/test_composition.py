from pathlib import Path

from benchmarks.configs import load_experiment, load_scenario
from scenarios.oracle_loader import load_oracle, resolve_oracle


ROOT = Path(__file__).resolve().parents[3]


def test_suite_composes_only_benchmark_owned_configuration():
    suite = ROOT / "benchmarks/configs/experiments/connectivity-smoke.toml"
    config = load_experiment(suite)
    assert config.experiment_id == "sme01-small-connectivity"
    assert config.testbed["id"] == "sme01-small"
    assert config.execution_budget_seconds == 400

    isolated = load_experiment(suite, result_dir="reports/manual/results/campaigns/test")
    assert isolated.result_dir == "reports/manual/results/campaigns/test"


def test_connectivity_and_qos_suites_compile_with_every_oracle_binding():
    for name in ("connectivity-smoke.toml", "qos-smoke.toml"):
        config = load_experiment(ROOT / "benchmarks/configs/experiments" / name)
        scenario = load_scenario(config.scenario_path)
        assert set(scenario.oracles) == {"healthy", "expected_degradation", "repair", "preservation"}
        for reference in scenario.oracles.values():
            resolved = resolve_oracle(
                load_oracle(reference.path, expected_version=reference.version),
                scenario.bindings,
            )
            assert resolved["domain"] == scenario.domain
        assert scenario.bindings["source"]
        assert scenario.bindings["destination_ip"]
        assert scenario.bindings["preservation_source"]
        assert scenario.bindings["preservation_destination_ip"]


def test_scenario_and_seed_can_be_selected_at_launch_time():
    suite = ROOT / "benchmarks/configs/experiments/connectivity-smoke.toml"
    scenario = load_scenario(
        suite, scenario_id="connectivity.remove_ip.m1", seed=17
    )
    assert scenario.scenario_id == "connectivity.remove_ip.m1"
    assert scenario.seed == 17


def test_the_campaign_overrides_reach_the_loaded_definitions():
    suite = ROOT / "benchmarks/configs/experiments/connectivity-smoke.toml"
    # --configured-model is what the judge was told; a suite file carries none.
    assert load_experiment(suite).configured_model is None
    assert load_experiment(suite, configured_model="openai/intended").configured_model == "openai/intended"
    # --no-fault keeps the plate but drops the injection and its oracle.
    plain = load_scenario(suite, scenario_id="connectivity.remove_ip.m1")
    quiet = load_scenario(suite, scenario_id="connectivity.remove_ip.m1", fault_applicable=False)
    assert plain.fault_applicable and "expected_degradation" in plain.oracles
    assert not quiet.fault_applicable and "expected_degradation" not in quiet.oracles
    # --cleanup decides what becomes of the lab once the episode is judged.
    assert load_experiment(suite).cleanup == "destroy"
    assert load_experiment(suite, cleanup="restore").cleanup == "restore"
