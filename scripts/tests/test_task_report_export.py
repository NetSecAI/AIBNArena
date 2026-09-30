"""The per-task report blocks added for the parameter sheet, and the trace split."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


generate_report = _load("generate_report")
export_results = _load("export_results")


def _payload() -> dict:
    operations = [
        {"operation": "get_running_config", "category": "read", "duration_seconds": 2.0, "ok": True},
        {"operation": "update_config", "category": "mutation", "duration_seconds": 5.0, "ok": True},
        {"operation": "execute_validation", "category": "validation", "duration_seconds": 3.0, "ok": False},
        {"operation": "execute_validation", "category": "validation", "duration_seconds": 4.0, "ok": True},
    ]
    return {
        "scenario": {"id": "connectivity.remove_ip.high.m1"},
        "status": "completed",
        "metrics": {"success": True, "repair_score": 1.0},
        "operation_counts": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050,
                             "llm_calls": 5, "containerlab_reads": 1, "containerlab_mutations": 1,
                             "sut_validations": 2},
        "sut_result": {"status": "completed", "ani_operations": operations,
                       "final_observation": {"passed": True},
                       "execution": {"elapsed_seconds": 100.0, "llm_calls": 5}},
    }


def test_time_by_phase_splits_the_episode_by_what_each_call_did():
    steps = {"seconds": {"thinking": 40.0}}
    split = generate_report.time_by_phase(_payload(), steps)
    assert split["reflection_seconds"] == 40.0
    assert split["action_seconds"] == 5.0           # the mutation
    assert split["reading_seconds"] == 2.0          # the ANI read
    assert split["validation_seconds"] == 7.0
    assert split["episode_seconds"] == 100.0
    assert split["unaccounted_seconds"] == pytest.approx(100.0 - 54.0)


def test_tokens_block_says_when_the_subject_recorded_nothing():
    block = generate_report.tokens_block(_payload())
    assert block["recorded"] is True and block["total_tokens"] == 1050 and block["output_tokens_per_call"] == 10.0
    silent = _payload()
    silent["operation_counts"] = {"input_tokens": None, "output_tokens": None, "llm_calls": 5}
    assert generate_report.tokens_block(silent)["recorded"] is False


def test_validation_block_counts_the_public_validations_and_their_failures():
    block = generate_report.validation_block(_payload())
    assert block["public_validations"] == 2
    assert block["public_validations_failed"] == 1
    assert block["last_public_validation_passed"] is True


def test_report_information_names_every_sheet_item_once():
    document = {"tokens": {"recorded": True, "total_tokens": 1050}, "validation": {"public_validations_failed": 1},
                "operation_counts": {"containerlab_reads": 1, "containerlab_mutations": 1, "sut_validations": 2},
                "timeline": {"steps": [{"type": "call", "ok": True}, {"type": "call", "ok": False},
                                       {"type": "thinking", "reasoning": "x"}],
                             "budget": {"exhausted": False}},
                "run": {"success": True},
                "time_by_phase": {"reflection_seconds": 1.0, "note": "n"}}
    rows = generate_report.report_information(document)
    assert len(rows) == len(generate_report.REPORT_INFORMATION_ITEMS) == 10
    assert [row["item"] for row in rows] == [item for item, _ in generate_report.REPORT_INFORMATION_ITEMS]
    assert rows[1]["value"] == "1 of 2 succeeded"
    assert rows[5]["value"] == 4
    assert rows[9]["value"] == "1 turns carry a reasoning span"


def test_split_trace_cuts_by_kind_and_stores_prompts_as_what_they_appended(tmp_path):
    lines = [
        {"title": "SUT received public A2A task", "payload": {"scenario_id": "x"}},
        {"title": "SUT prompt to model", "payload": {"turn": "1", "prompts": ["System: hello\nHuman: fix it"]}},
        {"title": "SUT model response", "payload": {"turn": "1", "text": "calling"}},
        {"title": "ANI update_config", "payload": {"arguments": {"device": "leaf1"}}},
        {"title": "ANI update_config result", "payload": {"ok": True}},
        {"title": "SUT prompt to model", "payload": {"turn": "2", "prompts": ["System: hello\nHuman: fix it\nAI: calling\nTool: ok"]}},
        {"title": "SUT prompt to model", "payload": {"turn": "3", "prompts": ["a different context"]}},
    ]
    source = tmp_path / "trace.jsonl"
    source.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n", encoding="utf-8")
    index = export_results.split_trace(source, tmp_path / "split")
    assert index["files"] == {"task.jsonl": 1, "prompts.jsonl": 3, "model_messages.jsonl": 1,
                              "ani_operations.jsonl": 2, "other.jsonl": 1}
    prompts = [json.loads(line) for line in (tmp_path / "split" / "prompts.jsonl").read_text().splitlines()]
    assert prompts[0]["seq"] == 1 and prompts[0]["payload"]["prompts"] == ["System: hello\nHuman: fix it"]
    assert prompts[1]["payload"] == {"turn": "2", "prompt_chars": len(lines[5]["payload"]["prompts"][0]), "continues_previous": True,
                                     "appended": "\nAI: calling\nTool: ok"}
    assert prompts[0]["payload"]["prompts"][0] + prompts[1]["payload"]["appended"] == lines[5]["payload"]["prompts"][0]
    assert prompts[2]["payload"]["prompts"] == ["a different context"]   # a restart is stored in full
    operations = [json.loads(line) for line in (tmp_path / "split" / "ani_operations.jsonl").read_text().splitlines()]
    assert [entry["seq"] for entry in operations] == [3, 4]
    other = json.loads((tmp_path / "split" / "other.jsonl").read_text())
    assert other == {"seq": 7, "raw": "not json"}
    assert json.loads((tmp_path / "split" / "index.json").read_text())["lines"] == 8


def test_split_trace_replaces_an_earlier_split(tmp_path):
    source = tmp_path / "trace.jsonl"
    source.write_text(json.dumps({"title": "SUT prompt to model", "payload": {"turn": "1", "prompts": ["p"]}}) + "\n")
    target = tmp_path / "split"
    export_results.split_trace(source, target)
    assert (target / "prompts.jsonl").exists()
    source.write_text(json.dumps({"title": "ANI get_routes", "payload": {}}) + "\n")
    export_results.split_trace(source, target)
    assert not (target / "prompts.jsonl").exists() and (target / "ani_operations.jsonl").exists()


def test_testbed_of_follows_the_experiment_binding():
    assert export_results.testbed_of("connectivity.remove_ip.high.m2") == "sme01-small"
    assert export_results.testbed_of("qos.link_impairment.high.m1") == "sme01-small"
    assert export_results.testbed_of("dhcp_dns.dhcp_provisioning.high.m3") == "sme01-dns"
    assert export_results.testbed_of("filtering.zone_policy_enforcement.high.m1") == "sme01-fw"
    assert export_results.testbed_of("qos.wan_shaping_policy_repair.high.m4") == "sme01-qos"
    assert export_results.testbed_of("qos.assured_bandwidth.m1") == "sme01-qos-greenfield"
    assert export_results.testbed_of("unknown.family.m1") == ""


def _langchain_trace(tmp_path: Path) -> Path:
    entries = [
        {"title": "SUT received public A2A task", "payload": {"scenario_id": "x"}},
        {"title": "SUT prompt to model", "payload": {"turn": 1, "prompts": ["p"]}},
        {"title": "SUT model response", "payload": {"turn": 1, "generations": [{
            "text": "", "content": [{"type": "thinking", "thinking": "look at leaf1"}],
            "reasoning_content": "look at leaf1",
            "tool_calls": [{"name": "get_state", "args": {}, "id": "a", "type": "tool_call"},
                           {"name": "update_config", "args": {}, "id": "b", "type": "tool_call"},
                           {"name": "update_config", "args": {}, "id": "c", "type": "tool_call"}]}]}},
        {"title": "SUT prompt to model", "payload": {"turn": 2, "prompts": ["pp"]}},
        {"title": "SUT model response", "payload": {"turn": 2, "generations": [{
            "text": "done", "content": [{"type": "text", "text": "done"}], "tool_calls": []}]}},
    ]
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    return path


def test_the_baseline_trace_yields_turns_with_reasoning_and_matched_operations(tmp_path):
    trace = generate_report.read_trace(_langchain_trace(tmp_path))
    assert [message["turn"] for message in trace["messages"]] == [1, 2]
    assert trace["messages"][0]["reasoning"] == "look at leaf1"
    assert trace["messages"][0]["tool_calls"] == ["get_state", "update_config", "update_config"]
    assert trace["messages"][1]["text"] == "done" and trace["messages"][1]["reasoning"] is None
    payload = {"sut_result": {"execution": {"elapsed_seconds": 50.0},
                              # the repeated update_config was refused before the ANI
                              "ani_operations": [{"operation": "get_state", "category": "read", "duration_seconds": 1.0, "ok": True},
                                                 {"operation": "update_config", "category": "mutation", "duration_seconds": 2.0, "ok": True}]}}
    steps = generate_report.timeline(payload, trace)
    assert steps["turns"] == 2 and steps["turns_source"].startswith("trace (inferred")
    assert [(step["type"], step.get("tool") or step.get("turn")) for step in steps["steps"]] == [
        ("thinking", 1), ("call", "get_state"), ("call", "update_config"), ("thinking", 2)]
    assert steps["steps"][0]["reasoning"] == "look at leaf1"
    assert steps["seconds"]["thinking"] is None and steps["seconds"]["unaccounted"] == 47.0
    split = generate_report.time_by_phase(payload, steps)
    assert split["reflection_seconds"] is None and "recorded no turn timing" in split["note"]
    assert split["unaccounted_seconds"] == 47.0


def test_recorded_turns_win_over_the_trace(tmp_path):
    trace = generate_report.read_trace(_langchain_trace(tmp_path))
    payload = {"sut_result": {"execution": {"elapsed_seconds": 50.0,
                                            "model_turns": [{"turn": 1, "seconds": 4.0, "kind": "tool_calls",
                                                             "tool_calls": ["get_state"], "operations": [1]}]},
                              "ani_operations": [{"operation": "get_state", "category": "read", "duration_seconds": 1.0, "ok": True}]}}
    steps = generate_report.timeline(payload, trace)
    assert steps["turns_source"].startswith("record") and steps["seconds"]["thinking"] == 4.0
    assert steps["steps"][0]["reasoning"] == "look at leaf1"    # the words still come from the trace


def test_device_configuration_summary_reads_the_judge_manifest():
    payload = {"device_configurations": {
        "directory": "/runs/x/devices/plate-1",
        "snapshots": {label: {"ok": True, "devices": {"leaf1": {"kind": "nokia_srlinux", "ok": True, "file": f"leaf1/{label}.txt"},
                                                       "host1": {"kind": "linux", "ok": label != "after_sut", "file": f"host1/{label}.txt"}}}
                      for label in ("healthy", "before_sut", "after_sut")},
        "changes": {"healthy_to_before_sut": {"leaf1": {"lines_added": 1, "lines_removed": 1, "file": "leaf1/healthy_to_before_sut.diff"}},
                    "before_sut_to_after_sut": {"leaf1": {"lines_added": 2, "lines_removed": 1, "file": "leaf1/before_sut_to_after_sut.diff"}}},
        "changed_by_sut": ["leaf1"], "note": "n"}}
    block = generate_report.device_configuration_summary(payload)
    assert block["available"] is True and block["changed_by_sut"] == ["leaf1"]
    assert block["snapshots_taken"] == ["after_sut", "before_sut", "healthy"] and block["snapshots_failed"] == []
    leaf, host = block["devices"][1], block["devices"][0]
    assert leaf["device"] == "leaf1" and leaf["changed_by_fault"] and leaf["changed_by_sut"] and leaf["sut_change"]["lines_added"] == 2
    assert host["device"] == "host1" and not host["changed_by_sut"] and host["snapshots"]["after_sut"] is False
    html = generate_report.device_configurations_html(block)
    assert "yes (+2 -1)" in html and "after_sut (failed)" in html
    assert generate_report.device_configuration_summary({})["available"] is False
    assert "no device snapshots" in generate_report.device_configurations_html(
        generate_report.device_configuration_summary({}))
