"""update_object compensations derived from the node's state read just before the change.

The SR Linux tree is leaf1's own running configuration as e10 recorded it
(connectivity.drop_traffic_to_from_subnet.high.m3, before the subject), cut at whole
blocks and otherwise verbatim: the `!!!` banner, quoted values, empty list entries and a
multi-line leaf-list are all the node's. The VyOS lines are `show configuration commands`
in the form the get_object probes already match; the Linux lines are `ip -o` output.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
from benchmarks.platforms.containerlab.object_rollback import (
    linux_mtu_compensation,
    linux_preimage_read,
    linux_route_compensation,
    parse_srl_running,
    srl_compensation,
    vyos_compensation,
)
from benchmarks.platforms.containerlab.object_writes import compile_update
from benchmarks.platforms.containerlab.safety import validate_linux_config_command

ROOT = Path(__file__).resolve().parents[4]

SRL_RUNNING = """\
    !!! ACL rules allowing incoming tcp/57411 for the eda-discovery grpc server
    interface ethernet-1/20 {
        admin-state enable
        subinterface 0 {
            admin-state enable
            ipv4 {
                admin-state enable
                address 10.10.20.1/24 {
                }
            }
        }
    }
    interface ethernet-1/30 {
        admin-state enable
    }
    network-instance default {
        interface ethernet-1/1.0 {
        }
        interface ethernet-1/20.0 {
        }
        static-routes {
            route 10.10.30.0/24 {
                admin-state enable
                next-hop-group to-leaf2
            }
            route 10.10.40.0/24 {
                admin-state enable
                next-hop-group to-leaf2
            }
        }
        next-hop-groups {
            group to-leaf2 {
                nexthop 1 {
                    ip-address 10.255.0.0
                    admin-state enable
                }
                nexthop 2 {
                    ip-address 10.255.0.2
                    admin-state enable
                }
            }
        }
    }
    network-instance mgmt {
        type ip-vrf
        admin-state enable
        description "Management network instance"
        interface mgmt0.0 {
        }
    }
    system {
        grpc-server eda-discovery {
            admin-state enable
            network-instance mgmt
            port 50052
            services [
                gnmi
                gnsi
            ]
        }
    }
    acl {
        acl-filter ibn-subnet-guard type ipv4 {
            entry 100 {
                match {
                    ipv4 {
                        source-ip {
                            prefix 10.10.20.0/24
                        }
                    }
                }
                action {
                    drop {
                    }
                }
            }
            entry 200 {
                match {
                    ipv4 {
                        destination-ip {
                            prefix 10.10.20.0/24
                        }
                    }
                }
                action {
                    drop {
                    }
                }
            }
        }
        interface ethernet-1/20.0 {
            interface-ref {
                interface ethernet-1/20
                subinterface 0
            }
            input {
                acl-filter ibn-subnet-guard type ipv4 {
                }
            }
            output {
                acl-filter ibn-subnet-guard type ipv4 {
                }
            }
        }
    }
"""

ROUTE = "network-instance default static-routes route"
GROUP = "network-instance default next-hop-groups group"


def _srl(kind: str, name: str, changes: dict) -> list[str] | None:
    commands = compile_update("nokia_srlinux", kind, name, changes).commands
    compensation = srl_compensation(commands, SRL_RUNNING)
    if compensation is not None:
        # Whatever is derived must be a batch the ANI's own SR Linux grammar accepts.
        ContainerLabExecutor.parse_srl_cli_batch(compensation)
    return compensation


# -- SR Linux -----------------------------------------------------------------------

def test_a_route_that_did_not_exist_is_deleted_again_with_the_group_made_for_it():
    assert _srl("route", "10.10.10.0/24", {"next_hop": "10.255.0.0"}) == [
        f"delete / {GROUP} nhg-10-10-10-0-24",
        f"delete / {ROUTE} 10.10.10.0/24",
    ]


def test_a_replaced_route_is_put_back_as_it_was():
    """e10 disable_routing.m1: the agent repointed existing routes at a new group."""
    assert _srl("route", "10.10.30.0/24", {"next_hop": "10.255.0.7"}) == [
        f"delete / {GROUP} nhg-10-10-30-0-24",
        f"delete / {ROUTE} 10.10.30.0/24",
        f"set / {ROUTE} 10.10.30.0/24 admin-state enable",
        f"set / {ROUTE} 10.10.30.0/24 next-hop-group to-leaf2",
    ]


def test_a_deleted_route_is_replayed_from_the_running_configuration():
    assert _srl("route", "10.10.40.0/24", {"delete": True}) == [
        f"set / {ROUTE} 10.10.40.0/24 admin-state enable",
        f"set / {ROUTE} 10.10.40.0/24 next-hop-group to-leaf2",
    ]


def test_an_mtu_goes_back_to_its_value_or_to_the_default():
    # ethernet-1/30 states no MTU: the change is undone by deleting the leaf.
    assert _srl("interface", "ethernet-1/30", {"mtu": 9000}) == ["delete / interface ethernet-1/30 mtu"]
    with_mtu = SRL_RUNNING.replace(
        "    interface ethernet-1/30 {\n        admin-state enable\n",
        "    interface ethernet-1/30 {\n        admin-state enable\n        mtu 9500\n")
    commands = compile_update("nokia_srlinux", "interface", "ethernet-1/30", {"mtu": 9000}).commands
    assert srl_compensation(commands, with_mtu) == ["set / interface ethernet-1/30 mtu 9500"]


def test_a_deleted_acl_entry_comes_back_with_its_match_and_action():
    assert _srl("acl", "ibn-subnet-guard", {"delete_entry": 100}) == [
        "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 match ipv4 source-ip prefix 10.10.20.0/24",
        "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 action drop",
    ]


def test_a_deleted_acl_comes_back_with_its_binding():
    compensation = _srl("acl", "ibn-subnet-guard", {"delete": True, "unbind_interfaces": ["ethernet-1/20"]})
    assert compensation is not None
    assert "set / acl interface ethernet-1/20.0 interface-ref interface ethernet-1/20" in compensation
    assert "set / acl interface ethernet-1/20.0 input acl-filter ibn-subnet-guard type ipv4" in compensation
    assert "set / acl interface ethernet-1/20.0 output acl-filter ibn-subnet-guard type ipv4" in compensation
    assert ("set / acl acl-filter ibn-subnet-guard type ipv4 entry 200 match ipv4 destination-ip "
            "prefix 10.10.20.0/24") in compensation
    assert not any(line.startswith("delete") for line in compensation), "nothing is left to delete"


def test_a_subtree_holding_a_leaf_list_gets_no_compensation():
    with_tags = SRL_RUNNING.replace(
        "            route 10.10.40.0/24 {\n",
        "            route 10.10.40.0/24 {\n                tags [\n                    a\n                ]\n")
    commands = compile_update("nokia_srlinux", "route", "10.10.40.0/24", {"delete": True}).commands
    assert srl_compensation(commands, with_tags) is None


def test_no_compensation_from_a_read_that_is_not_a_running_configuration():
    commands = compile_update("nokia_srlinux", "route", "10.10.30.0/24", {"next_hop": "10.255.0.7"}).commands
    # What a test runner or a confused CLI answers: rc 0, and not the node's configuration.
    assert srl_compensation(commands, "All changes have been committed. Leaving candidate mode.\n") is None
    assert srl_compensation(commands, "    interface ethernet-1/1 {\n") is None  # unbalanced
    assert srl_compensation(["set / system name host-name leaf9"], SRL_RUNNING) is None  # unknown shape


def test_the_running_configurations_e10_recorded_parse_whole():
    snapshots = sorted((ROOT / "reports/campaigns").glob("*/*/*/*/logs/devices/*/leaf*/before_sut.txt"))[:6]
    if not snapshots:
        pytest.skip("no recorded SR Linux running configuration in this checkout")
    for snapshot in snapshots:
        root = parse_srl_running(snapshot.read_text())
        assert {block.tokens[0] for block in root.children} >= {"interface", "network-instance"}, snapshot


# -- VyOS ---------------------------------------------------------------------------

VYOS_SHOWN = """\
set firewall ipv4 name DMZ-TO-INTERNAL default-action 'drop'
set firewall ipv4 name DMZ-TO-INTERNAL rule 1 action 'drop'
set firewall ipv4 name DMZ-TO-INTERNAL rule 1 description 'ibn-shadow-deny'
set firewall ipv4 name DMZ-TO-INTERNAL rule 10 action 'accept'
set interfaces ethernet eth1 address '10.10.10.1/24'
set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 option default-router '10.10.20.1'
set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 option name-server '10.10.60.2'
set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 subnet-id '2'
set service dhcp-server shared-network-name USER1 subnet 10.10.10.0/24 option default-router '10.10.10.2'
"""
GUEST1 = "service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24"


def _vyos(kind: str, name: str, changes: dict) -> list[str] | None:
    commands = compile_update("vyos", kind, name, changes).commands
    compensation = vyos_compensation(commands, VYOS_SHOWN)
    if compensation is not None:
        ContainerLabExecutor.parse_vyos_cli_batch(compensation)
    return compensation


def test_a_dhcp_name_server_goes_back_to_the_one_it_replaced():
    """e10 dhcp_provisioning.m4: the agent rewrote the GUEST1 resolver."""
    assert _vyos("dhcp_pool", "GUEST1", {"subnet": "10.10.20.0/24", "name_server": "10.10.60.10"}) == [
        f"delete {GUEST1} option name-server",
        f"set {GUEST1} option name-server '10.10.60.2'",
    ]


def test_a_dhcp_option_that_did_not_exist_is_deleted_again():
    assert _vyos("dhcp_pool", "GUEST1", {"subnet": "10.10.20.0/24", "domain_name": "sme01.internal"}) == [
        f"delete {GUEST1} option domain-name",
    ]


def test_a_firewall_rule_action_goes_back_and_a_deleted_rule_comes_back():
    """e10 filtering.zone_policy_enforcement.m2: rule 1 turned from drop to accept."""
    chain = "firewall ipv4 name DMZ-TO-INTERNAL"
    assert _vyos("firewall_rule", "DMZ-TO-INTERNAL", {"rule": 1, "action": "accept"}) == [
        f"delete {chain} rule 1 action",
        f"set {chain} rule 1 action 'drop'",
    ]
    assert _vyos("firewall_rule", "DMZ-TO-INTERNAL", {"rule": 1, "delete": True}) == [
        f"set {chain} rule 1 action 'drop'",
        f"set {chain} rule 1 description 'ibn-shadow-deny'",
    ]


def test_no_compensation_from_a_read_that_is_not_a_vyos_configuration():
    commands = compile_update("vyos", "dhcp_pool", "USER1", {"subnet": "10.10.10.0/24", "default_router": "10.10.10.1"}).commands
    assert vyos_compensation(commands, "Invalid command: [show]\n") is None


# -- Linux --------------------------------------------------------------------------

def _valid(command: str | None) -> str | None:
    if command is not None:
        validate_linux_config_command(command)
    return command


def test_a_deleted_route_is_replaced_as_the_kernel_printed_it():
    """e10 wrong_routing_table.m1: app1's default route, deleted and never restored."""
    command = compile_update("linux", "route", "default", {"delete": True}).commands[0]
    assert linux_preimage_read(command) == "ip -o -4 route show default"
    assert _valid(linux_route_compensation(command, "default via 10.10.50.1 dev eth1 \n")) == \
        "ip route replace default via 10.10.50.1 dev eth1"


def test_a_replaced_route_goes_back_or_away():
    command = compile_update("linux", "route", "default", {"next_hop": "10.10.40.1"}).commands[0]
    assert _valid(linux_route_compensation(command, "")) == "ip route del default"
    assert _valid(linux_route_compensation(command, "default via 10.10.40.254 dev eth1 linkdown\n")) == \
        "ip route replace default via 10.10.40.254 dev eth1"
    # e10 disable_routing.m3: the fault's prohibit route is what a replace overwrites.
    assert _valid(linux_route_compensation(command, "prohibit default \n")) == "ip route replace prohibit default"


def test_a_route_the_kernel_holds_twice_or_beside_a_metric_gets_none():
    delete = compile_update("linux", "route", "default", {"delete": True}).commands[0]
    replace = compile_update("linux", "route", "default", {"next_hop": "10.10.40.1"}).commands[0]
    both = "default via 10.10.40.1 dev eth1 \ndefault via 172.21.0.1 dev eth0 metric 100 \n"
    assert linux_route_compensation(delete, both) is None
    assert linux_route_compensation(replace, "default via 10.10.40.1 dev eth1 metric 100 \n") is None
    assert linux_route_compensation(delete, "") is None  # nothing there for the delete to remove


def test_no_linux_compensation_from_a_read_that_is_not_the_route():
    command = compile_update("linux", "route", "10.10.20.0/24", {"next_hop": "10.10.50.1"}).commands[0]
    assert linux_route_compensation(command, "All changes have been committed.\n") is None


def test_an_mtu_goes_back_to_the_one_the_link_had():
    command = compile_update("linux", "interface", "eth1", {"mtu": 1500}).commands[0]
    assert linux_preimage_read(command) == "ip -o link show dev eth1"
    shown = "8805: eth1@if8804: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 9500 qdisc noqueue state UP mode DEFAULT\n"
    assert _valid(linux_mtu_compensation(command, shown)) == "ip link set dev eth1 mtu 9500"


def test_a_traffic_control_tree_takes_more_than_one_command_and_gets_none():
    command = compile_update("linux", "qos", "eth2", {"delete_root": True}).commands[0]
    assert linux_preimage_read(command) is None
