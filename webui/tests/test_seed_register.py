"""A seed is only worth quoting if it still means what it meant.

The register is what makes a seed sufficient on its own: the compiler draws from
one stream per compilation, so the same number lands elsewhere under another
topology or another catalogue. These tests hold the two promises the register
makes -- that a seed recalls its context, and that a seed which no longer
resolves to it is refused rather than run.
"""
from __future__ import annotations

import json

import pytest

from webui import catalog, seeds
from webui.form import EpisodeRequest
from webui.orchestrator.seed_guard import SeedDrift, context, settle

EPISODE = {
    "architecture": "langchain_agent",
    "experiment": "connectivity-smoke",
    "scenario_id": "connectivity.disable_interface.m1",
    "model": "openai/gpt-4o-mini",
    "execution_budget_seconds": 400,
}


@pytest.fixture()
def register(tmp_path, monkeypatch):
    """A register of its own: these entries are shared records, not scratch."""
    monkeypatch.setenv("IBN_SEED_REGISTRY", str(tmp_path / "seed-registry.json"))
    return tmp_path / "seed-registry.json"


def episode(**overrides) -> EpisodeRequest:
    return EpisodeRequest(**{**EPISODE, **overrides})


def preset():
    return catalog.preset("connectivity-smoke")


def test_a_recorded_seed_carries_the_instance_it_resolved_to(register):
    entry = settle(episode(seed=9), preset(), run="a-run")
    assert entry["scenario_id"] == "connectivity.disable_interface.m1"
    assert entry["experiment"] == "connectivity-smoke"
    # The instance, not just the names: which device and which commands.
    assert entry["bindings"]["target"]
    assert entry["fault"]["commands"]
    assert entry["fingerprint"]
    assert json.loads(register.read_text())["episodes"]["9"] == entry


def test_the_seed_alone_brings_the_context_back(register):
    settle(episode(seed=9), preset(), run="a-run")
    found = context(9)
    assert found["kind"] == "episode"
    assert found["experiment"] == "connectivity-smoke"
    assert found["scenario_id"] == "connectivity.disable_interface.m1"
    assert context(123456) is None


def test_running_a_seed_again_adds_to_it_rather_than_rewriting_it(register):
    first = settle(episode(seed=9), preset(), run="run-one")
    second = settle(episode(seed=9), preset(), run="run-two")
    assert second["recorded_at"] == first["recorded_at"]
    assert second["fingerprint"] == first["fingerprint"]
    assert second["runs"] == ["run-one", "run-two"]


def test_a_seed_whose_instance_has_moved_is_refused_by_name(register):
    """The catalogue changing under a seed is the failure this exists to catch."""
    settle(episode(seed=9), preset(), run="run-one")
    document = seeds.load()
    document["episodes"]["9"]["bindings"]["interface"] = "ethernet-1/99"
    document["episodes"]["9"]["fingerprint"] = "no-longer-what-it-was"
    seeds.save(document)
    with pytest.raises(SeedDrift) as refused:
        settle(episode(seed=9), preset(), run="run-two")
    assert "ethernet-1/99" in str(refused.value)
    assert "connectivity.disable_interface.m1" in str(refused.value)


def test_a_seed_reused_for_another_scenario_is_refused(register):
    settle(episode(seed=9), preset(), run="run-one")
    with pytest.raises(SeedDrift) as refused:
        settle(episode(seed=9, scenario_id="connectivity.remove_ip.m1"), preset(),
               run="run-two")
    assert "9" in str(refused.value)


def test_a_drawn_seed_is_one_no_entry_holds(register):
    document = seeds.load()
    drawn = seeds.reserve(document, "episode")
    settle(episode(seed=drawn), preset(), run="drawn")
    assert seeds.reserve(seeds.load(), "episode") != drawn


def test_a_campaign_seed_recalls_its_whole_matrix(register):
    entry = seeds.record_campaign(
        4821,
        campaign_id="e6-20260922T120000Z",
        request={"architectures": ["langchain_agent"], "models": ["a-model"]},
        episode_seeds=[11, 22, 33],
        result_dir="reports/campaigns/runs/e6-20260922T120000Z",
    )
    assert entry["episode_seeds"] == [11, 22, 33]
    kind, found = seeds.lookup(4821)
    assert kind == "campaign"
    assert found["request"]["architectures"] == ["langchain_agent"]


def test_the_two_registers_never_hand_out_the_same_number(register):
    """A seed is quoted on its own, so it must not need saying which kind it is."""
    document = seeds.load()
    document["campaigns"]["500"] = {"seed": 500}
    document["episodes"]["501"] = {"seed": 501}
    seeds.save(document)
    for _ in range(200):
        assert seeds.reserve(seeds.load(), "episode") not in (500, 501)


def test_the_fingerprint_ignores_wording_but_not_the_fault(register):
    bindings = {"target": "leaf1", "interface": "ethernet-1/20"}
    fault = {"commands": [{"target": "leaf1", "command": "disable"}],
             "restore_commands": [], "description": "as written today"}
    reworded = {**fault, "description": "reworded tomorrow", "intent": "new wording"}
    moved = {**fault, "commands": [{"target": "leaf2", "command": "disable"}]}
    assert seeds.fingerprint(bindings, fault) == seeds.fingerprint(bindings, reworded)
    assert seeds.fingerprint(bindings, fault) != seeds.fingerprint(bindings, moved)


def test_validating_writes_nothing_to_the_register(register):
    """A previewed matrix must leave no trace.

    A seed recorded for a campaign somebody only validated would name a fault
    that was never run, and the register would fill with contexts nobody can
    point at.
    """
    from webui.orchestrator.seed_guard import check
    check(episode(seed=9), preset())
    assert not register.exists()
    assert seeds.load()["episodes"] == {}


def test_validating_still_refuses_a_drifted_seed(register):
    settle(episode(seed=9), preset(), run="run-one")
    document = seeds.load()
    document["episodes"]["9"]["fingerprint"] = "no-longer-what-it-was"
    seeds.save(document)
    from webui.orchestrator.seed_guard import check
    with pytest.raises(SeedDrift):
        check(episode(seed=9), preset())


def test_the_menu_offers_what_a_page_needs_to_fill_a_form(register):
    settle(episode(seed=9), preset(), run="run-one")
    settle(episode(seed=17), preset(), run="run-two")
    offered = seeds.summaries()["episodes"]
    assert [item["seed"] for item in offered] == [17, 9] or \
           [item["seed"] for item in offered] == [9, 17]
    first = offered[0]
    assert first["experiment"] == "connectivity-smoke"
    assert first["scenario_id"] == "connectivity.disable_interface.m1"
    assert first["instance"], "a menu entry must say which fault it runs"
    # The bulk stays out: a menu is chosen by, not checked against.
    assert "fault" not in first and "fingerprint" not in first


def test_a_seed_held_for_a_matrix_is_not_offered_until_it_has_a_context(register):
    """The launcher holds drawn seeds against the document before recording them."""
    document = seeds.load()
    document["episodes"]["555"] = {"seed": 555}
    seeds.save(document)
    assert [item["seed"] for item in seeds.summaries()["episodes"]] == []


def test_a_campaign_replayed_joins_its_entry_rather_than_replacing_it(register):
    """A campaign replayed is the same campaign run again, under a new directory."""
    for campaign_id in ("e6-first", "e6-second"):
        entry = seeds.record_campaign(
            4821, campaign_id=campaign_id,
            request={"architectures": ["langchain_agent"]},
            episode_seeds=[11, 22],
            result_dir=f"reports/campaigns/runs/{campaign_id}")
    assert entry["campaign_id"] == "e6-first", "the first run named the entry"
    assert entry["runs"] == ["e6-first", "e6-second"]
    assert seeds.summaries()["campaigns"][0]["runs"] == 2
