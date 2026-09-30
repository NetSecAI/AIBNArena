from __future__ import annotations

import json
from pathlib import Path

import pytest

from sut.langchain_agent.ani_tools import LangChainANIAdapter, RepeatedANIRequestError


class FakeANI:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def dispatch(self, operation, arguments, *, task=None):
        self.calls.append((operation, arguments, task))
        return {**self.result, "operation": operation}


def test_tools_are_langchain_wrappers_around_independent_ani(tmp_path: Path):
    ani = FakeANI({"ok": True, "nodes": [{"name": "leaf1"}]})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    result = json.loads(tools["get_topology"].invoke({"nodes": ["leaf1"]}))
    assert set(tools) == {"get_topology", "get_state", "get_running_config", "get_object", "update_config", "update_object", "execute_validation", "rollback_config"}
    assert ani.calls[0][0] == "get_topology"
    assert result["context"]["truncated"] is False
    assert adapter.trace.operations[0]["category"] == "read"


def test_tools_expose_the_full_strict_ani_schema(tmp_path: Path):
    adapter = LangChainANIAdapter(FakeANI({"ok": True}), task={}, artifact_directory=tmp_path)
    schemas = {item.name: item.args_schema.model_json_schema() for item in adapter.tools()}

    state_views = schemas["get_state"]["properties"]["views"]["anyOf"][0]["items"]
    assert state_views["enum"] == ["interfaces", "routes", "qdisc", "system"]

    update = schemas["update_config"]
    assert update["required"] == ["changes"]
    assert update["$defs"]["ConfigChange"]["required"] == ["target", "commands"]

    validation_type = schemas["execute_validation"]["$defs"]["ValidationCheck"]["properties"]["type"]
    assert validation_type["enum"] == ["public_success_criteria", "icmp"]
    assert all(schema["additionalProperties"] is False for schema in schemas.values())


def test_large_result_reports_truncation_and_artifact(tmp_path: Path):
    ani = FakeANI({"ok": True, "nodes": [{"name": f"node-{index}", "config": "x" * 400} for index in range(30)]})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path, max_context_chars=512, default_page_size=2)
    output = json.loads(adapter.tools()[0].invoke({"offset": 0, "limit": 2}))
    context = output["context"]
    assert context["truncated"] is True
    assert context["complete_size_bytes"] > context["returned_size_bytes"]
    assert context["page"] == {"offset": 0, "limit": 2, "total_items": 30}
    assert Path(context["artifact_ref"]).is_file()
    assert context["sha256"] in Path(context["artifact_ref"]).name
    assert adapter.trace.operations[0]["artifact_ref"] == context["artifact_ref"]


def test_operation_categories_never_call_reads_configuration_actions(tmp_path: Path):
    ani = FakeANI({"ok": True, "checks": [{"type": "public_success_criteria", "passed": True}]})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]})
    assert adapter.trace.operations[0]["category"] == "validation"
    assert adapter.trace.final_validation["passed"] is True


def test_three_identical_failed_requests_stop_the_agent_loop(tmp_path: Path):
    ani = FakeANI({"ok": False, "error": "invalid request"})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path)
    tool = {item.name: item for item in adapter.tools()}["get_state"]

    tool.invoke({"nodes": ["leaf1"], "views": ["routes"]})
    tool.invoke({"nodes": ["leaf1"], "views": ["routes"]})
    with pytest.raises(RepeatedANIRequestError, match="3 identical failed get_state"):
        tool.invoke({"nodes": ["leaf1"], "views": ["routes"]})

    assert adapter.trace.failed_operations == 3
    assert len(adapter.trace.operations) == 3


def test_get_object_is_a_read_of_one_named_thing(tmp_path: Path):
    ani = FakeANI({"ok": True, "node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "found": True,
                   "output": "admin-state enable"})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    result = json.loads(tools["get_object"].invoke({"node": "leaf2", "kind": "interface", "name": "ethernet-1/40"}))
    assert ani.calls[0][0] == "get_object"
    assert ani.calls[0][1] == {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40"}
    assert json.loads(result["data"])["found"] is True
    assert adapter.trace.operations[0]["category"] == "read"


def test_update_object_is_a_mutation_with_its_own_arguments(tmp_path: Path):
    ani = FakeANI({"ok": True, "transaction_id": "ani_tx_1", "changes": [{"target": "leaf2", "commands": ["set / interface ethernet-1/40 admin-state enable"], "result": {"ok": True, "safe": True}}],
                   "rollback_available": True, "compiled": {"commands": ["set / interface ethernet-1/40 admin-state enable"]}})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    result = json.loads(tools["update_object"].invoke({"node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "set": {"admin_state": "enable"}}))
    assert ani.calls[0][0] == "update_object"
    assert ani.calls[0][1]["set"] == {"admin_state": "enable"}
    assert json.loads(result["data"])["transaction_id"] == "ani_tx_1"
    assert adapter.trace.operations[0]["category"] == "mutation"


def test_update_object_names_every_attribute_the_ani_accepts():
    """The model sees this description, not the ANI's own: a word missing here is one it never uses.

    The two drifted apart once already -- address_action lost its values, and the model
    kept sending `replace`, which the ANI refuses (NetRepairArena gpt-5.4, 2026-09-25).
    """
    from benchmarks.platforms.containerlab.object_writes import ATTRIBUTES, OBJECT_WRITE_KINDS
    from sut.langchain_agent.ani_tools import UpdateObjectArguments

    described = UpdateObjectArguments.model_fields["set"].description
    missing = sorted(f"{kind}.{attribute}" for kind, attributes in ATTRIBUTES.items()
                     for attribute in attributes if attribute not in described)
    assert missing == []
    assert set(UpdateObjectArguments.model_fields["kind"].annotation.__args__) == set(OBJECT_WRITE_KINDS)
