"""Synchronous lifecycle adapter for the shared official A2A transport."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from benchmarks.platforms.containerlab import ContainerLabANI

from .a2a import A2AClient, A2ASubject
from .a2a_contracts import build_self_execute_request, parse_self_execute_report
from .reporting import device_changes_of
from .contracts import OperationCounts, SUTResponse


class A2ASUTClient:
    def __init__(
        self,
        url: str,
        *,
        execution_budget_seconds: float,
        timeout_seconds: float | None = None,
    ) -> None:
        self.url = url
        self.execution_budget_seconds = execution_budget_seconds
        self.timeout_seconds = timeout_seconds or execution_budget_seconds + 120.0

    def invoke(self, task: Mapping[str, Any]) -> SUTResponse:
        return asyncio.run(self._invoke(task))

    async def _invoke(self, task: Mapping[str, Any]) -> SUTResponse:
        payload = build_self_execute_request(
            scenario_id=str(task["scenario_id"]),
            intent=str(task["intent"]),
            constraints={
                "avoid_destructive_commands": True,
                "preserve_existing_connectivity": True,
                "do_not_modify_unrelated_devices": True,
            },
            available_tools=ContainerLabANI.tool_contract(),
            observation=(dict(task["observation"]) if isinstance(task.get("observation"), Mapping) else None),
            success_criteria=(dict(task["success_criteria"]) if task.get("success_criteria") else None),
            execution_budget_seconds=self.execution_budget_seconds,
        )
        subject = A2ASubject(
            url=self.url,
            timeout_seconds=self.timeout_seconds,
        )
        async with A2AClient(subject) as client:
            raw = await client.request(payload)
            card = client.agent_card
        report = parse_self_execute_report(raw)
        operations = _operation_counts(report)
        execution = report.get("execution") if isinstance(report.get("execution"), Mapping) else {}
        return SUTResponse(
            result=report,
            identity=str(getattr(card, "name", None) or "unknown-a2a-sut"),
            version=str(getattr(card, "version", None) or "unknown"),
            reported_model=_optional_string(report.get("model_reported_by_sut") or execution.get("reported_model")),
            runtime_parameters=(
                dict(execution.get("model_parameters"))
                if isinstance(execution.get("model_parameters"), Mapping)
                else {}
            ),
            provider_model=_optional_string(report.get("provider_reported_model") or execution.get("provider_model")),
            serving=(
                dict(execution.get("serving"))
                if isinstance(execution.get("serving"), Mapping)
                else {}
            ),
            operations=operations,
        )


def _operation_counts(report: Mapping[str, Any]) -> OperationCounts:
    execution = report.get("execution") if isinstance(report.get("execution"), Mapping) else {}
    counts = OperationCounts(llm_calls=int(execution.get("llm_calls", 0)))
    for item in report.get("ani_operations") or []:
        if not isinstance(item, Mapping):
            continue
        operation = str(item.get("operation", ""))
        category = item.get("category")
        if item.get("dispatched") is False:
            # Refused by an interaction cap before anything ran: it is an attempt the
            # subject made and a failed operation, but nothing was read, written,
            # validated or built, so no category counter may claim it.
            counts.failed_operations += 1
            continue
        if category == "read" or operation in {"get_topology", "get_state", "get_running_config", "get_object"}:
            # Like mutations: the attempt is counted here, the outcome beside it. A
            # read the router refused returned no state, so it adds to ani_reads and
            # failed_operations but not to successful_reads.
            counts.ani_reads += 1
            counts.successful_reads += int(item.get("ok") is True)
        elif category == "mutation" or operation in {"update_config", "update_object", "rollback_config"}:
            counts.ani_mutations += 1
            counts.successful_mutations += int(item.get("ok") is True)
        elif category == "validation" or operation == "execute_validation":
            # Unlike a refused read, a validation that ran and failed is a real validation
            # (the check was measured and the answer was no), so ok is not consulted here.
            counts.validations += 1
        if item.get("ok") is False:
            counts.failed_operations += 1
    counts.unsafe_operations = int(execution.get("unsafe_operations", 0))
    for action in device_changes_of(report) or []:
        if isinstance(action, Mapping) and (action.get("result") or {}).get("safe") is False:
            if "unsafe_operations" not in execution:
                counts.unsafe_operations += 1
    usage = execution.get("token_usage") if isinstance(execution.get("token_usage"), Mapping) else {}
    counts.input_tokens = _optional_int(usage.get("input_tokens"))
    counts.output_tokens = _optional_int(usage.get("output_tokens"))
    counts.total_tokens = _optional_int(usage.get("total_tokens"))
    return counts


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
