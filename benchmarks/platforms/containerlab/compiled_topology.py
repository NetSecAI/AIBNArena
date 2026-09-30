from __future__ import annotations

import shlex
from dataclasses import dataclass
from ipaddress import ip_interface
from pathlib import Path
from typing import Any

from scenarios.access import CompiledScenarioInstance
from scenarios.compiler.loader import load_yaml
from scenarios.compiler.topology import TopologyIndex

from .dhcp_clients import dhcp_renew_shell

from .env import ContainerLabEnvConfig
from .fault import LabFault
from .state import StateCommand
from .types import LabNode


class UnsupportedScenarioError(ValueError):
    pass


_OPERATIONS = {
    "set_interface_admin_state": {
        "nokia_srlinux": ("srl_cli", "set / interface {interface} admin-state {state}"),
        "linux": ("shell", "ip link set dev {interface} {linux_state}"),
        "vyos": ("vyos_cli", "{vyos_disable_verb} {vyos_interface} disable"),
    },
    "set_interface_mtu": {
        "linux": ("shell", "ip link set dev {interface} mtu {mtu}"),
    },
    "set_ipv4_forwarding": {
        "nokia_srlinux": (
            "srl_cli",
            "set / network-instance {network_instance} admin-state {state}",
        ),
        "linux": ("shell", "sysctl -w net.ipv4.ip_forward={linux_forwarding}"),
        "vyos": ("vyos_cli", "{vyos_forwarding_verb} system ip disable-forwarding"),
    },
    "set_nonlocal_route_lookup": {
        "linux": ("shell", "ip route replace prohibit default"),
    },
    "set_default_route_presence": {
        "linux": ("shell", "ip route del default"),
    },
    "set_default_route_gateway": {
        "linux": ("shell", "ip route replace default via {fault_gateway}"),
    },
}

def detached_shell(target: str, command: str) -> StateCommand:
    """A shell command run through ``sh -c`` on a node.

    ``run_shell`` execs the command directly after ``shlex.split``, with no shell
    in between, so a pipeline, a redirection, or a ``&`` would be handed to the
    binary as literal arguments. Anything that needs shell grammar has to say so
    explicitly, and quoting it here keeps the round trip through ``shlex.split``
    exact.
    """
    return StateCommand(target, "shell", "sh -c " + shlex.quote(command))


def _qos_teardown(target: str, interface: str) -> StateCommand:
    """Drop whatever hierarchy is on the interface, succeeding when there is none.

    ``tc qdisc replace`` is not idempotent for HTB: replacing a root HTB with
    another HTB of the same handle is rejected outright with EINVAL, so a state
    that is merely re-applied -- a healthy state on a live lab, or the reference
    restore of a fault that left the qdisc in place -- would fail. Tearing down
    first and adding makes every renderer below safe to run twice.
    """
    return detached_shell(target, f"tc qdisc del dev {interface} root 2>/dev/null || true")


def _qos_circuit(policy: dict[str, Any]) -> tuple[str, str, int, int, list[StateCommand]]:
    """The WAN circuit itself: the root hierarchy capped at the link rate.

    This is the physical uplink speed, not a quality-of-service decision, so it
    is present whether or not any class policy is. Keeping it separate is what
    makes the scenarios mean anything: wiping the policy has to leave a
    congested 10 Mbps circuit behind, not an unmetered one -- otherwise removing
    the shaper would *raise* the protected flow's throughput and the fault would
    reward the failure it models.
    """
    target = str(policy["node"])
    interface = str(policy["interface"])
    handle = int(policy.get("handle", 1))
    root = int(policy.get("root_class", 1))
    link_mbps = int(policy["link_mbps"])
    default_class = int(policy["default_class"])
    commands = [
        _qos_teardown(target, interface),
        StateCommand(
            target,
            "shell",
            f"tc qdisc add dev {interface} root handle {handle}: "
            f"htb default {default_class}",
        ),
        StateCommand(
            target,
            "shell",
            f"tc class add dev {interface} parent {handle}: "
            f"classid {handle}:{root} htb rate {link_mbps}mbit ceil {link_mbps}mbit",
        ),
    ]
    return target, interface, handle, root, commands


def render_qos_circuit_commands(policy: dict[str, Any]) -> list[StateCommand]:
    """The circuit with no class policy: one default class taking the whole link.

    Traffic is carried first come, first served and capped at the circuit rate.
    This is the greenfield starting point -- and the state a wiped policy falls
    back to -- so both are rendered from the same place.
    """
    target, interface, handle, root, commands = _qos_circuit(policy)
    link_mbps = int(policy["link_mbps"])
    default_class = int(policy["default_class"])
    commands.append(
        StateCommand(
            target,
            "shell",
            f"tc class add dev {interface} parent {handle}:{root} "
            f"classid {handle}:{default_class} htb rate {link_mbps}mbit "
            f"ceil {link_mbps}mbit",
        )
    )
    return commands


def render_qos_policy_commands(policy: dict[str, Any]) -> list[StateCommand]:
    """The circuit plus the full class policy, rendered from the descriptor.

    Every writer of this policy comes through here: the lab's healthy-state
    generator and the reference restore of every shaping fault. A second
    spelling of the same stanza would let a restore rebuild a policy the healthy
    state never contained, and the drift would only show up as an oracle that
    disagrees with itself.

    Isolation between classes comes from ``rate``, not from ``ceil``: both
    classes may burst into the whole circuit when it is idle, and HTB shares a
    congested circuit in proportion to the guaranteed rates.
    """
    target, interface, handle, root, commands = _qos_circuit(policy)
    for spec in policy["classes"]:
        commands.append(
            StateCommand(
                target,
                "shell",
                f"tc class add dev {interface} parent {handle}:{root} "
                f"classid {handle}:{spec['id']} htb rate {spec['rate_mbps']}mbit "
                f"ceil {spec['ceil_mbps']}mbit prio {spec['prio']}",
            )
        )
    for spec in policy["classes"]:
        match_src = spec.get("match_src")
        if not match_src:
            continue
        commands.append(
            StateCommand(
                target,
                "shell",
                f"tc filter add dev {interface} parent {handle}: protocol ip "
                f"prio {spec['prio']} u32 match ip src {match_src} "
                f"flowid {handle}:{spec['id']}",
            )
        )
    return commands


def split_interface(name: str) -> tuple[str, int]:
    """Split an executable interface name into its port and subinterface index.

    SR Linux addresses a port and a subinterface separately. A routed fabric
    hides this because every data subinterface is 0, so a bare port name means
    ``<port>`` subinterface 0. An IRB has no port of its own: ``irb0.40`` is
    subinterface 40 of ``irb0``, and rendering it as a port name is rejected by
    the device.
    """
    port, separator, index = name.partition(".")
    if not separator:
        return name, 0
    return port, int(index)


def vyos_interface_path(name: str) -> str:
    """VyOS configuration path for an executable interface name.

    ``eth3`` is a plain ethernet interface, ``eth1.10`` is VLAN 10 carried on
    ``eth1`` -- which VyOS addresses as a ``vif`` of the parent, not as an
    interface of its own.
    """
    port, index = split_interface(name)
    if name == port:
        return f"interfaces ethernet {port}"
    return f"interfaces ethernet {port} vif {index}"


def acl_interface_key(name: str) -> str:
    """The ``acl interface`` list key for an executable interface name.

    The key is a free-form identifier; keeping ``<port>.<index>`` makes it
    unambiguous and leaves routed topologies byte-identical to what they
    rendered before.
    """
    port, index = split_interface(name)
    return f"{port}.{index}"


_FORWARDING_ACL = "ibn-forwarding-guard"
_SUBNET_ACL = "ibn-subnet-guard"
_VYOS_SUBNET_RULESET = "IBN-SUBNET-GUARD"

# Where the reference zone files are mounted read-only in the DNS containers.
_DNS_SOURCE_DIR = "/opt/dns-src"
_DNS_CONFIG_DIR = "/etc/bind"
_DNS_OPERATIONS = {
    "set_dns_zone_presence",
    "set_dns_record_presence",
    "set_dns_forwarder",
    "set_endpoint_resolver",
}

# Filtering faults act on the zone model itself rather than on an address list:
# what they take away is a pair the policy declares it accepts.
_ZONE_OPERATIONS = {
    "set_zone_pair_binding",
    "set_zone_rule_precedence",
    "set_zone_interface_membership",
}

_DHCP_OPERATIONS = {
    "set_dhcp_pool_presence",
    "set_dhcp_default_router",
    "set_dhcp_resolver_presence",
    "set_dhcp_resolver",
}

# Where the healthy state leaves the DHCP client of an endpoint. A server-side
# fault changes what the next lease carries, not what the desk already holds, so
# every DHCP fault ends by releasing and renewing -- which is what the desk would
# do on its own when its lease expires, only without the wait.
# Kept for the readers that name it; the renewal shell lives in dhcp_clients.py.
_UDHCPC_PIDFILE = "/var/run/udhcpc.eth1.pid"


@dataclass(frozen=True)
class CompiledContainerLabTopology:
    """Build an executable ContainerLab environment directly from a topology."""

    document: dict[str, Any]

    def __post_init__(self) -> None:
        TopologyIndex(self.document)
        environment = self.document["topology"].get("environment") or {}
        if environment.get("type") != "containerlab":
            raise ValueError("topology.environment.type must be 'containerlab'")

    @classmethod
    def from_file(cls, path: str | Path) -> "CompiledContainerLabTopology":
        return cls(load_yaml(path))

    @property
    def topology(self) -> TopologyIndex:
        return TopologyIndex(self.document)

    def env_config(
        self,
        *,
        healthy_state_path: str | Path,
        fault_catalog_path: str | Path | None = None,
    ) -> ContainerLabEnvConfig:
        topology = self.topology
        environment = topology.topology["environment"]
        lab_name = str(environment["lab_name"])
        node_specs = {**topology.nodes, **topology.optional_nodes}
        nodes = {
            name: LabNode(
                name=name,
                kind=str(spec["kind"]),
                container_name=str(
                    spec.get("container_name") or f"clab-{lab_name}-{name}"
                ),
            )
            for name, spec in node_specs.items()
        }
        return ContainerLabEnvConfig(
            lab_name=lab_name,
            topology_path=Path(environment["topology_path"]),
            healthy_state_path=Path(healthy_state_path),
            fault_catalog_path=(
                Path(fault_catalog_path) if fault_catalog_path else None
            ),
            nodes=nodes,
            default_connectivity_checks=topology.connectivity_checks(),
        )

    def materialize(self, instance: CompiledScenarioInstance) -> LabFault:
        private = instance.private
        bindings = private["bindings"]
        method = private["evaluator"].get("selected_method") or {}
        operation = method.get("operation")
        if not isinstance(operation, dict) or not operation.get("name"):
            raise UnsupportedScenarioError(
                f"{instance.id}: selected method has no semantic operation"
            )

        return LabFault(
            id=instance.id,
            description=f"Compiled {private['scenario_definition']} scenario.",
            intent=instance.intent,
            expected_failure="The evaluator-private connectivity oracle must fail.",
            commands=self._render_operation(operation, bindings),
            expected_affected_nodes=[
                str(node) for node in bindings.get("affected_nodes", [])
            ],
            restore_commands=self._render_restore_operation(operation, bindings),
        )

    def _render_restore_operation(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        operation_name = str(operation["name"])
        if operation_name == "set_interface_admin_state":
            return self._render_operation(
                {**operation, "enabled": not bool(operation.get("enabled"))},
                bindings,
            )
        if operation_name == "set_interface_mtu":
            target = str(bindings["target"])
            interface = str(bindings["interface"])
            node = self.topology.nodes[target]
            interface_spec = node.get("interfaces", {}).get(interface, {})
            defaults = self.topology.topology.get("defaults", {})
            mtu = interface_spec.get("mtu", defaults.get("endpoint_interface_mtu"))
            if mtu is None:
                raise UnsupportedScenarioError(
                    f"topology has no reference MTU for {target}:{interface}"
                )
            return self._render_operation(
                {"name": "set_interface_mtu", "mtu": int(mtu)},
                bindings,
            )
        if operation_name == "set_ipv4_forwarding":
            return self._render_operation(
                {**operation, "enabled": not bool(operation.get("enabled"))},
                bindings,
            )
        if operation_name == "set_forwarded_traffic_policy":
            return self._render_forwarded_traffic_restore(bindings)
        if operation_name in _ZONE_OPERATIONS:
            return self._render_zone_policy_restore(operation, bindings)
        if operation_name == "set_subnet_traffic_policy":
            return self._render_subnet_traffic_restore(operation, bindings)
        if operation_name == "set_link_netem":
            return self._render_link_netem_restore(bindings)
        if operation_name == "set_traffic_shaping_policy":
            return self._render_traffic_shaping_restore()
        if operation_name == "start_background_traffic":
            return self._render_background_traffic_restore(bindings)
        if operation_name in {
            "set_nonlocal_route_lookup",
            "set_default_route_presence",
            "set_default_route_gateway",
        }:
            return [self._endpoint_default_route_restore(bindings)]
        if operation_name in {
            "set_interface_ipv4_presence",
            "set_interface_ipv4_address",
        }:
            return self._render_interface_ipv4_restore(operation, bindings)
        if operation_name in {
            "set_static_route_next_hop",
            "set_static_route_disposition",
            "create_static_route_loop",
        }:
            return self._render_static_route_restore(operation, bindings)
        if operation_name in {
            "set_bridge_domain_interface_presence",
            "set_access_port_bridge_domain",
            "set_trunk_vlan_presence",
        }:
            return self._render_bridge_domain_restore(operation, bindings)
        if operation_name in _DNS_OPERATIONS:
            return self._render_dns_restore(operation, bindings)
        if operation_name in _DHCP_OPERATIONS:
            return self._render_dhcp_restore(operation, bindings)
        raise UnsupportedScenarioError(
            f"operation '{operation_name}' has no reference restore"
        )

    def _render_operation(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        node = self.topology.nodes.get(target)
        if not node:
            raise UnsupportedScenarioError(f"topology has no target node '{target}'")

        operation_name = str(operation["name"])
        if operation_name == "set_forwarded_traffic_policy":
            return self._render_forwarded_traffic_policy(node, operation, bindings)
        if operation_name in _ZONE_OPERATIONS:
            return self._render_zone_policy_operation(node, operation, bindings)
        if operation_name == "set_subnet_traffic_policy":
            return self._render_subnet_traffic_policy(node, operation, bindings)
        if operation_name == "set_link_netem":
            return self._render_link_netem(node, operation, bindings)
        if operation_name == "set_traffic_shaping_policy":
            return self._render_traffic_shaping_policy(node, operation, bindings)
        if operation_name == "start_background_traffic":
            return self._render_background_traffic(node, operation, bindings)
        if operation_name in {
            "set_interface_ipv4_presence",
            "set_interface_ipv4_address",
        }:
            return self._render_interface_ipv4_operation(node, operation, bindings)
        if operation_name in {
            "set_static_route_next_hop",
            "set_static_route_disposition",
            "create_static_route_loop",
        }:
            return self._render_static_route_operation(node, operation, bindings)
        if operation_name in {
            "set_bridge_domain_interface_presence",
            "set_access_port_bridge_domain",
            "set_trunk_vlan_presence",
        }:
            return self._render_bridge_domain_operation(node, operation, bindings)
        if operation_name in _DNS_OPERATIONS:
            return self._render_dns_operation(node, operation, bindings)
        if operation_name in _DHCP_OPERATIONS:
            return self._render_dhcp_operation(node, operation, bindings)
        if (
            operation_name == "set_interface_admin_state"
            and node["kind"] == "nokia_srlinux"
            and "." in str(bindings.get("interface", ""))
        ):
            # A subinterface has no port-level admin-state to toggle; disabling
            # the port name it is written on would be a different, wider fault.
            port, index = split_interface(str(bindings["interface"]))
            state = "enable" if operation.get("enabled") else "disable"
            return [
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / interface {port} subinterface {index} admin-state {state}",
                )
            ]

        if (
            operation_name == "set_nonlocal_route_lookup"
            and operation.get("action") != "prohibit"
        ):
            raise UnsupportedScenarioError(
                "set_nonlocal_route_lookup currently supports action='prohibit'"
            )
        if (
            operation_name == "set_default_route_presence"
            and operation.get("present") is not False
        ):
            raise UnsupportedScenarioError(
                "set_default_route_presence currently supports present=false"
            )

        implementation = (_OPERATIONS.get(operation_name) or {}).get(node["kind"])
        if not implementation:
            raise UnsupportedScenarioError(
                f"operation '{operation_name}' is not implemented for kind '{node['kind']}'"
            )

        mode, template = implementation
        context = {
            **operation,
            **bindings,
            "network_instance": node.get("network_instance", "default"),
            "state": "enable" if operation.get("enabled") else "disable",
            "linux_state": "up" if operation.get("enabled") else "down",
            "linux_forwarding": 1 if operation.get("enabled") else 0,
            # VyOS has no enable/disable value: presence of the `disable` node is
            # the state, so enabling means deleting it.
            "vyos_disable_verb": "delete" if operation.get("enabled") else "set",
            "vyos_forwarding_verb": "delete" if operation.get("enabled") else "set",
            "vyos_interface": vyos_interface_path(str(bindings.get("interface", ""))),
        }
        try:
            command = template.format_map(context)
        except KeyError as exc:
            raise UnsupportedScenarioError(
                f"operation '{operation_name}' is missing binding {exc}"
            ) from exc
        return [StateCommand(target=target, mode=mode, command=command)]

    @staticmethod
    def _bridge_domain_membership(bindings: dict[str, Any], bridge_domain: str) -> str:
        """The mac-vrf membership line for the bound switch port."""
        interface = str(bindings["interface"])
        index = int(bindings["subinterface_index"])
        return f"network-instance {bridge_domain} interface {interface}.{index}"

    def _render_bridge_domain_operation(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        if node["kind"] != "nokia_srlinux":
            raise UnsupportedScenarioError(
                "bridge domain faults currently require nokia_srlinux"
            )
        operation_name = str(operation["name"])
        bridge_domain = str(bindings["bridge_domain"])
        membership = self._bridge_domain_membership(bindings, bridge_domain)

        if operation_name in {
            "set_bridge_domain_interface_presence",
            "set_trunk_vlan_presence",
        }:
            if operation.get("present") is not False:
                raise UnsupportedScenarioError(
                    f"{operation_name} currently supports present=false"
                )
            return [StateCommand(target, "srl_cli", f"delete / {membership}")]

        # set_access_port_bridge_domain: move the port to the wrong VLAN.
        fault_domain = str(bindings["fault_bridge_domain"])
        return [
            StateCommand(target, "srl_cli", f"delete / {membership}"),
            StateCommand(
                target,
                "srl_cli",
                f"set / {self._bridge_domain_membership(bindings, fault_domain)}",
            ),
        ]

    def _render_bridge_domain_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        bridge_domain = str(bindings["bridge_domain"])
        membership = self._bridge_domain_membership(bindings, bridge_domain)
        commands: list[StateCommand] = []
        if str(operation["name"]) == "set_access_port_bridge_domain":
            commands.append(
                StateCommand(
                    target,
                    "srl_cli",
                    f"delete / {self._bridge_domain_membership(bindings, str(bindings['fault_bridge_domain']))}",
                )
            )
        commands.append(StateCommand(target, "srl_cli", f"set / {membership}"))
        return commands

    def _render_dns_operation(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        operation_name = str(operation["name"])

        if operation_name == "set_endpoint_resolver":
            resolver = str(bindings["fault_resolver"])
            return [
                StateCommand(
                    target,
                    "shell",
                    f"sh -c 'printf \"nameserver {resolver}\\n\" > /etc/resolv.conf'",
                )
            ]

        if node["kind"] != "linux":
            raise UnsupportedScenarioError(
                f"DNS faults are not implemented for kind '{node['kind']}'"
            )
        zone = str(bindings["zone"])
        zone_file = str(bindings["zone_file"])
        local = f"{_DNS_CONFIG_DIR}/named.conf.local"

        if operation_name == "set_dns_zone_presence":
            if operation.get("present") is not False:
                raise UnsupportedScenarioError(
                    "set_dns_zone_presence currently supports present=false"
                )
            # Rename the zone so named no longer answers for it, without
            # touching the file mounted from the repository.
            return [
                StateCommand(
                    target,
                    "shell",
                    f"sed -i 's|zone \"{zone}\"|zone \"unserved.{zone}\"|' {local}",
                ),
                StateCommand(target, "shell", "rndc reload"),
            ]

        if operation_name == "set_dns_record_presence":
            record = str(bindings["record"])
            return [
                StateCommand(
                    target,
                    "shell",
                    f"sed -i '/^{record}[[:space:]]/d' {_DNS_CONFIG_DIR}/{zone_file}",
                ),
                StateCommand(target, "shell", "rndc reload"),
            ]

        # set_dns_forwarder
        healthy = str(bindings["healthy_forwarder"])
        fault = str(bindings["fault_forwarder"])
        return [
            StateCommand(target, "shell", f"sed -i 's|{healthy}|{fault}|' {local}"),
            StateCommand(target, "shell", "rndc reload"),
            StateCommand(target, "shell", "rndc flush"),
        ]

    def _render_dns_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        operation_name = str(operation["name"])

        if operation_name == "set_endpoint_resolver":
            resolver = str(bindings["healthy_resolver"])
            return [
                StateCommand(
                    target,
                    "shell",
                    f"sh -c 'printf \"nameserver {resolver}\\n\" > /etc/resolv.conf'",
                )
            ]

        # Every config fault is undone by copying the reference file back: the
        # source is mounted read-only, so it is always the pristine version.
        name = (
            str(bindings["zone_file"])
            if operation_name == "set_dns_record_presence"
            else "named.conf.local"
        )
        return [
            StateCommand(
                target,
                "shell",
                f"cp {_DNS_SOURCE_DIR}/{name} {_DNS_CONFIG_DIR}/{name}",
            ),
            StateCommand(target, "shell", "rndc reload"),
            StateCommand(target, "shell", "rndc flush"),
        ]

    @staticmethod
    def _dhcp_subnet_path(bindings: dict[str, Any]) -> str:
        """The configuration node holding the options of one reservation pool."""
        return (
            f"service dhcp-server shared-network-name {bindings['pool']} "
            f"subnet {bindings['subnet']}"
        )

    @staticmethod
    def _dhcp_release_command(bindings: dict[str, Any]) -> StateCommand:
        """Make the client ask again, forget what the last lease told it, and wait.

        The shell is `dhcp_clients.dhcp_renew_shell`, shared with the judge and the
        ANI. The wait it ends with is what made the injection's own measurement
        honest: the degradation oracle used to run in the gap between the release
        and the new lease, and the same pair came back once as loss-free and once
        as 100 % loss depending on where in that gap it landed.
        """
        return StateCommand(str(bindings["client"]), "shell", dhcp_renew_shell())

    # Rule 1 sorts ahead of every rule the healthy state writes -- it starts its
    # numbering at 10 -- so a deny placed here shadows the permits below it
    # without the fault having to know what those permits are.
    _SHADOW_RULE = 1

    def _render_zone_policy_operation(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        """Break one filtering decision the zone policy is meant to make.

        Three ways a zone policy stops passing traffic it declared it accepts,
        each addressing the model rather than a rule number the topology does not
        declare: the ruleset is unbound from the pair, a deny is placed ahead of
        the permits, or an interface leaves the zone it belongs to.
        """
        target = str(bindings["target"])
        if node["kind"] != "vyos":
            raise UnsupportedScenarioError(
                f"zone policy faults are not implemented for kind '{node['kind']}'"
            )
        name = str(operation["name"])
        ruleset = str(bindings["ruleset"])

        if name == "set_zone_pair_binding":
            if operation.get("present") is not False:
                raise UnsupportedScenarioError(
                    "set_zone_pair_binding currently supports present=false"
                )
            # The ruleset survives; the pair simply stops pointing at it, and the
            # zone model denies what it no longer has a rule for.
            return [self._vyos(
                target,
                f"delete firewall zone {bindings['to_zone']} "
                f"from {bindings['from_zone']}",
            )]

        if name == "set_zone_rule_precedence":
            rule = f"set firewall ipv4 name {ruleset} rule {self._SHADOW_RULE}"
            return [
                self._vyos(target, f"{rule} action drop"),
                self._vyos(target, f"{rule} description ibn-shadow-deny"),
            ]

        # set_zone_interface_membership
        if operation.get("present") is not False:
            raise UnsupportedScenarioError(
                "set_zone_interface_membership currently supports present=false"
            )
        return [self._vyos(
            target,
            f"delete firewall zone {bindings['zone']} "
            f"interface {bindings['interface']}",
        )]

    def _render_zone_policy_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        name = str(operation["name"])
        ruleset = str(bindings["ruleset"])

        if name == "set_zone_pair_binding":
            return [self._vyos(
                target,
                f"set firewall zone {bindings['to_zone']} "
                f"from {bindings['from_zone']} firewall name {ruleset}",
            )]
        if name == "set_zone_rule_precedence":
            return [self._vyos(
                target, f"delete firewall ipv4 name {ruleset} rule {self._SHADOW_RULE}"
            )]
        return [self._vyos(
            target,
            f"set firewall zone {bindings['zone']} interface {bindings['interface']}",
        )]

    @staticmethod
    def _vyos(target: str, command: str) -> StateCommand:
        return StateCommand(target=target, mode="vyos_cli", command=command)

    def _render_dhcp_operation(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        if node["kind"] != "vyos":
            raise UnsupportedScenarioError(
                f"DHCP faults are not implemented for kind '{node['kind']}'"
            )
        operation_name = str(operation["name"])
        subnet = self._dhcp_subnet_path(bindings)
        commands: list[StateCommand] = []

        if operation_name == "set_dhcp_pool_presence":
            if operation.get("present") is not False:
                raise UnsupportedScenarioError(
                    "set_dhcp_pool_presence currently supports present=false"
                )
            # One pool of several: a server left with none would not commit at
            # all, which is a different fault -- the whole service down, not one
            # subnet unserved.
            commands.append(
                StateCommand(
                    target,
                    "vyos_cli",
                    "delete service dhcp-server shared-network-name "
                    f"{bindings['pool']}",
                )
            )
        elif operation_name == "set_dhcp_default_router":
            commands.append(
                StateCommand(
                    target,
                    "vyos_cli",
                    f"set {subnet} option default-router {bindings['fault_gateway']}",
                )
            )
        elif operation_name == "set_dhcp_resolver_presence":
            if operation.get("present") is not False:
                raise UnsupportedScenarioError(
                    "set_dhcp_resolver_presence currently supports present=false"
                )
            commands.append(
                StateCommand(target, "vyos_cli", f"delete {subnet} option name-server")
            )
        else:
            # set_dhcp_resolver. `name-server` is a multi-value node: adding the
            # bogus address would leave the working resolver next to it and the
            # client would still resolve, so the option is replaced, not extended.
            commands.append(
                StateCommand(target, "vyos_cli", f"delete {subnet} option name-server")
            )
            commands.append(
                StateCommand(
                    target,
                    "vyos_cli",
                    f"set {subnet} option name-server {bindings['fault_resolver']}",
                )
            )

        commands.append(self._dhcp_release_command(bindings))
        return commands

    def _render_dhcp_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        operation_name = str(operation["name"])
        subnet = self._dhcp_subnet_path(bindings)
        commands: list[StateCommand] = []

        def vyos(command: str) -> None:
            commands.append(StateCommand(target, "vyos_cli", command))

        if operation_name == "set_dhcp_pool_presence":
            # The pool is gone, so the reference restore rebuilds it field by
            # field -- the same fields states/generate_healthy_state.py writes,
            # taken from the topology rather than from the running config.
            vyos(f"set {subnet} subnet-id {bindings['subnet_id']}")
            vyos(f"set {subnet} option default-router {bindings['healthy_gateway']}")
            vyos(f"set {subnet} option name-server {bindings['healthy_resolver']}")
            vyos(f"set {subnet} option domain-name {bindings['domain_name']}")
            for domain in bindings.get("search_domains", []):
                vyos(f"set {subnet} option domain-search {domain}")
            vyos(f"set {subnet} lease {bindings['lease_seconds']}")
            client = str(bindings["client"])
            vyos(f"set {subnet} static-mapping {client} mac {bindings['client_mac']}")
            vyos(
                f"set {subnet} static-mapping {client} ip-address "
                f"{bindings['client_address']}"
            )
        elif operation_name == "set_dhcp_default_router":
            vyos(f"set {subnet} option default-router {bindings['healthy_gateway']}")
        else:
            if operation_name == "set_dhcp_resolver":
                # The fault left a bogus address behind; deleting an absent
                # option would fail the batch, so only this branch clears it.
                vyos(f"delete {subnet} option name-server")
            vyos(f"set {subnet} option name-server {bindings['healthy_resolver']}")

        commands.append(self._dhcp_release_command(bindings))
        return commands

    def _render_static_route_operation(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        if node["kind"] != "nokia_srlinux":
            raise UnsupportedScenarioError(
                "static route faults currently require nokia_srlinux"
            )
        network_instance = str(node.get("network_instance", "default"))
        route_prefix = str(bindings["route_prefix"])
        group = str(bindings["temporary_next_hop_group"])
        operation_name = str(operation["name"])

        if operation_name == "set_static_route_next_hop":
            next_hop = str(bindings["fault_next_hop"])
            return [
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / network-instance {network_instance} next-hop-groups "
                    f"group {group} nexthop 1 ip-address {next_hop} "
                    "admin-state enable",
                ),
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / network-instance {network_instance} static-routes "
                    f"route {route_prefix} next-hop-group {group} "
                    "admin-state enable",
                ),
            ]

        if operation_name == "set_static_route_disposition":
            if operation.get("disposition") != "blackhole":
                raise UnsupportedScenarioError(
                    "set_static_route_disposition currently supports blackhole"
                )
            return [
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / network-instance {network_instance} next-hop-groups "
                    f"group {group} blackhole",
                ),
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / network-instance {network_instance} static-routes "
                    f"route {route_prefix} next-hop-group {group} "
                    "admin-state enable",
                ),
            ]

        if operation_name == "create_static_route_loop":
            peer = str(bindings["loop_peer"])
            peer_node = self.topology.nodes.get(peer)
            if not peer_node or peer_node["kind"] != "nokia_srlinux":
                raise UnsupportedScenarioError(
                    "static route loop peer must be nokia_srlinux"
                )
            peer_instance = str(peer_node.get("network_instance", "default"))
            peer_group = str(bindings["loop_peer_temporary_next_hop_group"])
            return [
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / network-instance {network_instance} next-hop-groups "
                    f"group {group} nexthop 1 ip-address "
                    f"{bindings['target_to_loop_peer']} admin-state enable",
                ),
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / network-instance {network_instance} static-routes "
                    f"route {route_prefix} next-hop-group {group} "
                    "admin-state enable",
                ),
                StateCommand(
                    peer,
                    "srl_cli",
                    f"set / network-instance {peer_instance} next-hop-groups "
                    f"group {peer_group} nexthop 1 ip-address "
                    f"{bindings['loop_peer_to_target']} admin-state enable",
                ),
                StateCommand(
                    peer,
                    "srl_cli",
                    f"set / network-instance {peer_instance} static-routes "
                    f"route {route_prefix} next-hop-group {peer_group} "
                    "admin-state enable",
                ),
            ]

        raise UnsupportedScenarioError(
            f"unsupported static route operation '{operation_name}'"
        )

    def _render_static_route_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        node = self.topology.nodes[target]
        network_instance = str(node.get("network_instance", "default"))
        route_prefix = str(bindings["route_prefix"])
        group = str(bindings["temporary_next_hop_group"])
        operation_name = str(operation["name"])

        if operation_name == "set_static_route_disposition":
            return [
                StateCommand(
                    target,
                    "srl_cli",
                    f"delete / network-instance {network_instance} static-routes "
                    f"route {route_prefix}",
                ),
                StateCommand(
                    target,
                    "srl_cli",
                    f"delete / network-instance {network_instance} "
                    f"next-hop-groups group {group}",
                ),
            ]

        commands = [
            StateCommand(
                target,
                "srl_cli",
                f"set / network-instance {network_instance} static-routes "
                f"route {route_prefix} next-hop-group "
                f"{bindings['canonical_next_hop_group']} admin-state enable",
            ),
            StateCommand(
                target,
                "srl_cli",
                f"delete / network-instance {network_instance} "
                f"next-hop-groups group {group}",
            ),
        ]
        if operation_name == "create_static_route_loop":
            peer = str(bindings["loop_peer"])
            peer_node = self.topology.nodes[peer]
            peer_instance = str(peer_node.get("network_instance", "default"))
            peer_group = str(bindings["loop_peer_temporary_next_hop_group"])
            commands.extend([
                StateCommand(
                    peer,
                    "srl_cli",
                    f"set / network-instance {peer_instance} static-routes "
                    f"route {route_prefix} next-hop-group "
                    f"{bindings['loop_peer_canonical_next_hop_group']} "
                    "admin-state enable",
                ),
                StateCommand(
                    peer,
                    "srl_cli",
                    f"delete / network-instance {peer_instance} "
                    f"next-hop-groups group {peer_group}",
                ),
            ])
        return commands

    def _render_interface_ipv4_operation(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        interface = str(bindings["interface"])
        healthy_ipv4 = str(bindings.get("healthy_ipv4", ""))
        if not healthy_ipv4:
            raise UnsupportedScenarioError("IPv4 operation requires healthy_ipv4")

        operation_name = str(operation["name"])
        if (
            operation_name == "set_interface_ipv4_presence"
            and operation.get("present") is not False
        ):
            raise UnsupportedScenarioError(
                "set_interface_ipv4_presence currently supports present=false"
            )

        if node["kind"] == "linux":
            commands = [
                StateCommand(
                    target,
                    "shell",
                    f"ip address del {healthy_ipv4} dev {interface}",
                )
            ]
            if operation_name == "set_interface_ipv4_address":
                fault_ipv4 = str(bindings.get("fault_ipv4", ""))
                if not fault_ipv4:
                    raise UnsupportedScenarioError(
                        "set_interface_ipv4_address requires fault_ipv4"
                    )
                commands.append(
                    StateCommand(
                        target,
                        "shell",
                        f"ip address add {fault_ipv4} dev {interface}",
                    )
                )
            return commands

        if node["kind"] == "vyos":
            path = f"{vyos_interface_path(interface)} address"
            commands = [
                StateCommand(target, "vyos_cli", f"delete {path} {healthy_ipv4}")
            ]
            if operation_name == "set_interface_ipv4_address":
                fault_ipv4 = str(bindings.get("fault_ipv4", ""))
                if not fault_ipv4:
                    raise UnsupportedScenarioError(
                        "set_interface_ipv4_address requires fault_ipv4"
                    )
                commands.append(
                    StateCommand(target, "vyos_cli", f"set {path} {fault_ipv4}")
                )
            return commands

        if node["kind"] == "nokia_srlinux":
            port, index = split_interface(interface)
            path = f"interface {port} subinterface {index} ipv4 address"
            commands = [
                StateCommand(target, "srl_cli", f"delete / {path} {healthy_ipv4}")
            ]
            if operation_name == "set_interface_ipv4_address":
                fault_ipv4 = str(bindings.get("fault_ipv4", ""))
                if not fault_ipv4:
                    raise UnsupportedScenarioError(
                        "set_interface_ipv4_address requires fault_ipv4"
                    )
                commands.append(
                    StateCommand(target, "srl_cli", f"set / {path} {fault_ipv4}")
                )
            return commands

        raise UnsupportedScenarioError(
            "interface IPv4 operations are not implemented for "
            f"kind '{node['kind']}'"
        )

    def _render_interface_ipv4_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        interface = str(bindings["interface"])
        healthy_ipv4 = str(bindings["healthy_ipv4"])
        node = self.topology.nodes[target]
        port, index = split_interface(interface)
        ipv4_path = f"{port} subinterface {index} ipv4"
        commands: list[StateCommand] = []

        if operation["name"] == "set_interface_ipv4_address":
            fault_ipv4 = str(bindings["fault_ipv4"])
            if node["kind"] == "linux":
                commands.append(
                    StateCommand(
                        target,
                        "shell",
                        f"ip address del {fault_ipv4} dev {interface}",
                    )
                )
            elif node["kind"] == "vyos":
                commands.append(
                    StateCommand(
                        target,
                        "vyos_cli",
                        f"delete {vyos_interface_path(interface)} address {fault_ipv4}",
                    )
                )
            elif node["kind"] == "nokia_srlinux":
                commands.append(
                    StateCommand(
                        target,
                        "srl_cli",
                        "delete / interface "
                        f"{ipv4_path} address {fault_ipv4}",
                    )
                )

        if node["kind"] == "linux":
            commands.append(
                StateCommand(
                    target,
                    "shell",
                    f"ip address replace {healthy_ipv4} dev {interface}",
                )
            )
            # A multi-homed Linux node -- the WAN edge -- is a forwarder, not an
            # endpoint sitting behind a gateway: it has no default route of its
            # own to put back, and the endpoint helper cannot tell which of its
            # segments to read one from. Re-adding the address is the whole
            # repair there.
            if len(self.topology.interfaces(target)) == 1:
                commands.append(self._endpoint_default_route_restore(bindings))
            return commands
        if node["kind"] == "vyos":
            commands.append(
                StateCommand(
                    target,
                    "vyos_cli",
                    f"set {vyos_interface_path(interface)} address {healthy_ipv4}",
                )
            )
            return commands
        if node["kind"] == "nokia_srlinux":
            commands.append(
                StateCommand(
                    target,
                    "srl_cli",
                    "set / interface "
                    f"{ipv4_path} address {healthy_ipv4}",
                )
            )
            return commands
        raise UnsupportedScenarioError(
            "interface IPv4 restore is not implemented for "
            f"kind '{node['kind']}'"
        )

    def _render_link_netem(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        if node["kind"] != "linux":
            raise UnsupportedScenarioError("set_link_netem currently requires a Linux endpoint")
        target = str(bindings["target"])
        interface = str(bindings["interface"])
        impairment = operation.get("impairment")
        if impairment == "delay":
            delay_ms = int(bindings["delay_ms"])
            command = f"tc qdisc replace dev {interface} root netem delay {delay_ms}ms"
        elif impairment == "corruption":
            corruption_percent = int(bindings["corruption_percent"])
            command = (
                f"tc qdisc replace dev {interface} root netem corrupt "
                f"{corruption_percent}%"
            )
        else:
            raise UnsupportedScenarioError(
                f"unsupported link netem impairment {impairment!r}"
            )
        return [StateCommand(target, "shell", command)]

    def _render_link_netem_restore(
        self,
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        # The same teardown the shaping renderers use: it succeeds when the qdisc
        # is already gone. A subject that repairs a netem impairment by deleting
        # the root qdisc leaves nothing to delete, and a bare `tc qdisc del` then
        # fails with "No such file or directory", which failed the restore phase
        # (and the episode's lifecycle) after a repair the oracles had passed.
        return [_qos_teardown(str(bindings["target"]), str(bindings["interface"]))]

    def _qos_policy(self) -> dict[str, Any]:
        policy = self.topology.topology.get("qos_policy")
        if not isinstance(policy, dict) or not policy.get("classes"):
            raise UnsupportedScenarioError(
                "topology declares no qos_policy to shape, break, or restore"
            )
        return policy

    @staticmethod
    def _policy_class(policy: dict[str, Any], class_id: int) -> dict[str, Any]:
        for spec in policy["classes"]:
            if int(spec["id"]) == int(class_id):
                return spec
        raise UnsupportedScenarioError(
            f"qos_policy declares no class {class_id}"
        )

    @staticmethod
    def _class_change(
        target: str,
        interface: str,
        handle: int,
        root: int,
        spec: dict[str, Any],
        rate_mbps: int,
    ) -> StateCommand:
        return StateCommand(
            target,
            "shell",
            f"tc class change dev {interface} parent {handle}:{root} "
            f"classid {handle}:{spec['id']} htb rate {rate_mbps}mbit "
            f"ceil {spec['ceil_mbps']}mbit prio {spec['prio']}",
        )

    def _render_traffic_shaping_policy(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        if node["kind"] != "linux":
            raise UnsupportedScenarioError(
                "set_traffic_shaping_policy requires a Linux WAN edge"
            )
        policy = self._qos_policy()
        target = str(bindings["target"])
        interface = str(bindings["interface"])
        handle = int(policy.get("handle", 1))
        root = int(policy.get("root_class", 1))
        action = str(operation.get("policy", ""))

        if action == "remove":
            # Wipe the class policy but leave the circuit standing, so the two
            # flows are left fighting over a congested 10 Mbps uplink rather
            # than handed an unmetered one.
            return render_qos_circuit_commands(policy)
        if action == "starve_assured_class":
            spec = self._policy_class(policy, bindings["assured_class"])
            rate_kbit = int(bindings["starved_kbit"])
            return [
                StateCommand(
                    target,
                    "shell",
                    f"tc class change dev {interface} parent {handle}:{root} "
                    f"classid {handle}:{spec['id']} htb rate {rate_kbit}kbit "
                    f"ceil {rate_kbit}kbit prio {spec['prio']}",
                )
            ]
        if action == "remove_classifier":
            spec = self._policy_class(policy, bindings["assured_class"])
            return [
                StateCommand(
                    target,
                    "shell",
                    f"tc filter del dev {interface} parent {handle}: protocol ip "
                    f"prio {spec['prio']} u32",
                )
            ]
        if action == "invert_class_allocation":
            # The two guarantees are swapped, which is what a policy written
            # against the wrong class looks like. Raising the best-effort rate
            # without lowering the assured one instead would push the children's
            # rates past the parent's: HTB then over-commits and stops enforcing
            # the circuit at all, so the fault would break the shaper rather
            # than misallocate it.
            assured = self._policy_class(policy, bindings["assured_class"])
            default = self._policy_class(policy, policy["default_class"])
            return [
                self._class_change(
                    target, interface, handle, root, assured, default["rate_mbps"]
                ),
                self._class_change(
                    target, interface, handle, root, default, assured["rate_mbps"]
                ),
            ]
        raise UnsupportedScenarioError(
            f"unsupported traffic shaping policy {action!r}"
        )

    def _render_traffic_shaping_restore(self) -> list[StateCommand]:
        """Rebuild the whole reference policy rather than undo one command.

        ``tc class change`` has no inverse that is safe against a fault which
        removed the qdisc outright, and re-rendering the descriptor's policy is
        idempotent for all four faults -- every command is a ``replace``.
        """
        return render_qos_policy_commands(self._qos_policy())

    def _render_background_traffic(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        if node["kind"] != "linux":
            raise UnsupportedScenarioError(
                "start_background_traffic requires a Linux endpoint"
            )
        target = str(bindings["target"])
        sink_ip = str(bindings["sink_ip"])
        rate_mbps = int(bindings["background_mbps"])
        seconds = int(bindings["background_seconds"])
        streams = int(bindings["background_streams"])
        sink_port = int(bindings["sink_port"])
        # setsid detaches the flood from the exec session, so it outlives the
        # docker exec that started it and keeps saturating the uplink while the
        # agent works.
        return [
            detached_shell(
                target,
                f"setsid iperf3 -c {sink_ip} -p {sink_port} -u -b {rate_mbps}M "
                f"-t {seconds} -P {streams} >/dev/null 2>&1 </dev/null &",
            )
        ]

    def _render_background_traffic_restore(
        self,
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        # Matching on the process name rather than the command line: a pattern
        # such as 'iperf3 -c' also appears in the killing shell's own argv, so
        # pkill would signal itself and the restore would report a failure it
        # did not have.
        return [detached_shell(str(bindings["target"]), "pkill -x iperf3 || true")]

    def _render_subnet_traffic_policy(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        interface = str(bindings["interface"])
        subnet = str(bindings["subnet_prefix"])
        policy = str(operation.get("policy", "")).lower()
        direction = str(operation.get("direction", "")).lower()
        protocol = operation.get("protocol")
        if policy != "drop":
            raise UnsupportedScenarioError(
                "set_subnet_traffic_policy currently supports policy='drop'"
            )
        if direction not in {"from", "to", "bidirectional"}:
            raise UnsupportedScenarioError(
                f"unsupported subnet traffic direction '{direction}'"
            )
        if protocol not in {None, "icmp"}:
            raise UnsupportedScenarioError(
                f"unsupported subnet traffic protocol '{protocol}'"
            )

        matches = []
        if direction in {"from", "bidirectional"}:
            matches.append(("source-ip", "input"))
        if direction in {"to", "bidirectional"}:
            matches.append(("destination-ip", "output"))

        if node["kind"] == "linux":
            commands = []
            for field, _ in matches:
                flag = "-s" if field == "source-ip" else "-d"
                protocol_arg = " -p icmp" if protocol == "icmp" else ""
                commands.append(
                    StateCommand(
                        target,
                        "shell",
                        f"iptables -I FORWARD 1 {flag} {subnet}"
                        f"{protocol_arg} -j DROP",
                    )
                )
            return commands
        if node["kind"] == "vyos":
            # One rule per direction in a dedicated ruleset, hooked on the global
            # forward chain: routed traffic is what the scenario is about, and
            # VyOS filters it in a single chain rather than per interface.
            # Traffic that matches nothing must return to the calling chain and
            # keep being evaluated by the zone policy. Without this the ruleset
            # default-action swallows every forwarded packet and the fault takes
            # down the whole lab instead of one subnet.
            commands = [
                StateCommand(
                    target,
                    "vyos_cli",
                    f"set firewall ipv4 name {_VYOS_SUBNET_RULESET} default-action return",
                )
            ]
            for sequence, (field, _) in enumerate(matches, start=1):
                rule = 100 + sequence
                selector = "source" if field == "source-ip" else "destination"
                prefix = f"set firewall ipv4 name {_VYOS_SUBNET_RULESET} rule {rule}"
                commands.append(StateCommand(target, "vyos_cli", f"{prefix} action drop"))
                commands.append(
                    StateCommand(target, "vyos_cli", f"{prefix} {selector} address {subnet}")
                )
                if protocol == "icmp":
                    commands.append(
                        StateCommand(target, "vyos_cli", f"{prefix} protocol icmp")
                    )
            commands.append(
                StateCommand(
                    target,
                    "vyos_cli",
                    f"set firewall ipv4 forward filter rule 10 action jump",
                )
            )
            commands.append(
                StateCommand(
                    target,
                    "vyos_cli",
                    f"set firewall ipv4 forward filter rule 10 jump-target {_VYOS_SUBNET_RULESET}",
                )
            )
            return commands

        if node["kind"] != "nokia_srlinux":
            raise UnsupportedScenarioError(
                "set_subnet_traffic_policy is not implemented for "
                f"kind '{node['kind']}'"
            )

        commands = []
        for sequence, (field, _) in enumerate(matches, start=1):
            entry = sequence * 100
            prefix = (
                f"set / acl acl-filter {_SUBNET_ACL} type ipv4 entry {entry}"
            )
            commands.append(
                StateCommand(
                    target,
                    "srl_cli",
                    f"{prefix} match ipv4 {field} prefix {subnet}",
                )
            )
            if protocol == "icmp":
                commands.append(
                    StateCommand(
                        target,
                        "srl_cli",
                        f"{prefix} match ipv4 protocol icmp",
                    )
                )
            commands.append(
                StateCommand(target, "srl_cli", f"{prefix} action drop")
            )

        acl_interface = acl_interface_key(interface)
        port, index = split_interface(interface)
        commands.extend([
            StateCommand(
                target,
                "srl_cli",
                f"set / acl interface {acl_interface} interface-ref "
                f"interface {port}",
            ),
            StateCommand(
                target,
                "srl_cli",
                f"set / acl interface {acl_interface} interface-ref subinterface {index}",
            ),
        ])
        for attachment in dict.fromkeys(item[1] for item in matches):
            commands.append(
                StateCommand(
                    target,
                    "srl_cli",
                    f"set / acl interface {acl_interface} {attachment} "
                    f"acl-filter {_SUBNET_ACL} type ipv4",
                )
            )
        return commands

    def _render_subnet_traffic_restore(
        self,
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        node = self.topology.nodes[target]
        interface = str(bindings["interface"])
        subnet = str(bindings["subnet_prefix"])
        direction = str(operation.get("direction", "")).lower()
        protocol = operation.get("protocol")
        matches = []
        if direction in {"from", "bidirectional"}:
            matches.append("-s")
        if direction in {"to", "bidirectional"}:
            matches.append("-d")

        if node["kind"] == "linux":
            protocol_arg = " -p icmp" if protocol == "icmp" else ""
            return [
                StateCommand(
                    target,
                    "shell",
                    f"iptables -D FORWARD {flag} {subnet}{protocol_arg} -j DROP",
                )
                for flag in matches
            ]
        if node["kind"] == "vyos":
            return [
                StateCommand(
                    target,
                    "vyos_cli",
                    "delete firewall ipv4 forward filter rule 10",
                ),
                StateCommand(
                    target,
                    "vyos_cli",
                    f"delete firewall ipv4 name {_VYOS_SUBNET_RULESET}",
                ),
            ]
        if node["kind"] != "nokia_srlinux":
            raise UnsupportedScenarioError(
                "set_subnet_traffic_policy restore is not implemented for "
                f"kind '{node['kind']}'"
            )
        return [
            StateCommand(
                target,
                "srl_cli",
                f"delete / acl interface {acl_interface_key(interface)}",
            ),
            StateCommand(
                target,
                "srl_cli",
                f"delete / acl acl-filter {_SUBNET_ACL} type ipv4",
            ),
        ]

    @staticmethod
    def _vyos_forward_filter_commands(target: str, verb: str) -> list[StateCommand]:
        """Set or clear the VyOS global IPv4 forward filter.

        VyOS filters routed traffic in one `firewall ipv4 forward filter` chain
        rather than per interface, so the whole guard is a single node.
        """
        return [
            StateCommand(
                target,
                "vyos_cli",
                f"{verb} firewall ipv4 forward filter default-action drop",
            )
        ]

    def _render_forwarded_traffic_policy(
        self,
        node: dict[str, Any],
        operation: dict[str, Any],
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        policy = str(operation.get("policy", "")).lower()
        if policy != "drop":
            raise UnsupportedScenarioError(
                "set_forwarded_traffic_policy currently supports policy='drop'"
            )

        if node["kind"] == "vyos":
            return self._vyos_forward_filter_commands(target, "set")

        if node["kind"] == "linux":
            return [
                StateCommand(
                    target=target,
                    mode="shell",
                    command="iptables -P FORWARD DROP",
                )
            ]
        if node["kind"] != "nokia_srlinux":
            raise UnsupportedScenarioError(
                "set_forwarded_traffic_policy is not implemented for "
                f"kind '{node['kind']}'"
            )

        interfaces = [str(name) for name in bindings.get("interfaces", [])]
        if not interfaces:
            raise UnsupportedScenarioError(
                "set_forwarded_traffic_policy requires selected data interfaces"
            )

        commands = [
            StateCommand(
                target=target,
                mode="srl_cli",
                command=(
                    f"set / acl acl-filter {_FORWARDING_ACL} type ipv4 "
                    "entry 65535 action drop"
                ),
            )
        ]
        for interface in interfaces:
            acl_interface = acl_interface_key(interface)
            port, index = split_interface(interface)
            commands.extend([
                StateCommand(
                    target=target,
                    mode="srl_cli",
                    command=(
                        f"set / acl interface {acl_interface} interface-ref "
                        f"interface {port}"
                    ),
                ),
                StateCommand(
                    target=target,
                    mode="srl_cli",
                    command=(
                        f"set / acl interface {acl_interface} interface-ref "
                        f"subinterface {index}"
                    ),
                ),
                StateCommand(
                    target=target,
                    mode="srl_cli",
                    command=(
                        f"set / acl interface {acl_interface} input acl-filter "
                        f"{_FORWARDING_ACL} type ipv4"
                    ),
                ),
            ])
        return commands

    def _render_forwarded_traffic_restore(
        self,
        bindings: dict[str, Any],
    ) -> list[StateCommand]:
        target = str(bindings["target"])
        node = self.topology.nodes[target]
        if node["kind"] == "vyos":
            return self._vyos_forward_filter_commands(target, "delete")
        if node["kind"] == "linux":
            return [StateCommand(target, "shell", "iptables -P FORWARD ACCEPT")]
        if node["kind"] != "nokia_srlinux":
            raise UnsupportedScenarioError(
                "set_forwarded_traffic_policy restore is not implemented for "
                f"kind '{node['kind']}'"
            )
        interfaces = [str(name) for name in bindings.get("interfaces", [])]
        commands = [
            StateCommand(
                target,
                "srl_cli",
                f"delete / acl interface {acl_interface_key(interface)}",
            )
            for interface in interfaces
        ]
        commands.append(
            StateCommand(
                target,
                "srl_cli",
                f"delete / acl acl-filter {_FORWARDING_ACL} type ipv4",
            )
        )
        return commands

    def _endpoint_default_route_restore(
        self,
        bindings: dict[str, Any],
    ) -> StateCommand:
        target = str(bindings["target"])
        interfaces = self.topology.interfaces(target)
        if len(interfaces) != 1:
            raise UnsupportedScenarioError(
                f"endpoint '{target}' must have exactly one data interface"
            )
        segment = interfaces[0].segment
        gateways = self.topology.segment_gateways(segment)
        if len(gateways) != 1:
            raise UnsupportedScenarioError(
                f"segment '{segment}' must have exactly one gateway"
            )
        gateway_interface = self.topology.segment_interface(gateways[0], segment)
        if gateway_interface is None or not gateway_interface.ipv4:
            raise UnsupportedScenarioError(
                f"gateway '{gateways[0]}' has no IPv4 address on '{segment}'"
            )
        gateway = ip_interface(gateway_interface.ipv4).ip
        return StateCommand(
            target=target,
            mode="shell",
            command=f"ip route replace default via {gateway}",
        )
