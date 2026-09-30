"""Which scenarios and methods a chosen experiment can actually run.

Two files decide it, and both are read rather than copied here: the scenario YAML
declares its methods, and `scenarios/topology_applicability.json` declares which
topologies the scenario binds against. A scenario absent from that file is
applicable everywhere, which is what the file itself states.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from webui.config import REPOSITORY

SCENARIOS_ROOT = "scenarios"
APPLICABILITY = "scenarios/topology_applicability.json"


@dataclass(frozen=True)
class Method:
    id: int
    name: str

    @property
    def suffix(self) -> str:
        return f"m{self.id}"


@dataclass(frozen=True)
class Task:
    """One wording of the scenario's task: the same fault put to the subject in
    more or less precise words (low, medium, high). The base wording keeps the
    bare instance id; the others carry their name in it, as the compiler emits
    them. A scenario with a single wording has one Task with no variant."""
    variant: str | None
    wording: str
    base: bool


@dataclass(frozen=True)
class Scenario:
    domain: str
    name: str
    path: str
    methods: tuple[Method, ...]
    topologies: frozenset[str] | None
    tasks: tuple[Task, ...] = ()

    def applies_to(self, topology: str) -> bool:
        return self.topologies is None or topology in self.topologies

    def scenario_id(self, method: Method, task: Task | None = None) -> str:
        wording = f"{task.variant}." if task is not None and not task.base else ""
        return f"{self.domain}.{self.name}.{wording}{method.suffix}"

    def as_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "name": self.name,
            "methods": [
                {"id": method.id, "name": method.name,
                 "scenario_id": self.scenario_id(method)}
                for method in self.methods
            ],
            "tasks": [
                {"variant": task.variant, "wording": task.wording, "base": task.base}
                for task in self.tasks
            ],
        }


def _applicability() -> dict[str, list[str]]:
    document = json.loads((REPOSITORY / APPLICABILITY).read_text(encoding="utf-8"))
    return {
        key: [str(name) for name in (value.get("topologies") or [])]
        for key, value in (document.get("scenarios") or {}).items()
    }


def _scenario(path: Path, domain: str, applicability: dict[str, list[str]]) -> Scenario | None:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    methods = document.get("methods")
    if not methods:
        # A scenario with no methods carries another variant axis -- the security
        # family's task_mode -- which this interface does not launch yet.
        return None
    declared = applicability.get(str(path.relative_to(REPOSITORY / SCENARIOS_ROOT)))
    task, base = document.get("task"), document.get("base_variant")
    tasks = (
        tuple(Task(variant=str(name), wording=" ".join(str(text).split()),
                   base=str(name) == str(base))
              for name, text in task.items())
        if isinstance(task, dict)
        else (Task(variant=None, wording=" ".join(str(task or "").split()), base=True),)
    )
    return Scenario(
        domain=domain,
        name=str(document.get("scenario") or path.stem),
        path=str(path.relative_to(REPOSITORY)),
        methods=tuple(
            Method(id=int(method["id"]), name=str(method.get("name") or f"m{method['id']}"))
            for method in methods
        ),
        topologies=None if declared is None else frozenset(declared),
        tasks=tasks,
    )


def scenarios(domain: str) -> tuple[Scenario, ...]:
    """Every scenario of one domain that carries methods, by name."""
    directory = REPOSITORY / SCENARIOS_ROOT / domain
    applicability = _applicability()
    found = (_scenario(path, domain, applicability)
             for path in sorted(directory.glob("*.yaml")))
    return tuple(item for item in found if item is not None)


def for_experiment(domain: str, topology: str) -> tuple[Scenario, ...]:
    """The scenarios an experiment on this topology may be pointed at."""
    return tuple(item for item in scenarios(domain) if item.applies_to(topology))
