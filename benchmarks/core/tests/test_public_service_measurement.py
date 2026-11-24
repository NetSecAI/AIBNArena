"""The subject's public throughput check gets the flow it measures from the judge's request."""
from __future__ import annotations

import json

from benchmarks.core.lifecycle import public_observation, public_service_measurement
from benchmarks.core import sut_client as client_module
from benchmarks.core.contracts import OperationCounts

QOS_CRITERIA = {"all_of": [{"type": "lab_connectivity"},
                           {"type": "observed_throughput", "min_mbps": "8", "under_contention": True}]}
QOS_BINDINGS = {"target": "wan1", "source": "wan1", "protected_source": "user1",
                "destination": "external1", "destination_ip": "203.0.113.10",
                "sink_ip": "203.0.113.10", "measurement_port": 5201, "load_port": 5202}


def test_a_throughput_criterion_names_the_protected_flow_without_its_ports():
    assert public_service_measurement(QOS_CRITERIA, QOS_BINDINGS) == {
        "source": "user1", "destination": "external1", "destination_ip": "203.0.113.10"}
    assert public_observation(QOS_CRITERIA, QOS_BINDINGS) == {
        "service_measurement": {"source": "user1", "destination": "external1", "destination_ip": "203.0.113.10"}}


def test_the_protected_source_wins_over_the_fault_target_and_source_is_the_fallback():
    bindings = dict(QOS_BINDINGS); del bindings["protected_source"]
    assert public_service_measurement(QOS_CRITERIA, bindings)["source"] == "wan1"


def test_criteria_without_a_throughput_check_carry_no_observation():
    connectivity = {"all_of": [{"type": "lab_connectivity"}]}
    assert public_service_measurement(connectivity, QOS_BINDINGS) is None
    assert public_observation(connectivity, QOS_BINDINGS) is None
    assert public_observation(None, QOS_BINDINGS) is None
    assert public_observation({"all_of": "not a list"}, QOS_BINDINGS) is None


def test_an_incomplete_flow_is_left_out_rather_than_half_published():
    assert public_service_measurement(QOS_CRITERIA, {"protected_source": "user1"}) is None


def test_the_flow_carries_the_healthy_rate_its_check_is_held_to():
    assert public_observation(QOS_CRITERIA, QOS_BINDINGS, 6.2741) == {"service_measurement": {
        "source": "user1", "destination": "external1", "destination_ip": "203.0.113.10",
        "healthy_throughput_mbps": 6.274}}
    connectivity = {"all_of": [{"type": "lab_connectivity"}]}
    assert public_observation(connectivity, QOS_BINDINGS, 6.274) is None


def test_the_healthy_rate_is_the_one_the_repair_oracle_reads():
    """Both shaped families: the healthy probe's reading of the metric the repair oracle's
    baseline-relative throughput threshold names, and nothing for any other oracle."""
    from pathlib import Path

    from benchmarks.core.lifecycle import repair_throughput_reference
    from scenarios.oracle_loader import load_oracle

    root = Path(__file__).resolve().parents[3]
    for family in ("shaping-repair", "assured-bandwidth"):
        document = load_oracle(root / "scenarios/oracles/qos" / family / "repair-v1.yaml")
        [probe] = [probe for probe in document["probes"] if probe["type"] == "throughput"]
        baseline = {probe["id"]: {"throughput_mbps": 6.274, "circuit_egress_mbps": 10.0}}
        assert repair_throughput_reference(document, baseline) == 6.274, family
        assert repair_throughput_reference(document, {}) is None, family
    connectivity = load_oracle(root / "scenarios/oracles/connectivity/repair-v1.yaml")
    assert repair_throughput_reference(
        connectivity, {probe["id"]: {"throughput_mbps": 6.274} for probe in connectivity["probes"]}) is None
    assert repair_throughput_reference(None, {}) is None


def test_the_client_forwards_the_observation_it_was_handed(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return {"payload": True}

    class FakeClient:
        def __init__(self, subject): self.agent_card = None
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def request(self, payload): return json.dumps({"status": "completed", "execution": {}})

    monkeypatch.setattr(client_module, "build_self_execute_request", fake_build)
    monkeypatch.setattr(client_module, "A2AClient", FakeClient)
    monkeypatch.setattr(client_module, "parse_self_execute_report", lambda raw: json.loads(raw))
    monkeypatch.setattr(client_module, "_operation_counts", lambda report: OperationCounts())
    client = client_module.A2ASUTClient("http://127.0.0.1:1", execution_budget_seconds=10)
    client.invoke({"scenario_id": "s", "intent": "fix", "success_criteria": QOS_CRITERIA,
                   "observation": public_observation(QOS_CRITERIA, QOS_BINDINGS)})
    assert captured["observation"] == {"service_measurement": {
        "source": "user1", "destination": "external1", "destination_ip": "203.0.113.10"}}
    client.invoke({"scenario_id": "s", "intent": "fix", "success_criteria": None, "observation": None})
    assert captured["observation"] is None
