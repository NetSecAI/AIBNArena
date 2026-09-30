"""The endpoints that must never start anything."""
import pytest
from fastapi.testclient import TestClient

from webui.app import app
from webui.campaign import CampaignRequest
from webui.form import EpisodeRequest
from webui.orchestrator import MANAGER, CampaignRecord, RunRecord, RunState

client = TestClient(app)

EPISODE = {
    "architecture": "langchain_agent",
    "experiment": "connectivity-smoke",
    "scenario_id": "connectivity.disable_interface.m1",
    "model": "openai/gpt-4o-mini",
    "seed": 9,
    "execution_budget_seconds": 400,
}

CAMPAIGN = {
    "name": "test matrix",
    "architectures": ["langchain_agent"],
    "models": ["openai/gpt-4o-mini"],
    "scenarios": [{"experiment": "connectivity-smoke",
                   "scenario_id": "connectivity.disable_interface.m1"}],
    "execution_budget_seconds": 400,
}


def test_the_catalog_offers_the_repository_as_it_stands():
    document = client.get("/api/catalog").json()
    assert {item["key"] for item in document["architectures"]} == {
        "langchain_agent", "langchain_rag_agent"}
    smoke = next(item for item in document["experiments"] if item["key"] == "connectivity-smoke")
    assert "disable_interface" in {scenario["name"] for scenario in smoke["scenarios"]}


def test_validating_compiles_the_episode_without_starting_it():
    response = client.post("/api/validate", json=EPISODE)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["scenario"]["id"] == "connectivity.disable_interface.m1"
    assert preview["scenario"]["seed"] == 9
    assert set(preview["oracles"]) >= {"healthy", "repair"}
    assert "--scenario-topology" in preview["sut_argv"]
    assert not MANAGER.busy


def test_the_no_fault_plate_drops_the_degradation_oracle():
    preview = client.post("/api/validate", json={**EPISODE, "no_fault": True}).json()
    assert preview["scenario"]["fault_applicable"] is False
    assert "expected_degradation" not in preview["oracles"]


def test_cleanup_is_overridden_for_this_episode_only():
    assert client.post("/api/validate", json=EPISODE).json()["cleanup"] == "destroy"
    overridden = client.post("/api/validate", json={**EPISODE, "cleanup": "restore"}).json()
    assert overridden["cleanup"] == "restore"


@pytest.mark.parametrize("broken, message", [
    ({"scenario_id": "connectivity.no_such_scenario.m1"}, "no_such_scenario"),
    ({"experiment": "no-such-experiment"}, "unknown experiment"),
])
def test_a_combination_that_cannot_run_is_refused_before_anything_starts(broken, message):
    response = client.post("/api/validate", json={**EPISODE, **broken})
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert not MANAGER.busy


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Keep the tests off the real `webui/models.json`."""
    path = tmp_path / "models.json"
    monkeypatch.setattr("webui.endpoints.STORE", path)
    return path


def test_an_endpoint_is_saved_and_listed_without_its_key(store):
    saved = client.put("/api/endpoints", json={
        "name": "Internal proxy", "api_base": "http://127.0.0.1:4000/v1",
        "api_key": "secret-key", "models": ["openai/gpt-5.4", " openai/gpt-4o-mini "],
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] == "internal-proxy"
    assert saved.json()["models"] == ["openai/gpt-5.4", "openai/gpt-4o-mini"]

    listed = client.get("/api/endpoints")
    assert "secret-key" not in listed.text
    assert listed.json()["endpoints"][0]["has_key"] is True
    assert "secret-key" in store.read_text(encoding="utf-8")


def test_the_launcher_offers_the_registered_models(store):
    client.put("/api/endpoints", json={
        "name": "Internal proxy", "api_base": "http://127.0.0.1:4000/v1",
        "api_key": "secret-key", "models": ["openai/gpt-5.4"]})
    catalog = client.get("/api/catalog")
    assert catalog.json()["endpoints"][0]["models"] == ["openai/gpt-5.4"]
    assert "secret-key" not in catalog.text


def test_an_endpoint_needs_a_name_and_a_base_url(store):
    assert client.put("/api/endpoints", json={"name": " ", "api_base": "http://x/v1"}).status_code == 400
    assert client.put("/api/endpoints", json={"name": "x", "api_base": " "}).status_code == 400


def test_deleting_an_unknown_endpoint_says_so(store):
    assert client.delete("/api/endpoints/nothing-here").status_code == 404
    client.put("/api/endpoints", json={"name": "Gone", "api_base": "http://x/v1"})
    assert client.delete("/api/endpoints/gone").status_code == 200
    assert client.get("/api/endpoints").json()["endpoints"] == []


def test_a_second_episode_is_refused_while_one_is_in_flight():
    running = RunRecord(id="test-busy", request=EpisodeRequest(**EPISODE),
                        experiment_id="webui-test-busy")
    MANAGER.create(running)
    MANAGER.set_state(running, RunState.RUNNING_BENCHMARK)
    try:
        response = client.post("/api/runs", json=EPISODE)
        assert response.status_code == 409
        assert "already in flight" in response.json()["detail"]
    finally:
        MANAGER.finish(running, error="ended by the test")
    assert not MANAGER.busy


def test_a_campaign_is_refused_while_an_episode_is_in_flight():
    """One lab, so the interlock has to hold across the two kinds of run.

    A campaign started over a running episode would deploy a testbed under
    the episode being measured on it.
    """
    running = RunRecord(id="test-busy-cross", request=EpisodeRequest(**EPISODE),
                        experiment_id="webui-test-busy-cross")
    MANAGER.create(running)
    MANAGER.set_state(running, RunState.RUNNING_BENCHMARK)
    try:
        response = client.post("/api/campaigns", json=CAMPAIGN)
        assert response.status_code == 409
        assert "already in flight" in response.json()["detail"]
    finally:
        MANAGER.finish(running, error="ended by the test")


def test_an_episode_is_refused_while_a_campaign_is_in_flight():
    matrix = CampaignRecord(
        id="test-busy-matrix",
        request=CampaignRequest(**CAMPAIGN),
        campaign_id="test-busy-matrix",
        result_dir="reports/campaigns/runs/test-busy-matrix",
    )
    MANAGER.create(matrix)
    MANAGER.set_state(matrix, RunState.RUNNING_BENCHMARK)
    try:
        response = client.post("/api/runs", json=EPISODE)
        assert response.status_code == 409
    finally:
        MANAGER.finish(matrix, error="ended by the test")
    assert not MANAGER.busy


def test_a_run_id_is_not_a_campaign_id():
    """The two pages read different fields, so a wrong-kind id is a 404."""
    record = RunRecord(id="test-kinds", request=EpisodeRequest(**EPISODE),
                       experiment_id="webui-test-kinds")
    MANAGER.create(record)
    MANAGER.finish(record, error=None)
    assert client.get("/api/runs/test-kinds").status_code == 200
    assert client.get("/api/campaigns/test-kinds").status_code == 404
    assert client.get("/campaign/test-kinds").status_code == 404


def test_validating_a_matrix_compiles_every_cell_without_starting_it():
    response = client.post("/api/campaigns/validate", json={
        **CAMPAIGN,
        "models": ["openai/gpt-4o-mini", "openai/fr-gpt-5.4"],
        "scenarios": [
            {"experiment": "connectivity-smoke",
             "scenario_id": "connectivity.disable_interface.m1", "seed": 9},
            {"experiment": "connectivity-smoke",
             "scenario_id": "connectivity.disable_interface.m1",
             "no_fault": True, "seed": 17},
        ],
    })
    assert response.status_code == 200, response.text
    preview = response.json()
    assert len(preview["cells"]) == 2 * 2
    assert preview["result_dir"].startswith("reports/campaigns/runs/test-matrix-")
    assert not MANAGER.busy


def test_a_matrix_naming_an_unknown_experiment_is_refused():
    response = client.post("/api/campaigns/validate", json={
        **CAMPAIGN,
        "scenarios": [{"experiment": "no-such-experiment", "scenario_id": "x.y.m1"}],
    })
    assert response.status_code == 400
    assert "no-such-experiment" in response.json()["detail"]
    assert not MANAGER.busy


def test_a_matrix_with_no_scenario_is_refused():
    """No row is no fault, so there is nothing to run."""
    response = client.post("/api/campaigns/validate", json={**CAMPAIGN, "scenarios": []})
    assert response.status_code == 422
    assert not MANAGER.busy


def test_the_matrix_page_renders():
    assert client.get("/campaigns").status_code == 200


def test_each_row_is_drawn_a_seed_of_its_own(tmp_path, monkeypatch):
    """A seed stands for one scenario, so two rows can never share one.

    They would be two different faults recorded under one number, and the
    register refuses the second -- after the first has already run.
    """
    monkeypatch.setenv("IBN_SEED_REGISTRY", str(tmp_path / "seed-registry.json"))
    response = client.post("/api/campaigns/validate", json={
        **CAMPAIGN,
        "scenarios": [
            {"experiment": "connectivity-smoke",
             "scenario_id": "connectivity.disable_interface.m1"},
            {"experiment": "connectivity-smoke",
             "scenario_id": "connectivity.remove_ip.m1"},
            # The same scenario twice: two rows, so two faults.
            {"experiment": "connectivity-smoke",
             "scenario_id": "connectivity.disable_interface.m1"},
        ],
    })
    assert response.status_code == 200, response.text
    drawn = [cell["seed"] for cell in response.json()["cells"]]
    assert len(drawn) == 3
    assert len(set(drawn)) == 3, "two rows were given the same seed"
    # A preview costs no seed: the register is only read.
    assert not (tmp_path / "seed-registry.json").exists()


def _run_page(identifier, **request):
    record = RunRecord(id=identifier, request=EpisodeRequest(**{**EPISODE, **request}),
                       experiment_id=f"webui-{identifier}")
    MANAGER.create(record)
    MANAGER.finish(record, error=None)
    return client.get(f"/run/{identifier}").text


def test_the_run_page_names_the_network_state_the_form_chose_not_the_file():
    page = _run_page("test-state")
    assert "<dt>Topology</dt><dd>sme_leaf_spine_dmz_small</dd>" in page
    assert ">healthy</dd>" in page
    assert "<dt>Experiment</dt>" not in page
    greenfield = _run_page("test-greenfield", experiment="qos-assured-bandwidth",
                           scenario_id="qos.assured_bandwidth.m1")
    assert "<dt>Topology</dt><dd>sme_wan_edge_qos</dd>" in greenfield
    assert ">healthy_greenfield</dd>" in greenfield


def test_the_run_page_says_when_no_fault_was_injected():
    ticked = _run_page("test-no-fault", no_fault=True)
    assert ">healthy · no fault injected (false-positive episode)</dd>" in ticked
    # A seed recorded on a false-positive file replays on it, box or not.
    recorded = _run_page("test-fp-file", experiment="connectivity-false-positive")
    assert ">healthy · no fault injected (false-positive episode)</dd>" in recorded


def test_a_task_wording_other_than_the_base_compiles_under_its_own_id():
    for scenario_id in ("connectivity.disable_interface.low.m1", "connectivity.disable_interface.high.m1"):
        response = client.post("/api/validate", json={**EPISODE, "scenario_id": scenario_id})
        assert response.status_code == 200, response.text
        assert response.json()["scenario"]["id"] == scenario_id
