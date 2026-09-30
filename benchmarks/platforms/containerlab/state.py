from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .types import CommandResult, LabNode

CommandMode = Literal["shell", "srl_cli", "vyos_cli"]


@dataclass(frozen=True)
class StateCommand:
    target: str
    mode: CommandMode
    command: str


@dataclass(frozen=True)
class LabState:
    """Declarative target state applied before benchmark scenarios."""

    id: str
    description: str
    commands: list[StateCommand]

    @classmethod
    def from_file(cls, path: str | Path) -> "LabState":
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        return cls(
            id=payload["id"],
            description=payload.get("description", ""),
            commands=[StateCommand(**item) for item in payload.get("commands", [])],
        )

    def grouped_srl_commands(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for command in self.commands:
            if command.mode == "srl_cli":
                grouped[command.target].append(command.command)
        return dict(grouped)

    def grouped_vyos_commands(self) -> dict[str, list[str]]:
        """VyOS commands per node, so each node gets one configure/commit batch."""
        grouped: dict[str, list[str]] = defaultdict(list)
        for command in self.commands:
            if command.mode == "vyos_cli":
                grouped[command.target].append(command.command)
        return dict(grouped)

    def shell_commands(self) -> list[StateCommand]:
        return [command for command in self.commands if command.mode == "shell"]


def command_result_for_unknown_target(target: str, command: str, nodes: dict[str, LabNode]) -> CommandResult:
    known = ", ".join(sorted(nodes))
    reason = f"unknown lab node '{target}'. Known nodes: {known}"
    return CommandResult(
        target=target,
        command=command,
        returncode=127,
        safe=False,
        reason=reason,
        stderr=reason,
    )
