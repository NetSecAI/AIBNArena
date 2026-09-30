from __future__ import annotations

from .executor import ContainerLabExecutor
from .fault import LabFault
from .types import CommandResult, LabNode


_BATCH_RUNNERS = {
    "srl_cli": ContainerLabExecutor.run_srl_cli_batch,
    "vyos_cli": ContainerLabExecutor.run_vyos_cli_batch,
}


class ContainerLabFaultInjector:
    """Apply explicit, reviewed faults to a Containerlab environment."""

    def __init__(self, executor: ContainerLabExecutor, nodes: dict[str, LabNode]):
        self.executor = executor
        self.nodes = nodes

    def inject(self, fault: LabFault) -> list[CommandResult]:
        results: list[CommandResult] = []
        index = 0
        while index < len(fault.commands):
            action = fault.commands[index]
            node = self.nodes.get(action.target)
            if node is None:
                known = ", ".join(sorted(self.nodes))
                reason = f"unknown lab node '{action.target}'. Known nodes: {known}"
                results.append(
                    CommandResult(
                        target=action.target,
                        command=action.command,
                        returncode=127,
                        safe=False,
                        reason=reason,
                        stderr=reason,
                    )
                )
                index += 1
                continue

            # Native CLIs are transactional: consecutive commands for the same
            # node go in as one batch, so a fault is applied atomically rather
            # than as a sequence of half-applied changes.
            batch_runner = _BATCH_RUNNERS.get(action.mode)
            if batch_runner is not None:
                commands = [action.command]
                index += 1
                while index < len(fault.commands):
                    next_action = fault.commands[index]
                    if (
                        next_action.mode != action.mode
                        or next_action.target != action.target
                    ):
                        break
                    commands.append(next_action.command)
                    index += 1
                results.append(batch_runner(self.executor, node, commands))
                continue

            results.append(self.executor.run_shell(node, action.command))
            index += 1
        return results
