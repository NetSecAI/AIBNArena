"""The experiment files the form offers, read from `benchmarks/configs/experiments`.

One file pins a topology, its containerlab testbed and the domain its scenarios
are compiled from, so choosing one is how the form chooses a topology. What stays
free inside that choice -- the scenario, its method, the seed, the budget -- is
what `benchmarks/run.py` accepts as an override.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from webui.config import REPOSITORY

EXPERIMENTS_DIR = "benchmarks/configs/experiments"

#: Domains whose scenarios this interface can launch. `security` is left out on
#: purpose: no experiment file pins it to a testbed, and its scenarios carry a
#: task mode (exploit/detect/correct/prevent) where the others carry a method.
LAUNCHABLE_DOMAINS = frozenset({"connectivity", "dhcp_dns", "filtering", "qos"})


@dataclass(frozen=True)
class ExperimentPreset:
    key: str
    experiment_id: str
    config_path: str
    domain: str
    default_scenario_id: str
    topology: str
    topology_descriptor: str
    testbed_id: str
    testbed_config: str
    #: The healthy state the subject's own ANI is wired to, read from the testbed.
    reference_state: str
    cleanup: str
    execution_budget_seconds: float
    seed: int
    fault_applicable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "experiment_id": self.experiment_id,
            "config_path": self.config_path,
            "domain": self.domain,
            "default_scenario_id": self.default_scenario_id,
            "topology": self.topology,
            "testbed_id": self.testbed_id,
            # Two files can share a topology and a testbed and still differ here:
            # the qos greenfield and brownfield plates start from different states.
            "reference_state": Path(self.reference_state).stem,
            "cleanup": self.cleanup,
            "execution_budget_seconds": self.execution_budget_seconds,
            "seed": self.seed,
            "fault_applicable": self.fault_applicable,
        }


def _read_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _preset(path: Path) -> ExperimentPreset:
    suite = _read_toml(path)
    testbed_config = str(suite["testbed_config"])
    testbed = _read_toml(REPOSITORY / testbed_config)
    scenario_id = str(suite["scenario_id"])
    descriptor = str(suite["topology_descriptor"])
    return ExperimentPreset(
        key=path.stem,
        experiment_id=str(suite["experiment_id"]),
        config_path=str(path.relative_to(REPOSITORY)),
        domain=scenario_id.split(".", 1)[0],
        default_scenario_id=scenario_id,
        topology=Path(descriptor).stem,
        topology_descriptor=descriptor,
        testbed_id=str(testbed["id"]),
        testbed_config=testbed_config,
        reference_state=str(testbed["reference_state"]),
        cleanup=str(suite.get("cleanup", "restore")),
        execution_budget_seconds=float(suite.get("execution_budget_seconds", 300)),
        seed=int(suite.get("seed", 0)),
        fault_applicable=bool(suite.get("fault_applicable", True)),
    )


def presets() -> tuple[ExperimentPreset, ...]:
    """Every launchable experiment file, by name."""
    directory = REPOSITORY / EXPERIMENTS_DIR
    found = (_preset(path) for path in sorted(directory.glob("*.toml")))
    return tuple(item for item in found if item.domain in LAUNCHABLE_DOMAINS)


def preset(key: str) -> ExperimentPreset:
    for item in presets():
        if item.key == key:
            return item
    raise ValueError(f"unknown experiment: {key}")
