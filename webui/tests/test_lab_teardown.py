"""The last cell on each testbed tears its lab down; the others leave it for the next."""
from __future__ import annotations

from types import SimpleNamespace

from webui.orchestrator.campaign_runner import lab_teardown_plan


def _cell(index, experiment):
    return SimpleNamespace(index=index, selection=SimpleNamespace(experiment=experiment))


PRESETS = {
    "filtering-smoke": SimpleNamespace(topology_descriptor="fw.yaml", reference_state="fw.json"),
    "dhcp_dns-smoke": SimpleNamespace(topology_descriptor="dns.yaml", reference_state="dns.json"),
    "connectivity-smoke": SimpleNamespace(topology_descriptor="small.yaml", reference_state="small.json"),
    "qos-smoke": SimpleNamespace(topology_descriptor="small.yaml", reference_state="small.json"),
}


def test_only_the_last_cell_on_a_testbed_destroys_it():
    plan = [_cell(0, "filtering-smoke"), _cell(1, "filtering-smoke"),
            _cell(2, "dhcp_dns-smoke"),
            _cell(3, "connectivity-smoke"), _cell(4, "connectivity-smoke"), _cell(5, "qos-smoke")]
    teardown = lab_teardown_plan(plan, lambda cell: PRESETS[cell.selection.experiment])
    # filtering ends at 1, dhcp at 2; connectivity and qos share the small lab, so it
    # stays up through 3 and 4 and comes down after the campaign's last cell.
    assert teardown == {1, 2, 5}


def test_a_single_cell_campaign_tears_down_at_its_end():
    assert lab_teardown_plan([_cell(0, "qos-smoke")], lambda cell: PRESETS[cell.selection.experiment]) == {0}
