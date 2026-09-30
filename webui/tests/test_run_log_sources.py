"""Every voice a run has reaches a pane on the page.

The runner tags each line with a `LogSource` and `run.js` looks the pane up by
that exact value. The two are in different languages and nothing else holds them
together, so a source renamed on one side would silently send its lines to the
launcher pane -- the judge's output quietly attributed to this tool.
"""
from __future__ import annotations

import re
from pathlib import Path

from webui.form import EpisodeRequest
from webui.orchestrator import LogSource, RunManager, RunRecord

WEBUI = Path(__file__).resolve().parents[1]

EPISODE = {
    "architecture": "langchain_agent",
    "experiment": "connectivity-false-positive",
    "scenario_id": "connectivity.disable_interface.m1",
    "model": "openai/gpt-4o-mini",
    "seed": 9,
    "execution_budget_seconds": 400,
}


def _record() -> RunRecord:
    return RunRecord(id="sources", request=EpisodeRequest(**EPISODE),
                     experiment_id="webui-sources")


def test_each_logged_line_names_its_source():
    manager, record = RunManager(), _record()
    manager.create(record)
    for source in LogSource:
        manager.log(record, f"a line from {source.value}", source)
    assert [event["source"] for event in record.events] == [s.value for s in LogSource]


def test_the_page_has_a_pane_for_every_source():
    page = (WEBUI / "templates" / "run.html").read_text(encoding="utf-8")
    for source in LogSource:
        assert f'id="log-{source.value}"' in page, f"no pane for {source.value}"


def test_the_script_routes_every_source():
    script = (WEBUI / "static" / "run.js").read_text(encoding="utf-8")
    panes = re.search(r"const PANES = \{(.*?)\};", script, re.S)
    assert panes is not None, "run.js no longer declares PANES"
    for source in LogSource:
        assert f"{source.value}:" in panes.group(1), f"run.js drops {source.value}"
