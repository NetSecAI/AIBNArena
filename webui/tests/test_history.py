"""Runs read back after a restart: the table, the page and its logs."""
from dataclasses import replace

from fastapi.testclient import TestClient

import webui.app as app_module
from webui import routes
from webui.form import EpisodeRequest
from webui.orchestrator import LogSource, RunManager, RunRecord, RunState, history
from webui.orchestrator.run_state import EVENT_HISTORY

EPISODE = {
    "architecture": "langchain_agent",
    "experiment": "connectivity-smoke",
    "scenario_id": "connectivity.disable_interface.low.m1",
    "model": "openai/fr-gpt-5.4",
    "seed": 9,
    "execution_budget_seconds": 400,
}


def _finished_run(identifier, root, *, error=None):
    manager = RunManager()
    record = RunRecord(id=identifier, request=EpisodeRequest(**EPISODE),
                       experiment_id=f"webui-{identifier}")
    manager.create(record)
    manager.log(record, "subject ready on port 8003", LogSource.LAUNCHER)
    # A process line lives in its log file; the record keeps the launcher's alone.
    manager.log(record, "phase invoke_sut: started", LogSource.BENCHMARK)
    record.verdict = {"benchmark_success": True}
    record.result_path = "reports/manual/results/x.json"
    manager.finish(record, error=error)
    assert history.save(record, root / identifier)
    return record


def _restored(root, identifier):
    manager = RunManager()
    history.restore(manager, root)
    return manager, manager.get(identifier)


def test_a_finished_run_is_read_back_as_it_ended(tmp_path):
    original = _finished_run("20260923T120000Z-aaaa", tmp_path)
    manager, record = _restored(tmp_path, original.id)
    assert record.summary() == original.summary()
    assert record.request.scenario_id == "connectivity.disable_interface.low.m1"
    # Nothing is running it, so it cannot hold the lab.
    assert not manager.busy
    assert [event["line"] for event in record.events if event["type"] == "log"] == [
        "subject ready on port 8003"]
    assert record.events[-1] == {"type": "finished", **original.summary()}


def test_a_run_the_server_stopped_midway_reads_back_as_failed(tmp_path):
    manager = RunManager()
    record = RunRecord(id="20260923T120000Z-bbbb", request=EpisodeRequest(**EPISODE),
                       experiment_id="webui-bbbb")
    manager.create(record)
    history.save(record, tmp_path / record.id)
    _, back = _restored(tmp_path, record.id)
    assert back.state is RunState.FAILED
    assert back.error == "the web server stopped before this run finished"


def test_the_process_logs_are_read_back_once_on_first_view(tmp_path):
    original = _finished_run("20260923T120000Z-cccc", tmp_path)
    (tmp_path / original.id / "benchmark.log").write_text("phase a\nbenchmark_success: True\n")
    (tmp_path / original.id / "sut.log").write_text("INFO: started\n")
    _, record = _restored(tmp_path, original.id)
    history.load_logs(record)
    history.load_logs(record)
    assert [(event["source"], event["line"]) for event in record.events if event["type"] == "log"] == [
        ("launcher", "subject ready on port 8003"),
        ("benchmark", "phase a"), ("benchmark", "benchmark_success: True"),
        ("sut", "INFO: started"),
    ]
    assert record.events[-1]["type"] == "finished"


def test_a_long_log_keeps_the_launcher_its_last_lines_and_the_end(tmp_path):
    original = _finished_run("20260923T120000Z-dddd", tmp_path)
    (tmp_path / original.id / "sut.log").write_text(
        "\n".join(f"line {number}" for number in range(EVENT_HISTORY + 100)))
    _, record = _restored(tmp_path, original.id)
    history.load_logs(record)
    events = list(record.events)
    assert len(events) == EVENT_HISTORY
    assert events[0]["line"] == "subject ready on port 8003"
    assert events[-2]["line"] == f"line {EVENT_HISTORY + 99}"
    assert events[-1]["type"] == "finished"


def test_a_record_this_version_cannot_read_is_skipped(tmp_path):
    (tmp_path / "20260923T000000Z-bad0").mkdir()
    (tmp_path / "20260923T000000Z-bad0" / "run.json").write_text("{not json")
    _finished_run("20260923T120000Z-good", tmp_path)
    assert history.restore(RunManager(), tmp_path) == 1


def test_a_restarted_server_lists_the_run_and_serves_its_page(tmp_path, monkeypatch):
    original = _finished_run("20260923T120000Z-eeee", tmp_path)
    (tmp_path / original.id / "sut.log").write_text("INFO: a subject line\n")
    fresh = RunManager()
    moved = replace(routes.config, log_dir=str(tmp_path))
    for module in (routes, app_module):
        monkeypatch.setattr(module, "config", moved)
        monkeypatch.setattr(module, "MANAGER", fresh)
    with TestClient(app_module.app) as client:
        assert original.id in client.get("/").text
        assert client.get(f"/run/{original.id}").status_code == 200
        stream = client.get(f"/api/runs/{original.id}/events").text
        assert "INFO: a subject line" in stream
        assert '"type": "finished"' in stream
