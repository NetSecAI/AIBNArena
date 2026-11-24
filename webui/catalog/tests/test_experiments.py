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


def test_every_topology_can_be_launched_in_every_domain_it_has_scenarios_for():
    """Each topology descriptor is one the form can choose, in each domain that has a
    scenario applicable to it: an experiment file pins that pair to a testbed."""
    import tomllib

    from webui.catalog.experiments import LAUNCHABLE_DOMAINS
    from webui.catalog.scenarios import for_experiment

    testbeds = {}
    for path in (ROOT / "benchmarks/configs/testbeds").glob("*.toml"):
        with path.open("rb") as stream:
            testbeds.setdefault(Path(tomllib.load(stream)["topology_descriptor"]).stem, path.stem)
    topologies = {path.stem for path in (ROOT / "scenarios/topologies").glob("*.yaml")}
    assert topologies <= set(testbeds), f"no testbed config for {sorted(topologies - set(testbeds))}"
    offered = {(item.topology, item.domain) for item in presets() if item.fault_applicable}
    # qos.link_impairment and qos.assured_bandwidth, which the source repository archived,
    # stay active here on the experiment files they were written for (qos-smoke,
    # qos-assured-bandwidth); they ask for no experiment file on every other topology.
    kept = {"link_impairment", "assured_bandwidth"}
    missing = sorted(
        (topology, domain)
        for topology in topologies
        for domain in LAUNCHABLE_DOMAINS
        if [item for item in for_experiment(domain, topology) if item.name not in kept]
        and (topology, domain) not in offered
    )
    assert not missing, f"no experiment file for {missing}"
