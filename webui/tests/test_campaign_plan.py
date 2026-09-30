"""What a matrix expands into, and in what order.

The plan is where a campaign is decided: how many episodes exist, what each
one is called, and how often a subject server has to be restarted to run them.
Everything after it is the single-episode path, already covered elsewhere.
"""
from __future__ import annotations

import pytest

from webui import catalog
from webui.campaign import (
    CampaignRequest,
    ScenarioSelection,
    cell_experiment_id,
    parse_seeds,
    plan,
    server_key,
)

CONNECTIVITY = {"experiment": "connectivity-smoke",
                "scenario_id": "connectivity.disable_interface.m1"}
QOS = {"experiment": "qos-smoke", "scenario_id": "qos.link_impairment.m1"}
FILTERING = {"experiment": "filtering-smoke",
             "scenario_id": "filtering.zone_policy_enforcement.m1"}


def request(**overrides) -> CampaignRequest:
    return CampaignRequest(**{
        "architectures": ["langchain_agent"],
        "models": ["openai/gpt-4o-mini"],
        "scenarios": [ScenarioSelection(**CONNECTIVITY, seed=9)],
        "execution_budget_seconds": 400,
        **overrides,
    })


def test_the_matrix_is_the_cross_product():
    """Subjects x models x rows. The seed is not an axis: it belongs to the row."""
    cells = plan(request(
        architectures=["langchain_agent"],
        models=["a-model", "another-model"],
        scenarios=[ScenarioSelection(**CONNECTIVITY, seed=9),
                   ScenarioSelection(**CONNECTIVITY, seed=17),
                   ScenarioSelection(**QOS, seed=29)],
    ))
    assert len(cells) == 1 * 2 * 3
    assert [cell.index for cell in cells] == list(range(6))


def test_a_scenario_on_two_seeds_is_two_rows_and_two_cells():
    """This is how a scenario is measured on more than one fault.

    Two rows of the same scenario with different seeds are two different faults,
    which is the whole point; they are not collapsed.
    """
    cells = plan(request(scenarios=[ScenarioSelection(**CONNECTIVITY, seed=9),
                                    ScenarioSelection(**CONNECTIVITY, seed=17)]))
    assert [cell.seed for cell in cells] == [9, 17]


def test_a_repeated_choice_is_one_cell():
    """The same row typed twice is one episode, not two runs of the same one."""
    cells = plan(request(
        architectures=["langchain_agent", "langchain_agent"],
        scenarios=[ScenarioSelection(**CONNECTIVITY, seed=9),
                   ScenarioSelection(**CONNECTIVITY, seed=9)],
    ))
    assert len(cells) == 1


def test_every_cell_of_a_row_shares_its_seed():
    """What a row is run against is compared on one fault, or not compared at all."""
    cells = plan(request(
        architectures=["langchain_agent"],
        models=["a-model", "another-model"],
        scenarios=[ScenarioSelection(**CONNECTIVITY, seed=9)],
    ))
    assert {cell.seed for cell in cells} == {9}
    assert len(cells) == 2


def test_a_scenario_selected_both_ways_is_two_cells():
    """With the fault and without it are different episodes of the same scenario."""
    cells = plan(request(scenarios=[
        ScenarioSelection(**CONNECTIVITY, seed=9),
        ScenarioSelection(**CONNECTIVITY, no_fault=True, seed=17),
    ]))
    assert len(cells) == 2
    assert {cell.selection.no_fault for cell in cells} == {True, False}


def test_the_order_restarts_the_subject_as_rarely_as_it_can():
    """Every cell sharing a server key is consecutive, so each key is entered once."""
    cells = plan(request(
        architectures=["langchain_agent"],
        models=["a-model", "another-model"],
        scenarios=[ScenarioSelection(**FILTERING, seed=9),
                   ScenarioSelection(**CONNECTIVITY, seed=17),
                   ScenarioSelection(**QOS, seed=29)],
    ))
    keys = [server_key(cell, catalog.preset(cell.selection.experiment)) for cell in cells]
    runs = [key for index, key in enumerate(keys) if index == 0 or key != keys[index - 1]]
    assert len(runs) == len(set(runs)), "a subject server is started twice for one key"


def test_two_experiments_over_one_topology_share_a_server():
    """connectivity and qos are judged from different files over the same lab."""
    connectivity, qos = (catalog.preset(key) for key in ("connectivity-smoke", "qos-smoke"))
    assert connectivity.topology_descriptor == qos.topology_descriptor
    cells = plan(request(scenarios=[ScenarioSelection(**CONNECTIVITY, seed=9),
                                    ScenarioSelection(**QOS, seed=17)]))
    keys = {server_key(cell, catalog.preset(cell.selection.experiment)) for cell in cells}
    assert len(keys) == 1


def test_a_false_positive_experiment_is_recorded_as_one():
    """The TOML pins it, so the cell is named after what will actually run."""
    cells = plan(request(scenarios=[ScenarioSelection(
        experiment="connectivity-false-positive",
        scenario_id="connectivity.disable_interface.m1", seed=9)]))
    assert cells[0].selection.no_fault is True
    assert "-nofault-" in cells[0].experiment_id


def test_the_fault_marker_cannot_be_read_as_part_of_another_cell():
    """`_newest_result` globs `{experiment_id}-*.json`, so no id may prefix another."""
    selections = [ScenarioSelection(**CONNECTIVITY),
                  ScenarioSelection(**CONNECTIVITY, no_fault=True)]
    identifiers = [cell_experiment_id("langchain_agent", "a-model", selection, seed)
                   for selection in selections for seed in (9, 90)]
    for identifier in identifiers:
        others = [other for other in identifiers if other != identifier]
        assert not [other for other in others if other.startswith(identifier + "-")]


@pytest.mark.parametrize("text, expected", [
    ("9", [9]),
    ("9, 17, 29", [9, 17, 29]),
    ("9 17\t29", [9, 17, 29]),
])
def test_seeds_are_read_from_what_people_type(text, expected):
    assert parse_seeds(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "9, seventeen"])
def test_a_seed_that_is_not_a_number_is_refused(text):
    with pytest.raises(ValueError):
        parse_seeds(text)


def test_a_row_may_arrive_without_a_seed():
    """The page submits rows to be drawn for; the launcher settles them."""
    row = ScenarioSelection(**CONNECTIVITY)
    assert row.seed is None
    assert plan(request(scenarios=[row]))[0].seed == 0, "planned, pending a draw"
