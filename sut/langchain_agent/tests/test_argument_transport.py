"""The ANI tools accept a list that arrived as a JSON string, and are bound with typed optionals.

E1 (2026-09-16): 766 `get_state` calls failed with "nodes: Input should be a valid list"
because vLLM's qwen3_xml parser delivered `nodes='["guest1"]'` for the anyOf-typed
optional list. The contract is unchanged: a scalar string still fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sut.langchain_agent.agent import ForcedToolChoiceRelaxed
from sut.langchain_agent.ani_tools import (
    GetStateArguments,
    LangChainANIAdapter,
    UpdateConfigArguments,
)
from sut.langchain_agent.tests.test_ani_tools import FakeANI


def test_a_list_that_arrived_as_a_json_string_validates_as_the_list():
    arguments = GetStateArguments.model_validate({"nodes": '["guest1"]', "views": '["interfaces"]'})
    assert arguments.nodes == ["guest1"] and arguments.views == ["interfaces"]


def test_nested_changes_decode_at_every_level():
    arguments = UpdateConfigArguments.model_validate({
        "changes": '[{"target": "leaf1", "commands": ["set x"], "rollback_commands": "[\\"delete x\\"]"}]'})
    assert arguments.changes[0].commands == ["set x"]
    assert arguments.changes[0].rollback_commands == ["delete x"]


def test_a_scalar_string_for_a_list_still_fails_as_before():
    with pytest.raises(ValidationError):
        GetStateArguments.model_validate({"nodes": "guest1"})
    with pytest.raises(ValidationError):
        GetStateArguments.model_validate({"nodes": "[not json"})


def test_the_tool_invocation_path_decodes_too(tmp_path: Path):
    ani = FakeANI({"ok": True, "nodes": [{"name": "guest1"}]})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    tools["get_state"].invoke({"nodes": '["guest1"]', "views": '["interfaces"]'})
    assert ani.calls[0][1]["nodes"] == ["guest1"] and ani.calls[0][1]["views"] == ["interfaces"]


def test_bound_tools_offer_optional_lists_with_a_declared_type(tmp_path: Path):
    adapter = LangChainANIAdapter(FakeANI({"ok": True}), task={}, artifact_directory=tmp_path)
    model = ForcedToolChoiceRelaxed(model="openai/x", api_key="none", tool_choice_override="auto")
    bound = model.bind_tools(adapter.tools(), tool_choice="any")
    offered = {tool["function"]["name"]: tool["function"]["parameters"] for tool in bound.kwargs["tools"]}
    for tool, parameter in (("get_state", "nodes"), ("get_state", "views"), ("get_topology", "nodes"),
                            ("get_running_config", "paths")):
        spec = offered[tool]["properties"][parameter]
        assert "anyOf" not in spec and spec["type"] == "array", (tool, parameter, spec)
    assert offered["get_state"]["properties"]["views"]["items"]["enum"] == ["interfaces", "routes", "qdisc", "system"]
    assert offered["update_config"]["properties"]["changes"]["type"] == "array"
    assert bound.kwargs["tool_choice"] == "auto"
