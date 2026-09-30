"""Agent-Network Interface (ANI) v0.1 for ContainerLab environments.

The ANI exposes generic discovery, configuration, validation, and rollback
operations. Scenario compilers and benchmark oracles remain private to the
Judge; an ANI client can only learn from the live network it explicitly queries.
"""
from __future__ import annotations

import ipaddress
import json
import secrets
import re
import shlex
import time
from dataclasses import dataclass, replace
from typing import Any

from .dhcp_clients import renew_dhcp_clients
from .env import ContainerLabEnv
from .executor import NoChangeExecutionError
from .safety import validate_linux_config_command
from .throughput import ASSURED_TOLERANCE, measure_throughput
from .object_writes import (
    ATTRIBUTES as OBJECT_WRITE_ATTRIBUTES, FORWARD_FILTER, OBJECT_WRITE_KINDS, UnsupportedUpdate, compile_update,
)
from .types import CommandResult, LabNode

#: v0.2 (2026-09-17) adds `get_object`, a read of one named object on one node, and
#: `update_object`, its write counterpart: attributes in, native commands out. The
#: version travels in every result, so a record says which surface its agent had.
ANI_VERSION = "ibn_eval.ani.v0.2"

#: Session verbs the ANI reserves for itself. It owns the candidate/commit lifecycle
#: because that is where the transaction id and the rollback record come from.
#:
#: Letting one through is worse than it looks: `run_srl_cli_batch` appends its own
#: `commit now`, so a caller-supplied commit closes the session first and the appended
#: one then fails outside candidate mode. sr_cli exits non-zero *after* the change has
#: been applied, and the operation is reported as a failed action on a network that was
#: in fact reconfigured.
SESSION_COMMANDS = frozenset({"enter", "commit", "discard", "quit", "exit"})
SUPPORTED_WRITE_KINDS = frozenset({"linux", "nokia_srlinux", "vyos"})

ANI_OPERATIONS = (
    "get_topology",
    "get_state",
    "get_running_config",
    "get_object",
    "update_config",
    "update_object",
    "execute_validation",
    "rollback_config",
)

#: What `get_object` can look at. One named thing on one node, whatever the platform:
#: the answer is small where `get_state` and `get_running_config` return whole views,
#: which is what filled a small model's context in E1-E4. The name is an interface, a
#: prefix or address, a MAC address, a ruleset, a zone or a pool, as the node calls it.
OBJECT_KINDS = ("interface", "route", "arp", "mac", "qos", "firewall_rule", "zone", "dhcp_pool")

#: How each platform answers for one object: a `scoped` command names the object
#: itself; a `filter` command lists the view and the ANI keeps the lines that mention
#: the object, with a few lines of context where the output is a tree. Missing entry:
#: the platform has no such objects (a Linux host has no zones).
_OBJECT_READS: dict[str, dict[str, tuple[str, str, int]]] = {
    "interface": {
        # The show report (10 lines: up/down, subinterfaces, addresses) rather than the
        # state tree (about 200 lines for one port) -- the point of this read is size.
        "nokia_srlinux": ("scoped", "show interface {name}", 0),
        "vyos": ("scoped", "show interfaces ethernet {name}", 0),
        "linux": ("scoped", "ip address show dev {name}", 0),
    },
    "route": {
        # SR Linux 26.7 has no per-prefix report ("show ... prefix" is rejected, and the
        # state path needs every list key): the routes view, kept to the prefix's block.
        "nokia_srlinux": ("filter", "info from state network-instance default route-table ipv4-unicast", 8),
        "vyos": ("scoped", "show ip route {name}", 0),
        "linux": ("scoped", "ip route show {name}", 0),
    },
    "arp": {
        "nokia_srlinux": ("scoped", "info from state interface * subinterface * ipv4 arp neighbor {name}", 0),
        "vyos": ("filter", "show arp", 0),
        "linux": ("scoped", "ip neigh show {name}", 0),
    },
    "mac": {
        "nokia_srlinux": ("filter", "info from state network-instance * bridge-table mac-table mac *", 4),
        "linux": ("filter", "ip -o link show", 0),
    },
    "qos": {
        "nokia_srlinux": ("filter", "info from state qos", 6),
        "vyos": ("filter", "show qos shaper", 3),
        "linux": ("scoped", "tc qdisc show dev {name}", 0),
    },
    "firewall_rule": {"vyos": ("config", "show configuration commands", 0)},
    "zone": {"vyos": ("config", "show configuration commands", 0)},
    "dhcp_pool": {"vyos": ("config", "show configuration commands", 0)},
}

#: A `config` read keeps the `set` lines of one configuration subtree: the object's
#: own lines and nothing that merely mentions its name (a zone called DMZ is not every
#: rule set whose name starts with DMZ-). One prefix per kind, `{name}` included.
_VYOS_CONFIG_SUBTREES: dict[str, tuple[str, ...]] = {
    "firewall_rule": ("set firewall ipv4 name {name} ", "set firewall ipv6 name {name} "),
    "zone": ("set firewall zone {name} ",),
    "dhcp_pool": ("set service dhcp-server shared-network-name {name} ",),
}
_OBJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$")

#: The check types ``execute_validation`` accepts, in the order the schema lists them.
VALIDATION_CHECK_TYPES = ("public_success_criteria", "icmp")

#: The criterion types a task's ``success_criteria`` may declare. Each is evaluated
#: inside ``execute_validation`` with type ``public_success_criteria``; none is a check
#: type of its own. Both levels carry a field named ``type`` and both are visible to
#: the model at once (the task payload prints its criteria), so a model naturally
#: copies a criterion type into the check slot. This explicit mirror of the branches
#: in ``evaluate_success_criteria`` lets the rejection name that confusion instead of
#: only refusing it.
PUBLIC_SUCCESS_CRITERION_TYPES = frozenset({
    "lab_connectivity",
    "observed_icmp_service",
    "observed_throughput",
})


def _declared_criterion_types(task: dict[str, Any] | None) -> tuple[str, ...]:
    criteria = (task or {}).get("success_criteria")
    if not isinstance(criteria, dict) or not isinstance(criteria.get("all_of"), list):
        return ()
    return tuple(str(item.get("type") or "") for item in criteria["all_of"]
                 if isinstance(item, dict))


def _unsupported_check_type_message(kind: str, task: dict[str, Any] | None = None) -> str:
    """A rejection that teaches: the supported enum, and where a criterion type belongs."""
    message = (f"unsupported validation check type: {kind}; supported: "
               f"{', '.join(VALIDATION_CHECK_TYPES)}.")
    if kind in _declared_criterion_types(task):
        message += (f" '{kind}' is a success-criterion type from your task's "
                    "success_criteria, not a check type; it is measured when you call "
                    "execute_validation with type public_success_criteria.")
    elif kind in PUBLIC_SUCCESS_CRITERION_TYPES:
        message += (f" '{kind}' is a success-criterion type, not a check type; "
                    "criteria are measured when you call execute_validation with type "
                    "public_success_criteria.")
    return message

#: How long a committed change is given to reach the data plane before the ANI probes
#: for its effect. A validation fired straight after a commit measures the *old* network:
#: on the SME-small lab, an access port brought back up needs about eight seconds before
#: routing follows. The SUT then reads its own correct repair as a failure, and either
#: undoes it or piles a second change on top. The wait is counted from the change itself,
#: so an agent that inspected state in between has already paid it and waits for nothing.
VALIDATION_SETTLE_SECONDS = 10.0

#: Pause before the single re-observation granted to a change that has not converged
#: within the settle window. The Judge polls the same way (`wait_until_healthy`), so this
#: aligns what the SUT believes with what it is scored on. It stays a *single* retry: an
#: unbounded poll would burn the execution budget whenever a change was simply wrong.
VALIDATION_RETRY_INTERVAL_SECONDS = 2.0

# One throughput sample taken by a validation check. Long enough for HTB to reach
# its steady share, short enough that an agent can afford several inside its
# execution budget.
THROUGHPUT_PROBE_SECONDS = 10


class ANIRequestError(ValueError):
    """The caller supplied an unsupported ANI request."""


@dataclass(frozen=True)
class ANITransaction:
    transaction_id: str
    rollback_changes: tuple[dict[str, Any], ...]
    rollback_available: bool
    previous_target_heads: tuple[tuple[str, str | None], ...] = ()
    rolled_back: bool = False
    indeterminate: bool = False
    applied_groups: int = 0


@dataclass(frozen=True)
class _PreparedANIChange:
    """One fully validated change which is safe to consider for execution."""

    node: LabNode
    commands: tuple[str, ...]
    rollback_commands: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class _NativeExecution:
    result: CommandResult
    indeterminate: bool = False
    noop: bool = False


class ContainerLabANI:
    """Generic, guarded ANI implementation backed by :class:`ContainerLabEnv`.

    ``topology_document`` is an optional reviewed abstract topology descriptor.
    It may describe legitimate nodes, links, roles, and capabilities, but never
    contains a compiled scenario, fault binding, or evaluator oracle data.
    """

    def __init__(
        self,
        env: ContainerLabEnv,
        *,
        topology_document: dict[str, Any] | None = None,
    ) -> None:
        self.env = env
        self.topology_document = topology_document or {}
        # How many operations this ANI will perform in one episode, or None for no
        # cap. Counted here rather than in the subject's loop because this is the
        # boundary the cap is named after: a subject tool that reads the devices and
        # writes them on the way to answering one model call spends the budget too,
        # and a cap a subject's own tools could walk around would bound nothing.
        self.call_budget: int | None = None
        self.calls_made = 0
        self.calls_refused = 0
        self._transactions: dict[str, ANITransaction] = {}
        # Per-device compare-and-swap heads make compensations LIFO on each target.
        # A transaction on a different device remains independent, while an older
        # compensation can never overwrite a newer change to the same device.
        self._target_transaction_heads: dict[str, str] = {}
        # Monotonic stamp of the last applied change, or None once a validation has
        # settled for it. One change earns one settle, not one per validation call.
        self._change_applied_at: float | None = None

    @staticmethod
    def tool_contract() -> list[dict[str, str]]:
        return [
            {
                "name": "get_topology",
                "type": "read",
                "description": "Discover reviewed nodes, links, roles, and capabilities in the live lab.",
            },
            {
                "name": "get_state",
                "type": "read",
                "description": "Read operational interface, route, traffic-control, or system state for selected nodes.",
            },
            {
                "name": "get_running_config",
                "type": "read",
                "description": "Read active configuration from selected nodes and paths, as CLI text or as whole-device structured JSON.",
            },
            {
                "name": "get_object",
                "type": "read",
                "description": "Read one named object on one node: an interface, a route, an ARP or MAC entry, a QoS policy, a firewall ruleset, a zone or a DHCP pool, as the node calls it.",
            },
            {
                "name": "update_config",
                "type": "write",
                "description": "Apply guarded native configuration commands to one or more selected nodes.",
            },
            {
                "name": "update_object",
                "type": "write",
                "description": "Change one named object on one node through attributes (interface state, address, MTU; route next hop; routing instance; ACL; DHCP pool; firewall rule set; zone; traffic shaping); the ANI writes the native commands, with the same safety checks, transaction and rollback as update_config.",
            },
            {
                "name": "execute_validation",
                "type": "read",
                "description": "Check the public task success criteria (type public_success_criteria evaluates every declared criterion, including throughput, and is the only validation the completion gate accepts) or run icmp probes on one path.",
            },
            {
                "name": "rollback_config",
                "type": "write",
                "description": "Apply caller-provided compensating changes recorded for a previous ANI transaction.",
            },
        ]

    @staticmethod
    def tool_schemas() -> list[dict[str, Any]]:
        """OpenAI/LiteLLM function schemas for the ANI v0.1 operations."""
        return [
            _function_schema(
                "get_topology",
                "Discover the network topology. Omit nodes to inspect the whole reviewed lab.",
                {
                    "type": "object",
                    "properties": {"nodes": _string_array("Optional node names to include.")},
                },
            ),
            _function_schema(
                "get_state",
                "Read live operational state. Use this before changing configuration.",
                {
                    "type": "object",
                    "properties": {
                        "nodes": _string_array("Optional node names; omit for all nodes."),
                        "views": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["interfaces", "routes", "qdisc", "system"]},
                            "description": "Operational state categories. Omit for interfaces and routes.",
                        },
                    },
                },
            ),
            _function_schema(
                "get_running_config",
                "Read active configuration. For SR Linux, paths are datastore paths such as '/ interface ethernet-1/40'.",
                {
                    "type": "object",
                    "properties": {
                        "nodes": _string_array("One or more node names."),
                        "paths": _string_array("Optional configuration paths; omit for the default running view."),
                        "format": {
                            "type": "string",
                            "enum": ["text", "json"],
                            "description": (
                                "Output form. 'text' (default) returns CLI output for the requested paths. "
                                "'json' ignores paths and returns whole-device data as parsed JSON: the running "
                                "configuration plus the interface, LLDP, network-instance, system and platform "
                                "state subtrees. SR Linux only."
                            ),
                        },
                    },
                    "required": ["nodes"],
                },
            ),
            _function_schema(
                "get_object",
                "Read one object on one node, by name: small output, use it instead of a whole view "
                "once you know what you are looking at. Kinds: interface (name as the node calls it, "
                "e.g. ethernet-1/40, eth1), route (prefix such as 10.10.40.0/24), arp (IPv4 address), "
                "mac (MAC address), qos (interface name), firewall_rule (ruleset name), zone (zone name), "
                "dhcp_pool (pool name).",
                {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "One node name."},
                        "kind": {"type": "string", "enum": list(OBJECT_KINDS), "description": "What the name denotes."},
                        "name": {"type": "string", "description": "The object's name on that node."},
                    },
                    "required": ["node", "kind", "name"],
                },
            ),
            _function_schema(
                "update_config",
                "Apply one or more scoped native commands. Send only configuration commands: the ANI opens the "
                "configuration session and commits it for you, so do not include 'enter', 'commit', 'discard', "
                "'quit' or 'exit'. Do not include shell wrappers, sr_cli, semicolons, or destructive commands. "
                "Supply rollback_commands when a safe compensation is known.",
                {
                    "type": "object",
                    "properties": {
                        "changes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "target": {"type": "string"},
                                    "commands": _string_array("Native commands for this node."),
                                    "rollback_commands": _string_array("Optional compensating native commands."),
                                    "reason": {"type": "string"},
                                },
                                "required": ["target", "commands"],
                            },
                        },
                    },
                    "required": ["changes"],
                },
            ),
            _function_schema(
                "update_object",
                "Change one object on one node by attributes instead of native commands: the ANI "
                "compiles them for the node's platform and applies them as one transaction (same "
                "safety checks and rollback as update_config). Kinds and their attributes: "
                + "; ".join(f"{kind}: " + ", ".join(f"{attr} ({meaning})" for attr, meaning in attrs.items())
                            for kind, attrs in OBJECT_WRITE_ATTRIBUTES.items())
                + ". Not every kind exists on every platform; the answer says so by name.",
                {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "One node name."},
                        "kind": {"type": "string", "enum": list(OBJECT_WRITE_KINDS), "description": "What the name denotes."},
                        "name": {"type": "string", "description": "The object's name on that node (interface, prefix or 'default', instance, filter, pool, rule set, zone)."},
                        "set": {"type": "object", "description": "The attributes to change and their values.",
                                "additionalProperties": True},
                        "reason": {"type": "string", "description": "Optional: the evidence behind the change."},
                    },
                    "required": ["node", "kind", "name", "set"],
                },
            ),
            _function_schema(
                "execute_validation",
                "Run active validation. Type public_success_criteria evaluates every criterion in the task's success_criteria (including a real throughput measurement under offered load where one is declared) and is the only validation the completion gate accepts. It is slow: after a change it first waits out a settle window of about ten seconds, then probes every declared pair and, for a throughput criterion, runs a twelve-second measurement; budget up to about a minute per call and do not repeat it idly. Type icmp probes one chosen path.",
                {
                    "type": "object",
                    "properties": {
                        "checks": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": list(VALIDATION_CHECK_TYPES)},
                                    "source": {"type": "string"},
                                    "destination": {"type": "string"},
                                    "destination_ip": {"type": "string"},
                                    "count": {"type": "integer", "minimum": 1, "maximum": 20},
                                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 10},
                                },
                                "required": ["type"],
                            },
                        },
                    },
                    "required": ["checks"],
                },
            ),
            _function_schema(
                "rollback_config",
                "Roll back one prior ANI transaction which recorded compensating commands.",
                {
                    "type": "object",
                    "properties": {"transaction_id": {"type": "string"}},
                    "required": ["transaction_id"],
                },
            ),
        ]

    def dispatch(
        self,
        operation: str,
        arguments: dict[str, Any] | None,
        *,
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one ANI operation and return structured, JSON-safe evidence."""
        args = arguments or {}
        if self.call_budget is not None and self.calls_made >= self.call_budget:
            # Refused before the lab sees it, and marked as never dispatched so no
            # counter files it as a read, a write or a validation that happened.
            self.calls_refused += 1
            return {
                "ok": False,
                "operation": operation,
                "dispatched": False,
                "error": (
                    f"ANI interaction limit reached: {self.call_budget} operation(s) allowed "
                    f"in this run and {self.calls_made} performed. No further ANI operation "
                    "will be dispatched; finish with what you have."
                ),
            }
        self.calls_made += 1
        if operation == "get_topology":
            return self.get_topology(args.get("nodes"))
        if operation == "get_state":
            return self.get_state(args.get("nodes"), args.get("views"))
        if operation == "get_running_config":
            return self.get_running_config(args.get("nodes"), args.get("paths"), args.get("format", "text"))
        if operation == "get_object":
            return self.get_object(args.get("node"), args.get("kind"), args.get("name"))
        if operation == "update_config":
            return self.update_config(args.get("changes"))
        if operation == "update_object":
            return self.update_object(args.get("node"), args.get("kind"), args.get("name"), args.get("set"),
                                      reason=args.get("reason"))
        if operation == "execute_validation":
            return self.execute_validation(args.get("checks"), task=task or {})
        if operation == "rollback_config":
            return self.rollback_config(args.get("transaction_id"))
        raise ANIRequestError(f"unsupported ANI operation: {operation}")

    def budget_state(self) -> dict[str, Any]:
        """What the ANI budget allowed, spent and refused this episode."""
        return {
            "limit": self.call_budget,
            "used": self.calls_made,
            "refused": self.calls_refused,
            "reached": self.call_budget is not None and self.calls_made >= self.call_budget,
        }

    def node_roles(self, name: str) -> list[str]:
        """Roles the abstract descriptor gives a node, or [] when it declares none.

        The same field `get_topology` reports, reachable without going through an ANI
        call. It is what separates a Linux node that forwards -- the shaped WAN edge --
        from the Linux endpoints of the same lab, which are hosts rather than network
        elements.
        """
        topology = self.topology_document.get("topology") or {}
        declared = {**(topology.get("nodes") or {}), **(topology.get("optional_nodes") or {})}
        return [str(role) for role in (declared.get(name) or {}).get("roles") or []]

    def get_topology(self, nodes: Any = None) -> dict[str, Any]:
        selected = self._selected_nodes(nodes)
        topology = self.topology_document.get("topology") or {}
        declared_nodes = {**(topology.get("nodes") or {}), **(topology.get("optional_nodes") or {})}
        response_nodes: list[dict[str, Any]] = []
        for name in selected:
            node = self.env.config.nodes[name]
            declared = declared_nodes.get(name) or {}
            interfaces = []
            for interface, details in (declared.get("interfaces") or {}).items():
                interfaces.append({
                    "name": str(interface),
                    "segment": (details or {}).get("segment"),
                })
            response_nodes.append({
                "name": name,
                "kind": node.kind,
                "roles": list(declared.get("roles") or []),
                "capabilities": list(declared.get("capabilities") or []),
                "interfaces": interfaces,
            })

        segments = []
        for name, segment in (topology.get("segments") or {}).items():
            members = [str(member) for member in segment.get("members") or []]
            if nodes is None or any(member in selected for member in members):
                segments.append({
                    "name": str(name),
                    "type": segment.get("type"),
                    "members": members,
                })
        return {"ok": True, "operation": "get_topology", "nodes": response_nodes, "segments": segments}

    def get_state(self, nodes: Any = None, views: Any = None) -> dict[str, Any]:
        selected = self._selected_nodes(nodes)
        requested_views = self._views(views)
        records: list[dict[str, Any]] = []
        ok = True
        for name in selected:
            node = self.env.config.nodes[name]
            values: dict[str, Any] = {}
            for view in requested_views:
                result = self._state_command(node.kind, name, view)
                ok = ok and result.ok
                values[view] = self._result_dict(result)
            records.append({"name": name, "kind": node.kind, "views": values})
        return {"ok": ok, "operation": "get_state", "nodes": records}

    def get_object(self, node_name: Any, kind: Any, name: Any) -> dict[str, Any]:
        """One named object on one node, read with the narrowest command the platform has.

        The answer for an object that does not exist is `found: false` with the node's
        own words, not an error: not finding a route is an observation the agent is
        entitled to make. An error is reserved for a request the ANI cannot form: an
        unknown node, an unknown kind, a name with characters no object has.
        """
        node = self._node(str(node_name or "").strip())
        kind = str(kind or "").strip()
        name = str(name or "").strip()
        if kind not in OBJECT_KINDS:
            raise ANIRequestError(
                f"get_object.kind must be one of {', '.join(OBJECT_KINDS)}; got {kind!r}")
        if not _OBJECT_NAME.match(name):
            raise ANIRequestError(
                "get_object.name must be the object's name as the node writes it "
                "(letters, digits, '.', '_', ':', '/', '-'; at most 80 characters)")
        reads = _OBJECT_READS[kind].get(node.kind)
        if reads is None:
            return {"ok": False, "operation": "get_object", "node": node.name, "kind": kind, "name": name,
                    "found": False, "command": None, "output": "",
                    "error": f"a {node.kind} node has no {kind} objects to read"}
        mode, template, context = reads
        if kind == "interface" and node.kind == "vyos" and not name.startswith("eth"):
            # Only Ethernet ports have a scoped report on VyOS; anything else is found
            # in the interface list.
            mode, template, context = "filter", "show interfaces", 0
        if kind == "route" and name.lower() == "default":
            # The default route under the name every platform's operator uses for it.
            name = "default" if node.kind == "linux" else "0.0.0.0/0"
        elif kind == "route" and node.kind == "linux" and "/" not in name:
            # A bare address is a lookup (the route that would carry it), a prefix is a
            # table entry; `ip route show <address>` answers nothing for the former.
            template = "ip route get {name}"
        command = template.replace("{name}", name)
        result = self._object_command(node, command)
        output = result.stdout or ""
        if mode == "filter":
            output = _lines_mentioning(output, name, context)
        elif mode == "config":
            subtrees = _VYOS_CONFIG_SUBTREES[kind]
            if kind == "firewall_rule" and name == FORWARD_FILTER:
                # The base chain update_object writes under that name, not a rule set.
                subtrees = ("set firewall ipv4 forward filter ",)
            prefixes = tuple(prefix.replace("{name}", name) for prefix in subtrees)
            output = "\n".join(line for line in output.splitlines() if line.startswith(prefixes))
        # "% Network not in table" and its kin: the node's way of saying there is no
        # such object, printed on stdout with a zero exit code.
        found = result.ok and bool(output.strip()) and not output.lstrip().startswith("%")
        return {
            "ok": result.ok, "operation": "get_object", "node": node.name, "kind": kind, "name": name,
            "found": found, "mode": mode, "command": command, "output": output,
            "stderr": result.stderr or "", "returncode": result.returncode,
            **({} if result.ok else {"error": (result.stderr or result.reason or "command failed").strip()[:200]}),
        }

    def update_object(self, node_name: Any, kind: Any, name: Any, changes: Any, *,
                      reason: Any = None) -> dict[str, Any]:
        """Change one named object through attributes; the commands are the ANI's.

        Compiled by `object_writes.compile_update` for the node's platform and applied
        through `update_config`, so the safety preflight, the transaction, the ledger
        and `rollback_config` are the ones every other write gets. On Linux each
        command is its own change (the platform applies one native command per
        change); on SR Linux and VyOS the commands are one candidate, committed once.
        A request the ANI cannot form (unknown attribute, a value that is not an
        address, a kind the platform does not have) is refused by name before any
        device sees it.
        """
        node = self._node(str(node_name or "").strip())
        kind = str(kind or "").strip()
        if node.kind not in SUPPORTED_WRITE_KINDS:
            raise ANIRequestError(f"unsupported write platform {node.kind!r} for target {node.name!r}")
        try:
            compiled = compile_update(node.kind, kind, str(name or "").strip(), changes)
        except UnsupportedUpdate as exc:
            raise ANIRequestError(f"update_object: {exc}") from exc
        why = str(reason or "").strip() or f"update_object {compiled.summary}"
        # A step whose compensation is derived from the request is only right when the
        # request changes something: read the object first and drop the steps that would
        # change nothing. That also spares SR Linux a candidate with nothing to commit,
        # which it reports as a failure.
        steps, skipped = [], []
        for step in compiled.steps:
            probe = step.probe
            if probe is not None:
                current = self.get_object(node.name, probe.kind, probe.name)
                if current.get("ok") and current.get("found"):
                    present = probe.marker.lower() in (current.get("output") or "").lower()
                    if present == probe.present_means_noop:
                        skipped.append({"command": step.command, "reason": "already in the requested state"})
                        continue
            steps.append(step)
        summary = {"commands": [s.command for s in steps], "rollback_commands": [], "summary": compiled.summary,
                   "skipped": skipped}
        if not steps:
            return {"ok": True, "operation": "update_object", "node": node.name, "kind": kind,
                    "name": str(name).strip(), "noop": True, "transaction_id": None, "changes": [],
                    "rollback_available": False, "compiled": summary,
                    "message": "nothing to change: the object is already in the requested state"}
        if all(s.rollback for s in steps):
            summary["rollback_commands"] = [s.rollback for s in reversed(steps)]
        if node.kind == "linux":
            prepared = [{"target": node.name, "commands": [s.command],
                         "rollback_commands": [s.rollback] if s.rollback else [], "reason": why} for s in steps]
        else:
            prepared = [{"target": node.name, "commands": summary["commands"],
                         "rollback_commands": summary["rollback_commands"], "reason": why}]
        result = self.update_config(prepared)
        return {
            **result,
            "operation": "update_object",
            "node": node.name, "kind": kind, "name": str(name).strip(),
            "compiled": summary,
        }

    def _object_command(self, node: LabNode, command: str) -> CommandResult:
        if node.kind == "nokia_srlinux":
            return self.env.executor.run_srl_cli(node, command)
        if node.kind == "vyos":
            return self.env.executor.run_vyos_op(node, command)
        return self.env.executor.run_shell(node, command)

    def get_running_config(self, nodes: Any, paths: Any = None, output_format: Any = "text") -> dict[str, Any]:
        requested_format = str(output_format or "text").lower()
        if requested_format not in {"text", "json"}:
            raise ANIRequestError("get_running_config.format must be 'text' or 'json'")
        selected = self._selected_nodes(nodes, required=True)
        if requested_format == "json":
            return self._structured_device_data(selected)
        selected_paths = self._configuration_paths(paths)
        records: list[dict[str, Any]] = []
        ok = True
        for name in selected:
            node = self.env.config.nodes[name]
            values: list[dict[str, Any]] = []
            for path in selected_paths:
                if node.kind == "nokia_srlinux":
                    command = f"info from running {path}" if path != "/" else "info from running /"
                    result = self.env.executor.run_srl_cli(node, command)
                elif node.kind == "vyos":
                    # Without this the Linux fallback below would answer with
                    # addresses and routes, hiding the zones and policies that
                    # are the whole point of a firewall node.
                    config_path = self._vyos_configuration_tokens(path)
                    result = self.env.executor.run_vyos_show_config(node, config_path)
                    command = shlex.join(["show", *config_path])
                else:
                    command = "ip address show && ip route show"
                    # Run separately because ANI never sends compound shell commands.
                    address = self.env.executor.run_shell(node, "ip address show")
                    routes = self.env.executor.run_shell(node, "ip route show")
                    result = CommandResult(
                        target=name,
                        command=command,
                        returncode=max(address.returncode, routes.returncode),
                        stdout=f"{address.stdout}{routes.stdout}",
                        stderr=f"{address.stderr}{routes.stderr}",
                        safe=address.safe and routes.safe,
                    )
                ok = ok and result.ok
                values.append({"path": path, "result": self._result_dict(result)})
            records.append({"name": name, "kind": node.kind, "config": values})
        return {"ok": ok, "operation": "get_running_config", "nodes": records}

    #: SR Linux state subtrees returned by the JSON format, and the sr_cli command
    #: that yields each. The `*` is required on list nodes; without it sr_cli parses
    #: the following token as a leaf filter and rejects `| as json`.
    _SRL_JSON_VIEWS = {
        "system": "info from state system information | as json",
        "platform": "info from state platform chassis | as json",
        "interface": "info from state /interface * | as json",
        "lldp": "info from state system lldp | as json",
        "network_instance": "info from state /network-instance * | as json",
    }
    _SRL_JSON_RUNNING = "info from running | as json"

    #: VyOS states its whole configuration as one JSON document: zones, rulesets, NAT
    #: rules, QoS shapers. There is no per-subtree equivalent worth fetching: the tree
    #: is small and one call keeps it consistent.
    _VYOS_JSON_CONFIG = "show configuration json"

    #: Facts VyOS keeps out of the configuration tree, as `<document>: <op command>`.
    #: `show version` carries the hardware vendor and model, which is the only place the
    #: chassis of a VyOS node is stated.
    #:
    #: `show ip route` is the FIB, and it is there for the exit interface. The
    #: configuration declares a next-hop alone ('protocols static route <CIDR>
    #: next-hop <IP>'); the interface that next-hop resolves to appears only once the
    #: route is installed, as `via 10.255.1.1, eth1`.
    #: `show arp` is what places a host behind the firewall on the topology: the DMZ
    #: servers (web1, app1, dns-dmz) have the firewall as their gateway, so only its ARP
    #: table learns their MAC addresses.
    _VYOS_DOCUMENTS = {"version_data.json": "show version",
                       "routes_data.json": "show ip route",
                       "arp_data.json": "show arp"}

    #: A containerlab `linux` node answers to no management API: its configuration lives
    #: in the kernel and is read with the commands an operator would type. Keyed by name
    #: rather than by position, as the SR Linux views are.
    _LINUX_NODE_COMMANDS = {
        "os_release": "cat /etc/os-release",
        "kernel": "uname -sr",
        "link": "ip -o link show",
        "addr": "ip -o -4 addr show",
        "addr6": "ip -o -6 addr show",
        "route": "ip -o -4 route show",
        # Listening TCP/UDP sockets: which service a node runs, and on which address --
        # bind on port 53 is what makes `dns-int` a resolver. A bind is what the daemon
        # was configured to listen on, not a traffic counter: no `-i`/`-e`/`-m`, so no
        # queue or byte statistics.
        "sockets": "ss -H -l -n -t -u",
    }

    #: Run once per interface. `tc` without `-s` on purpose: `-s` adds sent bytes, drops
    #: and backlog, which are measurements of traffic rather than statements about the
    #: configuration, and a configuration read reports configuration only.
    _LINUX_INTERFACE_COMMANDS = {
        "qdisc": "tc qdisc show dev",
        "class": "tc class show dev",
        "filter": "tc filter show dev",
    }

    #: `lo` is part of no topology. The management interface IS collected: the parsers
    #: read its address to fill `management_ip`.
    _LINUX_SKIP_INTERFACES = frozenset({"lo"})

    def _structured_device_data(self, selected: list[str]) -> dict[str, Any]:
        """Return whole-device data as parsed JSON rather than CLI text.

        Text output has to be scraped before anything can reason over it. This
        returns the running configuration plus the operational subtrees that
        expose neighbours, ARP and interface state, which is what a consumer
        building a structured model of the network needs.

        Three platforms answer, each in the only structured form it has:

        * **SR Linux** — native YANG, `state` per subtree plus the running datastore.
        * **VyOS** — the configuration tree from `show configuration json`, returned as
          `running`. `state` stays empty: VyOS has no operational datastore to mirror it,
          and the facts it keeps outside the tree come back as `documents` instead.
        * **Linux** — no management API at all, so `running` carries the text output of
          the commands an operator would type, keyed by name. It is configuration only:
          see `_LINUX_INTERFACE_COMMANDS` on why `tc` runs without `-s`.

        A kind none of the three covers is reported as unsupported rather than returned
        half-parsed. `documents` is optional and platform-specific; a caller that does
        not know a document simply ignores it.
        """
        builders = {
            "nokia_srlinux": self._srlinux_structured,
            "vyos": self._vyos_structured,
            "linux": self._linux_structured,
        }
        records: list[dict[str, Any]] = []
        ok = True
        for name in selected:
            node = self.env.config.nodes[name]
            builder = builders.get(node.kind)
            if builder is None:
                ok = False
                records.append({
                    "name": name,
                    "kind": node.kind,
                    "ok": False,
                    "error": f"json format is not available for {node.kind} nodes",
                })
                continue
            record = builder(node)
            ok = ok and record["ok"]
            records.append({"name": name, "kind": node.kind, **record})
        return {"ok": ok, "operation": "get_running_config", "format": "json", "nodes": records}

    def _srlinux_structured(self, node: LabNode) -> dict[str, Any]:
        state: dict[str, Any] = {}
        errors: list[str] = []
        for view, command in self._SRL_JSON_VIEWS.items():
            parsed, error = self._srl_json(node, command)
            if error:
                errors.append(f"{view}: {error}")
            else:
                state[view] = parsed
        running, running_error = self._srl_json(node, self._SRL_JSON_RUNNING)
        if running_error:
            errors.append(f"running: {running_error}")
        return {"ok": not errors, "state": state, "running": running,
                "error": "; ".join(errors) or None}

    def _vyos_structured(self, node: LabNode) -> dict[str, Any]:
        """The VyOS configuration tree, plus the facts it keeps outside that tree."""
        errors: list[str] = []
        result = self.env.executor.run_vyos_op(node, self._VYOS_JSON_CONFIG,
                                               timeout_seconds=180)
        running: Any = None
        if not result.ok:
            errors.append(f"running: {(result.stderr or result.reason or 'command failed').strip()[:200]}")
        else:
            try:
                running = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                errors.append(f"running: not JSON ({exc.msg})")

        documents: dict[str, Any] = {}
        for filename, command in self._VYOS_DOCUMENTS.items():
            doc = self.env.executor.run_vyos_op(node, command, timeout_seconds=60)
            if doc.ok:
                documents[filename] = {"command": command, "output": doc.stdout}
            else:
                # Not fatal: the configuration tree carries most of what the node
                # reports, and one missing document costs a few attributes rather than
                # the whole node.
                errors.append(f"{filename}: {(doc.stderr or 'command failed').strip()[:120]}")

        return {"ok": running is not None, "state": {}, "running": running,
                "documents": documents, "error": "; ".join(errors) or None}

    def _linux_structured(self, node: LabNode) -> dict[str, Any]:
        """Configured state of a Linux node, read command by command.

        A command that fails is not fatal: `tc` on an interface with no qdisc, or a
        missing binary in a slimmer image, is a normal absence rather than an error. The
        node fails only when it names no interface at all, which is what a container that
        is not running looks like.
        """
        data: dict[str, Any] = {}
        command_ok: dict[str, bool] = {}
        for key, command in self._LINUX_NODE_COMMANDS.items():
            result = self.env.executor.run_shell(node, command)
            data[key] = result.stdout if result.ok else ""
            # An empty successful `ip ... show` proves an empty collection; an empty
            # string after command failure proves nothing. Native-seat evidence needs
            # that distinction before it may add or remove addresses and routes.
            command_ok[key] = result.ok
        data["_command_ok"] = command_ok

        interfaces: dict[str, dict[str, Any]] = {}
        for iface in self._linux_interface_names(data.get("link", "")):
            values: dict[str, Any] = {}
            command_ok: dict[str, bool] = {}
            for key, command in self._LINUX_INTERFACE_COMMANDS.items():
                result = self.env.executor.run_shell(node, f"{command} {iface}")
                values[key] = result.stdout if result.ok else ""
                # Empty stdout is a valid, authoritative answer for a class/filter
                # query, while a failed query used to collapse to the same string.
                # Native-seat reconciliation must distinguish proven vacancy from
                # missing evidence before it may add or destructively replace state.
                command_ok[key] = result.ok
            values["_command_ok"] = command_ok
            interfaces[iface] = values
        data["interfaces"] = interfaces

        if not interfaces:
            return {"ok": False, "state": {}, "running": data,
                    "error": "no interface reported — is the container running?"}
        return {"ok": True, "state": {}, "running": data, "error": None}

    @classmethod
    def _linux_interface_names(cls, link_output: str) -> list[str]:
        """Interface names from `ip -o link show`, loopback aside.

        A line starts with "<index>: <name>[@<parent>]:", and the `@` suffix names the
        peer of a veth rather than part of the interface name.
        """
        names = []
        for line in link_output.splitlines():
            head = line.split(":", 2)
            if len(head) < 2:
                continue
            name = head[1].strip().split("@")[0]
            if name and name not in cls._LINUX_SKIP_INTERFACES:
                names.append(name)
        return names

    def _srl_json(self, node: LabNode, command: str) -> tuple[Any, str | None]:
        result = self.env.executor.run_srl_cli(node, command, timeout_seconds=180)
        if not result.ok:
            return None, (result.stderr or result.reason or "command failed").strip()[:200]
        try:
            return json.loads(result.stdout), None
        except json.JSONDecodeError as exc:
            return None, f"sr_cli did not return JSON: {exc}"

    def update_config(self, changes: Any) -> dict[str, Any]:
        prepared = self._prepare_update_changes(changes)
        transaction_id = f"ani_tx_{secrets.token_hex(8)}"
        applied: list[dict[str, Any]] = []
        rollback_changes: list[dict[str, Any]] = []
        previous_target_heads: dict[str, str | None] = {}
        successful_groups = 0
        fully_reversible = True
        indeterminate = False
        for change in prepared:
            commands = list(change.commands)
            execution = self._execute_native_commands(change.node, commands)
            result = execution.result
            applied.append({
                "target": change.node.name,
                "commands": commands,
                "reason": change.reason,
                "result": self._result_dict(result),
                **({"noop": True} if execution.noop else {}),
            })
            if result.ok or execution.indeterminate:
                if change.node.name not in previous_target_heads:
                    previous_target_heads[change.node.name] = (
                        self._target_transaction_heads.get(change.node.name))
                self._target_transaction_heads[change.node.name] = transaction_id
            if not result.ok:
                indeterminate = execution.indeterminate
                break
            successful_groups += 1
            if change.rollback_commands:
                rollback_changes.append({
                    "target": change.node.name,
                    "commands": list(change.rollback_commands),
                })
            else:
                fully_reversible = False
        rollback_available = (
            successful_groups > 0 and fully_reversible and not indeterminate)
        self._transactions[transaction_id] = ANITransaction(
            transaction_id=transaction_id,
            rollback_changes=tuple(rollback_changes),
            rollback_available=rollback_available,
            previous_target_heads=tuple(sorted(previous_target_heads.items())),
            indeterminate=indeterminate,
            applied_groups=successful_groups,
        )
        return {
            "ok": all(item["result"]["ok"] for item in applied),
            "operation": "update_config",
            "transaction_id": transaction_id,
            "changes": applied,
            "rollback_available": rollback_available,
            "indeterminate": indeterminate,
        }

    def _prepare_update_changes(self, changes: Any) -> list[_PreparedANIChange]:
        """Validate the whole request before the first device can be changed."""
        if not isinstance(changes, list) or not changes:
            raise ANIRequestError("update_config requires a non-empty changes list")
        prepared: list[_PreparedANIChange] = []
        for change in changes:
            if not isinstance(change, dict):
                raise ANIRequestError("each update_config change must be an object")
            target = self._required_string(
                change.get("target"), "update_config.target")
            commands = self._strings(change.get("commands"))
            if not commands:
                raise ANIRequestError(
                    "update_config.change.commands must be non-empty")
            rollback_commands = self._strings(change.get("rollback_commands"))
            node = self._node(target)
            if node.kind not in SUPPORTED_WRITE_KINDS:
                raise ANIRequestError(
                    f"unsupported write platform {node.kind!r} for target {node.name!r}")
            self._validate_native_commands(commands)
            self._validate_native_commands(rollback_commands)
            if node.kind == "nokia_srlinux":
                for label, batch in (
                    ("update_config.change.commands", commands),
                    ("update_config.change.rollback_commands", rollback_commands),
                ):
                    if not batch:
                        continue
                    try:
                        self.env.executor.parse_srl_cli_batch(batch)
                    except ValueError as exc:
                        raise ANIRequestError(
                            f"{label} failed backend safety preflight: {exc}") from exc
            elif node.kind == "vyos":
                for label, batch in (
                    ("update_config.change.commands", commands),
                    ("update_config.change.rollback_commands", rollback_commands),
                ):
                    if not batch:
                        continue
                    try:
                        self.env.executor.parse_vyos_cli_batch(batch)
                    except ValueError as exc:
                        raise ANIRequestError(
                            f"{label} failed backend safety preflight: {exc}") from exc
            elif node.kind == "linux":
                for label, batch in (
                    ("update_config.change.commands", commands),
                    ("update_config.change.rollback_commands", rollback_commands),
                ):
                    for command in batch:
                        try:
                            validate_linux_config_command(command)
                        except ValueError as exc:
                            raise ANIRequestError(
                                f"{label} failed backend safety preflight: {exc}") from exc
            self._validate_batch_atomicity(
                node, commands, "update_config.change.commands")
            self._validate_batch_atomicity(
                node, rollback_commands,
                "update_config.change.rollback_commands")
            prepared.append(_PreparedANIChange(
                node=node,
                commands=tuple(commands),
                rollback_commands=tuple(rollback_commands),
                reason=str(change.get("reason") or ""),
            ))
        return prepared

    def rollback_config(self, transaction_id: Any) -> dict[str, Any]:
        identifier = self._required_string(transaction_id, "rollback_config.transaction_id")
        transaction = self._transactions.get(identifier)
        if transaction is None:
            raise ANIRequestError(f"unknown ANI transaction: {identifier}")
        if transaction.rolled_back:
            return {
                "ok": True,
                "operation": "rollback_config",
                "transaction_id": identifier,
                "changes": [],
            }
        if transaction.indeterminate:
            return {
                "ok": False,
                "operation": "rollback_config",
                "transaction_id": identifier,
                "indeterminate": True,
                "error": ("transaction outcome is indeterminate; re-read live state "
                          "and reconcile instead of replaying compensation"),
                "changes": [],
            }
        if transaction.applied_groups == 0:
            # Every change of the transaction was refused by the device before it
            # applied, so the network is as it was: nothing to compensate. Saying
            # "rollback_commands are required" here (the message below) misled the
            # agents that had supplied them (E5 dhcp m1, 2026-09-19).
            return {
                "ok": True,
                "operation": "rollback_config",
                "transaction_id": identifier,
                "changes": [],
                "note": "no change of this transaction was applied; nothing to roll back",
            }
        if not transaction.rollback_available:
            return {
                "ok": False,
                "operation": "rollback_config",
                "transaction_id": identifier,
                "error": ("transaction has no complete rollback: every confirmed "
                          "successful change requires rollback_commands"),
                "changes": [],
            }
        remaining_targets = {
            str(change["target"]) for change in transaction.rollback_changes
        }
        for target in sorted(remaining_targets):
            if self._target_transaction_heads.get(target) != identifier:
                return {
                    "ok": False,
                    "operation": "rollback_config",
                    "transaction_id": identifier,
                    "error": (f"target {target!r} has a newer transaction; stale "
                              "compensation was not applied"),
                    "changes": [],
                }
        applied: list[dict[str, Any]] = []
        remaining = list(transaction.rollback_changes)
        previous_heads = dict(transaction.previous_target_heads)
        while remaining:
            change = remaining[-1]
            target = str(change["target"])
            commands = [str(command) for command in change["commands"]]
            node = self._node(target)
            execution = self._execute_native_commands(node, commands)
            result = execution.result
            applied.append({
                "target": node.name,
                "commands": commands,
                "result": self._result_dict(result),
            })
            if not result.ok:
                if execution.indeterminate:
                    transaction = replace(transaction, indeterminate=True)
                    self._transactions[identifier] = transaction
                break
            remaining.pop()
            if not any(str(item["target"]) == target for item in remaining):
                previous = previous_heads.get(target)
                if previous is None:
                    self._target_transaction_heads.pop(target, None)
                else:
                    self._target_transaction_heads[target] = previous
            transaction = replace(
                transaction,
                rollback_changes=tuple(remaining),
                rolled_back=not remaining,
            )
            # Consume each confirmed compensation immediately. If the next one fails,
            # a retry resumes there instead of replaying already restored state.
            self._transactions[identifier] = transaction
        return {
            "ok": all(item["result"]["ok"] for item in applied),
            "operation": "rollback_config",
            "transaction_id": identifier,
            "changes": applied,
            "indeterminate": transaction.indeterminate,
        }

    def execute_validation(self, checks: Any, *, task: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(checks, list) or not checks:
            raise ANIRequestError("execute_validation requires a non-empty checks list")
        results: list[dict[str, Any]] = []
        for check in checks:
            if not isinstance(check, dict):
                raise ANIRequestError("each validation check must be an object")
            kind = str(check.get("type") or "")
            if kind == "public_success_criteria":
                results.append(self.evaluate_success_criteria(task))
            elif kind == "icmp":
                results.append(self._icmp_probe(check))
            else:
                raise ANIRequestError(_unsupported_check_type_message(kind, task))
        return {"ok": all(result.get("passed") for result in results), "operation": "execute_validation", "checks": results}

    def evaluate_success_criteria(self, task: dict[str, Any]) -> dict[str, Any]:
        criteria = task.get("success_criteria")
        if not isinstance(criteria, dict) or not isinstance(criteria.get("all_of"), list):
            return {"type": "public_success_criteria", "passed": False, "error": "no supported public success_criteria"}
        observation = task.get("observation") if isinstance(task.get("observation"), dict) else {}
        settled_a_change = self._settle_after_change()
        results: list[dict[str, Any]] = []
        for criterion in criteria["all_of"]:
            if not isinstance(criterion, dict):
                results.append({"passed": False, "error": "invalid success criterion"})
                continue
            kind = str(criterion.get("type") or "")
            if kind not in PUBLIC_SUCCESS_CRITERION_TYPES:
                results.append({"type": kind, "passed": False, "error": f"unsupported public success criterion: {kind}"})
                continue
            if kind == "lab_connectivity":
                live = self.env.observe()
                passed = self.env.observation_is_healthy(live)
                if not passed and settled_a_change:
                    # The settle window is a floor, not a guarantee. Re-observe once so a
                    # reconvergence that ran slightly long is not reported as a failed
                    # repair; only a change just applied can turn this around, which is
                    # why an unchanged network is never re-probed.
                    time.sleep(VALIDATION_RETRY_INTERVAL_SECONDS)
                    live = self.env.observe()
                    passed = self.env.observation_is_healthy(live)
                results.append({
                    "type": kind,
                    "passed": passed,
                    "connectivity": [check.__dict__ for check in live.connectivity],
                })
            elif kind == "observed_icmp_service":
                observed_service = (
                    observation.get("service_measurement")
                    or observation.get("qos_service_measurement")
                    or {}
                )
                service = {
                    **(observed_service if isinstance(observed_service, dict) else {}),
                    **{key: criterion.get(key) for key in ("source", "destination", "destination_ip") if criterion.get(key)},
                }
                if any(not service.get(key) for key in ("source", "destination_ip")):
                    results.append({"type": kind, "passed": False, "error": "public service path is incomplete"})
                    continue
                measurement = self._icmp_probe({
                    "type": "icmp",
                    "source": service.get("source"),
                    "destination": service.get("destination"),
                    "destination_ip": service.get("destination_ip"),
                    "count": 3,
                    "timeout_seconds": 3,
                })
                loss_limit = float(criterion.get("max_packet_loss_percent", 0.0))
                rtt_limit = criterion.get("max_rtt_avg_ms")
                rtt = measurement.get("rtt_avg_ms")
                passed = bool(measurement.get("passed")) and float(measurement.get("packet_loss_percent", 100.0)) <= loss_limit
                if rtt_limit is not None:
                    passed = passed and rtt is not None and float(rtt) <= float(rtt_limit)
                results.append({
                    "type": kind,
                    "passed": passed,
                    "measurement": measurement,
                    "limits": {"max_packet_loss_percent": loss_limit, "max_rtt_avg_ms": rtt_limit},
                })
            elif kind == "observed_throughput":
                service = observation.get("service_measurement") or observation.get("qos_service_measurement")
                if not isinstance(service, dict):
                    results.append({"type": kind, "passed": False, "error": "missing public service measurement"})
                    continue
                minimum = criterion.get("min_mbps")
                if minimum is None:
                    results.append({"type": kind, "passed": False, "error": "observed_throughput requires min_mbps"})
                    continue
                # A rate guarantee only means anything while the link is disputed.
                # Where the fault is itself the competing flow the contention is
                # already there, and offering a second one would measure something
                # nobody promised; where the fault degrades a policy on an
                # otherwise idle link, the check has to create it.
                measurement = self._throughput_probe(
                    service,
                    contended=bool(criterion.get("under_contention", False)),
                    seconds=int(criterion.get("seconds", THROUGHPUT_PROBE_SECONDS)),
                )
                # The criterion states the contract; what any measurement can be
                # held to is the contract minus what the measurement itself
                # costs. Enforcing the nominal rate failed an intact policy on
                # the lab -- 7.605 Mbps against a bar of 8 -- so the same band
                # the runner judges on applies here, and both numbers are
                # reported so nothing about the verdict is implicit.
                contract = float(minimum)
                floor = contract * ASSURED_TOLERANCE
                observed = measurement.get("throughput_mbps")
                # The probe's own verdict comes first: a sample the offered
                # contention did not cover carries no rate at all, and an idle
                # link's reading must not clear a bar set for a disputed one.
                passed = (
                    bool(measurement.get("passed"))
                    and observed is not None
                    and float(observed) >= floor
                )
                results.append({
                    "type": kind,
                    "passed": passed,
                    "measurement": measurement,
                    "limits": {
                        "min_mbps": contract,
                        "enforced_floor_mbps": round(floor, 3),
                        "tolerance": ASSURED_TOLERANCE,
                    },
                })
            else:
                results.append({"type": kind, "passed": False, "error": f"unsupported public success criterion: {kind}"})
        outcome: dict[str, Any] = {
            "type": "public_success_criteria",
            "passed": bool(results) and all(item.get("passed") for item in results),
            "checks": results,
        }
        renewal = getattr(self, "_last_lease_renewal", None)
        if settled_a_change and renewal is not None:
            # Named in the answer so the subject's record says its check followed a
            # renewal, which is the only way a DHCP repair can show on the desk.
            outcome["lease_renewal"] = renewal
        return outcome

    def _throughput_probe(
        self,
        service: dict[str, Any],
        *,
        contended: bool,
        seconds: int,
    ) -> dict[str, Any]:
        """Measure the rate one flow achieves, optionally against offered load.

        The sink ports and the competing endpoint are read from the reviewed
        topology rather than from the task, so the validation never has to publish
        the instrument it measures with -- an agent that knew the measured port
        could shape that port instead of repairing the policy, and pass.

        The measurement itself is the runner's: same sink restart, same flood,
        same liveness checks and the same fail-closed verdict, so an agent is
        held to one instrument whichever oracle happens to run. What comes back
        is filtered before the agent sees it: the ports are in the client's own
        output and in the flood's report, and neither is published.
        """
        source = str(service.get("source") or "")
        destination = str(service.get("destination") or "")
        sink_ip = str(service.get("destination_ip") or "")
        if source not in self.env.config.nodes or destination not in self.env.config.nodes:
            return {"passed": False, "error": "unknown measurement endpoint"}
        port = self._iperf_port(destination, "measurement")
        if port is None or not sink_ip:
            return {"passed": False, "error": f"no measurement sink declared on '{destination}'"}

        load_source = load_port = None
        if contended:
            load_port = self._iperf_port(destination, "load")
            load_source = self._contention_source(source, destination)
            if load_source is None or load_port is None:
                return {"passed": False, "error": "no contention source or load sink declared"}

        result = measure_throughput(
            self.env,
            {
                "protected_source": source,
                "sink_ip": sink_ip,
                "measurement_port": port,
                "background_source": load_source,
                "load_port": load_port,
                "link_mbps": self._shaped_link_mbps(),
                "destination": destination,
                "destination_ip": sink_ip,
            },
            seconds=seconds,
            contended=contended,
        )
        return {
            "source": source,
            "destination": destination,
            "destination_ip": sink_ip,
            "seconds": seconds,
            "contended": contended,
            "timed_out": bool(result.get("timed_out")),
            "contention_observed": result.get("contention_observed"),
            "contention_checks": dict(result.get("contention_checks") or {}),
            "contention_attempts": result.get("contention_attempts"),
            # None when the flood did not cover the sample: that reading is of
            # an idle link and is kept beside it, unverified, so the agent sees
            # what it read and why it does not count.
            "throughput_mbps": result.get("throughput_mbps"),
            "unverified_throughput_mbps": result.get("unverified_throughput_mbps"),
            "passed": bool(result.get("ok")),
            "error": result.get("error"),
        }

    def _services(self) -> dict[str, Any]:
        return (self.topology_document.get("topology") or {}).get("services") or {}

    def _iperf_port(self, node: str, role: str) -> int | None:
        for spec in self._services().values():
            if not isinstance(spec, dict):
                continue
            if spec.get("application") != "iperf3" or spec.get("node") != node:
                continue
            if str(spec.get("role", "")) == role:
                return int(spec["port"])
        return None

    def _contention_source(self, protected: str, sink: str) -> str | None:
        """An endpoint that can offer competing load toward the same sink."""
        nodes = (self.topology_document.get("topology") or {}).get("nodes") or {}
        for name in sorted(nodes):
            if name in {protected, sink} or name not in self.env.config.nodes:
                continue
            if "traffic_generation" in (nodes[name] or {}).get("capabilities", []):
                return name
        return None

    def _shaped_link_mbps(self) -> int:
        policy = (self.topology_document.get("topology") or {}).get("qos_policy") or {}
        return int(policy.get("link_mbps") or 10)

    def _settle_after_change(self) -> bool:
        """Wait out what is left of the settle window for the last change, once.

        Returns whether a change was actually pending, which is what entitles the
        caller to a re-observation. The stamp is cleared either way: a second
        validation over the same unchanged network measures the same network, so
        making it wait again would only spend the SUT's execution budget.
        """
        applied_at, self._change_applied_at = getattr(self, "_change_applied_at", None), None
        if applied_at is None:
            return False
        remaining = VALIDATION_SETTLE_SECONDS - (time.monotonic() - applied_at)
        if remaining > 0:
            time.sleep(remaining)
        # A change to a DHCP server is invisible on the desks until they renew,
        # and their own clock would do it after the episode. The subject's
        # validation therefore observes after a renewal, as the judge does.
        self._last_lease_renewal = self._renew_dhcp_clients()
        return True

    def _renew_dhcp_clients(self) -> dict[str, Any] | None:
        def run(client: str, command: str) -> Any:
            node = self.env.config.nodes.get(client)
            if node is None:
                raise KeyError(f"unknown lab node '{client}'")
            return self.env.executor.run_shell(node, command, timeout_seconds=40)

        renewal = renew_dhcp_clients(self.topology_document, run)
        return renewal if renewal["clients"] else None

    def _icmp_probe(self, check: dict[str, Any]) -> dict[str, Any]:
        source = self._required_string(check.get("source"), "icmp.source")
        destination_ip = self._required_string(check.get("destination_ip"), "icmp.destination_ip")
        destination = str(check.get("destination") or destination_ip)
        count = int(check.get("count", 3))
        timeout = int(check.get("timeout_seconds", 3))
        if count < 1 or count > 20 or timeout < 1 or timeout > 10:
            raise ANIRequestError("icmp count must be 1..20 and timeout_seconds must be 1..10")
        node = self._node(source)
        if node.kind != "linux":
            raise ANIRequestError("icmp validation source must be a Linux endpoint")
        result = self.env.executor.run_shell(node, f"ping -q -c {count} -W {timeout} {destination_ip}", timeout_seconds=max(20, count * (timeout + 1) + 5))
        output = f"{result.stdout}{result.stderr}"
        loss = _packet_loss(output)
        rtt = _rtt_average(output)
        return {
            "type": "icmp",
            "source": source,
            "destination": destination,
            "destination_ip": destination_ip,
            "passed": result.ok and loss == 0.0,
            "packet_loss_percent": loss,
            "rtt_avg_ms": rtt,
            "result": self._result_dict(result),
        }

    def _state_command(self, kind: str, name: str, view: str) -> CommandResult:
        node = self._node(name)
        if kind == "linux":
            commands = {
                "interfaces": "ip -brief address",
                "routes": "ip route show",
                "qdisc": "tc qdisc show",
                "system": "ip -brief link",
            }
            return self.env.executor.run_shell(node, commands[view])
        if kind == "vyos":
            commands = {
                "interfaces": "show interfaces",
                "routes": "show ip route",
                # VyOS 1.5 (the lab image) has no "show queueing"; the shaper report
                # is the op-mode view of its QoS policies.
                "qdisc": "show qos shaper",
                "system": "show version",
            }
            return self.env.executor.run_vyos_op(node, commands[view])
        commands = {
            "interfaces": "show interface brief",
            # The state path, not the "show ... route-table ipv4-unicast summary" report:
            # SR Linux 26.7 (the lab image) rejects that report's tokens, so every
            # routes read on an SR Linux node failed and the LangChain subject gave up
            # on its third identical failure (E3 dhcp_provisioning.high.m2, 2026-09-16).
            "routes": "info from state network-instance default route-table ipv4-unicast",
            # "show qos interfaces" only prints the show usage text on SR Linux 26.7;
            # the qos state tree is the report that exists.
            "qdisc": "info from state qos",
            "system": "show version",
        }
        return self.env.executor.run_srl_cli(node, commands[view])

    def _apply_native_commands(self, kind: str, name: str, commands: list[str]) -> CommandResult:
        node = self._node(name)
        # Every ANI write reaches the lab through here, so this is where the settle clock
        # starts. It is stamped on the way out, including on a failure: a change that
        # errored halfway still left the data plane converging.
        try:
            if kind == "nokia_srlinux":
                return self.env.executor.run_srl_cli_batch(node, commands)
            if kind == "vyos":
                return self.env.executor.run_vyos_cli_batch(node, commands)
            if kind == "linux" and len(commands) == 1:
                return self.env.executor.run_linux_config(node, commands[0])
            if kind == "linux":
                raise ANIRequestError(
                    "Linux writes require exactly one native command per change")
            raise ANIRequestError(f"unsupported write platform {kind!r}")
        finally:
            self._change_applied_at = time.monotonic()

    def _execute_native_commands(self, node: LabNode,
                                 commands: list[str]) -> _NativeExecution:
        """Return an explicit indeterminate outcome for transport exceptions.

        A timeout can happen after a device committed but before its acknowledgement
        reached us. Treating that as an ordinary no-write failure would advertise a
        rollback whose starting state is unknown, so callers must re-read and reconcile.
        """
        try:
            return _NativeExecution(
                self._apply_native_commands(node.kind, node.name, commands))
        except NoChangeExecutionError as exc:
            # Determinate: the device is already in the requested state. Not a
            # mutation (nothing changed), not indeterminate (nothing to reconcile).
            return _NativeExecution(
                CommandResult(
                    target=node.name,
                    command="\n".join(commands),
                    returncode=1,
                    stderr=str(exc),
                    reason=("no change: the node is already in the requested state, "
                            "nothing was written"),
                ),
                noop=True,
            )
        except Exception as exc:  # noqa: BLE001 - executor failures are ANI results
            message = f"{type(exc).__name__}: {exc}"
            return _NativeExecution(
                CommandResult(
                    target=node.name,
                    command="\n".join(commands),
                    returncode=1,
                    stderr=message,
                    reason=("native command outcome is indeterminate after executor "
                            f"raised {message}"),
                ),
                indeterminate=True,
            )

    def _selected_nodes(self, nodes: Any, *, required: bool = False) -> list[str]:
        selected = self._strings(nodes)
        if not selected:
            if required:
                raise ANIRequestError("at least one node is required")
            return sorted(self.env.config.nodes)
        canonical = [self._node(name).name for name in selected]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for name in canonical:
            if name in seen:
                duplicates.add(name)
            seen.add(name)
        if duplicates:
            raise ANIRequestError(
                "duplicate lab nodes after alias normalization: "
                f"{', '.join(sorted(duplicates))}")
        return canonical

    def _node(self, name: str):
        normalized = name.removeprefix(f"clab-{self.env.config.lab_name}-")
        node = self.env.config.nodes.get(normalized)
        if node is None:
            raise ANIRequestError(f"unknown lab node: {name}")
        return node

    @staticmethod
    def _views(views: Any) -> list[str]:
        values = ContainerLabANI._strings(views) or ["interfaces", "routes"]
        allowed = {"interfaces", "routes", "qdisc", "system"}
        invalid = sorted(set(values) - allowed)
        if invalid:
            raise ANIRequestError(f"unsupported state views: {', '.join(invalid)}")
        return values

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ANIRequestError("expected a list of strings")
        return [ContainerLabANI._required_string(item, "list item") for item in value]

    @staticmethod
    def _configuration_paths(value: Any) -> list[str]:
        paths = ContainerLabANI._strings(value) or ["/"]
        forbidden = ("\x00", "\r", "\n", ";", "|", "&", ">", "<")
        for path in paths:
            if any(character in path for character in forbidden):
                raise ANIRequestError(
                    "get_running_config paths must be one datastore path without "
                    "CLI control characters")
        return paths

    @staticmethod
    def _vyos_configuration_tokens(path: str) -> list[str]:
        """Parse native or slash-delimited VyOS paths without splitting CIDRs."""
        normalized = path.strip()
        if normalized == "/":
            return []
        if not normalized.startswith("/"):
            try:
                return shlex.split(normalized)
            except ValueError as exc:
                raise ANIRequestError(
                    f"cannot parse VyOS configuration path: {exc}") from exc
        if normalized.startswith("/ "):
            try:
                return shlex.split(normalized[2:])
            except ValueError as exc:
                raise ANIRequestError(
                    f"cannot parse VyOS configuration path: {exc}") from exc

        pieces = normalized[1:].split("/")
        if any(not piece for piece in pieces):
            raise ANIRequestError("VyOS slash paths must not contain empty segments")
        tokens: list[str] = []
        for piece in pieces:
            if tokens and piece.isdecimal():
                candidate = f"{tokens[-1]}/{piece}"
                try:
                    ipaddress.ip_network(candidate, strict=False)
                except ValueError:
                    pass
                else:
                    tokens[-1] = candidate
                    continue
            tokens.append(piece)
        return tokens

    @staticmethod
    def _required_string(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ANIRequestError(f"{label} is required")
        return text

    @staticmethod
    def _validate_native_commands(commands: list[str]) -> None:
        for command in commands:
            if any(character in command for character in ("\x00", "\r", "\n", ";")):
                raise ANIRequestError("ANI commands must be one native command each; use the commands list")
            if command.lstrip().startswith(("sr_cli", "docker", "containerlab")):
                raise ANIRequestError("ANI commands must not include transport wrappers")
            # Native CLIs recognize more whitespace than an ASCII space.  Splitting
            # on all whitespace keeps `commit\tnow` and equivalent spellings from
            # bypassing the session boundary that the ANI owns.
            words = command.split()
            verb = words[0].lower() if words else ""
            if verb in SESSION_COMMANDS:
                raise ANIRequestError(
                    f"ANI commands must not include session control ('{verb}'): the ANI opens the "
                    "candidate session and commits it for you. Send only the configuration commands."
                )

    @staticmethod
    def _validate_batch_atomicity(node: LabNode, commands: list[str],
                                  label: str) -> None:
        if node.kind == "linux" and len(commands) > 1:
            raise ANIRequestError(
                f"{label} contains {len(commands)} commands for Linux target "
                f"{node.name}; Linux has no native multi-command transaction, so "
                f"submit one exactly compensable command per change")

    @staticmethod
    def _result_dict(result: CommandResult) -> dict[str, Any]:
        return {
            "target": result.target,
            "command": result.command,
            "returncode": result.returncode,
            "ok": result.ok,
            "safe": result.safe,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "reason": result.reason,
        }


def _lines_mentioning(text: str, name: str, context: int) -> str:
    """The lines of `text` that mention `name` (case-insensitively), each with `context`
    lines before and after it, in order and without duplicates: a filter that keeps the
    shape of a tree-like listing around the object."""
    lines = text.splitlines()
    needle = name.lower()
    keep: set[int] = set()
    for index, line in enumerate(lines):
        if needle in line.lower():
            keep.update(range(max(0, index - context), min(len(lines), index + context + 1)))
    return "\n".join(lines[index] for index in sorted(keep))


def _function_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def _string_array(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def _packet_loss(output: str) -> float:
    import re
    match = re.search(r"(?P<loss>[0-9]+(?:\.[0-9]+)?)%\s+packet loss", output)
    return float(match.group("loss")) if match else 100.0


def _rtt_average(output: str) -> float | None:
    import re
    match = re.search(r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = [0-9.]+/(?P<average>[0-9.]+)/", output)
    return float(match.group("average")) if match else None
