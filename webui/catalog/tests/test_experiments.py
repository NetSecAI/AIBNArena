from pathlib import Path

from webui.catalog.experiments import presets, preset

ROOT = Path(__file__).resolve().parents[3]


def test_every_offered_experiment_resolves_the_files_it_names():
    offered = presets()
    assert offered
    for item in offered:
        assert (ROOT / item.config_path).is_file()
        assert (ROOT / item.topology_descriptor).is_file()
        assert (ROOT / item.testbed_config).is_file()
        # The subject's own ANI is wired to this file; a missing one is a
        # subject that cannot start, discovered only after the lab deployed.
        assert (ROOT / item.reference_state).is_file()
        assert item.cleanup in {"restore", "destroy"}
        assert item.execution_budget_seconds > 0


def test_security_is_not_offered_while_no_experiment_pins_it_to_a_testbed():
    assert not [item for item in presets() if item.domain == "security"]


def test_an_experiment_carries_the_topology_and_testbed_its_files_name():
    smoke = preset("connectivity-smoke")
    assert smoke.domain == "connectivity"
    assert smoke.topology == "sme_leaf_spine_dmz_small"
    assert smoke.testbed_id == "sme01-small"
    assert smoke.fault_applicable


def test_the_false_positive_plate_is_offered_as_one():
    assert not preset("connectivity-false-positive").fault_applicable
