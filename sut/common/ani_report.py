"""Turning ANI results into the judge-facing `self_execute` report.

These projections define what the benchmark actually measures: `device_changes[]` feeds
repair action evidence, device scope, unsafe-action fallback accounting, and failed-action
count. Two SUTs must share them or their metrics mean different things.

`SELF_EXECUTE_MODE` mirrors `benchmarks/core/a2a_contracts.py` deliberately rather
than importing it: a SUT must not depend on the judge that scores it.
"""
from __future__ import annotations

from typing import Any

from benchmarks.platforms.containerlab import ANI_VERSION

SELF_EXECUTE_MODE = "self_execute"

__all__ = [
    "ANI_VERSION",
    "SELF_EXECUTE_MODE",
    "device_changes_from_ani_result",
    "objective_from_validation",
    "ani_category",
    "operation_category",
    "operation_summary",
    "public_task_for_model",
    "task_summary",
]


def public_task_for_model(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in ("intent", "question", "constraints", "observation", "success_criteria", "execution_budget", "ani")
        if task.get(key) is not None
    }


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": task.get("intent"),
        "question": task.get("question"),
        "execution_budget": task.get("execution_budget"),
        "success_criteria": task.get("success_criteria"),
        "observation_keys": sorted((task.get("observation") or {}).keys()) if isinstance(task.get("observation"), dict) else [],
    }


def device_changes_from_ani_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    operation = result.get("operation")
    if operation not in {"update_config", "update_object", "rollback_config"}:
        return []
    changes = []
    for change in result.get("changes") or []:
        if not isinstance(change, dict):
            continue
        action_result = change.get("result") or {}
        changes.append({
            "transaction_id": result.get("transaction_id"),
            "operation": operation,
            "action": {
                "machine": change.get("target"),
                "command": "\n".join(change.get("commands") or []),
                "reason": change.get("reason") or "",
            },
            "result": action_result,
            "verified_after_action": False,
        })
    return changes


def objective_from_validation(result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("operation") != "execute_validation":
        return None
    for check in result.get("checks") or []:
        if isinstance(check, dict) and check.get("type") == "public_success_criteria":
            return check
    return None


#: The ANI operations, by what they do to the lab. A summary is categorised by these
#: first, and by what the call turned out to perform second; see `operation_category`.
READ_OPERATIONS = ("get_topology", "get_state", "get_running_config", "get_object")
MUTATION_OPERATIONS = ("update_config", "update_object", "rollback_config")
VALIDATION_OPERATION = "execute_validation"


def ani_category(operation: str) -> str | None:
    """The ANI category a name implies, or None when the name is not an ANI operation.

    Public because a caller sometimes has only the name: an interaction limit has to
    know which budget a call would spend before the call is dispatched and there is
    any result to read.
    """
    if operation in READ_OPERATIONS:
        return "read"
    if operation in MUTATION_OPERATIONS:
        return "mutation"
    if operation == VALIDATION_OPERATION:
        return "validation"
    return None


def operation_category(operation: str, result: dict[str, Any]) -> str:
    """What the call did, not only what it was named.

    The name settles it for the ANI's own operations. A subject tool of its own that
    dispatches to the ANI is settled by the envelope it answers with: one that sends
    a change through `update_config` and returns the ANI's result unwrapped made a
    device change, whatever it is called, and filing it under the generic `tool` would
    hide it from every counter keyed on the category. A call that never reached the ANI
    keeps its own name in the envelope, so a failure before dispatch stays `tool`,
    which is what it is.
    """
    category = ani_category(operation)
    if category is not None:
        return category
    performed = result.get("operation")
    if isinstance(performed, str) and performed != operation:
        category = ani_category(performed)
        if category is not None:
            return category
    return "tool"


def operation_summary(
    operation: str,
    result: dict[str, Any],
    duration_seconds: float,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "operation": operation,
        "category": operation_category(operation, result),
        "ok": bool(result.get("ok")),
        "duration_seconds": duration_seconds,
        "transaction_id": result.get("transaction_id"),
        "error": result.get("error"),
    }
    if result.get("dispatched") is False:
        # Carried into the record so a counter can tell an attempt that was refused
        # before dispatch from one the lab actually performed and failed.
        summary["dispatched"] = False
    if result.get("indeterminate") is True:
        summary["indeterminate"] = True
    if arguments is not None:
        summary["arguments"] = arguments
    return summary
