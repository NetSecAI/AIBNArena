"""No ANI write reaches a node's out-of-band management plane.

A subject, with a host's data address removed, put the address back on eth0 or
routed the host out through the management gateway (E4 all_tools, remove_ip m2 and m4).
Both writes were accepted. They are refused now, on every platform, and nothing that a
scenario topology declares as data plane is caught with them.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from benchmarks.platforms.containerlab.ani import (
    ANI_VERSION,
    SUPPORTED_WRITE_KINDS,
    ANIRequestError,
    ContainerLabANI,
)
from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
from benchmarks.platforms.containerlab.management import (
    MANAGEMENT_INTERFACES,
    MANAGEMENT_NETWORK_INSTANCES,
    MANAGEMENT_PLANE_RULE,
    management_networks,
    management_plane_reference,
)
from benchmarks.platforms.containerlab.tests.test_executor_transactions import RecordingRunner
from benchmarks.platforms.containerlab.types import LabNode

REPO = Path(__file__).resolve().parents[4]
SMALL = Path("benchmarks/testbeds/containerlab/sme01-small/topology.clab.yml")
FW = Path("benchmarks/testbeds/containerlab/sme01-fw/topology.clab.yml")
SMALL_MGMT = (ipaddress.ip_network("172.21.0.0/24"),)
FW_MGMT = (ipaddress.ip_network("172.23.0.0/24"),)

#: containerlab's kind for each kind the scenario topologies write.
CLAB_KINDS = {"linux": "linux", "vyos": "vyosnetworks_vyos", "nokia_srlinux": "nokia_srlinux",
              "arista_ceos": "arista_ceos"}

SCENARIO_TOPOLOGIES = sorted((REPO / "scenarios" / "topologies").glob("*.yaml"))


def _ani(runner: RecordingRunner, topology_path: Path | None = SMALL) -> ContainerLabANI:
    nodes = {
        "app1": LabNode("app1", "linux", "clab-sme01-small-app1"),
        "guest1": LabNode("guest1", "linux", "clab-sme01-small-guest1"),
        "leaf1": LabNode("leaf1", "nokia_srlinux", "clab-sme01-small-leaf1"),
        "fw-int": LabNode("fw-int", "vyos", "clab-sme01-fw-fw-int"),
    }
    config = SimpleNamespace(nodes=nodes, lab_name="sme01-small", topology_path=topology_path)
    env = SimpleNamespace(config=config, executor=ContainerLabExecutor(Path("unused.yaml"), runner=runner))
    return ContainerLabANI(env)


# -- which interface is the management one, per device --------------------------------

@pytest.mark.parametrize("path", SCENARIO_TOPOLOGIES, ids=lambda p: p.stem)
def test_every_writable_node_has_its_management_interface_and_no_data_port_is_one(path):
    """The management interface is containerlab's, never one the scenario wires.

    Checked on both sides: the scenario topology declares no data interface or
    address the guard would refuse, and the containerlab file -- the one the lab is
    deployed from -- puts no link on the management interface and declares the very
    kind the guard reads the interface name from.
    """
    topology = yaml.safe_load(path.read_text())["topology"]
    clab_path = REPO / topology["environment"]["topology_path"]
    clab = yaml.safe_load(clab_path.read_text())
    networks = management_networks(clab_path)
    declared = {**topology["nodes"], **(topology.get("optional_nodes") or {})}

    for name, spec in declared.items():
        kind = spec["kind"]
        if kind not in SUPPORTED_WRITE_KINDS:
            continue
        assert kind in MANAGEMENT_INTERFACES, f"{path.stem}:{name} ({kind}) has no management interface"
        assert clab["topology"]["nodes"][name]["kind"] == CLAB_KINDS[kind]
        for interface, attributes in (spec.get("interfaces") or {}).items():
            assert management_plane_reference(kind, f"x {interface}", networks) is None, (
                f"{path.stem}:{name} declares {interface} as data plane")
            if attributes.get("ipv4"):
                assert management_plane_reference(kind, f"x {attributes['ipv4']}", networks) is None

    management = {f"{name}:{MANAGEMENT_INTERFACES[spec['kind']]}"
                  for name, spec in declared.items() if spec["kind"] in MANAGEMENT_INTERFACES}
    for link in clab["topology"].get("links") or []:
        # The brief form ("user1:eth1") and the extended one ({node, interface}).
        endpoints = {endpoint if isinstance(endpoint, str) else f"{endpoint['node']}:{endpoint['interface']}"
                     for endpoint in link["endpoints"]}
        assert not management.intersection(endpoints), link


@pytest.mark.parametrize("path", SCENARIO_TOPOLOGIES, ids=lambda p: p.stem)
def test_the_management_network_is_the_one_the_lab_is_deployed_with(path):
    clab_path = yaml.safe_load(path.read_text())["topology"]["environment"]["topology_path"]
    declared = yaml.safe_load((REPO / clab_path).read_text())["mgmt"]["ipv4-subnet"]
    # Relative, as the scenario topology names it, from whichever directory runs this.
    assert management_networks(clab_path) == (ipaddress.ip_network(declared),)


def test_containerlab_default_without_a_topology_file_and_an_error_for_a_missing_one(tmp_path):
    assert management_networks(None) == (ipaddress.ip_network("172.20.20.0/24"),)
    undeclared = tmp_path / "lab.clab.yml"
    undeclared.write_text("name: lab\ntopology: {nodes: {}}\n")
    assert management_networks(undeclared) == (ipaddress.ip_network("172.20.20.0/24"),)
    dual = tmp_path / "dual.clab.yml"
    dual.write_text("name: lab\nmgmt: {ipv4-subnet: 172.30.0.0/24, ipv6-subnet: '3fff:172:30::/64'}\n")
    assert management_networks(dual) == (ipaddress.ip_network("172.30.0.0/24"),
                                         ipaddress.ip_network("3fff:172:30::/64"))
    with pytest.raises(OSError):
        management_networks(tmp_path / "absent.clab.yml")


# -- what a command touches ------------------------------------------------------------

@pytest.mark.parametrize("kind, command, networks", [
    # Linux: eth0 and what hangs off it, and the management gateway as a next hop.
    ("linux", "ip address replace 10.10.20.10/24 dev eth0", SMALL_MGMT),
    ("linux", "ip address del 172.21.0.4/24 dev eth0", SMALL_MGMT),
    ("linux", "ip link set dev eth0 down", SMALL_MGMT),
    ("linux", "ip link set eth0.10 up", SMALL_MGMT),
    ("linux", "ip address add 10.10.20.10/24 dev eth1 label eth0:1", SMALL_MGMT),
    ("linux", "ip route replace default via 172.21.0.1", SMALL_MGMT),
    ("linux", "ip route replace 10.10.0.0/16 via 10.10.50.1 dev eth0", SMALL_MGMT),
    ("linux", "ip route del 172.21.0.0/24", SMALL_MGMT),
    ("linux", "ip address replace 172.21.0.50/24 dev eth1", SMALL_MGMT),
    ("linux", "tc qdisc del dev eth0 root", SMALL_MGMT),
    # SR Linux: mgmt0, its subinterface, the mgmt instance, the management gateway.
    ("nokia_srlinux", "set / interface mgmt0 admin-state disable", SMALL_MGMT),
    ("nokia_srlinux", "delete / interface mgmt0 subinterface 0 ipv4 address 172.21.0.6/24", SMALL_MGMT),
    ("nokia_srlinux", "set / network-instance default interface mgmt0.0", SMALL_MGMT),
    ("nokia_srlinux", "set / acl interface mgmt0.0 input acl-filter BLOCK type ipv4", SMALL_MGMT),
    ("nokia_srlinux", "set / network-instance mgmt admin-state disable", SMALL_MGMT),
    ("nokia_srlinux", "delete / network-instance mgmt static-routes route 0.0.0.0/0", SMALL_MGMT),
    ("nokia_srlinux", "set / system json-rpc-server network-instance mgmt admin-state disable", SMALL_MGMT),
    ("nokia_srlinux", "set / interface[name=mgmt0] admin-state disable", SMALL_MGMT),
    ("nokia_srlinux", "set / network-instance default next-hop-groups group g nexthop 1 ip-address 172.21.0.1", SMALL_MGMT),
    # VyOS: eth0, which its configuration tree does not even show, and the gateway.
    ("vyos", "set interfaces ethernet eth0 address 10.10.10.1/24", FW_MGMT),
    ("vyos", "set interfaces ethernet eth0 disable", FW_MGMT),
    ("vyos", "set firewall zone MGMT interface eth0", FW_MGMT),
    ("vyos", "set firewall zone USERS interface eth0.10", FW_MGMT),
    ("vyos", "set protocols static route 0.0.0.0/0 next-hop 172.23.0.1", FW_MGMT),
    # Arista EOS: Management0 under every spelling EOS accepts, and the gateway.
    ("arista_ceos", "interface Management0", SMALL_MGMT),
    ("arista_ceos", "interface Management 0", SMALL_MGMT),
    ("arista_ceos", "interface ma0", SMALL_MGMT),
    ("arista_ceos", "default interface Manag0", SMALL_MGMT),
    ("arista_ceos", "ip route 0.0.0.0/0 172.21.0.1", SMALL_MGMT),
])
def test_the_management_plane_is_recognised(kind, command, networks):
    assert management_plane_reference(kind, command, networks) is not None


@pytest.mark.parametrize("kind, command, networks", [
    ("linux", "ip address replace 10.10.20.10/24 dev eth1", SMALL_MGMT),
    ("linux", "ip route replace default via 10.10.50.1 dev eth1", SMALL_MGMT),
    ("linux", "ip route replace 0.0.0.0/0 via 10.10.50.1", SMALL_MGMT),
    ("linux", "ip route replace 172.16.0.0/12 via 10.10.50.1", SMALL_MGMT),
    ("linux", "ip link set dev eth10 up", SMALL_MGMT),
    ("linux", "tc class replace dev eth1 parent 1: classid 1:10 htb rate 10mbit ceil 10mbit", SMALL_MGMT),
    # The in-band management VLAN is data plane.
    ("nokia_srlinux", "set / interface ethernet-1/57 admin-state enable", SMALL_MGMT),
    ("nokia_srlinux", "set / interface ethernet-1/57 subinterface 0 ipv4 address 10.10.99.1/24", SMALL_MGMT),
    ("nokia_srlinux", "set / network-instance vlan99 description mgmt-vlan99", SMALL_MGMT),
    ("nokia_srlinux", "set / network-instance default static-routes route 0.0.0.0/0 next-hop-group to-leaf2 admin-state enable", SMALL_MGMT),
    ("nokia_srlinux", 'set / interface ethernet-1/1 description "uplink, not mgmt0"', SMALL_MGMT),
    ("vyos", "set interfaces ethernet eth1 vif 99 description mgmt-gateway", FW_MGMT),
    ("vyos", "set firewall zone MGMT interface eth1.99", FW_MGMT),
    ("vyos", "set firewall ipv4 name MGMT-TO-USERS rule 10 action accept", FW_MGMT),
    ("vyos", "set protocols static route 10.10.40.0/24 next-hop 10.255.1.2", FW_MGMT),
    # Another lab's management network is that lab's business.
    ("vyos", "set protocols static route 10.10.40.0/24 next-hop 172.21.0.1", FW_MGMT),
    ("arista_ceos", "interface Ethernet3", SMALL_MGMT),
    ("arista_ceos", "description uplink, Management0 is not this one", SMALL_MGMT),
    ("arista_ceos", "mlag 2", SMALL_MGMT),
    ("arista_ceos", "max-routes 2", SMALL_MGMT),
    ("arista_ceos", "ip route 10.10.40.0/24 10.255.1.2", SMALL_MGMT),
])
def test_the_data_plane_is_left_alone(kind, command, networks):
    assert management_plane_reference(kind, command, networks) is None


# -- through the ANI -------------------------------------------------------------------

def test_the_address_put_back_on_eth0_is_refused_before_any_device_access():
    """E4 all_tools remove_ip m2: guest1 lost 10.10.20.10/24 on eth1."""
    runner = RecordingRunner(0)
    with pytest.raises(ANIRequestError, match=r"management plane of guest1 .*eth0"):
        _ani(runner).dispatch("update_object", {"node": "guest1", "kind": "interface", "name": "eth0",
                                                "set": {"address": "10.10.20.10/24"}})
    assert runner.calls == []


def test_the_default_route_through_the_management_gateway_is_refused():
    """E4 all_tools remove_ip m4: app1's address was narrowed to /32."""
    runner = RecordingRunner(0)
    with pytest.raises(ANIRequestError, match=r"172\.21\.0\.1 lies in the management network 172\.21\.0\.0/24"):
        _ani(runner).dispatch("update_object", {"node": "app1", "kind": "route", "name": "default",
                                                "set": {"next_hop": "172.21.0.1"}})
    assert runner.calls == []


def test_a_raw_update_config_is_refused_whole_and_names_the_plane():
    runner = RecordingRunner(0)
    changes = [
        # A legitimate change first: the request is refused whole, before it runs.
        {"target": "app1", "commands": ["ip address replace 10.10.50.10/24 dev eth1"],
         "rollback_commands": ["ip address del 10.10.50.10/24 dev eth1"]},
        {"target": "leaf1", "commands": ["set / interface mgmt0 admin-state disable"]},
    ]
    with pytest.raises(ANIRequestError) as refused:
        _ani(runner).dispatch("update_config", {"changes": changes})
    message = str(refused.value)
    assert "update_config.change.commands refused" in message
    assert "interface mgmt0, network 172.21.0.0/24" in message
    assert "data-plane interfaces" in message
    assert runner.calls == []


def test_a_compensation_into_the_management_plane_is_refused_too():
    runner = RecordingRunner(0)
    with pytest.raises(ANIRequestError, match="update_config.change.rollback_commands refused"):
        _ani(runner).dispatch("update_config", {"changes": [
            {"target": "app1", "commands": ["ip route replace default via 10.10.50.1"],
             "rollback_commands": ["ip route replace default via 172.21.0.1 dev eth0"]}]})
    assert runner.calls == []


def test_the_management_network_comes_from_the_lab_being_written():
    # sme01-fw's management network is 172.23.0.0/24; 172.21.0.1 is nothing there.
    runner = RecordingRunner(0)
    ani = _ani(runner, FW)
    with pytest.raises(ANIRequestError, match="172.23.0.1"):
        ani.dispatch("update_config", {"changes": [
            {"target": "fw-int", "commands": ["set protocols static route 0.0.0.0/0 next-hop 172.23.0.1"]}]})
    result = ani.dispatch("update_config", {"changes": [
        {"target": "app1", "commands": ["ip route replace 10.10.0.0/16 via 10.10.50.1 dev eth1"]}]})
    assert result["ok"] is True
    assert len(runner.calls) == 1


def test_data_plane_writes_still_go_through():
    runner = RecordingRunner(0)
    result = _ani(runner).dispatch("update_config", {"changes": [
        {"target": "leaf1", "commands": ["set / interface ethernet-1/57 admin-state enable"],
         "rollback_commands": ["set / interface ethernet-1/57 admin-state disable"]},
        {"target": "fw-int", "commands": ["set firewall zone MGMT interface eth1.99"]},
    ]})
    assert result["ok"] is True
    assert len(runner.calls) == 2


# -- what the agent is told before writing -----------------------------------------------

def test_the_rule_names_every_management_interface_and_instance_the_guard_refuses():
    for interface in MANAGEMENT_INTERFACES.values():
        assert interface in MANAGEMENT_PLANE_RULE
    for instance in MANAGEMENT_NETWORK_INSTANCES.values():
        assert f"{instance} network-instance" in MANAGEMENT_PLANE_RULE


def test_both_write_operations_state_the_rule_and_the_version_says_so():
    """The surface an agent is shown changed, so the version every record carries did too."""
    assert ANI_VERSION == "ibn_eval.ani.v0.11"
    contract = {item["name"]: item["description"] for item in ContainerLabANI.tool_contract()}
    schemas = {schema["function"]["name"]: schema["function"]["description"]
               for schema in ContainerLabANI.tool_schemas()}
    for name in ("update_config", "update_object"):
        assert MANAGEMENT_PLANE_RULE in contract[name], name
        assert MANAGEMENT_PLANE_RULE in schemas[name], name
    reads = ("get_topology", "get_state", "get_running_config", "get_object", "execute_validation")
    assert not any(MANAGEMENT_PLANE_RULE in schemas[name] for name in reads), "reads are not restricted"
