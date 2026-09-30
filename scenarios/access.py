from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


_PRIVATE_KEYS = {
    "affected_nodes",
    "bindings",
    "scenario_definition",
    "corruption_percent",
    "delay_ms",
    "evaluator",
    "expected_affected_nodes",
    "expected_failure",
    "expected_keywords",
    "fault_id",
    "injection",
    "oracle_hint",
    "private",
    "private_scenario_info",
    "selected_method",
    "source_file",
    "task_id",
    "topology_patch",
}


@dataclass(frozen=True)
class CompiledScenarioInstance:
    """One evaluator-private task produced by the scenario compiler."""

    document: dict[str, Any]

    def __post_init__(self) -> None:
        for key in ("id", "version", "domain", "oracles", "public", "private"):
            if key not in self.document:
                raise ValueError(f"compiled scenario is missing '{key}'")
        intent = self.document["public"].get("intent")
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError(f"compiled scenario {self.id!r} has no public intent")

    @property
    def id(self) -> str:
        return str(self.document["id"])

    @property
    def domain(self) -> str:
        return str(self.document["domain"])

    @property
    def intent(self) -> str:
        return str(self.document["public"]["intent"])

    @property
    def private(self) -> dict[str, Any]:
        return copy.deepcopy(self.document["private"])

    def public_view(self) -> dict[str, Any]:
        """Return only fields explicitly authored for SUT-visible use."""
        public = copy.deepcopy(self.document["public"])
        assert_no_private_fields(public)
        return public


@dataclass(frozen=True)
class CompiledScenarioSuite:
    document: dict[str, Any]

    def __post_init__(self) -> None:
        if self.document.get("schema_version") != "0.1":
            raise ValueError("compiled scenario suite must use schema_version '0.1'")
        if not isinstance(self.document.get("tasks"), list):
            raise ValueError("compiled scenario suite must contain a tasks list")
        ids = [str(item.get("id")) for item in self.document["tasks"]]
        duplicates = sorted(item_id for item_id, count in Counter(ids).items() if count > 1)
        if duplicates:
            raise ValueError(f"compiled scenario suite contains duplicate IDs: {duplicates}")

    @classmethod
    def from_file(cls, path: str | Path) -> "CompiledScenarioSuite":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: expected a YAML mapping")
        return cls(payload)

    @property
    def topology_id(self) -> str:
        return str(self.document["topology"]["id"])

    def instances(self, *, domain: str | None = None) -> list[CompiledScenarioInstance]:
        result = [CompiledScenarioInstance(item) for item in self.document["tasks"]]
        return [item for item in result if domain is None or item.domain == domain]

    def select(self, instance_ids: Iterable[str]) -> list[CompiledScenarioInstance]:
        by_id = {item.id: item for item in self.instances()}
        selected: list[CompiledScenarioInstance] = []
        for instance_id in instance_ids:
            if instance_id not in by_id:
                known = ", ".join(sorted(by_id))
                raise KeyError(f"unknown compiled scenario '{instance_id}'. Known scenarios: {known}")
            selected.append(by_id[instance_id])
        return selected


def build_sut_request(
    *,
    intent: str,
    scenario_id: str,
    contract_version: str,
    expected_output_modes: list[str],
    constraints: dict[str, Any],
    available_tools: list[dict[str, Any]],
    action_schema: dict[str, Any],
    output_contracts: dict[str, Any],
    observation: dict[str, Any] | None,
    success_criteria: dict[str, Any] | None = None,
    execution_budget: dict[str, Any] | None = None,
    ani: dict[str, Any] | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    """Build the SUT envelope from a strict allowlist of public fields."""
    payload: dict[str, Any] = {
        "contract_version": contract_version,
        "expected_output_modes": copy.deepcopy(expected_output_modes),
        "scenario_id": scenario_id,
        "intent": intent,
        "constraints": copy.deepcopy(constraints),
        "available_tools": copy.deepcopy(available_tools),
        "action_schema": copy.deepcopy(action_schema),
        "output_contracts": copy.deepcopy(output_contracts),
        "observation": copy.deepcopy(observation),
    }
    if success_criteria is not None:
        payload["success_criteria"] = copy.deepcopy(success_criteria)
    if execution_budget is not None:
        payload["execution_budget"] = copy.deepcopy(execution_budget)
    if ani is not None:
        payload["ani"] = copy.deepcopy(ani)
    if question is not None:
        payload["question"] = question
    assert_no_private_fields(payload)
    return payload


def assert_no_private_fields(payload: Any, path: str = "$") -> None:
    """Fail closed if an evaluator-private key enters an A2A request."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _PRIVATE_KEYS:
                raise ValueError(f"private benchmark field leaked into SUT payload at {path}.{key}")
            assert_no_private_fields(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            assert_no_private_fields(value, f"{path}[{index}]")
