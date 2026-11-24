"""The vlan family and the combined scenarios (scenarios/generator).

Pinned here: every VLAN fault binds to an access switch with an exact blast radius,
renders as EOS lines and restores exactly what the healthy fabric renders; a combined
task binds each component as its own family would, on disjoint blast radii, the same
alone or in a whole suite, injects every component and undoes them in reverse; its
oracles keep each component's own as a group; and the generator writes only what it has
seen compile.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.core.contracts import ProbeEvaluation  # noqa: E402
from benchmarks.core.oracle_evaluator import OracleEvaluator, _combine_groups  # noqa: E402
from benchmarks.platforms.containerlab.compiled_topology import (  # noqa: E402
    CompiledContainerLabTopology,
    UnsupportedScenarioError,
)
from benchmarks.testbeds.campus_fabric import fabric_commands  # noqa: E402
from scenarios.access import CompiledScenarioInstance, CompiledScenarioSuite, assert_no_private_fields  # noqa: E402
from scenarios.compiler.compiler import ScenarioCompiler, _render  # noqa: E402
from scenarios.compiler.loader import combination_blocker, load_yaml  # noqa: E402
from scenarios.generator import combine_scenarios  # noqa: E402
from scenarios.oracle_loader import load_oracle, resolve_oracle  # noqa: E402

CAMPUS = ("campus_small", "campus_medium", "campus_large")
#: The vlan family faults VLAN membership only (scenarios/vlan/vlan_misconfiguration.yaml).
VLAN_FAMILIES = {
    "active": (ROOT, {"set_access_port_vlan", "set_trunk_allowed_vlan"}),
}


def _topology(name: str) -> Path:
    return ROOT / "topologies" / f"{name}.yaml"


def _suite(topology: str, *, seed: int = 9, domains=None, root: Path = ROOT) -> CompiledScenarioSuite:
    compiler = ScenarioCompiler.from_topology_file(_topology(topology), seed=seed)
    return CompiledScenarioSuite(compiler.compile_scenarios(root, domains=domains))


def _materializer(topology: str) -> CompiledContainerLabTopology:
    return CompiledContainerLabTopology.from_file(_topology(topology))


# --- the vlan family -------------------------------------------------------------------

@pytest.mark.parametrize("family", sorted(VLAN_FAMILIES))
@pytest.mark.parametrize("topology", CAMPUS)
def test_every_vlan_fault_binds_to_an_access_switch_with_an_exact_blast_radius(topology, family):
    root, operations = VLAN_FAMILIES[family]
    document = load_yaml(_topology(topology))["topology"]
    nodes, segments = document["nodes"], document["segments"]
    for seed in range(6):
        instances = _suite(topology, seed=seed, domains={"vlan"}, root=root).instances()
        assert {i.private["evaluator"]["selected_method"]["operation"]["name"] for i in instances} == operations
        for instance in instances:
            bindings = instance.private["bindings"]
            operation = instance.private["evaluator"]["selected_method"]["operation"]["name"]
            switch = nodes[bindings["target"]]
            assert "access_switch" in switch["roles"] and switch["kind"] == "arista_ceos"
            port = switch["interfaces"][bindings["interface"]]
            vlan_segment = bindings["vlan_segment"]
            assert segments[vlan_segment]["vlan_id"] == bindings["vlan_id"]
            # Every endpoint the switch attaches to the VLAN, or the one on the port.
            on_vlan = sorted(
                spec["attached_endpoint"] for spec in switch["interfaces"].values()
                if spec["segment"] == vlan_segment and spec.get("attached_endpoint")
                and "service" not in nodes[spec["attached_endpoint"]]["roles"])
            if operation in {"set_access_port_vlan", "set_switchport_mode"}:
                assert bindings["affected_nodes"] == [port["attached_endpoint"]]
            else:
                assert bindings["affected_nodes"] == on_vlan
            if operation in {"set_trunk_allowed_vlan", "set_trunk_native_vlan"}:
                assert segments[port["segment"]]["type"] == "trunk"
                assert bindings["vlan_id"] in segments[port["segment"]]["tagged_vlans"]
            else:
                assert port["segment"] == vlan_segment
            if operation == "set_access_port_vlan":
                uplinks = [s for s in switch["interfaces"].values() if segments[s["segment"]]["type"] == "trunk"]
                assert bindings["fault_vlan_id"] != bindings["vlan_id"]
                assert any(bindings["fault_vlan_id"] in segments[s["segment"]]["tagged_vlans"] for s in uplinks)


def _fabric_blocks(topology_document: dict, switch: str) -> dict[str, list[str]]:
    """The fabric's configuration of a switch, as {mode line: the lines under it}."""
    blocks: dict[str, list[str]] = {}
    current = None
    for command in fabric_commands(topology_document, switch):
        line = command["command"]
        if line.startswith(("interface ", "vlan ", "mlag ")):
            current = line
            blocks.setdefault(current, [])
        elif current is not None:
            blocks[current].append(line)
    return blocks


@pytest.mark.parametrize("family", sorted(VLAN_FAMILIES))
@pytest.mark.parametrize("topology", CAMPUS)
def test_vlan_faults_change_one_line_and_restore_what_the_fabric_renders(topology, family):
    root, _ = VLAN_FAMILIES[family]
    document = load_yaml(_topology(topology))["topology"]
    materializer = _materializer(topology)
    for seed in (0, 3, 9):
        for instance in _suite(topology, seed=seed, domains={"vlan"}, root=root).instances():
            bindings = instance.private["bindings"]
            operation = instance.private["evaluator"]["selected_method"]["operation"]["name"]
            fault = materializer.materialize(instance)
            assert {(c.target, c.mode) for c in fault.commands + fault.restore_commands} == {
                (bindings["target"], "eos_cli")}
            blocks = _fabric_blocks(document, bindings["target"])
            restore = [c.command for c in fault.restore_commands]
            mode_line, *settings = restore
            assert mode_line in blocks, (instance.id, mode_line)
            healthy = blocks[mode_line]
            vlan = bindings["vlan_id"]
            for line in settings:
                if line == "no switchport trunk native vlan":
                    assert not any(item.startswith("switchport trunk native vlan") for item in healthy)
                elif line.startswith("switchport trunk allowed vlan add "):
                    allowed = next(item for item in healthy if item.startswith("switchport trunk allowed vlan "))
                    assert str(vlan) in allowed.rsplit(" ", 1)[-1].split(",")
                else:
                    assert line in healthy, (instance.id, line, healthy)
            faulty = [c.command for c in fault.commands]
            assert faulty != restore and not (set(faulty[1:]) & set(healthy)), (instance.id, faulty)
            if operation == "set_vlan_presence":
                assert faulty == [f"no vlan {vlan}"]


def test_vlan_faults_refuse_a_switch_they_have_no_rendering_for():
    # sme_leaf_firewall_dmz binds the family (SR Linux leaves) and is excluded for this.
    compiler = ScenarioCompiler.from_topology_file(_topology("sme_leaf_firewall_dmz"), seed=0)
    tasks = compiler.compile_scenario("vlan", load_yaml(ROOT / "vlan" / "vlan_misconfiguration.yaml"))
    with pytest.raises(UnsupportedScenarioError, match="arista_ceos"):
        _materializer("sme_leaf_firewall_dmz").materialize(CompiledScenarioInstance(tasks[0]))
    applicability = json.loads((ROOT / "topology_applicability.json").read_text(encoding="utf-8"))
    assert "sme_leaf_firewall_dmz" not in applicability["scenarios"]["vlan/vlan_misconfiguration.yaml"]["topologies"]


def test_vlan_tasks_leak_nothing_private():
    for instance in _suite("campus_small", domains={"vlan"}).instances():
        public = instance.public_view()
        assert_no_private_fields(public)
        text = json.dumps(public).lower()
        for word in ("switchport", "eos", "ethernet", "port-channel", "fault"):
            assert word not in text, (instance.id, word)


# --- combined scenarios ----------------------------------------------------------------

def _combined_entries() -> dict[str, dict]:
    applicability = json.loads((ROOT / "topology_applicability.json").read_text(encoding="utf-8"))
    return {k: v for k, v in applicability["scenarios"].items() if k.startswith("combined/")}


def test_every_generated_combination_is_declared_and_pins_its_components():
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "combined").rglob("*.yaml")}
    assert on_disk, "no combined scenario in the catalog"
    assert on_disk == set(_combined_entries())
    for relative in on_disk:
        document = load_yaml(ROOT / relative)
        for component in document["combination"]["components"]:
            source = load_yaml(ROOT / component["source"])
            assert source["version"] == component["version"], (relative, component["source"])
            assert combination_blocker(component["source"].split("/")[0], source) is None


@pytest.mark.parametrize("topology", CAMPUS)
def test_a_combined_task_injects_every_component_on_disjoint_blast_radii(topology):
    materializer = _materializer(topology)
    for seed in (0, 9, 23):
        instances = _suite(topology, seed=seed, domains={"combined"}).instances()
        assert instances
        for instance in instances:
            bindings = instance.private["bindings"]
            parts = bindings["components"]
            radii = [set(part["bindings"]["affected_nodes"]) for part in parts]
            assert all(radii) and sum(map(len, radii)) == len(set().union(*radii)), instance.id
            assert bindings["affected_nodes"] == sorted(set().union(*radii))
            fault = materializer.materialize(instance)
            expected, restores = [], []
            for part in parts:
                operation = part["method"]["operation"]
                expected += materializer._render_operation(operation, part["bindings"])
                restores.append(materializer._render_restore_operation(operation, part["bindings"]))
                assert part["method"] in load_yaml(ROOT / part["source"])["methods"]
            assert fault.commands == expected
            assert fault.restore_commands == [c for block in reversed(restores) for c in block]
            assert sorted(fault.expected_affected_nodes) == bindings["affected_nodes"]
            assert_no_private_fields(instance.public_view())


def test_a_combined_task_binds_the_same_alone_and_in_a_whole_suite():
    alone = _suite("campus_small", seed=9, domains={"combined"}).document["tasks"]
    whole = [t for t in _suite("campus_small", seed=9).document["tasks"] if t["domain"] == "combined"]
    assert alone == whole


def test_a_combined_task_states_each_wording_its_components_share():
    instances = _suite("campus_small", seed=9, domains={"combined"}).instances()
    by_variant = {}
    for instance in instances:
        by_variant.setdefault(instance.private.get("task_variant"), []).append(instance)
    assert set(by_variant) == {"low", "medium", "high"}
    # low: the one generic sentence the generator writes, whatever is combined.
    assert {i.intent for i in by_variant["low"]} == {combine_scenarios.LOW_WORDING}
    for variant in ("medium", "high"):
        for instance in by_variant[variant]:
            # The components' own wordings, each rendered with its own draw, concatenated.
            expected = []
            for part in instance.private["bindings"]["components"]:
                template = load_yaml(ROOT / part["source"])["task"][variant]
                text = " ".join(_render(template, part["bindings"]).split())
                if text not in expected:
                    expected.append(text)
            assert instance.intent == " ".join(expected), instance.id
            assert "{{" not in instance.intent
    for instance in by_variant["high"]:
        for node in instance.private["bindings"]["affected_nodes"]:
            assert node in instance.intent, (instance.id, node)


def _two_connectivity_faults(version: str = "1.1.0") -> dict:
    return {
        "schema_version": "0.1", "version": "1.0.0",
        "oracles": {phase: {"path": path, "version": "1.0.0"}
                    for phase, path in combine_scenarios.ORACLES.items()},
        "scenario": "disable_interface__remove_ip", "base_variant": "medium",
        "success_criteria": {"all_of": [{"type": "lab_connectivity"}]},
        "combination": {"blast_radius": "disjoint", "components": [
            {"source": "connectivity/disable_interface.yaml", "scenario": "disable_interface", "version": version},
            {"source": "connectivity/remove_ip.yaml", "scenario": "remove_ip", "version": "1.1.0"},
        ]},
        "methods": [{"id": 1, "name": "service_interface_down+service_wrong_ip", "components": [2, 3]}],
    }


def test_a_wording_the_file_does_not_state_is_the_components_own_concatenated():
    compiler = ScenarioCompiler.from_topology_file(_topology("campus_small"), seed=4)
    tasks = compiler.compile_scenario("combined", _two_connectivity_faults(), scenarios_root=ROOT)
    by_variant = {t["private"]["task_variant"]: t for t in tasks}
    # Two connectivity faults: one high sentence each, naming each fault's own endpoints.
    high = by_variant["high"]["public"]["intent"]
    first, second = (p["bindings"]["affected_nodes"][0] for p in by_variant["high"]["private"]["bindings"]["components"])
    assert high.startswith(f"{first} lost IPv4 connectivity")
    assert high.count("lost IPv4 connectivity") == 2 and f"{second} lost IPv4 connectivity" in high
    # The same medium wording twice is said once.
    medium = by_variant["medium"]["public"]["intent"]
    assert medium == " ".join(load_yaml(ROOT / "connectivity" / "remove_ip.yaml")["task"]["medium"].split())
    assert by_variant["medium"]["id"] == "combined.disable_interface__remove_ip.m1"
    # A wording the file states is used as written.
    stated = {**_two_connectivity_faults(), "task": {"low": "Fix it."}}
    tasks = compiler.compile_scenario("combined", stated, scenarios_root=ROOT)
    assert next(t for t in tasks if t["private"]["task_variant"] == "low")["public"]["intent"] == "Fix it."
    with pytest.raises(ValueError, match="not every"):
        compiler.compile_scenario("combined", {**stated, "task": {"urgent": "Now."}}, scenarios_root=ROOT)


def test_a_combination_refuses_a_component_whose_version_moved():
    compiler = ScenarioCompiler.from_topology_file(_topology("campus_small"), seed=0)
    with pytest.raises(ValueError, match="regenerate the combinations"):
        compiler.compile_scenario("combined", _two_connectivity_faults(version="1.0.9"), scenarios_root=ROOT)


def test_only_injected_faults_judged_on_reachability_are_combinable():
    assert combination_blocker("qos", load_yaml(ROOT / "qos" / "wan_shaping_policy_repair.yaml"))
    assert combination_blocker("security", load_yaml(ROOT / "security" / "arp_spoofing.yaml"))
    for source in ("connectivity/remove_ip.yaml", "dhcp_dns/dhcp_provisioning.yaml",
                   "filtering/zone_policy_enforcement.yaml", "vlan/vlan_misconfiguration.yaml"):
        assert combination_blocker(source.split("/")[0], load_yaml(ROOT / source)) is None


# --- the composed oracles --------------------------------------------------------------

def _compiled_combination(components: set[str]) -> dict:
    for instance in _suite("campus_small", seed=9, domains={"combined"}).instances():
        parts = instance.private["bindings"]["components"]
        if {p["scenario_definition"] for p in parts} == components:
            return instance.private["bindings"]
    raise AssertionError(f"no generated combination of {components}")


def test_a_composed_oracle_keeps_each_component_as_its_own_group():
    bindings = _compiled_combination({"dhcp_provisioning", "zone_policy_enforcement"})
    oracles = {phase: resolve_oracle(load_oracle(REPO_ROOT / path, expected_version="1.0.0"), bindings)
               for phase, path in combine_scenarios.ORACLES.items()}
    degradation = oracles["expected_degradation"]
    assert degradation["domain"] == "combined"
    # DHCP keeps its own rule: either symptom shows the fault.
    assert degradation["success"]["groups"] == {"c1": {"mode": "any"}, "c2": {"mode": "all"}}
    radius = {p["id"]: p.get("affected_nodes") for p in degradation["probes"] if p.get("affected_nodes")}
    parts = bindings["components"]
    assert radius["c1_affected_paths"] == parts[0]["bindings"]["affected_nodes"]
    assert radius["c2_affected_paths"] == parts[1]["bindings"]["affected_nodes"]
    # The required paths are measured once, for both.
    shared = [p for p in oracles["repair"]["probes"] if p["type"] == "connectivity_matrix"]
    assert len(shared) == 1 and shared[0]["groups"] == ["c1", "c2"]
    # Preservation: the paths no fault touched, whichever component asks.
    preservation = oracles["preservation"]["probes"]
    assert len(preservation) == 1 and preservation[0]["affected_nodes"] == bindings["affected_nodes"]


def test_all_groups_passes_only_when_every_group_does():
    declared = [{"id": "a", "groups": ["c1"]}, {"id": "b", "groups": ["c1"]}, {"id": "c", "groups": ["c2"]}]

    def evaluated(*passed):
        return [ProbeEvaluation(probe_id=p["id"], passed=v, metrics={}, thresholds=(), threshold_results=())
                for p, v in zip(declared, passed)]

    rule = {"mode": "all_groups", "groups": {"c1": {"mode": "any"}, "c2": {"mode": "all"}}}
    assert _combine_groups(declared, evaluated(False, True, True), rule)
    assert not _combine_groups(declared, evaluated(True, True, False), rule)
    assert not _combine_groups(declared, evaluated(False, False, True), rule)
    # A group no probe names was not measured, so it cannot pass.
    assert not _combine_groups(declared, evaluated(True, True, True),
                               {"mode": "all_groups", "groups": {**rule["groups"], "c3": {"mode": "all"}}})
    assert not _combine_groups(declared, evaluated(True, True, True), {"mode": "all_groups"})


def test_the_evaluator_judges_a_composed_oracle_by_its_groups():
    class Runner:
        def run_probe(self, probe):
            return {"failed_checks": 0 if probe["id"].startswith("c1") else 2}

    oracle = {"oracle_id": "combined.test", "version": "1.0.0", "phase": "repair",
              "probes": [{"id": i, "groups": [i[:2]],
                          "thresholds": [{"metric": "failed_checks", "operator": "eq", "value": 0}],
                          "success": {"mode": "all"}} for i in ("c1_paths", "c2_paths")],
              "success": {"mode": "all_groups", "groups": {"c1": {"mode": "all"}, "c2": {"mode": "all"}}}}
    assert not OracleEvaluator(Runner()).evaluate(oracle).passed
    oracle["success"]["groups"]["c2"] = {"mode": "minimum_pass_rate", "minimum_pass_rate": 0}
    assert OracleEvaluator(Runner()).evaluate(oracle).passed


# --- the generator ---------------------------------------------------------------------

def _catalog_copy(tmp_path: Path) -> Path:
    root = tmp_path / "scenarios"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__", "tests", "combined"))
    applicability = json.loads((root / "topology_applicability.json").read_text(encoding="utf-8"))
    applicability["scenarios"] = {k: v for k, v in applicability["scenarios"].items()
                                  if not k.startswith("combined/")}
    (root / "topology_applicability.json").write_text(json.dumps(applicability, indent=2) + "\n", encoding="utf-8")
    return root


def test_the_generator_writes_what_it_checked_and_versions_what_it_rewrites(tmp_path, capsys):
    root = _catalog_copy(tmp_path)
    arguments = ["--scenarios", str(root), "--combination", "disable_interface,vlan_misconfiguration",
                 "--topology", "campus_small", "--check-seeds", "2", "--methods", "2"]
    assert combine_scenarios.main(arguments) == 0
    path = root / "combined" / "2_scenarios" / "disable_interface__vlan_misconfiguration.yaml"
    document = load_yaml(path)
    assert document["version"] == "1.0.0"
    assert [c["source"] for c in document["combination"]["components"]] == [
        "connectivity/disable_interface.yaml", "vlan/vlan_misconfiguration.yaml"]
    assert len(document["methods"]) == 2
    entry = json.loads((root / "topology_applicability.json").read_text(encoding="utf-8"))["scenarios"][
        "combined/2_scenarios/disable_interface__vlan_misconfiguration.yaml"]
    assert entry["topologies"] == ["campus_small"]
    assert "does not apply" in entry["excluded"]["clos01_mvp"]
    assert "Not checked" in entry["excluded"]["campus_medium"]
    # The file it wrote compiles from the catalog it wrote it in, on the topology it named.
    compiler = ScenarioCompiler.from_topology_file(root / "topologies" / "campus_small.yaml", seed=1)
    suite = compiler.compile_scenarios(root, domains={"combined"})
    assert len(suite["tasks"]) == 2 * 3

    assert combine_scenarios.main(arguments) == 0
    assert load_yaml(path)["version"] == "1.0.0", "the same content keeps its version"
    assert combine_scenarios.main([*arguments[:-1], "1"]) == 0
    assert load_yaml(path)["version"] == "1.1.0", "different content takes the next minor"

    assert combine_scenarios.main(["--scenarios", str(root), "--combination", "remove_ip,disable_routing",
                                   "--topology", "campus_small", "--check-seeds", "1", "--clean"]) == 0
    assert not path.exists()
    assert (root / "combined" / "2_scenarios" / "disable_routing__remove_ip.yaml").exists()
    out = capsys.readouterr().out
    assert "left out qos/wan_shaping_policy_repair.yaml" in out


def _withdraw(path: Path, method_id: int) -> tuple[int, ...]:
    document = load_yaml(path)
    method = next(m for m in document["methods"] if m["id"] == method_id)
    document["methods"].remove(method)
    document["combination"]["withdrawn"] = [{**method, "reason": "refused by a lab check"}]
    path.write_text(combine_scenarios.render_yaml(document), encoding="utf-8")
    return tuple(method["components"])


def test_a_withdrawn_tuple_is_never_proposed_again_and_its_id_never_reused(tmp_path):
    root = _catalog_copy(tmp_path)
    arguments = ["--scenarios", str(root), "--combination", "disable_interface,vlan_misconfiguration",
                 "--topology", "campus_small", "--check-seeds", "2", "--methods", "2"]
    assert combine_scenarios.main(arguments) == 0
    path = root / "combined" / "2_scenarios" / "disable_interface__vlan_misconfiguration.yaml"
    kept = tuple(next(m for m in load_yaml(path)["methods"] if m["id"] == 2)["components"])
    refused = _withdraw(path, 1)

    assert combine_scenarios.main(arguments) == 0
    document = load_yaml(path)
    methods = {m["id"]: tuple(m["components"]) for m in document["methods"]}
    assert refused not in methods.values()
    assert methods[2] == kept, "a tuple the file already lists keeps its id"
    assert sorted(methods) == [2, 3], "the new tuple takes an id the file never used"
    assert [tuple(w["components"]) for w in document["combination"]["withdrawn"]] == [refused]
    assert document["version"] == "1.1.0"


def test_a_combined_file_may_not_list_a_withdrawn_tuple_or_reuse_its_id():
    from scenarios.compiler.loader import validate_scenario

    path = ROOT / "combined" / "3_scenarios" / "disable_routing__zone_policy_enforcement__vlan_misconfiguration.yaml"
    document = load_yaml(path)
    validate_scenario("combined", document, path)
    withdrawn = document["combination"]["withdrawn"][0]
    relisted = json.loads(json.dumps(document))
    relisted["methods"].append({"id": 9, "name": "again", "components": withdrawn["components"]})
    with pytest.raises(ValueError, match="withdrawn tuple"):
        validate_scenario("combined", relisted, path)
    reused = json.loads(json.dumps(document))
    reused["methods"][0]["id"] = withdrawn["id"]
    with pytest.raises(ValueError, match="belongs to a withdrawn tuple"):
        validate_scenario("combined", reused, path)


def test_the_generated_files_read_back_as_the_generator_wrote_them():
    for path in sorted((ROOT / "combined").rglob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert combine_scenarios.render_yaml(document) == path.read_text(encoding="utf-8"), path.name
