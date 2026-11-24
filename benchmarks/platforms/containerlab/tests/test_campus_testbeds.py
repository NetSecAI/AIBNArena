"""The campus labs: each lab, its descriptor and its reference state agree.

Three files describe the same lab and nothing joins them but these checks: a
desk's MAC is pinned in the containerlab links, named in the descriptor, and
keyed into a DHCP reservation by the reference state; a ruleset a filtering
fault addresses is spelled by the descriptor's template and written by the
generator; a route a fault rewrites is declared by the descriptor and held by the
reference state. A drift in any of them compiles cleanly and fails only on the lab.
"""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from benchmarks.platforms.containerlab.compiled_topology import (
    CompiledContainerLabTopology,
    render_qos_circuit_commands,
    render_qos_policy_commands,
    vyos_interface_path,
)
from benchmarks.platforms.containerlab.dhcp_clients import dhcp_renew_shell
from benchmarks.testbeds import campus_fabric, campus_site
from scenarios.access import CompiledScenarioSuite
from scenarios.compiler.compiler import ScenarioCompiler

REPO = Path(__file__).resolve().parents[4]
#: The domains the campaigns run, every method of which each campus lab must carry.
CAMPAIGN_DOMAINS = ("connectivity", "dhcp_dns", "filtering", "qos")


@dataclass(frozen=True)
class Lab:
    name: str
    #: The firewall serving the desks' leases, the only ones on the lab.
    lan_server: str
    #: The firewall shaping the LAN trunks, and those trunks.
    shaper: str
    trunks: tuple[str, ...]
    #: Whether the aggregates form an MLAG pair (campus_small has no aggregates).
    mlag: bool

    @property
    def descriptor(self) -> Path:
        return REPO / f"scenarios/topologies/{self.name}.yaml"

    @property
    def topology(self) -> dict:
        return yaml.safe_load(self.descriptor.read_text(encoding="utf-8"))["topology"]

    @property
    def clab(self) -> dict:
        path = REPO / f"benchmarks/testbeds/containerlab/{self.name}/topology.clab.yml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def commands(self, target: str, mode: str, state: str = "healthy.json") -> list[str]:
        path = REPO / f"benchmarks/testbeds/containerlab/{self.name}/states/{state}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [c["command"] for c in payload["commands"] if c["target"] == target and c["mode"] == mode]

    def vyos(self, target: str) -> list[str]:
        return self.commands(target, "vyos_cli")

    def suite(self, seed: int, *domains: str) -> CompiledScenarioSuite:
        compiler = ScenarioCompiler.from_topology_file(self.descriptor, seed=seed)
        return CompiledScenarioSuite(compiler.compile_scenarios(REPO / "scenarios", domains=set(domains)))


LABS = [
    Lab("campus_medium", lan_server="firewall",
        shaper="firewall", trunks=("bond0",), mlag=True),
    Lab("campus_small", lan_server="firewall",
        shaper="firewall", trunks=("eth1", "eth2"), mlag=False),
    Lab("campus_large", lan_server="firewall",
        shaper="firewall", trunks=("bond0", "eth8"), mlag=True),
]
by_lab = pytest.mark.parametrize("lab", LABS, ids=[lab.name for lab in LABS])


def _pinned_macs(lab: Lab) -> dict[str, str]:
    pinned = {}
    for link in lab.clab["topology"]["links"]:
        for endpoint in link["endpoints"]:
            if isinstance(endpoint, dict) and endpoint.get("mac"):
                pinned[endpoint["node"]] = endpoint["mac"]
    return pinned


def _document(lab: Lab) -> dict:
    return yaml.safe_load(lab.descriptor.read_text(encoding="utf-8"))


def _firewalls(lab: Lab) -> list[str]:
    return sorted(name for name, node in lab.topology["nodes"].items()
                  if node["kind"] == "vyos" and "firewall" in node["roles"])


@by_lab
def test_every_dhcp_client_is_pinned_and_reserved_at_its_declared_address(lab):
    pinned = _pinned_macs(lab)
    clients = {name: node for name, node in lab.topology["nodes"].items()
               if node.get("address_assignment") == "dhcp"}
    assert set(clients) == set(pinned)
    for name, node in clients.items():
        address = ipaddress.ip_interface(node["interfaces"]["eth1"]["ipv4"])
        mapping = [c for c in lab.vyos(lab.lan_server) if f" subnet {address.network} static-mapping {name} " in c]
        assert node["mac"] == pinned[name], name
        assert any(c.endswith(f" mac {node['mac']}") for c in mapping), name
        assert any(c.endswith(f" ip-address {address.ip}") for c in mapping), name


@by_lab
def test_only_the_desks_lease_their_address(lab):
    """A server keeps what it is given: the firewall serves it no lease."""
    nodes = lab.topology["nodes"]
    leased = {name for name, node in nodes.items() if node.get("address_assignment") == "dhcp"}
    assert leased == set(lab.topology["services"]["dhcp_campus"]["pools"])
    assert not leased & set(CompiledContainerLabTopology(_document(lab)).topology.service_nodes())
    assert [name for name, spec in lab.topology["services"].items()
            if spec.get("application") == "dhcp"] == ["dhcp_campus"]
    assert not any("shared-network-name DMZ" in c for c in lab.vyos(lab.lan_server))


@by_lab
def test_the_dmz_servers_hold_their_address_route_and_resolver_by_hand(lab):
    """What a lease gave them, the name checks of the healthy oracle still need."""
    for name in ("web-dmz", "sftp-dmz"):
        node = lab.topology["nodes"][name]
        address = ipaddress.ip_interface(node["interfaces"]["eth1"]["ipv4"])
        shell = lab.commands(name, "shell")
        assert f"ip address replace {address} dev eth1" in shell
        assert f"ip route replace default via {address.network[254]}" in shell
        written = next(c for c in shell if "/etc/resolv.conf" in c)
        assert "search dmz.lab" in written and f"nameserver {node['resolver']}" in written
        assert not any("udhcpc" in c for c in shell), name
        container = lab.clab["topology"]["nodes"][name]
        assert not any("udhcpc" in bind for bind in container.get("binds", [])), name


@by_lab
def test_no_fault_sits_on_what_a_lease_holds(lab):
    """A renewal rewrites a desk's address, link and default route, and the judge and
    the ANI both renew: such a fault would be undone by the bench, not by the agent
    (main01, 163 successes). On a desk, a DHCP fault only renews."""
    leased = set(lab.topology["services"]["dhcp_campus"]["pools"])
    topology = CompiledContainerLabTopology(_document(lab))
    for seed in range(1, 9):
        for domain in CAMPAIGN_DOMAINS:
            for instance in lab.suite(seed, domain).instances():
                on_desks = [c for c in topology.materialize(instance).commands if c.target in leased]
                if domain == "dhcp_dns":
                    assert all(c.command == dhcp_renew_shell() for c in on_desks), instance.id
                else:
                    assert not on_desks, instance.id


@by_lab
def test_a_host_brought_back_up_gets_the_default_route_the_kernel_dropped(lab):
    """A DHCP client had a renewal to give it back; a static host has this restore."""
    topology = CompiledContainerLabTopology(_document(lab))
    for seed in range(1, 9):
        for instance in lab.suite(seed, "connectivity").instances():
            if ".disable_interface." not in instance.id or not instance.id.endswith(".m2"):
                continue
            restore = [c.command for c in topology.materialize(instance).restore_commands]
            assert any(c.startswith("ip link set") for c in restore), instance.id
            assert any("default via" in c for c in restore), instance.id


@by_lab
def test_dhcp_faults_bind_to_the_lan_pools_the_reference_state_holds(lab):
    topology = lab.topology
    pools = topology["services"]["dhcp_campus"]["pools"]
    lan = lab.vyos(lab.lan_server)
    for client, pool in pools.items():
        network = ipaddress.ip_interface(topology["nodes"][client]["interfaces"]["eth1"]["ipv4"]).network
        prefix = f"set service dhcp-server shared-network-name {pool['name']} subnet {network}"
        assert f"{prefix} subnet-id {pool['subnet_id']}" in lan, client
    for seed in range(1, 9):
        for instance in lab.suite(seed, "dhcp_dns").instances():
            bindings = instance.private["bindings"]
            assert bindings["target"] == lab.lan_server, instance.id
            assert bindings["pool"] == pools[bindings["client"]]["name"], instance.id


@by_lab
def test_every_ruleset_a_filtering_fault_can_name_is_bound_to_its_pair(lab):
    topology = lab.topology
    template = topology["defaults"]["zone_ruleset_template"]
    for policy in topology["zone_policies"]:
        if policy["action"] != "accept":
            continue
        ruleset = template.format(from_zone=policy["from"], to_zone=policy["to"])
        binding = f"set firewall zone {policy['to']} from {policy['from']} firewall name {ruleset}"
        assert binding in lab.vyos(policy["node"]), binding


@by_lab
def test_a_dropped_pair_has_no_ruleset_on_the_lab(lab):
    """The pairs declared `drop` are denied by the zone model, not by a rule."""
    for policy in lab.topology["zone_policies"]:
        if policy["action"] == "drop":
            pair = f"set firewall zone {policy['to']} from {policy['from']} "
            assert not any(c.startswith(pair) for c in lab.vyos(policy["node"])), pair


@by_lab
def test_every_declared_firewall_interface_is_addressed_and_zoned_as_declared(lab):
    topology = lab.topology
    for name in _firewalls(lab):
        commands = lab.vyos(name)
        for interface, spec in topology["nodes"][name]["interfaces"].items():
            path = f"set {vyos_interface_path(interface)}"
            assert f"{path} address {spec['ipv4']}" in commands, (name, interface)
            assert f"set firewall zone {spec['zone']} interface {interface}" in commands, (name, interface)


@by_lab
def test_every_shaper_class_matches_a_vif_of_the_trunk_it_shapes(lab):
    """A class is a QoS rule on the L3 interface `<port>.<vif>`; a class matching a vif
    its port does not hold names an interface that is not there."""
    commands = lab.vyos(lab.shaper)
    egress = {c.split()[3]: c.split()[5] for c in commands if c.startswith("set qos interface ")}
    vifs = {(c.split()[3], c.split()[5]) for c in commands
            if c.startswith(("set interfaces ethernet ", "set interfaces bonding ")) and " vif " in c}
    assert tuple(sorted(egress)) == lab.trunks
    for port, policy in egress.items():
        matched = {c.split()[-1] for c in commands
                   if c.startswith(f"set qos policy shaper {policy} class ") and " match " in c}
        assert matched and all((port, vif) in vifs for vif in matched), (port, policy, matched)


@by_lab
def test_every_vlan_is_carried_by_a_tree_of_trunks(lab):
    """A VLAN on two paths is a loop, unless the two are one MLAG bundle.

    The pair of an MLAG domain answers as one switch, so it is folded into one node
    first; a bundle with a cable to each peer is then a single edge, and the
    peer-link no edge at all. What remains must be a tree for every VLAN.
    """
    one_switch: dict[str, str] = {}
    for name, segment in lab.topology["segments"].items():
        if segment["type"] == campus_fabric.PEER_LINK:
            for member in segment["members"]:
                one_switch[member] = name
    assert bool(one_switch) == lab.mlag
    edges: dict[int, list[tuple[str, str]]] = {}
    for segment in lab.topology["segments"].values():
        if segment["type"] == "trunk":
            ends = tuple(sorted({one_switch.get(member, member) for member in segment["members"]}))
            assert len(ends) == 2, segment
            for vlan in segment["tagged_vlans"]:
                edges.setdefault(vlan, []).append(ends)
    assert edges
    for vlan, links in edges.items():
        nodes = {node for link in links for node in link}
        # Connected with one edge fewer than it has nodes: a tree, so no loop.
        assert len(links) == len(nodes) - 1, (vlan, links)
        reached, frontier = set(), [next(iter(nodes))]
        while frontier:
            node = frontier.pop()
            reached.add(node)
            frontier += [b if a == node else a for a, b in links if node in (a, b)
                         and (b if a == node else a) not in reached]
        assert reached == nodes, (vlan, links)


def _cables(lab: Lab) -> dict[str, str]:
    """Every cabled port, `node:EthernetN` for a switch, to the one at the other end."""
    def name(endpoint) -> str:
        node, interface = (endpoint.split(":") if isinstance(endpoint, str)
                           else (endpoint["node"], endpoint["interface"]))
        if lab.topology["nodes"][node]["kind"] == "arista_ceos":
            interface = "Ethernet" + interface.removeprefix("eth")
        return f"{node}:{interface}"
    cables = {}
    for link in lab.clab["topology"]["links"]:
        a, b = (name(endpoint) for endpoint in link["endpoints"])
        cables[a], cables[b] = b, a
    return cables


@by_lab
def test_every_switch_port_is_cabled_where_its_segment_says(lab):
    """The descriptor's ports are the cables containerlab lays: a port-channel's every
    member, a trunk or an access port, each lands on a member of its segment -- the
    attached endpoint, for an access port -- and no switch port is cabled undeclared."""
    nodes = lab.topology["nodes"]
    cables = _cables(lab)
    declared = set()
    for switch in campus_fabric.switches(lab.topology):
        for interface, spec in nodes[switch]["interfaces"].items():
            segment = lab.topology["segments"][spec["segment"]]
            for port in spec.get("members") or [interface]:
                declared.add(f"{switch}:{port}")
                far_node = cables[f"{switch}:{port}"].split(":")[0]
                assert far_node in segment["members"] and far_node != switch, (switch, port, far_node)
                if spec.get("attached_endpoint"):
                    assert far_node == spec["attached_endpoint"], (switch, port)
    cabled = {port for port in cables if nodes[port.split(":")[0]]["kind"] == "arista_ceos"}
    assert cabled == declared, sorted(cabled ^ declared)


@by_lab
def test_every_switch_holds_what_the_descriptor_renders(lab):
    """The reference state's switch lines are campus_fabric's, in its order: the one
    function the descriptor is read through, so a port the descriptor gives the
    switch is a port the lab configures."""
    switches = campus_fabric.switches(lab.topology)
    assert switches
    for switch in switches:
        rendered = [c["command"] for c in campus_fabric.fabric_commands(lab.topology, switch)]
        assert lab.commands(switch, "eos_cli") == rendered, switch
        assert lab.clab["topology"]["nodes"][switch]["kind"] == "arista_ceos"


@by_lab
def test_the_filtering_scenario_leaks_no_ruleset_name(lab):
    rulesets = {"TRUST-TRANSIT", "TRUST-DMZ", "TRUST-UNTRUST", "DMZ-UNTRUST", "TRUST-INTRA", "TRUST-SITE"}
    for seed in range(1, 9):
        for instance in lab.suite(seed, "filtering").instances():
            assert instance.private["bindings"].get("ruleset", "TRUST-DMZ") in rulesets
            blob = json.dumps(instance.public_view())
            for secret in (*rulesets, "firewall zone", "vyos_cli", "ibn-shadow-deny"):
                assert secret not in blob, (instance.id, secret)


@by_lab
def test_every_declared_static_route_is_the_one_the_lab_holds(lab):
    """The route faults bind to `static_routes` and restore to it, so the lab holds
    exactly what is declared -- and a router holding a route declares it."""
    for name, node in lab.topology["nodes"].items():
        if node["kind"] == "vyos":
            held = {c for c in lab.vyos(name) if c.startswith("set protocols static route ")}
        elif "static_routes" in node:
            held = {c for c in lab.commands(name, "shell")
                    if c.startswith("ip route replace ") and " default " not in f" {c} "}
        else:
            continue
        declared = {c["command"] for c in campus_site.static_route_commands(lab.topology, name)}
        assert held == declared, name
        assert not held or "static_routes" in node, name


@by_lab
def test_the_site_circuit_carries_the_declared_policy_and_greenfield_only_the_circuit(lab):
    policy = lab.topology["qos_policy"]
    edge = policy["node"]
    shaped = [c.command for c in render_qos_policy_commands(policy)]
    circuit = [c.command for c in render_qos_circuit_commands(policy)]
    healthy = lab.commands(edge, "shell")
    greenfield = lab.commands(edge, "shell", "healthy_greenfield.json")
    assert all(command in healthy for command in shaped)
    assert all(command in greenfield for command in circuit)
    assert not set(shaped) - set(circuit) & set(greenfield)


@by_lab
def test_every_route_a_fault_deletes_is_one_the_reference_state_holds(lab):
    """A VyOS batch that deletes an absent route fails whole on the device."""
    document = yaml.safe_load(lab.descriptor.read_text(encoding="utf-8"))
    topology = CompiledContainerLabTopology(document)
    for seed in range(1, 9):
        for instance in lab.suite(seed, "connectivity").instances():
            if ".wrong_routing_table." not in instance.id:
                continue
            fault = topology.materialize(instance)
            for command in fault.commands:
                if command.mode == "vyos_cli" and command.command.startswith("delete protocols static route "):
                    prefix = command.command.split()[4]
                    held = f"set protocols static route {prefix} "
                    assert any(c.startswith(held) for c in lab.vyos(command.target)), (instance.id, command)


@by_lab
def test_every_method_of_the_campaign_domains_renders(lab):
    # A scenario that scenarios/topology_applicability.json does not declare for this lab
    # (qos.assured_bandwidth and qos.link_impairment, active here, archived in the source
    # repository) is not one the lab has to render.
    applicability = json.loads((REPO / "scenarios" / "topology_applicability.json").read_text(encoding="utf-8"))
    declared = set()
    for domain in CAMPAIGN_DOMAINS:
        for path in sorted((REPO / "scenarios" / domain).glob("*.yaml")):
            entry = applicability["scenarios"].get(f"{domain}/{path.name}")
            if entry is not None and lab.descriptor.stem not in entry["topologies"]:
                continue
            scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
            declared.update(f"{domain}.{scenario['scenario']}.m{method['id']}" for method in scenario["methods"])
    topology = CompiledContainerLabTopology.from_file(lab.descriptor)
    for seed in range(1, 5):
        rendered = set()
        for instance in lab.suite(seed, *CAMPAIGN_DOMAINS).instances():
            topology.materialize(instance)
            parts = instance.id.split(".")
            rendered.add(".".join((parts[0], parts[1], parts[-1])))
        assert rendered == declared, sorted(declared ^ rendered)


@by_lab
def test_the_lan_zone_names_every_declared_host_at_its_address(lab):
    """The DHCP oracles have a leased desk resolve its own name in this zone: a record
    missing or pointing elsewhere fails the healthy lab, and the descriptor's `hosts`
    is what tells the compiler which names exist."""
    service = next(spec for spec in lab.topology["services"].values()
                   if spec.get("application") == "dns" and spec["node"] == "dns-access-4")
    path = REPO / f"benchmarks/testbeds/containerlab/{lab.name}/dns/access/{service['zone_file']}"
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split(";")[0].split()
        if len(words) == 4 and words[1:3] == ["IN", "A"]:
            records[words[0]] = words[3]
    nodes = lab.topology["nodes"]
    declared = {name: nodes[name]["interfaces"]["eth1"]["ipv4"].split("/")[0] for name in service["hosts"]}
    assert records == declared
    leased = set(lab.topology["services"]["dhcp_campus"]["pools"])
    assert leased <= set(service["hosts"])
