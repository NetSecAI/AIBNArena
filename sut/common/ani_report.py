"""Turning ANI results into the judge-facing `self_execute` report.

These projections define what the benchmark actually measures: `device_changes[]` feeds
repair action evidence, device scope, unsafe-action fallback accounting, and failed-action
count. Two SUTs must share them or their metrics mean different things.

`SELF_EXECUTE_MODE` mirrors `benchmarks/core/a2a_contracts.py` deliberately rather
than importing it: a SUT must not depend on the judge that scores it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from benchmarks.platforms.containerlab import ANI_OPERATIONS, ANI_VERSION

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


#: What the judge hands a subject for its ANI and not for its model: the rate the protected
#: flow measured on the healthy network of the episode, which the ANI's throughput check holds
#: the flow to (`healthy_throughput_mbps`, benchmarks/core/lifecycle.py). Shown to the model
#: under `observation` in main04, it was read as the rate under the fault in turn 1 of all 8
#: QoS episodes; a subject then tuned qos m3's class rates from 7.651 Mbit/s and never
#: measured the 0.0 the removed classifier left. The ANI still reads it from the whole task.
JUDGE_ONLY_OBSERVATION_KEYS = ("healthy_throughput_mbps",)


def public_task_for_model(task: dict[str, Any], surface: Iterable[str] | None = None) -> dict[str, Any]:
    """The task as the model reads it.

    Given `surface`, the names of the tools the model is handed, `tools` names those
    rather than the judge's list. The judge declares the platform's operations; a subject
    offers its own selection of them beside tools of its own (the documentation search),
    and a tool named to the model that it cannot call invites the call. A platform operation the judge did not declare stays out.

    The observation leaves out what only the ANI uses (`JUDGE_ONLY_OBSERVATION_KEYS`); the
    task itself, which the subject hands its ANI, is not changed.
    """
    public = {
        key: task.get(key)
        for key in ("intent", "question", "constraints", "observation", "success_criteria", "execution_budget", "tools")
        if task.get(key) is not None
    }
    if isinstance(public.get("observation"), dict):
        public["observation"] = {
            name: ({key: value for key, value in entry.items() if key not in JUDGE_ONLY_OBSERVATION_KEYS}
                   if isinstance(entry, dict) else entry)
            for name, entry in public["observation"].items()
        }
    if surface is not None and isinstance(public.get("tools"), list):
        declared = set(public["tools"])
        public["tools"] = [name for name in surface if name in declared or name not in ANI_OPERATIONS]
    return public


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
    # A subject write that failed half-way compensates itself and carries the
    # rollback_config envelope under "rollback"; those are device mutations too and
    # the report must count them, or a compensated failure reads as fewer actions
    # than an uncompensated one.
    rollback = result.get("rollback")
    if isinstance(rollback, dict) and rollback.get("operation") == "rollback_config":
        changes.extend(device_changes_from_ani_result(rollback))
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
#: `get_topology_simple` is a subject's narrower answer from one `get_topology` read.
READ_OPERATIONS = ("get_topology", "get_topology_simple", "get_state", "get_running_config", "get_object")
MUTATION_OPERATIONS = ("update_config", "update_object", "rollback_config")
VALIDATION_OPERATION = "execute_validation"
#: The agent's own end of the episode (ANI v0.4): neither a read, a write nor a check.
SUBMISSION_OPERATION = "submit_result"


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
    if operation == SUBMISSION_OPERATION:
        return "submission"
    return None


def operation_category(operation: str, result: dict[str, Any]) -> str:
    """What the call did, not only what it was named.

    The name settles it for the ANI's own operations.

    A SUT tool that dispatches to the ANI is settled by the envelope it answers with: a
    tool that sends its change through `update_config` and returns the ANI's own result
    unwrapped performed a mutation, and categorising it by its tool name would file a
    real device change under the generic `tool`, where every counter keyed on the
    category misses it. A call that never reached the ANI keeps its own name in the
    envelope, so a dry run and a failure before dispatch stay `tool`, which is what
    they are.
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
