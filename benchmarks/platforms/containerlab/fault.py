from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .state import StateCommand


@dataclass(frozen=True)
class LabFault:
    """A controlled fault injected before evaluating a SUT."""

    id: str
    description: str
    intent: str
    commands: list[StateCommand]
    expected_failure: str = ""
    expected_affected_nodes: list[str] = field(default_factory=list)
    restore_commands: list[StateCommand] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict) -> "LabFault":
        return cls(
            id=payload["id"],
            description=payload.get("description", ""),
            intent=payload.get("intent", ""),
            commands=[StateCommand(**item) for item in payload.get("commands", [])],
            expected_failure=payload.get("expected_failure", ""),
            expected_affected_nodes=[
                str(node) for node in payload.get("expected_affected_nodes", [])
            ],
            restore_commands=[
                StateCommand(**item) for item in payload.get("restore_commands", [])
            ],
        )


@dataclass(frozen=True)
class FaultCatalog:
    """A named collection of controlled faults."""

    faults: dict[str, LabFault]

    @classmethod
    def from_file(cls, path: str | Path) -> "FaultCatalog":
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        faults = {item["id"]: LabFault.from_mapping(item) for item in payload.get("faults", [])}
        return cls(faults=faults)

    def get(self, fault_id: str) -> LabFault:
        if fault_id not in self.faults:
            known = ", ".join(sorted(self.faults))
            raise KeyError(f"unknown fault '{fault_id}'. Known faults: {known}")
        return self.faults[fault_id]
