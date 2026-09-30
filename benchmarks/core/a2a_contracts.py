"""Stable Judge-to-SUT output contract for autonomous benchmark runs."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from benchmarks.core.reporting import device_changes_of
from benchmarks.platforms.containerlab import ANI_OPERATIONS, ANI_VERSION
from scenarios.access import build_sut_request

A2A_CONTRACT_VERSION = "ibn_eval.a2a.v1"
SELF_EXECUTE_MODE = "self_execute"


def require_self_execute_contract(
    contract_version: str,
    expected_output_modes: list[str] | None,
) -> list[str]:
    """Fail closed: active A2A evaluation accepts autonomous reports only."""
    modes = list(expected_output_modes or [SELF_EXECUTE_MODE])
    if contract_version != A2A_CONTRACT_VERSION:
        raise ValueError(
            f"unsupported A2A contract '{contract_version}'; expected {A2A_CONTRACT_VERSION}"
        )
    if modes != [SELF_EXECUTE_MODE]:
        raise ValueError(
            "active A2A evaluation requires "
            "sut_expected_output_modes = ['self_execute']"
        )
    return modes


def ani_contract() -> dict[str, Any]:
    return {
        "version": ANI_VERSION,
        "operations": list(ANI_OPERATIONS),
        "transport": "local-api-now; MCP-compatible-later",
    }


def self_execute_output_contract() -> dict[str, Any]:
    return {
        "self_execute": {
            "description": (
                "The SUT uses ANI tools, executes its own configuration changes, "
                "and returns an execution report. The Judge independently checks "
                "the final environment state and never replays the changes."
            ),
            "schema": {
                "sut_identity": "string",
                "sut_version": "string",
                "model_reported_by_sut": "string|null",
                "provider_reported_model": "string|null",
                "mode": SELF_EXECUTE_MODE,
                "status": "completed|failed|timeout",
                "verified": "boolean",
                "device_changes": [
                    {
                        "transaction_id": "optional ANI transaction id",
                        "operation": "update_config|update_object|rollback_config",
                        "action": {"machine": "node", "command": "native command(s)"},
                        "result": {"ok": "boolean", "safe": "boolean"},
                        "verified_after_action": "boolean",
                        "objective_verification": "optional public success-criteria evidence",
                    }
                ],
                "ani_operations": "operation summaries without private reasoning",
                "execution": {
                    "ani_version": ANI_VERSION,
                    "budget_seconds": "number",
                    "elapsed_seconds": "number",
                    "tool_call_count": "integer",
                    "llm_calls": "integer",
                    "token_usage": "provider usage when available",
                },
                "final_observation": "optional SUT-side public validation evidence",
                "error": "optional error message",
            },
        }
    }


def build_self_execute_request(
    *,
    scenario_id: str,
    intent: str,
    constraints: dict[str, Any],
    available_tools: list[dict[str, Any]],
    observation: dict[str, Any] | None,
    success_criteria: dict[str, Any] | None = None,
    execution_budget_seconds: float = 300.0,
) -> dict[str, Any]:
    """Build the shared public A2A request envelope for an ANI episode."""
    return build_sut_request(
        contract_version=A2A_CONTRACT_VERSION,
        expected_output_modes=[SELF_EXECUTE_MODE],
        scenario_id=scenario_id,
        intent=intent,
        constraints=constraints,
        available_tools=available_tools,
        action_schema=self_execute_output_contract()[SELF_EXECUTE_MODE]["schema"],
        output_contracts=self_execute_output_contract(),
        observation=observation,
        success_criteria=success_criteria,
        execution_budget={"wall_clock_seconds": execution_budget_seconds},
        ani=ani_contract(),
    )


def parse_self_execute_report(raw_response: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ValueError("subject agent response must be a JSON self_execute report") from exc
    if not isinstance(payload, dict):
        raise ValueError("subject agent response must be a JSON object")
    if payload.get("mode") != SELF_EXECUTE_MODE:
        raise ValueError("subject agent response mode must be 'self_execute'")
    if not isinstance(device_changes_of(payload), list):
        raise ValueError("self_execute report must include a device_changes list")
    return payload


def self_execute_report_has_device_change(payload: dict[str, Any]) -> bool:
    for item in device_changes_of(payload) or []:
        if not isinstance(item, dict):
            continue
        action = item.get("action") or {}
        result = item.get("result") or {}
        if action and result.get("ok") is True:
            return True
    return False
