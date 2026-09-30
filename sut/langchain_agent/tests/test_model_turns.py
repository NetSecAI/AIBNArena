"""The baseline measures its model turns like the shared loop does."""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from sut.langchain_agent.agent import RunUsageCallback


def _response(names: list[str], reasoning: str = "") -> LLMResult:
    message = AIMessage(
        content="hello",
        tool_calls=[{"name": name, "args": {}, "id": f"call-{index}"} for index, name in enumerate(names)],
        additional_kwargs={"reasoning_content": reasoning} if reasoning else {},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_turns_are_measured_and_their_operations_attributed():
    operations: list[int] = []
    callback = RunUsageCallback(None, operations=lambda: len(operations))
    callback.on_llm_start({}, ["prompt 1"])
    callback.on_llm_end(_response(["get_state", "update_config"], reasoning="think"))
    operations += [1, 2]                    # both calls ran through the ANI
    callback.on_llm_start({}, ["prompt 2"])
    callback.on_llm_end(_response([]))
    snapshot = callback.snapshot()
    turns = snapshot["model_turns"]
    assert [turn["turn"] for turn in turns] == [1, 2]
    assert turns[0]["tool_calls"] == ["get_state", "update_config"]
    assert turns[0]["operations"] == [1, 2]
    assert turns[0]["kind"] == "tool_calls" and turns[0]["reasoning_chars"] == 5 and turns[0]["content_chars"] == 5
    assert turns[1]["kind"] == "text" and turns[1]["operations"] == [] and turns[1]["tool_calls"] == []
    assert all(isinstance(turn["seconds"], float) and turn["seconds"] >= 0.0 for turn in turns)
    assert snapshot["model_seconds"] == round(sum(turn["seconds"] for turn in turns), 3)
    assert snapshot["llm_calls"] == 2


def test_a_call_refused_before_the_ani_leaves_no_operation_on_the_turn():
    operations: list[int] = []
    callback = RunUsageCallback(None, operations=lambda: len(operations))
    callback.on_llm_start({}, ["prompt"])
    callback.on_llm_end(_response(["update_config", "update_config"]))
    operations.append(1)                    # the repeat was refused before the ANI
    snapshot = callback.snapshot()
    assert snapshot["model_turns"][0]["tool_calls"] == ["update_config", "update_config"]
    assert snapshot["model_turns"][0]["operations"] == [1]
    # A second snapshot does not attribute the same operations twice.
    assert callback.snapshot()["model_turns"][0]["operations"] == [1]


def test_without_a_counter_the_turns_are_still_measured():
    callback = RunUsageCallback(None)
    callback.on_llm_start({}, ["prompt"])
    callback.on_llm_end(_response(["get_topology"]))
    snapshot = callback.snapshot()
    assert snapshot["model_turns"][0]["operations"] == [] and snapshot["model_turns"][0]["seconds"] >= 0.0
