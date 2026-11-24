"""The request names the tools the agent may call under `tools`, and the model reads them there."""
from __future__ import annotations

from benchmarks.core import build_self_execute_request
from benchmarks.platforms.containerlab import ANI_OPERATIONS, ContainerLabANI
from sut.common.ani_report import public_task_for_model


def test_the_request_lists_the_agent_tools_under_tools():
    request = build_self_execute_request(
        scenario_id="scn_0123456789abcdef", intent="Restore connectivity.", constraints={},
        available_tools=ContainerLabANI.tool_contract(), observation=None)
    assert request["tools"] == list(ANI_OPERATIONS)
    # The field was `ani` until 2026-10-28: the ANI version, its operations and a
    # transport note.
    assert "ani" not in request
    assert public_task_for_model(request)["tools"] == list(ANI_OPERATIONS)
