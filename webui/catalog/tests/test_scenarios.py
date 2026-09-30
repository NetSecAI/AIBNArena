import json
from pathlib import Path

from webui.catalog.experiments import presets
from webui.catalog.scenarios import for_experiment, scenarios

ROOT = Path(__file__).resolve().parents[3]
APPLICABILITY = json.loads(
    (ROOT / "scenarios/topology_applicability.json").read_text(encoding="utf-8"))


def test_methods_are_read_from_the_scenario_file():
    disable_interface = next(
        item for item in scenarios("connectivity") if item.name == "disable_interface")
    assert [method.id for method in disable_interface.methods] == [1, 2, 3]
    assert disable_interface.scenario_id(disable_interface.methods[0]) == \
        "connectivity.disable_interface.m1"


def test_a_scenario_is_only_offered_on_a_topology_it_binds_against():
    offered = {item.name for item in for_experiment("connectivity", "sme_leaf_spine_dmz_vlan")}
    # Structurally inexpressible on the VLAN fabric, and the applicability file says so.
    assert "wrong_routing_table" not in offered
    assert "disable_interface" in offered


def test_every_offered_scenario_is_declared_applicable_to_its_experiment_topology():
    for experiment in presets():
        for scenario in for_experiment(experiment.domain, experiment.topology):
            declared = APPLICABILITY["scenarios"].get(
                f"{scenario.domain}/{Path(scenario.path).name}")
            if declared is None:
                continue
            assert experiment.topology in declared["topologies"], (
                f"{scenario.name} is offered on {experiment.topology}, "
                "which topology_applicability.json does not list")


def test_the_scenario_an_experiment_defaults_to_is_one_it_offers():
    for experiment in presets():
        offered = {
            scenario.scenario_id(method)
            for scenario in for_experiment(experiment.domain, experiment.topology)
            for method in scenario.methods
        }
        assert experiment.default_scenario_id in offered


def test_every_wording_of_the_task_is_offered_with_the_id_the_compiler_gives_it():
    by_name = {item.name: item for domain in ("connectivity", "qos") for item in scenarios(domain)}
    disable_interface = by_name["disable_interface"]
    m1 = disable_interface.methods[0]
    assert {task.variant: disable_interface.scenario_id(m1, task)
            for task in disable_interface.tasks} == {
        "low": "connectivity.disable_interface.low.m1",
        "medium": "connectivity.disable_interface.m1",  # the base wording keeps the bare id
        "high": "connectivity.disable_interface.high.m1",
    }
    assured = by_name["assured_bandwidth"]
    assert [task.variant for task in assured.tasks if task.base] == ["high"]
    assert assured.scenario_id(assured.methods[0], next(
        task for task in assured.tasks if task.variant == "medium")) == "qos.assured_bandwidth.medium.m1"


def test_every_launchable_scenario_names_exactly_one_base_wording():
    for experiment in presets():
        for scenario in for_experiment(experiment.domain, experiment.topology):
            assert sum(task.base for task in scenario.tasks) == 1, scenario.name
