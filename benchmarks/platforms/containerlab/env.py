from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .executor import ContainerLabExecutor
from .fault import FaultCatalog, LabFault
from .injector import ContainerLabFaultInjector
from .observer import ContainerLabObserver
from .state import LabState, command_result_for_unknown_target
from .types import CommandResult, LabNode, Observation


@dataclass(frozen=True)
class ContainerLabEnvConfig:
    """Configuration for the MVP clos01 ContainerLab adapter."""

    lab_name: str = "clos01"
    topology_path: Path = Path("benchmarks/testbeds/containerlab/clos01.clab.yml")
    healthy_state_path: Path = Path("benchmarks/testbeds/containerlab/states/healthy.json")
    fault_catalog_path: Path | None = Path("benchmarks/testbeds/containerlab/faults/mvp.json")
    nodes: dict[str, LabNode] = field(default_factory=lambda: {
        "spine": LabNode("spine", "nokia_srlinux", "clab-clos01-spine"),
        "leaf1": LabNode("leaf1", "nokia_srlinux", "clab-clos01-leaf1"),
        "leaf2": LabNode("leaf2", "nokia_srlinux", "clab-clos01-leaf2"),
        "client1": LabNode("client1", "linux", "clab-clos01-client1"),
        "client2": LabNode("client2", "linux", "clab-clos01-client2"),
    })
    # (source, destination, destination_ip) and optionally a hostname to ping
    # instead of the address -- see TopologyIndex.connectivity_checks().
    default_connectivity_checks: list[tuple[str, ...]] = field(default_factory=lambda: [
        ("client1", "client2", "192.168.20.10"),
        ("client2", "client1", "192.168.10.10"),
    ])


class ContainerLabEnv:
    """Containerlab environment facade used by the shared platform.

    It exposes deployment, reference state, observation, injection, execution,
    and verification without depending on a benchmark domain or agent framework.
    """
    def __init__(self, config: ContainerLabEnvConfig | None = None):
        self.config = config or ContainerLabEnvConfig()
        self.executor = ContainerLabExecutor(self.config.topology_path)
        self.observer = ContainerLabObserver(self.executor, self.config.lab_name, self.config.nodes)
        self.injector = ContainerLabFaultInjector(self.executor, self.config.nodes)

    def start(self) -> CommandResult:
        completed = self.executor.deploy()
        return CommandResult(
            target=self.config.lab_name,
            command=f"containerlab deploy -t {self.config.topology_path}",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def stop(self) -> CommandResult:
        completed = self.executor.destroy()
        return CommandResult(
            target=self.config.lab_name,
            command=f"containerlab destroy -t {self.config.topology_path} --cleanup",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def reset(self) -> list[CommandResult]:
        return [self.stop(), self.start()]

    def apply_healthy_state(self) -> list[CommandResult]:
        return self.apply_state(LabState.from_file(self.config.healthy_state_path))

    def apply_state(self, state: LabState) -> list[CommandResult]:
        results: list[CommandResult] = []

        for command in state.shell_commands():
            node = self.config.nodes.get(command.target)
            if node is None:
                results.append(command_result_for_unknown_target(command.target, command.command, self.config.nodes))
                continue
            results.append(self.executor.run_shell(node, command.command))

        for target, commands in state.grouped_srl_commands().items():
            node = self.config.nodes.get(target)
            if node is None:
                results.append(command_result_for_unknown_target(target, "\n".join(commands), self.config.nodes))
                continue
            results.append(self.executor.run_srl_cli_batch(node, commands))

        for target, commands in state.grouped_vyos_commands().items():
            node = self.config.nodes.get(target)
            if node is None:
                results.append(command_result_for_unknown_target(target, "\n".join(commands), self.config.nodes))
                continue
            results.append(self.executor.run_vyos_cli_batch(node, commands))

        return results

    def inject_fault(self, fault_id: str) -> list[CommandResult]:
        if self.config.fault_catalog_path is None:
            raise ValueError("inject_fault requires a fixed fault_catalog_path")
        fault = FaultCatalog.from_file(self.config.fault_catalog_path).get(fault_id)
        return self.injector.inject(fault)

    def inject(self, fault: LabFault) -> list[CommandResult]:
        return self.injector.inject(fault)

    def observe(self) -> Observation:
        return self.observer.observe(self.config.default_connectivity_checks)

    def execute(self, machine: str, command: str) -> CommandResult:
        node = self._get_node(machine)
        if node.kind == "nokia_srlinux":
            return self.executor.run_srl_cli_batch(node, [command])
        if node.kind == "vyos":
            return self.executor.run_vyos_cli_batch(node, [command])
        return self.executor.run_linux_config(node, command)

    def verify(self) -> bool:
        return self.observation_is_healthy(self.observe())

    def wait_until_healthy(self, timeout_seconds: float = 15.0, interval_seconds: float = 2.0) -> tuple[bool, Observation]:
        deadline = time.monotonic() + timeout_seconds
        last_observation = self.observe()
        while True:
            if self.observation_is_healthy(last_observation):
                return True, last_observation
            if time.monotonic() >= deadline:
                return False, last_observation
            time.sleep(interval_seconds)
            last_observation = self.observe()

    @staticmethod
    def observation_is_healthy(observation: Observation) -> bool:
        return all(check.success for check in observation.connectivity)

    def _get_node(self, machine: str) -> LabNode:
        normalized = machine.removeprefix(f"clab-{self.config.lab_name}-")
        if normalized not in self.config.nodes:
            known = ", ".join(sorted(self.config.nodes))
            raise KeyError(f"unknown lab node '{machine}'. Known nodes: {known}")
        return self.config.nodes[normalized]
