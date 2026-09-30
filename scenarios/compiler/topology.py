from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_interface, ip_network
from typing import Any


@dataclass(frozen=True)
class InterfaceRef:
    node: str
    name: str
    segment: str
    ipv4: str | None = None


class TopologyIndex:
    #: Roles that make a Linux node infrastructure rather than an evaluated
    #: endpoint. A management client is never part of the connectivity checks;
    #: a Linux node that forwards for others -- a WAN edge carrying the shaped
    #: uplink, say -- is a router that happens to run Linux: counting it as an
    #: endpoint would put it in every blast radius it merely transits; and a
    #: `service` node is one the endpoints consume rather than one being
    #: evaluated -- a nameserver is queried, it is not a user of the network.
    #: Reachability to a service is still checked, but through an explicit
    #: connectivity requirement rather than by counting it as an endpoint.
    NON_ENDPOINT_ROLES = frozenset({"admin_client", "transit", "service"})

    def __init__(self, document: dict[str, Any]):
        if "topology" not in document:
            raise ValueError("topology YAML must contain a top-level 'topology' mapping")
        self.document = document
        self.topology = document["topology"]
        self.nodes: dict[str, dict[str, Any]] = self.topology.get("nodes", {})
        self.optional_nodes: dict[str, dict[str, Any]] = self.topology.get("optional_nodes", {})
        self.segments: dict[str, dict[str, Any]] = self.topology.get("segments", {})
        self.services: dict[str, dict[str, Any]] = self.topology.get("services", {})
        self._validate()

    def _validate(self) -> None:
        if not self.nodes:
            raise ValueError("abstract topology must contain at least one node")
        overlap = set(self.nodes).intersection(self.optional_nodes)
        if overlap:
            raise ValueError(f"nodes and optional_nodes overlap: {sorted(overlap)}")

        addresses: dict[str, str] = {}
        for node_name, node in self.nodes.items():
            for interface_name, interface in node.get("interfaces", {}).items():
                segment_name = interface.get("segment")
                if segment_name not in self.segments:
                    raise ValueError(
                        f"{node_name}:{interface_name} references unknown segment '{segment_name}'"
                    )
                members = self.segments[segment_name].get("members", [])
                if node_name not in members:
                    raise ValueError(
                        f"{node_name}:{interface_name} is absent from segment '{segment_name}' members"
                    )
                address = interface.get("ipv4")
                cidr = self.segments[segment_name].get("cidr")
                if address and cidr:
                    parsed = ip_interface(address)
                    if parsed.ip not in ip_network(cidr, strict=False):
                        raise ValueError(
                            f"{node_name}:{interface_name} address {address} is outside {cidr}"
                        )
                    key = str(parsed.ip)
                    if key in addresses:
                        raise ValueError(
                            f"duplicate IPv4 address {key} on {addresses[key]} and {node_name}:{interface_name}"
                        )
                    addresses[key] = f"{node_name}:{interface_name}"

        known_nodes = set(self.nodes).union(self.optional_nodes)
        for segment_name, segment in self.segments.items():
            unknown = set(segment.get("members", [])).difference(known_nodes)
            if unknown:
                raise ValueError(
                    f"segment '{segment_name}' references unknown members: {sorted(unknown)}"
                )

        for service_name, service in self.services.items():
            if service.get("node") not in self.nodes:
                raise ValueError(
                    f"service '{service_name}' references unknown node '{service.get('node')}'"
                )

    def nodes_with_role(self, *roles: str, include_optional: bool = False) -> list[str]:
        pool = dict(self.nodes)
        if include_optional:
            pool.update(self.optional_nodes)
        wanted = set(roles)
        return sorted(
            name for name, spec in pool.items()
            if wanted.intersection(spec.get("roles", []))
        )

    def nodes_with_capability(self, capability: str) -> list[str]:
        return sorted(
            name for name, spec in self.nodes.items()
            if capability in spec.get("capabilities", [])
        )

    def interfaces(self, node: str, *, exclude_management: bool = True) -> list[InterfaceRef]:
        spec = self.nodes[node]
        refs: list[InterfaceRef] = []
        for name, iface in spec.get("interfaces", {}).items():
            segment = iface.get("segment")
            if not segment:
                continue
            if exclude_management and self.segments.get(segment, {}).get("type") == "management":
                continue
            refs.append(InterfaceRef(node=node, name=name, segment=segment, ipv4=iface.get("ipv4")))
        return refs

    def segment_interface(self, node: str, segment: str) -> InterfaceRef | None:
        for ref in self.interfaces(node, exclude_management=False):
            if ref.segment == segment:
                return ref
        return None

    def _is_evaluated_endpoint(self, spec: dict[str, Any]) -> bool:
        return (
            spec.get("kind") in {"host", "linux"}
            and not self.NON_ENDPOINT_ROLES.intersection(spec.get("roles", []))
        )

    def endpoint_nodes(self) -> list[str]:
        return sorted(
            name for name, spec in self.nodes.items()
            if self._is_evaluated_endpoint(spec)
        )

    def required_endpoint_pairs(self) -> list[str]:
        requirements = self.topology.get("connectivity_requirements", [])
        if requirements:
            pairs: list[str] = []
            for item in requirements:
                source = str(item["source"])
                destination = str(item["destination"])
                if source not in self.nodes or destination not in self.nodes:
                    raise ValueError(
                        f"connectivity requirement references unknown pair {source}->{destination}"
                    )
                pairs.append(f"{source}->{destination}")
                if item.get("bidirectional", False):
                    pairs.append(f"{destination}->{source}")
            return sorted(set(pairs))

        endpoints = self.endpoint_nodes()
        return [f"{a}->{b}" for a in endpoints for b in endpoints if a != b]

    def segment_hosts(self, segment: str) -> list[str]:
        return sorted(
            member for member in self.segments[segment].get("members", [])
            if member in self.nodes
            and self._is_evaluated_endpoint(self.nodes[member])
        )

    def connectivity_checks(self) -> list[tuple[str, ...]]:
        """The reachability checks the oracle runs, by address and by name.

        Every requirement yields an address ping. It yields a second, name-based
        ping whenever the source resolves through a resolver and some zone names
        the destination -- the pair that lets the oracle separate a broken path
        from a broken resolution: the address check keeps passing while the name
        check fails. A topology that declares no DNS emits the address checks
        alone, exactly as before.
        """
        checks: list[tuple[str, ...]] = []

        def add(source: str, destination: str) -> None:
            address = self.node_ipv4(destination)
            checks.append((source, destination, address))
            hostname = self.dns_fqdn(destination)
            if hostname and self.resolver_for(source):
                checks.append((source, destination, address, hostname))

        for requirement in self.topology.get("connectivity_requirements", []):
            source = str(requirement["source"])
            destination = str(requirement["destination"])
            add(source, destination)
            if requirement.get("bidirectional", False):
                add(destination, source)
        if not checks:
            raise ValueError("topology must define connectivity_requirements")
        return checks

    def node_ipv4(self, node: str) -> str:
        addresses = [
            ref.ipv4
            for ref in self.interfaces(node, exclude_management=False)
            if ref.ipv4
        ]
        if not addresses:
            raise ValueError(f"node '{node}' has no IPv4 address")
        return str(ip_interface(addresses[0]).ip)

    def access_interfaces(self, node: str) -> list[InterfaceRef]:
        """Switch ports facing an endpoint segment.

        A layer-2 leaf has no IPv4 of its own, so an interface is identified as an
        access port by the segment it belongs to: one that actually carries hosts.
        """
        refs: list[InterfaceRef] = []
        for ref in self.interfaces(node):
            if self.segments.get(ref.segment, {}).get("type") == "trunk":
                continue
            if not self.access_port_endpoints(node, ref):
                continue
            refs.append(ref)
        return refs

    def access_port_endpoints(self, node: str, ref: InterfaceRef) -> list[str]:
        """Endpoints that lose connectivity when this access port goes down.

        ``attached_endpoint`` names the node cabled to the port, which is what a
        bridged access port actually cuts. Without it the segment hosts are the
        best available answer, which stays correct as long as a segment has a
        single access port. An attached node absent from ``nodes`` -- an optional
        attacker, typically -- yields no expectation and the port is not selectable.
        """
        evaluated = set(self.endpoint_nodes())
        attached = self.nodes[node].get("interfaces", {}).get(ref.name, {}).get("attached_endpoint")
        if attached is not None:
            return [attached] if attached in evaluated else []
        return sorted(set(self.segment_hosts(ref.segment)) & evaluated)

    def trunk_interfaces(self, node: str, *, toward_gateway_only: bool = True) -> list[InterfaceRef]:
        """Switch uplinks carrying tagged VLANs toward the spines.

        With ``toward_gateway_only`` the result keeps the uplinks whose peer holds
        the ``gateway`` role. Cutting one of those detaches every VLAN it carries
        from its default gateway, which is what makes the blast radius exact; a
        uplink toward a standby router carries no traffic and would compile to an
        empty, and therefore untestable, expectation.
        """
        refs: list[InterfaceRef] = []
        for ref in self.interfaces(node):
            segment = self.segments.get(ref.segment, {})
            if segment.get("type") != "trunk":
                continue
            if toward_gateway_only and not self.segment_gateways(ref.segment):
                continue
            refs.append(ref)
        return refs

    def trunk_hosts(self, segment: str) -> list[str]:
        """Endpoints that lose their gateway when this trunk goes down."""
        vlan_ids = self.segments.get(segment, {}).get("tagged_vlans", [])
        evaluated = set(self.endpoint_nodes())
        hosts: set[str] = set()
        for name, spec in self.segments.items():
            if spec.get("vlan_id") in vlan_ids:
                hosts.update(set(self.segment_hosts(name)) & evaluated)
        return sorted(hosts)

    def bridge_domain(self, segment: str) -> str | None:
        return self.segments.get(segment, {}).get("bridge_domain")

    def segment_for_vlan(self, vlan_id: int) -> str | None:
        for name, spec in self.segments.items():
            if spec.get("vlan_id") == vlan_id:
                return name
        return None

    def segment_gateways(self, segment: str) -> list[str]:
        return sorted(
            member for member in self.segments[segment].get("members", [])
            if member in self.nodes and "gateway" in self.nodes[member].get("roles", [])
        )

    def _dns_service(self, node: str) -> dict[str, Any]:
        for spec in self.services.values():
            if spec.get("node") == node and spec.get("application") == "dns":
                return spec
        return {}

    def dns_zone(self, node: str) -> str | None:
        """The zone this nameserver is authoritative for."""
        return self._dns_service(node).get("zone")

    def dns_zone_file(self, node: str) -> str | None:
        return self._dns_service(node).get("zone_file")

    def dns_record(self, node: str) -> str | None:
        """A record whose removal is observable by the clients of this server."""
        return self._dns_service(node).get("probe_record")

    def dns_fqdn(self, node: str) -> str | None:
        """The name a client resolves to reach this node, if a zone holds one.

        A DNS service lists the nodes its zone answers for through `hosts`, under
        their own name -- which is how the reference zone files spell them. A node
        no zone names, the outside client for instance, has no name check to run:
        pinging a name nothing answers for would fail on a healthy lab.
        """
        for spec in self.services.values():
            if spec.get("application") != "dns":
                continue
            hosts = [str(item) for item in spec.get("hosts", [])]
            zone = str(spec.get("zone", ""))
            if node in hosts and zone:
                return f"{node}.{zone}"
        return None

    def dns_forwarders(self, node: str) -> list[str]:
        return [str(item) for item in self._dns_service(node).get("forwarders", [])]

    def dns_clients(self, node: str) -> list[str]:
        """Endpoints that resolve through this server.

        Restricted to evaluated endpoints, so a fault never carries an
        expectation no connectivity check can observe.
        """
        evaluated = set(self.endpoint_nodes())
        return sorted(
            name for name, spec in self.nodes.items()
            if spec.get("resolver") == self.node_ipv4(node) and name in evaluated
        )

    def iperf_port(self, node: str, role: str) -> int | None:
        """The port of the iperf3 sink this node runs for a given role.

        A throughput oracle needs two of them on the same host -- one it measures
        against and one it floods -- because a single iperf3 server runs one test
        at a time. The topology names which is which through `role`.
        """
        for spec in sorted(self.services.values(), key=lambda item: str(item.get("port", ""))):
            if spec.get("application") != "iperf3" or spec.get("node") != node:
                continue
            if str(spec.get("role", "")) == role:
                return int(spec["port"])
        return None

    def zone_policies(self) -> list[dict[str, Any]]:
        """The declared zone pairs and what the policy does with each.

        A pair absent from the list has no ruleset and is denied by the zone
        model itself, which is how these designs isolate without writing a single
        deny rule. A pair listed with `action: accept` is the opposite: something
        the network is meant to let through, and therefore something a filtering
        fault can take away where an oracle will see it.
        """
        return [dict(item) for item in self.topology.get("zone_policies", [])]

    def segment_zone(self, segment: str) -> str | None:
        return self.segments.get(segment, {}).get("zone")

    def node_zone(self, node: str) -> str | None:
        """The zone an endpoint sits in, through the segment it is attached to."""
        for ref in self.interfaces(node):
            zone = self.segment_zone(ref.segment)
            if zone:
                return str(zone)
        return None

    def zone_enforcers(self) -> list[str]:
        """Nodes that actually apply a zone policy."""
        return [
            name for name in sorted(self.nodes)
            if "zone_policy" in (self.nodes[name] or {}).get("capabilities", [])
        ]

    def zone_interfaces(self, node: str, zone: str) -> list[InterfaceRef]:
        """The enforcer's own interfaces that belong to one zone."""
        return [
            ref for ref in self.interfaces(node)
            if self.segment_zone(ref.segment) == zone
        ]

    def dhcp_service(self) -> tuple[str, dict[str, Any]]:
        """The DHCP service and the node running it.

        A relay is not a server: it forwards the broadcast of a subnet it does
        not allocate for, so the pools -- and every option a fault can corrupt --
        live on the node the `dhcp_server` role names.
        """
        for name, spec in sorted(self.services.items()):
            if spec.get("application") == "dhcp":
                return name, spec
        raise ValueError("topology declares no DHCP service")

    def dhcp_pools(self) -> dict[str, dict[str, Any]]:
        """Reservation pools, keyed by the client they serve."""
        _, service = self.dhcp_service()
        return {
            str(client): dict(spec or {})
            for client, spec in (service.get("pools") or {}).items()
        }

    def dhcp_clients(self) -> list[str]:
        """Endpoints whose address comes from a pool of this server.

        Restricted to evaluated endpoints and to nodes the topology actually
        marks as DHCP-addressed, so a fault never carries an expectation the
        connectivity oracle cannot observe -- an admin client is served a lease
        like any other desk, but nothing pings it.
        """
        evaluated = set(self.endpoint_nodes())
        return sorted(
            client for client, _ in self.dhcp_pools().items()
            if client in evaluated
            and self.nodes.get(client, {}).get("address_assignment") == "dhcp"
        )

    def dhcp_client_segment(self, client: str) -> str:
        """The segment whose broadcast domain the client leases its address in."""
        refs = [ref for ref in self.interfaces(client) if ref.ipv4]
        if not refs:
            raise ValueError(f"DHCP client '{client}' has no addressed data interface")
        return refs[0].segment

    def resolver_for(self, node: str) -> str | None:
        return self.nodes.get(node, {}).get("resolver")

    def unused_address_in(self, address: str) -> str:
        """An address in the same /24 that no node in the topology holds."""
        network = ip_network(f"{address}/24", strict=False)
        taken = {
            str(ip_interface(ref.ipv4).ip)
            for name in self.nodes
            for ref in self.interfaces(name, exclude_management=False)
            if ref.ipv4
        }
        for candidate in network.hosts():
            if str(candidate) not in taken:
                return str(candidate)
        raise ValueError(f"no unused address available in {network}")

    def security_zone(self, segment: str) -> str | None:
        return self.segments.get(segment, {}).get("security_zone")

    def attachable_attacker(self, segment: str) -> str:
        for name, spec in sorted(self.optional_nodes.items()):
            if "attacker" in spec.get("roles", []) and segment in spec.get("attachable_segments", []):
                return name
        raise ValueError(f"no optional attacker can attach to segment '{segment}'")

    def can_attach_attacker(self, segment: str) -> bool:
        return any(
            "attacker" in spec.get("roles", []) and segment in spec.get("attachable_segments", [])
            for spec in self.optional_nodes.values()
        )

    def data_segments_with_host_gateway_and_attacker(self) -> list[str]:
        return sorted(
            name for name, spec in self.segments.items()
            if spec.get("type") in {"vlan", "lan"}
            and self.segment_hosts(name)
            and self.segment_gateways(name)
            and self.can_attach_attacker(name)
        )

    def service(self, *, application: str | None = None, transport: str | None = None) -> tuple[str, dict[str, Any]]:
        for name, spec in sorted(self.services.items()):
            if application and spec.get("application") != application:
                continue
            if transport and spec.get("transport") != transport:
                continue
            return name, spec
        criteria = {"application": application, "transport": transport}
        raise ValueError(f"no service matches {criteria}")

    def first_data_segment_with_host_and_gateway(self) -> str:
        for name, spec in sorted(self.segments.items()):
            if spec.get("type") not in {"vlan", "lan"}:
                continue
            if self.segment_hosts(name) and self.segment_gateways(name):
                return name
        raise ValueError("topology has no L2 data segment containing a host and gateway")

    def first_router_with_interfaces(self, minimum: int = 2) -> str:
        candidates = self.nodes_with_capability("ipv4_forwarding")
        for node in candidates:
            if len(self.interfaces(node)) >= minimum:
                return node
        raise ValueError(f"topology has no IPv4-forwarding node with >= {minimum} data interfaces")

    def l3_segments(self, *, exclude_management: bool = True) -> list[str]:
        result = []
        for name, spec in sorted(self.segments.items()):
            if not spec.get("cidr"):
                continue
            if exclude_management and spec.get("type") == "management":
                continue
            result.append(name)
        return result
