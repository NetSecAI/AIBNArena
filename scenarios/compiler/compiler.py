from __future__ import annotations

import copy
import random
import re
from ipaddress import ip_address, ip_interface, ip_network
from pathlib import Path
from typing import Any

from .loader import (
    COMBINED_DOMAIN,
    combination_blocker,
    discover_scenarios,
    load_topology_applicability,
    load_yaml,
    scenario_applies_to_topology,
    validate_scenario,
)
from .topology import InterfaceRef, TopologyIndex

_TEMPLATE = re.compile(r"{{\s*([A-Za-z0-9_]+)\s*}}")

#: Layer-2 faults of the vlan family, bound by `_bind_vlan_method` to an access switch.
_VLAN_OPERATIONS = frozenset({
    "set_access_port_vlan",
    "set_switchport_mode",
    "set_vlan_presence",
    "set_trunk_allowed_vlan",
    "set_trunk_native_vlan",
})

#: Change requests: methods that name what the subject must build rather than what is
#: broken. The materializer injects nothing for them (`change_request` on the fault),
#: and their bindings carry the paths the scenario's own oracles judge the change on.
CHANGE_REQUEST_OPERATIONS = frozenset({"provision_isolated_vlan", "provision_service_publication"})

#: How a service host's role reads in a task: "the application server app1".
_SERVICE_ROLE_WORDS = {
    "application_server": "application server",
    "dmz_server": "DMZ server",
    "dns_resolver": "DNS resolver",
    "dns_authoritative": "authoritative DNS server",
}

#: Families whose methods all share one binding: the context is drawn once per family
#: and only the fault parameters vary per method.
_SHARED_BINDING_KINDS = {
    "set_subnet_traffic_policy": "subnet",
    "set_link_netem": "netem",
    "set_traffic_shaping_policy": "shaping",
    "start_background_traffic": "contention",
}

#: Draws a combined method may take to find components whose blast radii are disjoint.
#: Each draw is cheap (no I/O), and the generator only keeps the method tuples that
#: bound on every seed it checked, so a miss here is a tuple that is rarely bindable.
COMBINATION_ATTEMPTS = 256


def _shared_binding_kind(methods: list[dict[str, Any]]) -> str | None:
    names = {str((method.get("operation") or {}).get("name")) for method in methods}
    if len(names) != 1:
        return None
    return _SHARED_BINDING_KINDS.get(names.pop())


def _term(value: Any) -> str:
    """A binding as it should read inside a sentence.

    A list binding (the endpoints a fault affects, say) would otherwise reach the
    model as a Python repr, which is not how an operator states a symptom.
    """
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return f"{', '.join(items[:-1])} and {items[-1]}"
    return str(value)


def _render(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _TEMPLATE.sub(
            lambda match: _term(bindings[match.group(1)])
            if match.group(1) in bindings else match.group(0),
            value)
    if isinstance(value, list):
        return [_render(item, bindings) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, bindings) for key, item in value.items()}
    return value


def _contains_template(value: Any) -> bool:
    if isinstance(value, str):
        return _TEMPLATE.search(value) is not None
    if isinstance(value, list):
        return any(_contains_template(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_template(item) for item in value.values())
    return False


def _task_variants(
    task_spec: Any, base_variant: Any = None,
) -> list[tuple[str | None, str | None, Any]]:
    """The task templates a scenario declares, as (id part, variant name, template).

    One string is the scenario's only wording and carries no name. A mapping is a
    named set, which is how intent precision is varied without touching the fault
    or the oracles. The scenario names one of those wordings as its base, and that
    one keeps the instance id it had before the set existed, so adding a wording
    does not rename the plates a campaign already ran. Its name is still recorded.
    """
    if not isinstance(task_spec, dict):
        return [(None, None, task_spec)]
    if not task_spec:
        raise ValueError("a task mapping must declare at least one wording")
    base = None if base_variant is None else str(base_variant)
    if base is not None and base not in {str(name) for name in task_spec}:
        raise ValueError(
            f"base_variant {base!r} is not one of the declared wordings "
            f"{sorted(str(name) for name in task_spec)}")
    return [
        (None if str(name) == base else str(name), str(name), template)
        for name, template in task_spec.items()
    ]


class ScenarioCompiler:
    def __init__(self, topology_document: dict[str, Any], seed: int = 0):
        self.topology_document = topology_document
        self.topology = TopologyIndex(topology_document)
        self.rng = random.Random(seed)
        self.seed = seed
        # Component scenarios of the combinations, by path: one compiler reads each once.
        self._component_cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_topology_file(cls, path: str | Path, seed: int = 0) -> "ScenarioCompiler":
        return cls(load_yaml(path), seed=seed)

    def compile_scenarios(
        self,
        scenarios_root: str | Path,
        *,
        domains: set[str] | None = None,
        expand_methods: bool = True,
        security_modes: set[str] | None = None,
        samples_per_scenario: int = 1,
    ) -> dict[str, Any]:
        if samples_per_scenario < 1:
            raise ValueError("samples_per_scenario must be >= 1")
        tasks: list[dict[str, Any]] = []
        applicability = load_topology_applicability(scenarios_root)
        topology_id = str(self.topology.topology.get("id", ""))
        for domain, path, scenario in discover_scenarios(scenarios_root):
            if domains and domain not in domains:
                continue
            # The file's path in the catalog, as the applicability map keys it:
            # `<domain>/<file>`, or `combined/<n>_scenarios/<file>`.
            relative = Path(path).relative_to(Path(scenarios_root)).as_posix()
            if not scenario_applies_to_topology(applicability, relative, topology_id):
                continue
            for sample_index in range(1, samples_per_scenario + 1):
                compiled = self.compile_scenario(
                    domain,
                    scenario,
                    source_path=path,
                    expand_methods=expand_methods,
                    security_modes=security_modes,
                    scenarios_root=scenarios_root,
                    sample_index=sample_index,
                )
                if samples_per_scenario > 1:
                    for item in compiled:
                        item["id"] = f"{item['id']}.i{sample_index:03d}"
                        item["private"]["sample_index"] = sample_index
                tasks.extend(compiled)
        unresolved = [
            task["id"]
            for task in tasks
            if _contains_template(task)
        ]
        if unresolved:
            raise ValueError(f"compiled scenarios contain unresolved templates: {unresolved}")
        return {
            "schema_version": "0.1",
            "generator": {
                "name": "scenarios.compiler.ScenarioCompiler",
                "seed": self.seed,
                "samples_per_scenario": samples_per_scenario,
                "note": "Evaluator/private fields must not be exposed verbatim to the SUT.",
            },
            "topology": copy.deepcopy(self.topology_document["topology"]),
            "tasks": tasks,
        }

    def compile_scenario(
        self,
        domain: str,
        scenario: dict[str, Any],
        *,
        source_path: Path | None = None,
        expand_methods: bool = True,
        security_modes: set[str] | None = None,
        scenarios_root: str | Path | None = None,
        sample_index: int = 1,
    ) -> list[dict[str, Any]]:
        if domain == COMBINED_DOMAIN or scenario.get("combination"):
            root = scenarios_root
            if root is None and source_path is not None:
                root = Path(source_path).resolve().parent.parent
            if root is None:
                raise ValueError(
                    f"{scenario.get('scenario')}: a combined scenario needs the catalog root "
                    "its components are read from")
            return self._compile_combination(
                scenario,
                scenarios_root=root,
                sample_index=sample_index,
                expand_methods=expand_methods,
            )
        if scenario.get("methods"):
            return self._compile_method_scenario(
                domain,
                scenario,
                expand_methods=expand_methods,
            )

        strategy = scenario.get("extra_info", {}).get("binding", {}).get("strategy")
        if not strategy:
            raise ValueError(f"{scenario['scenario']}: missing extra_info.binding.strategy")
        bindings, topology_patch = self._bind(strategy, scenario)
        common = {
            "domain": domain,
            "version": scenario["version"],
            "oracles": copy.deepcopy(scenario["oracles"]),
            "private": {
                "scenario_definition": scenario["scenario"],
                "bindings": bindings,
                "topology_patch": topology_patch,
                "metadata": {
                    "source_file": str(source_path) if source_path else None,
                    "security_properties": scenario.get("security_properties"),
                    "stride": scenario.get("stride"),
                    "network_layers": scenario.get("extra_info", {}).get("network_layers"),
                },
            },
        }

        if domain == "security":
            compiled = []
            for mode, task_template in scenario["task_mode"].items():
                if security_modes and mode not in security_modes:
                    continue
                item = copy.deepcopy(common)
                item["id"] = f"security.{scenario['scenario']}.{mode}"
                item["public"] = self._public_task(scenario, task_template, bindings)
                item["private"]["task_mode"] = mode
                item["private"]["evaluator"] = {
                    "private_scenario_info": _render(scenario.get("extra_info", {}), bindings)
                }
                compiled.append(item)
            return compiled

        variants = _task_variants(scenario["task"], scenario.get("base_variant"))
        methods = scenario.get("extra_info", {}).get("benchmark", {}).get("methods")
        compiled = []
        for id_part, variant, task_template in variants:
            selected_methods = methods if (methods and expand_methods) else ([self.rng.choice(methods)] if methods else [None])
            for method in selected_methods:
                item = copy.deepcopy(common)
                suffix = []
                if id_part:
                    suffix.append(id_part)
                if variant:
                    item["private"]["task_variant"] = variant
                if method:
                    suffix.append(f"m{method['id']}")
                item["id"] = ".".join([domain, scenario["scenario"], *suffix])
                item["public"] = self._public_task(scenario, task_template, bindings)
                evaluator: dict[str, Any] = {
                    "private_scenario_info": _render(scenario.get("extra_info", {}), bindings)
                }
                if method:
                    evaluator["selected_method"] = _render(method, bindings)
                item["private"]["evaluator"] = evaluator
                compiled.append(item)
        return compiled

    def _compile_method_scenario(
        self,
        domain: str,
        scenario: dict[str, Any],
        *,
        expand_methods: bool,
    ) -> list[dict[str, Any]]:
        methods = scenario["methods"]
        # A family whose methods all break the same object -- one subnet guard, one
        # shaping policy on the same WAN edge -- resolves that context once, and only
        # the fault parameters vary per method.
        kind = _shared_binding_kind(methods)
        shared_bindings = self._bind_shared_context(kind, methods) if kind else None
        selected = methods if expand_methods else [self.rng.choice(methods)]
        compiled = []
        for method in selected:
            bindings = (
                copy.deepcopy(shared_bindings)
                if shared_bindings is not None
                else self._bind_method(method)
            )
            bindings = self._add_method_bindings(kind, method, bindings)
            # A scenario may state one task or a named set of them, which is how the
            # precision of the intent is varied: the same fault is put to the subject
            # in more or less specific words. Each variant is its own instance, so the
            # id says which wording was measured and the oracles stay untouched.
            for id_part, variant, task_template in _task_variants(
                    scenario["task"], scenario.get("base_variant")):
                suffix = f"{id_part}." if id_part else ""
                item = {
                    "id": f"{domain}.{scenario['scenario']}.{suffix}m{method['id']}",
                    "domain": domain,
                    "version": scenario["version"],
                    "oracles": copy.deepcopy(scenario["oracles"]),
                    "public": self._public_task(scenario, task_template, bindings),
                    "private": {
                        "scenario_definition": scenario["scenario"],
                        "bindings": bindings,
                        "evaluator": {"selected_method": copy.deepcopy(method)},
                    },
                }
                if variant:
                    item["private"]["task_variant"] = variant
                compiled.append(item)
        return compiled

    # --- combined scenarios ------------------------------------------------------------

    def _combination_components(
        self, scenario: dict[str, Any], scenarios_root: str | Path,
    ) -> list[dict[str, Any]]:
        """The component scenarios, read from the catalog at the version the combination pins."""
        label = scenario.get("scenario", "<combination>")
        components = []
        for spec in scenario["combination"]["components"]:
            source = str(spec["source"])
            domain = source.split("/", 1)[0]
            path = Path(scenarios_root) / source
            if not path.exists():
                raise ValueError(f"{label}: component {source} is not in the catalog at {scenarios_root}")
            cached = self._component_cache.get(str(path.resolve()))
            if cached is None:
                cached = load_yaml(path)
                validate_scenario(domain, cached, path)
                self._component_cache[str(path.resolve())] = cached
            document = copy.deepcopy(cached)
            if str(document["scenario"]) != str(spec["scenario"]):
                raise ValueError(
                    f"{label}: {source} defines {document['scenario']!r}, not {spec['scenario']!r}")
            if str(document["version"]) != str(spec["version"]):
                raise ValueError(
                    f"{label}: combined {source} at version {spec['version']}, which is now at "
                    f"{document['version']}; regenerate the combinations "
                    "(scenarios/generator/combine_scenarios.py) rather than run a combination "
                    "whose components moved under it")
            blocker = combination_blocker(domain, document)
            if blocker:
                raise ValueError(f"{label}: {source} cannot be combined: {blocker}")
            components.append({"source": source, "domain": domain, "document": document})
        return components

    @staticmethod
    def _combination_variants(
        scenario: dict[str, Any], components: list[dict[str, Any]],
    ) -> list[tuple[str | None, str | None]]:
        """The wordings every component declares, as (id part, variant name), base first-class.

        The combined task is stated at each precision its components share; the base
        variant keeps the bare instance id, as it does for a single scenario.
        """
        mapped = [c["document"]["task"] for c in components if isinstance(c["document"]["task"], dict)]
        if not mapped:
            return [(None, None)]
        names = [str(name) for name in mapped[0] if all(name in other for other in mapped[1:])]
        base = str(scenario["base_variant"])
        if base not in names:
            raise ValueError(
                f"{scenario['scenario']}: base_variant {base!r} is not a wording every "
                f"component declares ({names})")
        stated = sorted(set(scenario.get("task") or {}) - set(names))
        if stated:
            raise ValueError(
                f"{scenario['scenario']}: states the wordings {stated}, which not every "
                f"component declares ({names})")
        return [(None if name == base else name, name) for name in names]

    @staticmethod
    def _combination_intent(
        scenario: dict[str, Any],
        components: list[dict[str, Any]],
        parts: list[dict[str, Any]],
        bindings: dict[str, Any],
        variant: str | None,
    ) -> str:
        """The combined task at one precision.

        A wording the combined file states is used as written (the generator states
        `low`: the vaguest wording is one generic sentence, whatever is combined). Any
        other is the components' own wordings at that precision, each rendered with its
        own draw and concatenated in component order. A wording two components share
        word for word is said once: repeated, it would add nothing but its length. The
        number of faults is not announced.
        """
        stated = scenario.get("task") or {}
        if variant in stated:
            return " ".join(str(_render(stated[variant], bindings)).split())
        wordings: list[str] = []
        for component, part in zip(components, parts):
            task = component["document"]["task"]
            template = str(task[variant] if isinstance(task, dict) else task)
            text = " ".join(str(_render(template, part["bindings"])).split())
            if text not in wordings:
                wordings.append(text)
        return " ".join(wordings)

    def _bind_combination(
        self,
        scenario: dict[str, Any],
        components: list[dict[str, Any]],
        method: dict[str, Any],
        sample_index: int,
    ) -> list[dict[str, Any]]:
        """Bind every component's method so that no two blast radii meet.

        Each draw takes its own stream, seeded by the suite seed, the combination, the
        method, the sample and the attempt: a combined task binds the same whether it is
        compiled alone or in a whole suite, and compiling it moves no other scenario's
        draw. A draw where two components cut a common endpoint is discarded -- the
        faults would then mask each other, one oracle could pass on the other's symptom,
        and a repair of one could not be told from a repair of both.
        """
        label = f"{scenario['scenario']} m{method['id']}"
        chosen = []
        for component, pick in zip(components, method["components"]):
            by_id = {int(m["id"]): m for m in component["document"]["methods"]}
            if int(pick) not in by_id:
                raise ValueError(
                    f"{label}: {component['source']} has no method {pick} (it declares {sorted(by_id)})")
            chosen.append(by_id[int(pick)])
        topology_id = str(self.topology.topology.get("id", ""))
        # Keyed by the component methods, not by the method's id: the generator checks a
        # tuple before it knows where in the file it will sit, and the draw it checked
        # must be the draw the file compiles to.
        picks = "-".join(str(int(pick)) for pick in method["components"])
        saved = self.rng
        last_error: Exception | None = None
        try:
            for attempt in range(COMBINATION_ATTEMPTS):
                self.rng = random.Random(
                    f"{self.seed}:{scenario['scenario']}:{picks}:i{sample_index}:a{attempt}")
                parts = []
                try:
                    for index, (component, component_method) in enumerate(zip(components, chosen), start=1):
                        bindings = self._bind_family_method(component["document"], component_method)
                        parts.append({
                            "id": f"c{index}",
                            "domain": component["domain"],
                            "scenario_definition": str(component["document"]["scenario"]),
                            "source": component["source"],
                            "version": str(component["document"]["version"]),
                            "oracles": copy.deepcopy(component["document"]["oracles"]),
                            "method": copy.deepcopy(component_method),
                            "bindings": bindings,
                        })
                except ValueError as exc:
                    last_error = exc
                    continue
                radii = [set(str(n) for n in part["bindings"].get("affected_nodes") or []) for part in parts]
                if all(radii) and sum(map(len, radii)) == len(set().union(*radii)):
                    return parts
        finally:
            self.rng = saved
        reason = f" (last binding error: {last_error})" if last_error else ""
        raise ValueError(
            f"{label}: no draw in {COMBINATION_ATTEMPTS} gave the components disjoint, non-empty "
            f"blast radii on {topology_id}{reason}")

    def _compile_combination(
        self,
        scenario: dict[str, Any],
        *,
        scenarios_root: str | Path,
        sample_index: int,
        expand_methods: bool,
    ) -> list[dict[str, Any]]:
        """Compile a combined scenario: several faults, from several families, in one task.

        Each component keeps the bindings and the method its own family would give it;
        the task carries them all under `bindings.components`, which is also what the
        combined oracles compose their groups from and what the materializer renders.
        The top-level blast radius is the union of the components'.
        """
        components = self._combination_components(scenario, scenarios_root)
        variants = self._combination_variants(scenario, components)
        methods = scenario["methods"]
        selected = methods if expand_methods else [self.rng.choice(methods)]
        compiled = []
        for method in selected:
            parts = self._bind_combination(scenario, components, method, sample_index)
            bindings = {
                "affected_nodes": sorted({
                    str(node) for part in parts for node in part["bindings"]["affected_nodes"]
                }),
                "components": parts,
            }
            selected_method = {
                "id": method["id"],
                "name": method["name"],
                "components": [
                    {
                        "id": part["id"],
                        "domain": part["domain"],
                        "scenario_definition": part["scenario_definition"],
                        "method": copy.deepcopy(part["method"]),
                    }
                    for part in parts
                ],
            }
            for id_part, variant in variants:
                suffix = f"{id_part}." if id_part else ""
                public = {"intent": self._combination_intent(scenario, components, parts, bindings, variant)}
                if scenario.get("success_criteria") is not None:
                    public["success_criteria"] = _render(scenario["success_criteria"], bindings)
                item = {
                    "id": f"{COMBINED_DOMAIN}.{scenario['scenario']}.{suffix}m{method['id']}",
                    "domain": COMBINED_DOMAIN,
                    "version": scenario["version"],
                    "oracles": copy.deepcopy(scenario["oracles"]),
                    "public": public,
                    "private": {
                        "scenario_definition": scenario["scenario"],
                        "bindings": copy.deepcopy(bindings),
                        "evaluator": {"selected_method": copy.deepcopy(selected_method)},
                    },
                }
                if variant:
                    item["private"]["task_variant"] = variant
                compiled.append(item)
        return compiled

    def _bind_shared_context(self, kind: str, methods: list[dict[str, Any]]) -> dict[str, Any]:
        if kind == "subnet":
            return self._bind_subnet_method(
                methods[0],
                directions={str((m.get("operation") or {}).get("direction")) for m in methods})
        if kind == "netem":
            return self._bind_qos_link_context()
        if kind == "shaping":
            return self._bind_qos_shaping_context()
        if kind == "contention":
            return self._bind_qos_contention_context()
        raise ValueError(f"unknown shared binding kind {kind!r}")

    def _add_method_bindings(
        self, kind: str | None, method: dict[str, Any], bindings: dict[str, Any],
    ) -> dict[str, Any]:
        if kind == "netem":
            return self._add_qos_impairment_bindings(method, bindings)
        if kind == "shaping":
            return self._add_qos_shaping_bindings(method, bindings)
        if kind == "contention":
            return self._add_qos_contention_bindings(method, bindings)
        return bindings

    def _bind_family_method(
        self, scenario: dict[str, Any], method: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind one method the way its own family binds it when compiled alone."""
        kind = _shared_binding_kind(scenario["methods"])
        bindings = (
            self._bind_shared_context(kind, scenario["methods"])
            if kind else self._bind_method(method)
        )
        return self._add_method_bindings(kind, method, bindings)

    @staticmethod
    def _public_task(
        scenario: dict[str, Any], task_template: str, bindings: dict[str, Any]
    ) -> dict[str, Any]:
        public = {"intent": _render(task_template, bindings)}
        criteria = scenario.get("success_criteria")
        if criteria is not None:
            public["success_criteria"] = _render(criteria, bindings)
        return public

    def _bind_method(self, method: dict[str, Any]) -> dict[str, Any]:
        selector = method.get("selector") or {}
        node_selector = selector.get("node")
        if method.get("operation", {}).get("name") == "set_link_netem":
            return self._add_qos_impairment_bindings(
                method, self._bind_qos_link_context()
            )
        if method.get("operation", {}).get("name") == "set_traffic_shaping_policy":
            return self._add_qos_shaping_bindings(
                method, self._bind_qos_shaping_context()
            )
        if method.get("operation", {}).get("name") == "start_background_traffic":
            return self._add_qos_contention_bindings(
                method, self._bind_qos_contention_context()
            )
        if method.get("operation", {}).get("name") in _VLAN_OPERATIONS:
            return self._bind_vlan_method(method)
        if method.get("operation", {}).get("name") == "provision_isolated_vlan":
            return self._bind_isolated_vlan_method(method)
        if method.get("operation", {}).get("name") == "provision_service_publication":
            return self._bind_service_publication_method(method)
        if selector.get("subnet") is not None:
            return self._bind_subnet_method(method)
        if selector.get("route") is not None:
            return self._bind_route_method(method)
        if node_selector == "zone_enforcer":
            return self._bind_zone_policy_method(method)
        if node_selector == "dns_server":
            return self._bind_dns_method(method)
        if node_selector == "dhcp_server":
            return self._bind_dhcp_method(method)
        if selector.get("interface") in {"access", "trunk"}:
            return self._bind_switch_port_method(method)
        if node_selector == "router":
            nodes = self.topology.nodes_with_role("router")
        elif node_selector == "gateway":
            nodes = self.topology.nodes_with_role("gateway")
        elif node_selector == "endpoint":
            nodes = self.topology.endpoint_nodes()
        elif node_selector == "service":
            # A host fault that stays on a server providing one of the network's
            # services (2026-09-23, Hugo). Same
            # blast radius rule as an endpoint, the host itself. The draw is still
            # made over the endpoint pool and folded onto the service hosts
            # afterwards (`_fold_onto_services`), so that the shared random stream
            # consumes exactly what it did before and every other method of the
            # suite keeps the target it had: the plates measured on the old
            # definition stay comparable. A topology without service hosts (clos01,
            # two clients) keeps the endpoint pool.
            nodes = self.topology.endpoint_nodes()
        else:
            raise ValueError(
                f"method {method.get('name')!r} has unsupported node selector "
                f"{node_selector!r}"
            )

        if not nodes:
            raise ValueError(f"method {method.get('name')!r} found no matching node")

        if selector.get("interface") == "data":
            candidates = [
                (node, interface)
                for node in nodes
                for interface in self.topology.interfaces(node)
            ]
            if (method.get("operation") or {}).get("name") in {
                "set_interface_ipv4_presence",
                "set_interface_ipv4_address",
            }:
                # A switched fabric gives a router interfaces with no address of
                # their own -- the tagged trunks. They cannot carry an IPv4
                # fault, so they are not candidates for one.
                candidates = [item for item in candidates if item[1].ipv4]
            if not candidates:
                raise ValueError(f"method {method.get('name')!r} found no interface")
            target, interface = self._fold_onto_services(
                node_selector, candidates, self.rng.choice(candidates), lambda item: item[0])
            if node_selector not in {"endpoint", "service"}:
                target, interface = self._fold_onto_observable(
                    method, candidates, (target, interface),
                    lambda item: self.topology.segment_hosts(item[1].segment))
            affected_nodes = (
                [target]
                if node_selector in {"endpoint", "service"}
                else self.topology.segment_hosts(interface.segment)
            )
            bindings = {
                "target": target,
                "interface": interface.name,
                "segment": interface.segment,
                "affected_nodes": affected_nodes,
            }
            return self._add_ipv4_method_bindings(method, interface, bindings)

        if (method.get("operation") or {}).get("name") == "set_endpoint_resolver":
            # Only nodes that actually resolve through a resolver can carry this
            # fault. Nameservers are already out of `nodes` -- they hold the
            # `service` role -- but a topology may also declare an endpoint with
            # no resolver of its own.
            nodes = [node for node in nodes if self.topology.resolver_for(node)]
            if not nodes:
                raise ValueError(
                    f"method {method.get('name')!r} found no endpoint with a resolver"
                )

        target = self._fold_onto_services(
            node_selector, nodes, self.rng.choice(nodes), lambda item: item)
        interfaces = self.topology.interfaces(target)
        bindings: dict[str, Any] = {
            "target": target,
            "affected_nodes": (
                [target]
                if node_selector in {"endpoint", "service"}
                else sorted({
                    host
                    for interface in interfaces
                    for host in self.topology.segment_hosts(interface.segment)
                })
            ),
        }
        if selector.get("interfaces") == "data":
            if (method.get("operation") or {}).get("name") == "set_forwarded_traffic_policy":
                # An IPv4 ACL only attaches to an interface that does IPv4. On a
                # switched fabric the router also owns tagged trunks, which have
                # no subinterface to bind to; keeping them makes the whole
                # candidate transaction fail, so the fault silently does nothing.
                interfaces = [interface for interface in interfaces if interface.ipv4]
            if not interfaces:
                raise ValueError(f"method {method.get('name')!r} found no interfaces")
            bindings["interfaces"] = [interface.name for interface in interfaces]
            bindings["segments"] = [interface.segment for interface in interfaces]
        elif selector.get("interfaces") is not None:
            raise ValueError(
                f"method {method.get('name')!r} has unsupported interfaces selector"
            )
        if (method.get("operation") or {}).get("name") == "set_endpoint_resolver":
            resolver = self.topology.resolver_for(str(bindings["target"]))
            if not resolver:
                raise ValueError(
                    f"method {method.get('name')!r} selected an endpoint with no resolver"
                )
            bindings["healthy_resolver"] = resolver
            bindings["fault_resolver"] = self.topology.unused_address_in(resolver)
            return bindings
        return self._add_endpoint_route_bindings(method, bindings)

    def _fold_onto_services(self, node_selector, candidates, picked, node_of):
        """For `node: service`, map a draw made over the endpoint pool onto the
        service hosts, keeping the random stream identical to an endpoint draw.

        The candidate list is the endpoint pool's, so `rng.choice` consumed the
        same bits it always did; the picked slot's index, taken modulo the number
        of service candidates, names the service candidate. Every seed therefore
        reaches every service host, and no other method's draw moves.
        """
        if node_selector != "service":
            return picked
        services = set(self.topology.service_nodes())
        service_candidates = [item for item in candidates if node_of(item) in services]
        if not service_candidates:
            return picked
        return service_candidates[candidates.index(picked) % len(service_candidates)]

    def _fold_onto_observable(self, method, candidates, picked, affected_of, *, observed=None):
        """Map a draw whose blast radius no required path touches onto one it does.

        A router interface on a link between two routers, or on a segment whose
        only host is a resolver, has no evaluated endpoint beside it: the
        expected-degradation oracle then selects no required path, and the
        episode stops before the subject is called. Up to a third of the seeds of
        some topologies drew such an interface. As `_fold_onto_services` does, the
        draw stays over the full list -- `rng.choice` consumed the bits it always
        did, so no other method's target moves -- and only a draw that would
        have been voided is folded, by its index modulo the observable candidates.

        ``observed`` narrows which nodes count: by default every node a required
        flow names. A fault that only stops some sessions passes the node sets the
        blast radius must meet, all of them -- the flows' destinations for one that
        stops the sessions opened toward its target, their sources for one that
        stops those its target opens.
        """
        wanted = observed if observed is not None else [{
            str(requirement[key])
            for requirement in self.topology.topology.get("connectivity_requirements", [])
            for key in ("source", "destination")
        }]

        def observable(item) -> bool:
            affected = set(affected_of(item))
            return all(nodes & affected for nodes in wanted)

        if observable(picked):
            return picked
        pool = [item for item in candidates if observable(item)]
        if not pool:
            raise ValueError(
                f"method {method.get('name')!r} found no interface whose blast radius "
                "a required path observes"
            )
        return pool[candidates.index(picked) % len(pool)]

    def _required_destinations(self) -> set[str]:
        """The nodes some required flow opens a session toward."""
        destinations = set()
        for requirement in self.topology.topology.get("connectivity_requirements", []):
            destinations.add(str(requirement["destination"]))
            if requirement.get("bidirectional"):
                destinations.add(str(requirement["source"]))
        return destinations

    def _required_sources(self) -> set[str]:
        """The nodes some required flow opens a session from."""
        sources = set()
        for requirement in self.topology.topology.get("connectivity_requirements", []):
            sources.add(str(requirement["source"]))
            if requirement.get("bidirectional"):
                sources.add(str(requirement["destination"]))
        return sources

    def _bind_zone_policy_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a filtering fault to one zone pair the network is meant to allow.

        A zone pair absent from `zone_policies` has no ruleset and is denied by
        the zone model itself; those pairs are the design working as intended and
        there is nothing to break. Only a declared `accept` pair carries traffic,
        and only a pair some required flow actually crosses can be observed --
        binding anywhere else would compile an expectation no oracle can see.
        """
        needs_interface = (method.get("selector") or {}).get("interface") == "zoned"
        enforcers = set(self.topology.zone_enforcers())
        if not enforcers:
            raise ValueError(
                f"method {method.get('name')!r} found no node applying a zone policy"
            )

        evaluated = set(self.topology.endpoint_nodes())
        zones = {node: self.topology.node_zone(node) for node in evaluated}
        peers = self._required_peers()

        candidates: list[tuple[dict[str, Any], list[str]]] = []
        for policy in self.topology.zone_policies():
            if policy.get("action") != "accept":
                continue
            node = str(policy.get("node") or (sorted(enforcers)[0] if len(enforcers) == 1 else ""))
            if node not in enforcers:
                continue
            source_zone, target_zone = str(policy["from"]), str(policy["to"])
            in_source = [n for n in sorted(evaluated) if zones.get(n) == source_zone]
            in_target = {n for n in evaluated if zones.get(n) == target_zone}
            if in_target:
                # Endpoints on both sides: the blast radius is exactly the ones
                # whose required flow crosses this pair.
                affected = [n for n in in_source if peers.get(n, set()) & in_target]
            else:
                # A transit zone holds no endpoint of its own -- everything the
                # source zone sends outward leaves through this pair.
                affected = [
                    n for n in in_source
                    if any(zones.get(p) != source_zone for p in peers.get(n, set()))
                ]
            if not affected:
                continue
            if needs_interface:
                # Only an interface facing endpoints is selectable, because only
                # there is the blast radius exact: detaching a fabric uplink
                # would cut the endpoints behind it, which are not the ones the
                # zone pair names. A pair whose source zone offers no such
                # interface cannot carry this method at all.
                in_zone = self.topology.zone_interfaces(node, source_zone)
                # VyOS refuses to commit a zone left without interfaces -- the
                # whole firewall block fails validation, so the fault would not
                # apply at all. Only a zone that keeps one can give another up.
                ports = (
                    [
                        (ref, hosts)
                        for ref in in_zone
                        if (hosts := sorted(set(self.topology.segment_hosts(ref.segment)) & evaluated))
                    ]
                    if len(in_zone) > 1
                    else []
                )
                if not ports:
                    continue
                candidates.append((dict(policy, node=node), affected, ports))
            else:
                candidates.append((dict(policy, node=node), affected, []))

        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no accepted zone pair that a "
                "required flow crosses"
                + (" through an endpoint-facing interface" if needs_interface else "")
            )

        policy, affected, ports = self.rng.choice(candidates)
        source_zone, target_zone = str(policy["from"]), str(policy["to"])
        template = str(
            self.topology.topology.get("defaults", {}).get(
                "zone_ruleset_template", "{from_zone}-TO-{to_zone}"
            )
        )
        bindings: dict[str, Any] = {
            "target": str(policy["node"]),
            "from_zone": source_zone,
            "to_zone": target_zone,
            "ruleset": template.format(from_zone=source_zone, to_zone=target_zone),
            "affected_nodes": affected,
        }

        if needs_interface:
            # Traffic arriving on an interface no zone claims is dropped before
            # any ruleset is consulted, so detaching one denies everything it
            # carries. The pair says which endpoints may be cut; the interface
            # says which of them this fault actually reaches.
            interface, hosts = self.rng.choice(ports)
            bindings["interface"] = interface.name
            bindings["zone"] = source_zone
            bindings["affected_nodes"] = hosts
        return bindings

    def _bind_dns_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a method to a DNS server and one of the zones it holds.

        The zone a server serves and the zone it forwards are different fault
        surfaces: breaking the first denies the names it owns, breaking the
        second denies the names it relays. The topology declares both through
        the `services` entries of the nameservers.
        """
        selector = method.get("selector") or {}
        scope = selector.get("zone")
        servers = self.topology.nodes_with_role("dns_resolver", "dns_authoritative")
        if not servers:
            raise ValueError(
                f"method {method.get('name')!r} found no node with a DNS role"
            )

        candidates: list[tuple[str, str, list[str]]] = []
        for server in servers:
            zone = self.topology.dns_zone(server)
            if not zone:
                continue
            forwarders = self.topology.dns_forwarders(server)
            if scope == "forwarded" and not forwarders:
                continue
            clients = self.topology.dns_clients(server)
            if not clients:
                continue
            candidates.append((server, zone, clients))

        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no DNS server with a "
                f"{scope!r} zone and a client behind it"
            )

        target, zone, clients = self.rng.choice(candidates)
        bindings: dict[str, Any] = {
            "target": target,
            "zone": zone,
            # The clients of a resolver are what a broken zone actually denies.
            "affected_nodes": sorted(clients),
            "zone_file": self.topology.dns_zone_file(target),
        }
        record = self.topology.dns_record(target)
        if record:
            bindings["record"] = record
        forwarders = self.topology.dns_forwarders(target)
        if forwarders:
            bindings["healthy_forwarder"] = forwarders[0]
            bindings["fault_forwarder"] = self.topology.unused_address_in(
                forwarders[0]
            )
        return bindings

    def _bind_dhcp_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a method to the DHCP server and to one client's reservation pool.

        The pool is the unit a DHCP fault acts on: it is what allocates one
        subnet, and every option a client receives -- its address, its gateway,
        its resolver -- is a field of that one pool. Binding per pool is also
        what keeps the blast radius to a single endpoint, so a scenario can
        break name resolution for one desk while the others keep leasing
        correctly.
        """
        _, service = self.topology.dhcp_service()
        server = str(service["node"])
        clients = self.topology.dhcp_clients()
        if not clients:
            raise ValueError(
                f"method {method.get('name')!r} found no evaluated endpoint "
                "leasing its address from the DHCP server"
            )
        client = self.rng.choice(clients)
        pool = self.topology.dhcp_pools()[client]

        segment = self.topology.dhcp_client_segment(client)
        gateways = self.topology.segment_gateways(segment)
        if not gateways:
            raise ValueError(
                f"method {method.get('name')!r} selected a client whose segment "
                f"'{segment}' has no gateway to hand out"
            )
        gateway_interface = self.topology.segment_interface(gateways[0], segment)
        if not gateway_interface or not gateway_interface.ipv4:
            raise ValueError(
                f"method {method.get('name')!r} found no gateway address on '{segment}'"
            )
        gateway = str(ip_interface(gateway_interface.ipv4).ip)

        client_interface = self.topology.segment_interface(client, segment)
        if not client_interface or not client_interface.ipv4:
            raise ValueError(
                f"method {method.get('name')!r} selected a client with no address "
                f"on '{segment}' to reserve"
            )
        reserved = ip_interface(client_interface.ipv4)
        resolvers = [str(item) for item in service.get("dns_servers", [])]
        if not resolvers:
            raise ValueError(
                f"method {method.get('name')!r} needs a DHCP service that hands "
                "out a resolver"
            )

        bindings: dict[str, Any] = {
            "target": server,
            "client": client,
            # One pool, one subnet, one endpoint: what the fault denies is
            # exactly the desk this pool allocates for.
            "affected_nodes": [client],
            "pool": str(pool["name"]),
            "subnet": str(reserved.network),
            "subnet_id": int(pool["subnet_id"]),
            "client_address": str(reserved.ip),
            "client_mac": str(self.topology.nodes[client]["mac"]),
            "healthy_gateway": gateway,
            "fault_gateway": self.topology.unused_address_in(gateway),
            "healthy_resolver": resolvers[0],
            "fault_resolver": self.topology.unused_address_in(resolvers[0]),
            "domain_name": str(service.get("domain_name", "")),
            "search_domains": [str(item) for item in service.get("search_domains", [])],
            "lease_seconds": int(service.get("lease_seconds", 0)),
            "probe_name": self._lease_probe_name(method, client, resolvers[0]),
        }
        return bindings

    def _lease_probe_name(self, method: dict[str, Any], client: str, resolver: str) -> str:
        """The name a leased client resolves to prove what its lease handed out.

        Its own, in the zone of the resolver the lease names: the lab's resolver
        holds a record for each of its endpoints, so the answer needs the address,
        the gateway and the resolver the lease carried. Fully qualified, because
        containerlab writes every lab node's short name into each container's
        /etc/hosts, which answers a bare name -- with the management address --
        whatever DHCP and DNS do. Until the oracles took the name from here they
        asked for `web1`, which sme01-dns's /etc/hosts answered that way and no
        campus zone held at all.
        """
        resolver_node = self.topology.node_with_address(resolver)
        zone = self.topology.dns_zone(resolver_node) if resolver_node else None
        if zone and client in self.topology.dns_hosts(resolver_node):
            return f"{client}.{zone}"
        raise ValueError(
            f"method {method.get('name')!r} needs {client} named in the zone of its "
            f"lease's resolver {resolver}, to prove the lease by"
        )

    def _bind_switch_port_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a method to a layer-2 switch port.

        Access ports and trunk uplinks are bound separately because their blast
        radius is computed differently: an access port cuts the endpoint cabled
        to it, a trunk uplink cuts every endpoint whose VLAN it carries toward the
        gateway.
        """
        selector = method.get("selector") or {}
        node_selector = selector.get("node")
        if node_selector != "switch":
            raise ValueError(
                f"method {method.get('name')!r} uses an access/trunk interface "
                f"selector with node selector {node_selector!r}; only 'switch' is supported"
            )
        switches = self.topology.nodes_with_role("switch")
        if not switches:
            raise ValueError(
                f"method {method.get('name')!r} found no node with the 'switch' role"
            )

        scope = selector.get("interface")
        candidates: list[tuple[str, InterfaceRef, list[str]]] = []
        for switch in switches:
            if scope == "access":
                for ref in self.topology.access_interfaces(switch):
                    candidates.append(
                        (switch, ref, self.topology.access_port_endpoints(switch, ref))
                    )
            else:
                for ref in self.topology.trunk_interfaces(switch):
                    candidates.append((switch, ref, self.topology.trunk_hosts(ref.segment)))

        candidates = [item for item in candidates if item[2]]
        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no {scope} port with an "
                "endpoint behind it"
            )

        target, interface, affected = self.rng.choice(candidates)
        bindings: dict[str, Any] = {
            "target": target,
            "interface": interface.name,
            "segment": interface.segment,
            "affected_nodes": sorted(affected),
        }
        return self._add_bridge_domain_bindings(method, interface, bindings)

    def _switch_uplinks(self, switch: str) -> list[InterfaceRef]:
        return [
            ref for ref in self.topology.interfaces(switch)
            if self.topology.segments.get(ref.segment, {}).get("type") == "trunk"
        ]

    def _switch_vlan_endpoints(self, switch: str, vlan_id: int) -> list[str]:
        """The evaluated endpoints ``switch`` attaches to ``vlan_id`` on its access ports."""
        return sorted({
            endpoint
            for ref in self.topology.access_interfaces(switch)
            if self.topology.segments.get(ref.segment, {}).get("vlan_id") == vlan_id
            for endpoint in self.topology.access_port_endpoints(switch, ref)
        })

    def _bind_vlan_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a VLAN fault to an access switch: one access port, or one VLAN of its uplink.

        The blast radius is read from the switch alone. Only an access switch is drawn,
        so nothing hangs below it and the endpoints it attaches are all a switch-side
        VLAN fault can cut: the one cabled to the port for a port's own setting, every
        one the switch attaches to the VLAN when the VLAN itself goes (deleted from the
        switch, pruned from or untagged on the uplink). A VLAN the switch carries on
        more than one uplink is not drawn for an uplink fault: the other one would keep
        it alive, and the oracle would observe nothing.
        """
        selector = method.get("selector") or {}
        operation = method.get("operation") or {}
        name = str(operation.get("name"))
        if selector.get("node") != "switch":
            raise ValueError(
                f"method {method.get('name')!r}: VLAN faults bind to node 'switch', "
                f"not {selector.get('node')!r}")
        scope = selector.get("interface")
        switches = self.topology.nodes_with_role("access_switch")
        if not switches:
            raise ValueError(
                f"method {method.get('name')!r} found no node with the 'access_switch' role")
        segments = self.topology.segments

        candidates: list[tuple[str, InterfaceRef, int, list[str], list[int]]] = []
        for switch in switches:
            uplinks = self._switch_uplinks(switch)
            carried: dict[int, int] = {}
            for ref in uplinks:
                for vlan_id in segments[ref.segment].get("tagged_vlans", []):
                    carried[int(vlan_id)] = carried.get(int(vlan_id), 0) + 1
            if scope == "access":
                for ref in self.topology.access_interfaces(switch):
                    vlan_id = segments.get(ref.segment, {}).get("vlan_id")
                    if vlan_id is None:
                        continue
                    vlan_id = int(vlan_id)
                    if name == "set_vlan_presence":
                        affected = self._switch_vlan_endpoints(switch, vlan_id)
                    else:
                        affected = self.topology.access_port_endpoints(switch, ref)
                    # A wrong access VLAN has to be one the switch actually carries to
                    # a gateway: anything else is a VLAN that does not exist here, which
                    # is the deleted-VLAN fault under another name.
                    others = sorted(vlan for vlan in carried if vlan != vlan_id)
                    if name == "set_access_port_vlan" and not others:
                        continue
                    if affected:
                        candidates.append((switch, ref, vlan_id, affected, others))
            elif scope == "trunk":
                for ref in uplinks:
                    for vlan_id in segments[ref.segment].get("tagged_vlans", []):
                        vlan_id = int(vlan_id)
                        if carried[vlan_id] != 1:
                            continue
                        affected = self._switch_vlan_endpoints(switch, vlan_id)
                        if affected:
                            candidates.append((switch, ref, vlan_id, affected, []))
            else:
                raise ValueError(
                    f"method {method.get('name')!r}: VLAN faults bind to an 'access' or "
                    f"'trunk' interface, not {scope!r}")
        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no {scope} port of an access switch "
                "with an evaluated endpoint behind it"
                + (" and a second VLAN to move it to" if name == "set_access_port_vlan" else ""))

        switch, ref, vlan_id, affected, others = self.rng.choice(candidates)
        bindings: dict[str, Any] = {
            "target": switch,
            "interface": ref.name,
            "segment": ref.segment,
            "vlan_id": vlan_id,
            "vlan_segment": self.topology.segment_for_vlan(vlan_id),
            "affected_nodes": sorted(affected),
        }
        if name == "set_access_port_vlan":
            if operation.get("value_source") != "other_switch_vlan":
                raise ValueError("set_access_port_vlan requires value_source 'other_switch_vlan'")
            fault_vlan = self.rng.choice(others)
            bindings["fault_vlan_id"] = fault_vlan
            bindings["fault_vlan_segment"] = self.topology.segment_for_vlan(fault_vlan)
        elif name == "set_switchport_mode":
            if operation.get("mode") != "trunk":
                raise ValueError("set_switchport_mode currently supports mode 'trunk'")
        elif name in {"set_vlan_presence", "set_trunk_allowed_vlan"}:
            if operation.get("present") is not False:
                raise ValueError(f"{name} currently supports present=false")
        elif name == "set_trunk_native_vlan":
            if operation.get("value_source") != "carried_vlan":
                raise ValueError("set_trunk_native_vlan requires value_source 'carried_vlan'")
        return bindings

    def _bind_isolated_vlan_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a change request: a group of users and a service, isolated in a new VLAN.

        Drawn on one access switch that attaches every host of a user VLAN (the group)
        and the host of a service whose role the operation names. Both on one switch is
        what keeps the request safe on a dual-spine fabric: a VLAN spread over two
        leaves would be bridged between them by both spines, a layer-2 loop that no
        spanning tree breaks there.

        Nothing is drawn about the implementation. The bindings carry what the oracles
        measure: the members keep reaching each other (`reachable_paths`), the group
        reaches the service without crossing a router (`same_segment_paths`, the mark of
        one broadcast domain), no other evaluated endpoint reaches a member and no member
        reaches one (`isolated_paths`, both directions), and the required paths between
        the other endpoints keep working (`preserved_paths`). The VLAN id, subnet and
        addresses are a plan the precise wording of the task states; the oracles read the
        addresses the members hold when they measure, whatever the subject chose.
        """
        selector = method.get("selector") or {}
        operation = method.get("operation") or {}
        if selector.get("node") != "switch":
            raise ValueError(
                f"method {method.get('name')!r}: provision_isolated_vlan binds to node "
                f"'switch', not {selector.get('node')!r}")
        role = str(operation.get("service_role") or "")
        if role not in TopologyIndex.SERVICE_ROLES:
            raise ValueError(
                f"method {method.get('name')!r}: service_role must be one of "
                f"{sorted(TopologyIndex.SERVICE_ROLES)}, not {role!r}")
        segments = self.topology.segments
        nodes = self.topology.nodes

        candidates: list[tuple[str, str, list[str], str, dict[str, Any], dict[str, InterfaceRef]]] = []
        for switch in self.topology.nodes_with_role("access_switch"):
            # Every evaluated endpoint the switch attaches, with its port and VLAN segment.
            attached: dict[str, InterfaceRef] = {}
            for ref in self.topology.access_interfaces(switch):
                for endpoint in self.topology.access_port_endpoints(switch, ref):
                    attached[endpoint] = ref
            services = [
                (name, spec) for name, spec in sorted(self.topology.services.items())
                if spec.get("node") in attached
                and role in nodes[str(spec["node"])].get("roles", [])
            ]
            for segment in sorted({ref.segment for ref in attached.values()}):
                if segments.get(segment, {}).get("type") not in {"vlan", "lan"}:
                    continue
                group = self.topology.segment_hosts(segment)
                if not group or any(
                    attached.get(host) is None or attached[host].segment != segment
                    or "client" not in nodes[host].get("roles", [])
                    or "external_client" in nodes[host].get("roles", [])
                    for host in group
                ):
                    continue
                for name, spec in services:
                    service_node = str(spec["node"])
                    if service_node in group:
                        continue
                    ports = {host: attached[host] for host in [*group, service_node]}
                    candidates.append((switch, segment, group, name, spec, ports))
        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no access switch attaching both every "
                f"host of a user VLAN and a {role} service")

        # The first candidate, not a draw: a change request is one fixed request, and a binder
        # that consumed the shared random stream would move the draws of every scenario
        # compiled after it in the same suite.
        switch, segment, group, service_name, service, ports = candidates[0]
        service_node = str(service["node"])
        members = [*group, service_node]
        vlan_id = self._unused_vlan_id()
        subnet = self._unused_subnet(str(segments[segment].get("cidr") or "10.0.0.0/24"), vlan_id)
        hosts = list(subnet.hosts())
        plan = {host: hosts[9 + index] for index, host in enumerate(group)}
        plan[service_node] = hosts[99] if len(hosts) > 99 + len(group) else hosts[9 + len(group)]
        member_set = set(members)
        outsiders = [node for node in self.topology.endpoint_nodes() if node not in member_set]
        if not outsiders:
            raise ValueError(
                f"method {method.get('name')!r}: no evaluated endpoint is left outside the "
                "new VLAN, so its isolation could not be observed")
        gateways = self.topology.segment_gateways(segment)
        return {
            "target": switch,
            "switch": switch,
            "group": list(group),
            "group_name": str(segments[segment].get("security_zone") or segment),
            "group_segment": segment,
            "group_vlan_id": segments[segment].get("vlan_id"),
            "service": service_name,
            "service_node": service_node,
            "service_description": _SERVICE_ROLE_WORDS.get(role, role.replace("_", " ")),
            "service_application": str(service.get("application") or service_name),
            "service_segment": ports[service_node].segment,
            "vlan_id": vlan_id,
            "vlan_subnet": str(subnet),
            "vlan_gateway": str(hosts[0]),
            "gateway_node": gateways[0] if gateways else None,
            "address_plan": [f"{host} {plan[host]}/{subnet.prefixlen}" for host in members],
            "access_ports": [f"{ports[host].name} ({host})" for host in members],
            "members": members,
            "affected_nodes": sorted(members),
            "reachable_paths": [
                f"{source}->{destination}"
                for source in members for destination in members if source != destination
            ],
            "same_segment_paths": [f"{host}->{service_node}" for host in group],
            "isolated_paths": [
                path
                for outsider in outsiders for member in members
                for path in (f"{outsider}->{member}", f"{member}->{outsider}")
            ],
            "preserved_paths": [
                pair for pair in self.topology.required_endpoint_pairs()
                if not member_set.intersection(pair.split("->", 1))
            ],
        }

    def _bind_service_publication_method(self, method: dict[str, Any]) -> dict[str, Any]:
        """Bind a change request: publish one TCP service of a filtered zone to the outside,
        and nothing else.

        Drawn on a zone enforcer: the outside is the zone of an external client, the
        service one the descriptor declares over TCP on a host whose role the operation
        names, in a zone the outside has no accepted pair toward -- a service already
        published is no request. Every Linux host of that zone is a closed host: the
        outside may reach none of them but the service, on its port.

        Nothing is drawn about the implementation (a ruleset, its zone pair, an address
        group). The bindings carry what the oracles measure: the outside client reaches
        the service port (`published_paths`), and no port the closed hosts listen on but
        the service's, nor a ping, reaches them from it (`closed_paths`, `open_services`).
        Every path the descriptor requires or isolates stays as it is.
        """
        selector = method.get("selector") or {}
        operation = method.get("operation") or {}
        if selector.get("node") != "zone_enforcer":
            raise ValueError(
                f"method {method.get('name')!r}: provision_service_publication binds to node "
                f"'zone_enforcer', not {selector.get('node')!r}")
        role = str(operation.get("service_role") or "")
        if role not in TopologyIndex.SERVICE_ROLES:
            raise ValueError(
                f"method {method.get('name')!r}: service_role must be one of "
                f"{sorted(TopologyIndex.SERVICE_ROLES)}, not {role!r}")
        topology, nodes = self.topology, self.topology.nodes
        enforcers = topology.zone_enforcers()
        if not enforcers:
            raise ValueError(f"method {method.get('name')!r} found no zone enforcer")
        accepted = {
            (str(item.get("from")), str(item.get("to")))
            for item in topology.zone_policies() if str(item.get("action")) == "accept"
        }
        candidates: list[tuple[str, str, str, str, dict[str, Any], str]] = []
        for client in topology.nodes_with_role("external_client"):
            outside = topology.node_zone(client)
            if not outside or client not in topology.endpoint_nodes():
                continue
            for name, spec in sorted(topology.services.items()):
                host = str(spec.get("node"))
                zone = topology.node_zone(host)
                if (str(spec.get("transport")) != "tcp" or not spec.get("port")
                        or role not in nodes[host].get("roles", [])
                        or not zone or zone == outside or (outside, zone) in accepted):
                    continue
                enforcer = next((item for item in enforcers if topology.zone_interfaces(item, zone)
                                 and topology.zone_interfaces(item, outside)), None)
                if enforcer:
                    candidates.append((enforcer, client, outside, name, spec, zone))
        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no TCP {role} service that an external "
                "client's zone cannot reach yet")
        # The first candidate, not a draw: see _bind_isolated_vlan_method.
        enforcer, client, outside, name, spec, zone = candidates[0]
        host = str(spec["node"])
        closed = sorted(
            node for node, node_spec in nodes.items()
            if node_spec.get("kind") in {"host", "linux"} and topology.node_zone(node) == zone
        )
        port = int(spec["port"])
        return {
            "target": enforcer,
            "service": name,
            "service_node": host,
            "service_port": port,
            "service_transport": "tcp",
            "service_application": str(spec.get("application") or name),
            "service_address": topology.node_ipv4(host),
            "service_zone": zone,
            "outside_zone": outside,
            "external_source": client,
            "closed_hosts": closed,
            # The request reshapes no required path: convergence waits on all of them.
            "affected_nodes": [],
            "published_paths": [f"{client}->{host}"],
            "closed_paths": [f"{client}->{node}" for node in closed],
            "open_services": [f"{host}:{port}"],
        }

    def _unused_vlan_id(self) -> int:
        """The lowest multiple of ten no segment uses, as an access or a tagged VLAN."""
        used: set[int] = set()
        for spec in self.topology.segments.values():
            if spec.get("vlan_id") is not None:
                used.add(int(spec["vlan_id"]))
            used.update(int(vlan) for vlan in spec.get("tagged_vlans") or [])
        candidate = 10
        while candidate in used:
            candidate += 10
        if candidate > 4094:
            raise ValueError("no unused VLAN id left below 4095")
        return candidate

    def _unused_subnet(self, near: str, vlan_id: int):
        """A /24 no segment overlaps, in the /16 of ``near``: the one numbered after the
        VLAN when it is free (10.10.70.0/24 for VLAN 70 beside 10.10.30.0/24), else the
        first free one."""
        used = [
            ip_network(str(spec["cidr"]), strict=False)
            for spec in self.topology.segments.values() if spec.get("cidr")
        ]
        base = ip_network(near, strict=False)
        parent = base.supernet(new_prefix=16) if base.prefixlen > 16 else base
        subnets = list(parent.subnets(new_prefix=24))
        preferred = [subnets[vlan_id]] if vlan_id < len(subnets) else []
        for candidate in [*preferred, *subnets]:
            if not any(candidate.overlaps(network) for network in used):
                return candidate
        raise ValueError(f"no free /24 left in {parent}")

    def _add_bridge_domain_bindings(
        self,
        method: dict[str, Any],
        interface: InterfaceRef,
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        """Add the bridge-domain context needed by layer-2 operations."""
        operation = method.get("operation") or {}
        operation_name = operation.get("name")
        if operation_name not in {
            "set_bridge_domain_interface_presence",
            "set_access_port_bridge_domain",
            "set_trunk_vlan_presence",
        }:
            return bindings

        segments = self.topology.segments
        scope = (method.get("selector") or {}).get("interface")

        if scope == "trunk":
            # A trunk subinterface belongs to one VLAN; pick the VLAN whose
            # endpoints the fault is expected to affect.
            vlan_ids = segments.get(interface.segment, {}).get("tagged_vlans", [])
            vlan_candidates = [
                (vlan_id, name)
                for vlan_id in vlan_ids
                if (name := self.topology.segment_for_vlan(vlan_id))
                and set(self.topology.segment_hosts(name))
                & set(self.topology.endpoint_nodes())
            ]
            if not vlan_candidates:
                raise ValueError(
                    f"method {method.get('name')!r} found no VLAN with endpoints on "
                    f"trunk '{interface.segment}'"
                )
            vlan_id, vlan_segment = self.rng.choice(sorted(vlan_candidates))
            bindings["affected_nodes"] = sorted(
                set(self.topology.segment_hosts(vlan_segment))
                & set(self.topology.endpoint_nodes())
            )
            bindings["vlan_id"] = vlan_id
            bindings["vlan_segment"] = vlan_segment
            bindings["subinterface_index"] = vlan_id
        else:
            vlan_id = segments.get(interface.segment, {}).get("vlan_id")
            bindings["vlan_id"] = vlan_id
            bindings["vlan_segment"] = interface.segment
            # An untagged access port is bridged through subinterface 0.
            bindings["subinterface_index"] = 0
            vlan_segment = interface.segment

        bridge_domain = self.topology.bridge_domain(vlan_segment)
        if not bridge_domain:
            raise ValueError(
                f"segment '{vlan_segment}' declares no bridge_domain"
            )
        bindings["bridge_domain"] = bridge_domain

        if operation_name == "set_access_port_bridge_domain":
            others = sorted(
                domain
                for name, spec in segments.items()
                if name != vlan_segment
                and (domain := spec.get("bridge_domain"))
                and name in {
                    ref.segment
                    for ref in self.topology.access_interfaces(str(bindings["target"]))
                }
            )
            if not others:
                raise ValueError(
                    f"method {method.get('name')!r} found no other bridge domain on "
                    f"{bindings['target']}"
                )
            bindings["fault_bridge_domain"] = self.rng.choice(others)

        return bindings

    def _bind_qos_wan_edge_context(self) -> dict[str, Any]:
        """Resolve the shaped WAN edge and the two flows that contend on it.

        Everything is read back from the descriptor's ``qos_policy`` rather than
        named in the scenario: the protected source is whichever endpoint sits on
        the prefix the assured class classifies, so a policy that changes which
        prefix it protects moves the scenario with it.
        """
        policy = self.topology.topology.get("qos_policy")
        if not isinstance(policy, dict) or not policy.get("classes"):
            raise ValueError("QoS shaping scenarios require a topology qos_policy")

        edge = str(policy["node"])
        if edge not in self.topology.nodes:
            raise ValueError(f"qos_policy names unknown node '{edge}'")
        if "wan_edge" not in self.topology.nodes[edge].get("roles", []):
            raise ValueError(f"qos_policy node '{edge}' has no wan_edge role")
        interface_name = str(policy["interface"])
        interface = (
            self.topology.nodes[edge].get("interfaces", {}).get(interface_name)
        )
        if not interface:
            raise ValueError(f"'{edge}' has no interface '{interface_name}'")
        segment = str(interface["segment"])

        sinks = [
            host
            for host in self.topology.segment_hosts(segment)
            if "traffic_sink" in self.topology.nodes[host].get("capabilities", [])
        ]
        if not sinks:
            raise ValueError(
                f"segment '{segment}' behind the WAN edge declares no traffic sink"
            )
        sink = sinks[0]

        assured = next(
            (spec for spec in policy["classes"] if spec.get("match_src")), None
        )
        if assured is None:
            raise ValueError("qos_policy declares no classified class to protect")
        protected_prefix = ip_network(str(assured["match_src"]))

        peers = self._required_peers()
        protected = None
        for endpoint in self.topology.endpoint_nodes():
            interfaces = self.topology.interfaces(endpoint)
            if not interfaces or not interfaces[0].ipv4:
                continue
            if ip_interface(interfaces[0].ipv4).ip not in protected_prefix:
                continue
            if sink not in peers.get(endpoint, set()):
                continue
            protected = endpoint
            break
        if protected is None:
            raise ValueError(
                f"no endpoint on {protected_prefix} has a required flow to '{sink}'"
            )

        background_candidates = sorted(
            endpoint
            for endpoint in self.topology.endpoint_nodes()
            if endpoint not in {protected, sink}
            and "traffic_generation"
            in self.topology.nodes[endpoint].get("capabilities", [])
            and sink in peers.get(endpoint, set())
        )
        if not background_candidates:
            raise ValueError(
                f"no second traffic source has a required flow to '{sink}'"
            )
        background = self.rng.choice(background_candidates)

        # Two sinks on the same host: the oracle measures the protected flow
        # against one while it floods the other, since one iperf3 server runs one
        # test at a time and a shared port would queue the measurement behind the
        # contention it exists to measure.
        measurement_port = self.topology.iperf_port(sink, "measurement")
        if measurement_port is None:
            raise ValueError(
                f"sink '{sink}' declares no iperf3 service with role 'measurement'; "
                "the shaped QoS scenarios cannot be measured without one"
            )
        load_port = self.topology.iperf_port(sink, "load")
        if load_port is None:
            raise ValueError(
                f"sink '{sink}' declares no iperf3 service with role 'load'"
            )

        return {
            "wan_edge": edge,
            "shaped_interface": interface_name,
            "protected_source": protected,
            "background_source": background,
            "destination": sink,
            "destination_ip": self.topology.node_ipv4(sink),
            "sink_ip": self.topology.node_ipv4(sink),
            "measurement_port": measurement_port,
            "load_port": load_port,
            "assured_class": int(assured["id"]),
            "assured_bandwidth_mbps": int(assured["rate_mbps"]),
            "link_mbps": int(policy["link_mbps"]),
            # The contention degrades the protected flow; the WAN edge and the
            # background source are instruments, not victims.
            "affected_nodes": [protected],
        }

    def _required_peers(self) -> dict[str, set[str]]:
        peers: dict[str, set[str]] = {}
        for requirement in self.topology.topology.get(
            "connectivity_requirements", []
        ):
            source = str(requirement["source"])
            destination = str(requirement["destination"])
            peers.setdefault(source, set()).add(destination)
            if requirement.get("bidirectional", False):
                peers.setdefault(destination, set()).add(source)
        return peers

    def _bind_qos_shaping_context(self) -> dict[str, Any]:
        context = self._bind_qos_wan_edge_context()
        # The shaping commands are written on the WAN edge itself.
        context["target"] = context["wan_edge"]
        context["interface"] = context["shaped_interface"]
        return context

    def _bind_qos_contention_context(self) -> dict[str, Any]:
        context = self._bind_qos_wan_edge_context()
        # The flood is generated from the competing endpoint.
        context["target"] = context["background_source"]
        interfaces = self.topology.interfaces(context["background_source"])
        if not interfaces:
            raise ValueError(
                f"background source '{context['background_source']}' has no data interface"
            )
        context["interface"] = interfaces[0].name
        return context

    def _add_qos_shaping_bindings(
        self,
        method: dict[str, Any],
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        operation = method.get("operation") or {}
        policy = operation.get("policy")
        if policy == "starve_assured_class":
            lower, upper = self._int_range(operation, "starved_kbit_range")
            bindings["starved_kbit"] = self.rng.randint(lower, upper)
        elif policy not in {"remove", "remove_classifier", "invert_class_allocation"}:
            raise ValueError(f"unsupported traffic shaping policy {policy!r}")
        bindings["shaping_policy"] = policy
        return bindings

    def _add_qos_contention_bindings(
        self,
        method: dict[str, Any],
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        operation = method.get("operation") or {}
        lower, upper = self._int_range(operation, "background_mbps_range")
        bindings["background_mbps"] = self.rng.randint(lower, upper)
        bindings["background_seconds"] = self._positive_int(operation, "duration_seconds")
        bindings["background_streams"] = self._positive_int(operation, "streams")
        bindings["sink_port"] = self._positive_int(operation, "sink_port")
        return bindings

    @staticmethod
    def _positive_int(operation: dict[str, Any], key: str) -> int:
        value = operation.get(key)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
        return value

    def _bind_qos_link_context(self) -> dict[str, Any]:
        peers: dict[str, set[str]] = {}
        for requirement in self.topology.topology.get("connectivity_requirements", []):
            source = str(requirement["source"])
            destination = str(requirement["destination"])
            peers.setdefault(source, set()).add(destination)
            if requirement.get("bidirectional", False):
                peers.setdefault(destination, set()).add(source)

        candidates: list[tuple[str, str, InterfaceRef]] = []
        for endpoint, endpoint_peers in peers.items():
            if len(endpoint_peers) != 1:
                continue
            node = self.topology.nodes.get(endpoint, {})
            if node.get("kind") != "linux":
                continue
            interfaces = self.topology.interfaces(endpoint)
            if len(interfaces) != 1:
                continue
            candidates.append((endpoint, next(iter(endpoint_peers)), interfaces[0]))
        if not candidates:
            raise ValueError(
                "QoS link impairment requires a Linux endpoint with one required peer"
            )

        target, destination, interface = self.rng.choice(sorted(candidates))
        return {
            "target": target,
            "interface": interface.name,
            "segment": interface.segment,
            "destination": destination,
            "destination_ip": self.topology.node_ipv4(destination),
            "affected_nodes": [target],
        }

    def _add_qos_impairment_bindings(
        self,
        method: dict[str, Any],
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        operation = method.get("operation") or {}
        impairment = operation.get("impairment")
        if impairment == "delay":
            lower, upper = self._int_range(operation, "delay_ms_range")
            bindings.update({"impairment": "delay", "delay_ms": self.rng.randint(lower, upper)})
            return bindings
        if impairment == "corruption":
            lower, upper = self._int_range(operation, "corruption_percent_range")
            bindings.update({
                "impairment": "corruption",
                "corruption_percent": self.rng.randint(lower, upper),
            })
            return bindings
        raise ValueError(f"unsupported QoS impairment {impairment!r}")

    @staticmethod
    def _int_range(operation: dict[str, Any], key: str) -> tuple[int, int]:
        values = operation.get(key)
        if (
            not isinstance(values, list)
            or len(values) != 2
            or not all(isinstance(value, int) for value in values)
            or values[0] <= 0
            or values[0] > values[1]
        ):
            raise ValueError(f"{key} must be a positive [minimum, maximum] integer range")
        return values[0], values[1]

    def _bind_subnet_method(
        self, method: dict[str, Any], *, directions: set[str] | None = None
    ) -> dict[str, Any]:
        """Bind a subnet traffic policy; ``directions`` are those of every method sharing the binding."""
        if directions is None:
            directions = {str((method.get("operation") or {}).get("direction"))}
        selector = method["selector"]
        if selector.get("node") != "gateway":
            raise ValueError("subnet traffic policy requires node='gateway'")
        if selector.get("subnet") != "required_endpoint_segment":
            raise ValueError(
                "set_subnet_traffic_policy requires required_endpoint_segment"
            )

        required_endpoints = {
            str(requirement[key])
            for requirement in self.topology.topology.get(
                "connectivity_requirements", []
            )
            for key in ("source", "destination")
        }
        gateways = [
            node
            for node in self.topology.nodes_with_role("gateway")
            if "acl" in self.topology.nodes[node].get("capabilities", [])
            and any(
                self.topology.segment_hosts(interface.segment)
                and required_endpoints.intersection(
                    self.topology.segment_hosts(interface.segment)
                )
                for interface in self.topology.interfaces(node)
            )
        ]
        if not gateways:
            raise ValueError(
                f"method {method.get('name')!r} found no ACL-capable gateway"
            )
        target = self.rng.choice(gateways)
        interfaces = [
            interface
            for interface in self.topology.interfaces(target)
            if self.topology.segment_hosts(interface.segment)
            and required_endpoints.intersection(
                self.topology.segment_hosts(interface.segment)
            )
        ]
        if not interfaces:
            raise ValueError(
                f"method {method.get('name')!r} found no required endpoint segment"
            )
        interface = self.rng.choice(interfaces)
        # A drop toward the subnet stops the sessions opened toward it, a drop from
        # it the sessions it opens: a stateful enforcer (VyOS) still lets back the
        # replies to the others. With one-way requirements a subnet that only opens
        # sessions loses nothing to the first (campus_medium drew CORP for seed 9),
        # one that only receives them nothing to the second (campus_large drew
        # sftp-dmz). The family shares this binding across its directions, so the
        # subnet must suit every one-way direction among them.
        wanted = []
        if "to" in directions:
            wanted.append(self._required_destinations())
        if "from" in directions:
            wanted.append(self._required_sources())
        if wanted:
            interface = self._fold_onto_observable(
                method, interfaces, interface,
                lambda ref: self.topology.segment_hosts(ref.segment),
                observed=wanted)
        segment = self.topology.segments[interface.segment]
        subnet_prefix = segment.get("cidr")
        if not subnet_prefix:
            raise ValueError(f"segment '{interface.segment}' has no IPv4 prefix")
        affected_nodes = sorted(
            required_endpoints.intersection(
                self.topology.segment_hosts(interface.segment)
            )
        )
        return {
            "target": target,
            "interface": interface.name,
            "segment": interface.segment,
            "subnet_prefix": str(subnet_prefix),
            "affected_nodes": affected_nodes,
        }

    def _bind_route_method(self, method: dict[str, Any]) -> dict[str, Any]:
        selector = method["selector"]
        route_scope = selector.get("route")
        if route_scope not in {"required_remote_prefix", "required_remote_host"}:
            raise ValueError(
                f"method {method.get('name')!r} has unsupported route selector "
                f"{route_scope!r}"
            )

        candidates: list[tuple[str, str, str, str]] = []
        requirements = self.topology.topology.get("connectivity_requirements", [])
        for destination in self.topology.endpoint_nodes():
            destination_interfaces = self.topology.interfaces(destination)
            if len(destination_interfaces) != 1:
                continue
            destination_segment = destination_interfaces[0].segment
            destination_gateways = self.topology.segment_gateways(destination_segment)
            if len(destination_gateways) != 1:
                continue
            destination_gateway = destination_gateways[0]

            peers = {
                str(requirement["destination"])
                if requirement.get("source") == destination
                else str(requirement["source"])
                for requirement in requirements
                if destination in {
                    requirement.get("source"),
                    requirement.get("destination"),
                }
            }
            if not peers:
                continue
            peer_gateways = set()
            valid = True
            for peer in peers:
                peer_interfaces = self.topology.interfaces(peer)
                if len(peer_interfaces) != 1:
                    valid = False
                    break
                gateways = self.topology.segment_gateways(peer_interfaces[0].segment)
                if len(gateways) != 1:
                    valid = False
                    break
                peer_gateways.add(gateways[0])
            if (
                not valid
                or len(peer_gateways) != 1
                or destination_gateway in peer_gateways
            ):
                continue
            target = next(iter(peer_gateways))
            if "static_routing" not in self.topology.nodes[target].get(
                "capabilities", []
            ):
                continue
            candidates.append(
                (destination, destination_segment, destination_gateway, target)
            )

        if not candidates:
            raise ValueError(
                f"method {method.get('name')!r} found no required remote route"
            )
        operation = method["operation"]
        operation_name = operation["name"]
        value_source = operation.get("value_source")

        def remote_prefix(candidate: tuple[str, str, str, str]) -> str:
            return str(self.topology.segments[candidate[1]]["cidr"])

        if operation_name in {"set_static_route_next_hop", "create_static_route_loop"}:
            # These rewrite the route the target holds toward the destination, so
            # a target whose routes are declared must hold that exact one: there is
            # nothing to rewrite in a route it only reaches through its default.
            candidates = [
                candidate for candidate in candidates
                if self._holds_route(candidate[3], remote_prefix(candidate))
            ]
            if not candidates:
                raise ValueError(
                    f"method {method.get('name')!r} found no required remote route "
                    "its router declares"
                )
        if value_source == "unresolvable_address":
            # A route whose next hop cannot be resolved is not installed, and the
            # traffic falls to a shorter one. Where that one still leads to the
            # destination, the fault changes nothing.
            candidates = [
                candidate for candidate in candidates
                if self._fallback_misses(candidate[3], candidate[0], remote_prefix(candidate))
            ]
            if not candidates:
                raise ValueError(
                    f"method {method.get('name')!r} found no required remote route "
                    "whose fallback misses the destination"
                )
        if operation_name == "create_static_route_loop":
            # Only a route with a second router on its path can loop. Drawing
            # first and looking for that router afterwards made the method bind
            # or fail with the seed wherever some candidates had none.
            candidates = [
                candidate for candidate in candidates
                if self._loop_peers(candidate[3], candidate[2], remote_prefix(candidate))
            ]
            if not candidates:
                raise ValueError(
                    f"method {method.get('name')!r} found no deterministic loop peer"
                )
        destination, segment, gateway, target = self.rng.choice(sorted(candidates))
        group_template = self.topology.topology.get("defaults", {}).get(
            "static_next_hop_group_template", "to-{gateway}"
        )
        canonical_group = str(group_template).format(gateway=gateway)
        destination_ip = self.topology.node_ipv4(destination)
        route_prefix = (
            f"{destination_ip}/32"
            if route_scope == "required_remote_host"
            else str(self.topology.segments[segment]["cidr"])
        )
        method_id = int(method["id"])
        segment_prefix = str(self.topology.segments[segment]["cidr"])
        bindings: dict[str, Any] = {
            "target": target,
            "route_prefix": route_prefix,
            "destination": destination,
            "destination_segment": segment,
            "destination_gateway": gateway,
            "canonical_next_hop_group": canonical_group,
            # The same healthy route by address, for a platform that names its
            # next hops rather than grouping them (VyOS).
            "canonical_next_hops": self._healthy_next_hops(target, gateway, segment_prefix),
            "temporary_next_hop_group": f"ibn-wrt-m{method_id}",
            "affected_nodes": [destination],
        }

        if operation_name == "set_static_route_disposition":
            if operation.get("disposition") != "blackhole":
                raise ValueError(
                    "set_static_route_disposition currently supports blackhole"
                )
            return bindings

        if (
            operation_name == "set_static_route_next_hop"
            and value_source == "wrong_reachable_egress"
        ):
            wrong_endpoints = sorted({
                host
                for interface in self.topology.interfaces(target)
                for host in self.topology.segment_hosts(interface.segment)
                if host != destination
            })
            if not wrong_endpoints:
                raise ValueError(
                    f"method {method.get('name')!r} found no wrong egress endpoint"
                )
            wrong_endpoint = wrong_endpoints[0]
            bindings["fault_next_hop"] = self.topology.node_ipv4(wrong_endpoint)
            bindings["fault_next_hop_source"] = wrong_endpoint
            return bindings

        if (
            operation_name == "set_static_route_next_hop"
            and value_source == "unresolvable_address"
        ):
            bindings["fault_next_hop"] = self._unused_documentation_address()
            return bindings

        if operation_name == "create_static_route_loop":
            loop_peer, target_to_peer, peer_to_target = self._loop_peers(
                target, gateway, segment_prefix
            )[0]
            bindings.update({
                "loop_peer": loop_peer,
                "target_to_loop_peer": target_to_peer,
                "loop_peer_to_target": peer_to_target,
                "loop_peer_canonical_next_hop_group": canonical_group,
                "loop_peer_canonical_next_hops": self._healthy_next_hops(
                    loop_peer, gateway, segment_prefix
                ),
                "loop_peer_temporary_next_hop_group": f"ibn-wrt-m{method_id}-back",
            })
            return bindings

        raise ValueError(
            f"method {method.get('name')!r} has unsupported route operation"
        )

    def _routers_toward(self, node: str, gateway: str) -> list[tuple[str, str, str]]:
        """Routers next to ``node``, other than ``gateway``, that also sit on a segment of ``gateway``.

        Each one is ``(router, its address, node's address)`` on the segment it
        shares with ``node``, sorted. These are the routers a route from ``node``
        toward the gateway's segments can go through -- and so the peers it can
        be made to loop with.
        """
        routers = []
        for node_interface in self.topology.interfaces(node):
            for peer in self.topology.segments[node_interface.segment].get("members", []):
                if (
                    peer == node
                    or peer == gateway
                    or peer not in self.topology.nodes
                    or "router" not in self.topology.nodes[peer].get("roles", [])
                ):
                    continue
                peer_interface = self.topology.segment_interface(
                    peer, node_interface.segment
                )
                if (
                    not node_interface.ipv4
                    or peer_interface is None
                    or not peer_interface.ipv4
                ):
                    continue
                peer_reaches_gateway = any(
                    gateway in self.topology.segments[interface.segment].get("members", [])
                    for interface in self.topology.interfaces(peer)
                )
                if peer_reaches_gateway:
                    routers.append((
                        peer,
                        str(ip_interface(peer_interface.ipv4).ip),
                        str(ip_interface(node_interface.ipv4).ip),
                    ))
        return sorted(routers)

    def _next_hops_toward(self, node: str, gateway: str) -> list[str]:
        """The addresses ``node`` routes toward ``gateway``'s segments in the reference state.

        The gateway itself on a segment the two share; otherwise every router
        next to ``node`` that shares one with the gateway, which is what an
        equal-cost next-hop group toward it holds.
        """
        adjacent = sorted(
            str(ip_interface(gateway_interface.ipv4).ip)
            for node_interface in self.topology.interfaces(node)
            if node_interface.ipv4
            and (
                gateway_interface := self.topology.segment_interface(
                    gateway, node_interface.segment
                )
            )
            and gateway_interface.ipv4
        )
        if adjacent:
            return adjacent
        return [address for _, address, _ in self._routers_toward(node, gateway)]

    def _holds_route(self, node: str, prefix: str) -> bool:
        """Whether ``node`` routes ``prefix`` itself, as far as the descriptor says.

        Only a node that declares its static routes can be told not to; one that
        declares none follows the derived convention, under which it does.
        """
        routes = self.topology.static_routes(node)
        return routes is None or bool(routes.get(prefix, {}).get("next_hops"))

    def _fallback_misses(self, node: str, destination: str, prefix: str) -> bool:
        """Whether ``destination`` is lost once ``node``'s route to ``prefix`` is unusable."""
        if self.topology.static_routes(node) is None:
            return True
        return self.topology.delivers(node, destination, withdrawn=(node, prefix)) is False

    def _loop_peers(self, node: str, gateway: str, prefix: str) -> list[tuple[str, str, str]]:
        """The routers toward ``gateway`` a route to ``prefix`` can be made to loop with.

        The peer's own route is rewritten to point back, so it must hold one.
        """
        return [
            router for router in self._routers_toward(node, gateway)
            if self._holds_route(router[0], prefix)
        ]

    def _healthy_next_hops(self, node: str, gateway: str, prefix: str) -> list[str]:
        """The next hops of ``node``'s healthy route to ``prefix``: as declared, else derived."""
        routes = self.topology.static_routes(node)
        if routes and routes.get(prefix, {}).get("next_hops"):
            return list(routes[prefix]["next_hops"])
        return self._next_hops_toward(node, gateway)

    def _add_endpoint_route_bindings(
        self,
        method: dict[str, Any],
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        operation = method.get("operation") or {}
        if operation.get("name") != "set_default_route_gateway":
            return bindings
        if operation.get("value_source") != "unused_same_subnet_address":
            raise ValueError(
                "set_default_route_gateway requires unused_same_subnet_address"
            )

        target = str(bindings["target"])
        interfaces = self.topology.interfaces(target)
        if len(interfaces) != 1 or not interfaces[0].ipv4:
            raise ValueError(
                f"endpoint '{target}' must have exactly one IPv4 data interface"
            )
        interface = interfaces[0]
        gateways = self.topology.segment_gateways(interface.segment)
        if len(gateways) != 1:
            raise ValueError(
                f"segment '{interface.segment}' must have exactly one gateway"
            )
        gateway_interface = self.topology.segment_interface(
            gateways[0], interface.segment
        )
        if gateway_interface is None or not gateway_interface.ipv4:
            raise ValueError(
                f"gateway '{gateways[0]}' has no IPv4 on '{interface.segment}'"
            )

        network = ip_network(
            self.topology.segments[interface.segment]["cidr"], strict=False
        )
        used = {
            ip_interface(ref.ipv4).ip
            for node in self.topology.nodes
            for ref in self.topology.interfaces(node, exclude_management=False)
            if ref.ipv4
        }
        fault_gateway = None
        candidate = int(network.broadcast_address) - 1
        while candidate > int(network.network_address):
            address = ip_address(candidate)
            if address not in used:
                fault_gateway = address
                break
            candidate -= 1
        if fault_gateway is None:
            raise ValueError(
                f"segment '{interface.segment}' has no unused gateway address"
            )

        bindings.update({
            "interface": interface.name,
            "segment": interface.segment,
            "healthy_gateway": str(ip_interface(gateway_interface.ipv4).ip),
            "fault_gateway": str(fault_gateway),
        })
        return bindings

    def _unused_documentation_address(self) -> str:
        used_networks = [
            ip_network(segment["cidr"], strict=False)
            for segment in self.topology.segments.values()
            if segment.get("cidr")
        ]
        for candidate in (
            ip_network("192.0.2.0/24"),
            ip_network("198.51.100.0/24"),
            ip_network("203.0.113.0/24"),
        ):
            if not any(candidate.overlaps(used) for used in used_networks):
                return str(candidate.network_address + 1)
        raise ValueError("no unused IPv4 documentation prefix is available")

    def _add_ipv4_method_bindings(
        self,
        method: dict[str, Any],
        interface: InterfaceRef,
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        operation = method.get("operation") or {}
        operation_name = operation.get("name")
        if operation_name not in {
            "set_interface_ipv4_presence",
            "set_interface_ipv4_address",
        }:
            return bindings
        if not interface.ipv4:
            raise ValueError(
                f"method {method.get('name')!r} selected an interface without IPv4"
            )

        bindings["healthy_ipv4"] = interface.ipv4
        if operation_name == "set_interface_ipv4_presence":
            return bindings

        value_source = operation.get("value_source")
        healthy = ip_interface(interface.ipv4)
        if value_source == "wrong_subnet":
            used_networks = [
                ip_network(segment["cidr"], strict=False)
                for segment in self.topology.segments.values()
                if segment.get("cidr")
            ]
            candidates = (
                ip_network("192.0.2.0/24"),
                ip_network("198.51.100.0/24"),
                ip_network("203.0.113.0/24"),
            )
            wrong_network = next(
                (
                    candidate
                    for candidate in candidates
                    if not any(candidate.overlaps(used) for used in used_networks)
                ),
                None,
            )
            if wrong_network is None:
                raise ValueError(
                    "wrong_subnet requires an unused IPv4 documentation prefix"
                )
            host_offset = int(healthy.ip) - int(healthy.network.network_address)
            host_offset = min(max(host_offset, 1), wrong_network.num_addresses - 2)
            bindings["fault_ipv4"] = (
                f"{wrong_network.network_address + host_offset}/"
                f"{wrong_network.prefixlen}"
            )
            return bindings

        if value_source == "wrong_prefix":
            prefix_length = int(operation.get("prefix_length", 32))
            bindings["fault_ipv4"] = f"{healthy.ip}/{prefix_length}"
            return bindings

        if value_source == "duplicate_other_subnet":
            candidates = sorted(
                (
                    ref
                    for node in self.topology.endpoint_nodes()
                    if node != bindings["target"]
                    for ref in self.topology.interfaces(node)
                    if ref.ipv4 and ref.segment != interface.segment
                ),
                key=lambda ref: (ref.node, ref.name),
            )
            if not candidates:
                raise ValueError(
                    f"method {method.get('name')!r} found no IPv4 address to duplicate"
                )
            direct_peers = {
                str(requirement[peer])
                for requirement in self.topology.topology.get(
                    "connectivity_requirements", []
                )
                for endpoint, peer in (
                    ("source", "destination"),
                    ("destination", "source"),
                )
                if requirement.get(endpoint) == bindings["target"]
            }
            non_peer_candidates = [
                ref for ref in candidates if ref.node not in direct_peers
            ]
            duplicate = (non_peer_candidates or candidates)[0]
            bindings["fault_ipv4"] = duplicate.ipv4
            bindings["duplicate_source"] = duplicate.node
            return bindings

        raise ValueError(
            f"method {method.get('name')!r} has unsupported value_source "
            f"{value_source!r}"
        )

    def _bind(self, strategy: str, scenario: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        handler = getattr(self, f"_bind_{strategy}", None)
        if handler is None:
            raise ValueError(f"unsupported binding strategy '{strategy}'")
        return handler(scenario)

    def _bind_router_subnet(self, scenario: dict[str, Any]):
        router = self._choose_router(scenario, minimum_interfaces=2)
        refs = self._candidate_interfaces(router, scenario)
        ref = self.rng.choice(refs)
        segment = self.topology.segments[ref.segment]
        return {
            "router": router,
            "interface": ref.name,
            "segment": ref.segment,
            "affected_nodes": self.topology.segment_hosts(ref.segment),
            "subnet": segment.get("cidr"),
        }, []

    def _bind_router_interface(self, scenario: dict[str, Any]):
        router = self._choose_router(scenario, minimum_interfaces=2)
        ref = self.rng.choice(self._candidate_interfaces(router, scenario))
        return {
            "router": router,
            "interface": ref.name,
            "segment": ref.segment,
            "affected_nodes": self.topology.segment_hosts(ref.segment),
            "original_ip": ref.ipv4,
            "subnet": self.topology.segments[ref.segment].get("cidr"),
        }, []

    def _bind_router_two_subnets(self, scenario: dict[str, Any]):
        router = self._choose_router(scenario, minimum_interfaces=2)
        refs = self.topology.interfaces(router)
        del_ref, add_ref = self.rng.sample(refs, 2)
        # The original scenario family couples subnets and router interfaces. Preserve that
        # coupling instead of selecting unrelated abstract segments.
        from_segment = del_ref.segment
        to_segment = add_ref.segment
        to_ip = add_ref.ipv4.split("/")[0] if add_ref.ipv4 else self.topology.segments[to_segment]["cidr"].split("/")[0]
        return {
            "router": router,
            "from_segment": from_segment,
            "to_segment": to_segment,
            "from_subnet": self.topology.segments[from_segment].get("cidr"),
            "to_subnet": self.topology.segments[to_segment].get("cidr"),
            "del_interface": del_ref.name,
            "add_interface": add_ref.name,
            "to_ip": to_ip,
        }, []

    def _choose_router(self, scenario: dict[str, Any], minimum_interfaces: int) -> str:
        selector = scenario.get("extra_info", {}).get("binding", {})
        candidates = [
            node
            for node in self.topology.nodes_with_capability("ipv4_forwarding")
            if len(self.topology.interfaces(node)) >= minimum_interfaces
        ]
        if selector.get("router_scope") == "gateway":
            candidates = [
                node
                for node in candidates
                if "gateway" in self.topology.nodes[node].get("roles", [])
            ]
        if selector.get("interface_scope") == "endpoint_access":
            candidates = [
                node
                for node in candidates
                if any(
                    self.topology.segment_hosts(ref.segment)
                    for ref in self.topology.interfaces(node)
                )
            ]
        if not candidates:
            raise ValueError("scenario binding found no compatible IPv4-forwarding node")
        return self.rng.choice(candidates)

    def _candidate_interfaces(self, router: str, scenario: dict[str, Any]):
        refs = self.topology.interfaces(router)
        selector = scenario.get("extra_info", {}).get("binding", {})
        if selector.get("interface_scope") == "endpoint_access":
            refs = [
                ref for ref in refs
                if self.topology.segment_hosts(ref.segment)
            ]
        if not refs:
            raise ValueError(f"scenario binding found no compatible interface on '{router}'")
        return refs

    def _bind_routing_setup(self, scenario: dict[str, Any]):
        endpoints = self.topology.endpoint_nodes()
        if len(endpoints) < 2:
            raise ValueError("routing_setup requires at least two endpoint nodes")
        return {
            "endpoints": endpoints,
            "required_endpoint_pairs": self.topology.required_endpoint_pairs(),
        }, []

    def _bind_qos_assured_bandwidth(self, scenario: dict[str, Any]):
        source_candidates = self.topology.nodes_with_role("employee") or self.topology.nodes_with_role("client")
        if not source_candidates:
            raise ValueError("QoS assured-bandwidth scenario requires a client traffic source")
        source = self.rng.choice(source_candidates)
        background_candidates = [
            n for n in self.topology.nodes_with_role("guest") if n != source
        ] or [
            n for n in self.topology.nodes_with_role("client") if n != source
        ]
        if not background_candidates:
            raise ValueError("QoS assured-bandwidth scenario requires a second traffic source")
        destination_candidates = self.topology.nodes_with_role("application_server") or self.topology.nodes_with_role("server")
        if not destination_candidates:
            raise ValueError("QoS assured-bandwidth scenario requires a traffic destination")
        destination = self.rng.choice(destination_candidates)
        assured = scenario["extra_info"]["binding"].get("assured_bandwidth_mbps", 100)
        return {
            "source": source,
            "background_source": background_candidates[0],
            "destination": destination,
            "assured_bandwidth_mbps": assured,
        }, []

    def _bind_security_same_l2_gateway(self, scenario: dict[str, Any]):
        candidates = self.topology.data_segments_with_host_gateway_and_attacker()
        if not candidates:
            raise ValueError("same-L2 security scenario requires a host, gateway, and attachable attacker")
        segment = self.rng.choice(candidates)
        victim = self.rng.choice(self.topology.segment_hosts(segment))
        gateway = self.rng.choice(self.topology.segment_gateways(segment))
        attacker = self.topology.attachable_attacker(segment)
        return {
            "segment": segment,
            "victim": victim,
            "gateway": gateway,
            "attacker": attacker,
        }, [{"op": "attach_optional_node", "node": attacker, "segment": segment}]

    def _bind_security_management_audit(self, scenario: dict[str, Any]):
        actors = self.topology.nodes_with_role("management_actor")
        devices = self.topology.nodes_with_role("leaf", "spine")
        if not actors or not devices:
            raise ValueError("management audit scenario requires a management actor and network device")
        return {
            "management_actor": self.rng.choice(actors),
            "target_device": self.rng.choice(devices),
        }, []

    def _bind_security_http_path(self, scenario: dict[str, Any]):
        selector = scenario["extra_info"]["binding"]
        _, service = self.topology.service(application=selector.get("application", "http"))
        server = service["node"]
        # Prefer an employee endpoint. nodes_with_role() has OR semantics, so
        # selecting from a mixed employee/client query could otherwise bind a
        # restricted client such as a finance endpoint.
        employees = [n for n in self.topology.nodes_with_role("employee") if n != server]
        clients = [n for n in self.topology.nodes_with_role("client") if n != server]
        candidates = employees or clients
        if not candidates:
            raise ValueError("HTTP disclosure scenario requires a client endpoint")
        client = candidates[0]
        client_segment = self.topology.interfaces(client, exclude_management=False)[0].segment
        gateways = self.topology.segment_gateways(client_segment)
        if not gateways:
            raise ValueError("HTTP disclosure scenario requires an on-path gateway or tap")
        # A passive host on a switched LAN does not automatically receive other
        # hosts' unicast traffic. Use an explicitly on-path network device as
        # the observation point; a future runtime adapter may realize this as
        # a compromised router, mirror/SPAN, or network tap.
        observer = gateways[0]
        return {
            "client": client,
            "server": server,
            "observer": observer,
            "service_port": service["port"],
        }, []

    def _bind_security_external_service(self, scenario: dict[str, Any]):
        selector = scenario["extra_info"]["binding"]
        _, service = self.topology.service(
            application=selector.get("application"),
            transport=selector.get("transport", "tcp"),
        )
        server = service["node"]
        source_zones = set(selector.get("source_zones", ["external"]))
        attacker_segments = [
            name for name, spec in self.topology.segments.items()
            if spec.get("security_zone") in source_zones and self.topology.can_attach_attacker(name)
        ]
        if not attacker_segments:
            raise ValueError("external-service scenario requires an attacker-accessible source zone")
        attacker_segment = self.rng.choice(sorted(attacker_segments))
        attacker = self.topology.attachable_attacker(attacker_segment)
        return {
            "attacker": attacker,
            "attacker_segment": attacker_segment,
            "server": server,
            "service_port": service["port"],
        }, [{"op": "attach_optional_node", "node": attacker, "segment": attacker_segment}]

    def _bind_security_cross_zone_vlan(self, scenario: dict[str, Any]):
        selector = scenario["extra_info"]["binding"]
        source_zones = set(selector.get("source_zones", ["guest"]))
        target_zones = set(selector.get("target_zones", ["finance", "management", "servers"]))
        source_candidates = [
            name for name, spec in self.topology.segments.items()
            if spec.get("security_zone") in source_zones and self.topology.can_attach_attacker(name)
        ]
        if not source_candidates:
            raise ValueError("VLAN isolation scenario requires an attacker-accessible source zone")
        source_segment = self.rng.choice(sorted(source_candidates))
        target_candidates = [
            name for name, spec in self.topology.segments.items()
            if spec.get("security_zone") in target_zones and self.topology.segment_hosts(name)
        ]
        if not target_candidates:
            raise ValueError("VLAN isolation scenario requires a restricted target zone")
        target_segment = self.rng.choice(sorted(target_candidates))
        target_hosts = self.topology.segment_hosts(target_segment)
        attacker = self.topology.attachable_attacker(source_segment)
        return {
            "attacker": attacker,
            "source_segment": source_segment,
            "source_zone": self.topology.security_zone(source_segment),
            "target_segment": target_segment,
            "target_zone": self.topology.security_zone(target_segment),
            "target": self.rng.choice(target_hosts),
        }, [{"op": "attach_optional_node", "node": attacker, "segment": source_segment}]
