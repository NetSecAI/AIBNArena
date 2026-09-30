"""The two rules that decide what a failure means inside a matrix.

An episode that fails is one bad cell; a record that names the wrong model is a
campaign nobody can read. The first is counted and the matrix carries on, the
second stops it. These are the checks that tell them apart.
"""
from __future__ import annotations

from webui.campaign import CampaignRequest, ScenarioSelection, plan
from webui.orchestrator import CellRecord, CampaignRecord, RunManager
from webui.orchestrator.campaign_runner import _skip_remaining
from webui.orchestrator.results import provenance_mismatch

MODEL = "openai/gpt-4o-mini"


def agreeing(**overrides) -> dict:
    return {
        "configured_model": MODEL,
        "sut_reported_model": MODEL,
        "provider_reported_model": MODEL,
        **overrides,
    }


def test_a_record_that_names_one_model_throughout_passes():
    assert provenance_mismatch(agreeing(), MODEL) is None


def test_a_provider_that_names_no_model_is_silent_not_wrong():
    """A proxy that reports nothing has not contradicted anything."""
    assert provenance_mismatch(agreeing(provider_reported_model=None), MODEL) is None


def test_a_subject_reporting_another_model_is_named():
    mismatch = provenance_mismatch(agreeing(sut_reported_model="some/other-model"), MODEL)
    assert mismatch is not None
    assert "sut_reported_model" in mismatch and "some/other-model" in mismatch


def test_a_provider_serving_another_model_is_named():
    mismatch = provenance_mismatch(agreeing(provider_reported_model="served/something-else"), MODEL)
    assert mismatch is not None
    assert "provider_reported_model" in mismatch


def test_every_disagreement_is_reported_not_just_the_first():
    mismatch = provenance_mismatch(
        agreeing(configured_model="one", sut_reported_model="two"), MODEL)
    assert "configured_model" in mismatch and "sut_reported_model" in mismatch


def _record() -> CampaignRecord:
    request = CampaignRequest(
        architectures=["langchain_agent"],
        models=[MODEL],
        # Four rows: one fault each, which is what makes four cells.
        scenarios=[ScenarioSelection(experiment="connectivity-smoke",
                                     scenario_id="connectivity.disable_interface.m1",
                                     seed=seed)
                   for seed in (9, 17, 29, 41)],
        execution_budget_seconds=400,
    )
    cells = plan(request)
    return CampaignRecord(
        id="guards", request=request, campaign_id="guards",
        result_dir="reports/campaigns/runs/guards",
        plan=cells, cells=[CellRecord(cell=cell.summary()) for cell in cells])


def test_cells_never_reached_are_skipped_and_not_failed():
    """A cell that was not attempted has measured nothing.

    Recording it as a failure would put it in the same column as a subject that
    ran and did not repair, which is the one thing a campaign must not confuse.
    """
    record = _record()
    record.cells[0].state = "done"
    record.cells[1].state = "failed"
    record.cells[2].state = "running"
    _skip_remaining(RunManager(), record, "stopped by the test")
    assert [outcome.state for outcome in record.cells] == [
        "done", "failed", "skipped", "skipped"]
    assert record.counts == {"total": 4, "done": 1, "failed": 1, "skipped": 2}


def test_a_record_without_a_subject_is_not_a_mismatch():
    # The judge can fail before the subject runs (a lab that would not reset); the
    # record then names no subject model. That is a failed cell, not another model.
    assert provenance_mismatch(agreeing(sut_reported_model=None), MODEL) is None
    assert provenance_mismatch(agreeing(sut_reported_model=None, provider_reported_model=None), MODEL) is None


def test_an_outcome_already_recorded_keeps_its_own_error():
    record = _record()
    record.cells[0].state = "failed"
    record.cells[0].error = "the judge exited with code 4"
    _skip_remaining(RunManager(), record, "stopped by the test")
    assert record.cells[0].error == "the judge exited with code 4"
    assert "not attempted" in record.cells[1].error
