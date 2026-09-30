"""update_object's compiler: attributes in, the platform's native commands out."""
from __future__ import annotations

import pytest

from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
from benchmarks.platforms.containerlab.object_writes import ATTRIBUTES, OBJECT_WRITE_KINDS, UnsupportedUpdate, compile_update
from benchmarks.platforms.containerlab.safety import validate_linux_config_command


def test_every_kind_documents_its_attributes():
    assert set(ATTRIBUTES) == set(OBJECT_WRITE_KINDS)


#: The repairs the benchmark's faults call for, per platform (scenario restore commands).
REPAIRS = [
    ("nokia_srlinux", "interface", "ethernet-1/40", {"admin_state": "enable"},
     ["set / interface ethernet-1/40 admin-state enable"], ["set / interface ethernet-1/40 admin-state disable"]),
    ("nokia_srlinux", "interface", "ethernet-1/50", {"address": "10.10.50.1/24"},
     ["set / interface ethernet-1/50 subinterface 0 ipv4 address 10.10.50.1/24"],
     ["delete / interface ethernet-1/50 subinterface 0 ipv4 address 10.10.50.1/24"]),
    ("nokia_srlinux", "route", "10.10.20.0/24", {"next_hop_group": "to-leaf1"},
     ["set / network-instance default static-routes route 10.10.20.0/24 next-hop-group to-leaf1 admin-state enable"], []),
    ("nokia_srlinux", "route", "10.10.10.10/32", {"delete": True},
     ["delete / network-instance default static-routes route 10.10.10.10/32"], []),
    ("nokia_srlinux", "route", "10.10.20.0/24", {"next_hop": "10.255.0.4"},
     ["set / network-instance default next-hop-groups group nhg-10-10-20-0-24 nexthop 1 ip-address 10.255.0.4 admin-state enable",
      "set / network-instance default static-routes route 10.10.20.0/24 next-hop-group nhg-10-10-20-0-24 admin-state enable"], []),
    ("nokia_srlinux", "routing", "default", {"admin_state": "enable"},
     ["set / network-instance default admin-state enable"], ["set / network-instance default admin-state disable"]),
    ("nokia_srlinux", "acl", "ibn-subnet-guard", {"delete": True, "unbind_interfaces": ["ethernet-1/20"]},
     ["delete / acl interface ethernet-1/20.0", "delete / acl acl-filter ibn-subnet-guard type ipv4"], []),
    ("linux", "interface", "eth1", {"admin_state": "enable"}, ["ip link set dev eth1 up"], ["ip link set dev eth1 down"]),
    ("linux", "interface", "eth1", {"mtu": 1500}, ["ip link set dev eth1 mtu 1500"], []),
    ("linux", "interface", "eth1", {"address": "10.10.20.10/24"},
     ["ip address replace 10.10.20.10/24 dev eth1"], ["ip address del 10.10.20.10/24 dev eth1"]),
    ("linux", "interface", "eth1", {"address": "192.0.2.10/24", "address_action": "delete"},
     ["ip address del 192.0.2.10/24 dev eth1"], ["ip address replace 192.0.2.10/24 dev eth1"]),
    ("linux", "route", "default", {"next_hop": "10.10.40.1"}, ["ip route replace default via 10.10.40.1"], []),
    ("linux", "route", "default", {"delete": True}, ["ip route del default"], []),
    ("linux", "qos", "eth1", {"delete_root": True}, ["tc qdisc del dev eth1 root"], []),
    ("linux", "qos", "eth2", {"htb": {"default": 20, "rate": "10mbit",
                                       "classes": [{"id": "1:10", "rate": "8mbit", "ceil": "10mbit", "prio": 1}],
                                       "filters": [{"src": "10.10.10.0/24", "flowid": "1:10"}]}},
     ["tc qdisc replace dev eth2 root handle 1: htb default 20",
      "tc class replace dev eth2 parent 1: classid 1:1 htb rate 10mbit ceil 10mbit",
      "tc class replace dev eth2 parent 1:1 classid 1:10 htb rate 8mbit ceil 10mbit prio 1",
      "tc filter add dev eth2 parent 1: protocol ip prio 1 u32 match ip src 10.10.10.0/24 flowid 1:10"], []),
    ("linux", "qos", "eth2", {"class": {"id": "1:10", "rate": "8mbit", "ceil": "10mbit", "prio": 1}},
     ["tc class change dev eth2 parent 1:1 classid 1:10 htb rate 8mbit ceil 10mbit prio 1"], []),
    ("vyos", "dhcp_pool", "GUEST1", {"subnet": "10.10.20.0/24", "name_server": "10.10.60.10"},
     ["delete service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 option name-server",
      "set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 option name-server 10.10.60.10"], []),
    ("vyos", "dhcp_pool", "USER1", {"subnet": "10.10.10.0/24", "default_router": "10.10.10.1"},
     ["set service dhcp-server shared-network-name USER1 subnet 10.10.10.0/24 option default-router 10.10.10.1"], []),
    ("vyos", "dhcp_pool", "GUEST1", {"subnet": "10.10.20.0/24", "subnet_id": 20, "lease": 3600,
                                     "static_mapping": [{"host": "guest1", "mac": "02:C1:AB:0A:14:0A", "ip": "10.10.20.10"}]},
     ["set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 subnet-id 20",
      "set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 lease 3600",
      "set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 static-mapping guest1 mac 02:c1:ab:0a:14:0a",
      "set service dhcp-server shared-network-name GUEST1 subnet 10.10.20.0/24 static-mapping guest1 ip-address 10.10.20.10"], []),
    ("vyos", "zone", "UPLINK", {"from_zone": "GUEST", "ruleset": "GUEST-TO-UPLINK"},
     ["set firewall zone UPLINK from GUEST firewall name GUEST-TO-UPLINK"], ["delete firewall zone UPLINK from GUEST"]),
    ("vyos", "zone", "DMZ", {"add_interface": "eth3"},
     ["set firewall zone DMZ interface eth3"], ["delete firewall zone DMZ interface eth3"]),
    ("vyos", "firewall_rule", "DMZ-TO-INTERNAL", {"rule": 1, "delete": True},
     ["delete firewall ipv4 name DMZ-TO-INTERNAL rule 1"], []),
    ("vyos", "firewall_rule", "DMZ-TO-INTERNAL", {"rule": 10, "action": "accept"},
     ["set firewall ipv4 name DMZ-TO-INTERNAL rule 10 action accept"], []),
    ("vyos", "route", "10.10.40.0/24", {"next_hop": "10.255.0.1"},
     ["set protocols static route 10.10.40.0/24 next-hop 10.255.0.1"], ["delete protocols static route 10.10.40.0/24 next-hop 10.255.0.1"]),
    ("vyos", "interface", "eth3", {"admin_state": "disable"},
     ["set interfaces ethernet eth3 disable"], ["delete interfaces ethernet eth3 disable"]),
]

#: What the vocabulary had no word for before the NetRepairArena gpt-5.4 campaign (2026-09-25):
#: every one of these had to be written as native commands, and most attempts were refused.
EXTENSIONS = [
    # SR Linux subinterfaces: the port's admin state is not the subinterface's, nor its IPv4's
    ("nokia_srlinux", "interface", "ethernet-1/3.0", {"admin_state": "enable"},
     ["set / interface ethernet-1/3 subinterface 0 admin-state enable"], []),
    ("nokia_srlinux", "interface", "ethernet-1/3", {"ipv4_admin_state": "enable"},
     ["set / interface ethernet-1/3 subinterface 0 ipv4 admin-state enable"], []),
    ("nokia_srlinux", "interface", "irb0.40", {"ipv4_admin_state": "enable", "address": "192.0.2.1/24"},
     ["set / interface irb0 subinterface 40 ipv4 admin-state enable",
      "set / interface irb0 subinterface 40 ipv4 address 192.0.2.1/24"], []),
    # one entry out of a filter that stays; a binding named by its subinterface
    ("nokia_srlinux", "acl", "EDGE-IN", {"delete_entry": [10, 20]},
     ["delete / acl acl-filter EDGE-IN type ipv4 entry 10", "delete / acl acl-filter EDGE-IN type ipv4 entry 20"], []),
    ("nokia_srlinux", "acl", "BLOCK-X", {"delete": True, "unbind_interfaces": ["ethernet-1/20.0"]},
     ["delete / acl interface ethernet-1/20.0", "delete / acl acl-filter BLOCK-X type ipv4"], []),
    # VyOS VLAN subinterfaces are vifs of their parent, not interfaces of their own
    ("vyos", "interface", "eth1.10", {"admin_state": "enable"},
     ["delete interfaces ethernet eth1 vif 10 disable"], ["set interfaces ethernet eth1 vif 10 disable"]),
    ("vyos", "interface", "eth1.10", {"address": "192.168.100.1/24"},
     ["set interfaces ethernet eth1 vif 10 address 192.168.100.1/24"],
     ["delete interfaces ethernet eth1 vif 10 address 192.168.100.1/24"]),
    # VyOS forwarding and its base forward filter
    ("vyos", "routing", "default", {"admin_state": "enable"}, ["delete system ip disable-forwarding"], []),
    ("vyos", "routing", "default", {"admin_state": "disable"}, ["set system ip disable-forwarding"], []),
    ("vyos", "firewall_rule", "forward", {"default_action": "accept"},
     ["set firewall ipv4 forward filter default-action accept"], []),
    ("vyos", "firewall_rule", "forward", {"rule": 10, "delete": True}, ["delete firewall ipv4 forward filter rule 10"], []),
    # QoS classifiers, alone or with the hierarchy, in the form the Linux grammar accepts
    ("linux", "qos", "eth2", {"filter": {"src": "192.168.10.0/24", "flowid": "1:10"}},
     ["tc filter add dev eth2 parent 1: protocol ip prio 1 u32 match ip src 192.168.10.0/24 flowid 1:10"], []),
    ("linux", "qos", "eth2", {"filter": {"dst": "198.51.100.0/24", "flowid": "1:20", "prio": 2}},
     ["tc filter add dev eth2 parent 1: protocol ip prio 2 u32 match ip dst 198.51.100.0/24 flowid 1:20"], []),
    ("linux", "qos", "eth2", {"delete_filter": {"prio": 1}}, ["tc filter del dev eth2 parent 1: protocol ip prio 1 u32"], []),
    ("linux", "qos", "eth2", {"delete_root": True, "htb": {"default": 20, "rate": "20mbit",
                                                          "classes": [{"id": "1:20", "rate": "7mbit", "ceil": "20mbit"}],
                                                          "filters": [{"dst": "198.51.100.0/24", "flowid": "1:20"}]}},
     ["tc qdisc del dev eth2 root",
      "tc qdisc replace dev eth2 root handle 1: htb default 20",
      "tc class replace dev eth2 parent 1: classid 1:1 htb rate 20mbit ceil 20mbit",
      "tc class replace dev eth2 parent 1:1 classid 1:20 htb rate 7mbit ceil 20mbit",
      "tc filter add dev eth2 parent 1: protocol ip prio 1 u32 match ip dst 198.51.100.0/24 flowid 1:20"], []),
]


@pytest.mark.parametrize("platform, kind, name, changes, commands, rollback", REPAIRS + EXTENSIONS)
def test_the_faults_repairs_compile_to_their_native_commands(platform, kind, name, changes, commands, rollback):
    compiled = compile_update(platform, kind, name, changes)
    assert compiled.commands == commands
    assert compiled.rollback_commands == rollback


@pytest.mark.parametrize("platform, kind, name, changes, message", [
    ("linux", "zone", "GUEST", {"add_interface": "eth1"}, "no zone objects"),
    ("nokia_srlinux", "interface", "ethernet-1/40", {"speed": "10G"}, "no attribute speed"),
    ("nokia_srlinux", "interface", "ethernet-1/40", {"admin_state": "on"}, "admin_state must be one of"),
    ("linux", "route", "default", {"next_hop": "gateway"}, "next_hop must be an IPv4 address"),
    ("linux", "route", "10.10.40.0/24", {}, "non-empty object"),
    ("linux", "route", "default", {"delete": True, "next_hop": "10.10.40.1"}, "delete does not combine"),
    ("vyos", "dhcp_pool", "GUEST1", {"lease": 3600}, "needs subnet"),
    ("vyos", "firewall_rule", "X", {"action": "drop"}, "need a rule number"),
    ("linux", "interface", "eth1", {"address": "10.10.20.10"}, "prefix length"),
    ("linux", "interface", "eth1; reboot", {"mtu": 1500}, "name must be"),
    ("nokia_srlinux", "acl", "guard", {"unbind_interfaces": ["ethernet-1/20"]}, "delete: true"),
    ("linux", "qos", "eth2", {"class": {"id": "1:10"}}, "needs id and rate"),
    # a number given as an object must be refused by name, not escape as a TypeError
    # (E4 wan_shaping.high.m1, 2026-09-20: the agent put the class list under htb.default)
    ("linux", "qos", "eth2", {"htb": {"default": {"ceil": "20mbit", "classes": []}}}, "htb.default must be an integer"),
    ("linux", "qos", "eth2", {"htb": {"default": "twenty"}}, "htb.default must be an integer"),
    ("linux", "qos", "eth2", {"class": {"id": "1:10", "rate": "8mbit", "prio": {"high": True}}}, "class.prio must be an integer"),
    ("linux", "qos", "eth2", {"htb": {"filters": [{"dst": "10.10.20.0/24", "flowid": "1:10", "prio": [1]}]}}, "filters.prio must be an integer"),
])
def test_requests_the_ani_cannot_form_are_refused_by_name(platform, kind, name, changes, message):
    with pytest.raises(UnsupportedUpdate, match=message):
        compile_update(platform, kind, name, changes)


def _grammar(platform: str):
    """The check the ANI applies to a native command on that platform before any device sees it."""
    if platform == "linux":
        return validate_linux_config_command
    if platform == "vyos":
        return lambda command: ContainerLabExecutor.parse_vyos_cli_batch([command])
    return lambda command: ContainerLabExecutor.parse_srl_cli_batch([command])


@pytest.mark.parametrize("platform, kind, name, changes, commands, rollback", REPAIRS + EXTENSIONS)
def test_every_compiled_command_passes_the_grammar_the_ani_enforces(platform, kind, name, changes, commands, rollback):
    """What update_object writes, update_config's preflight must accept, compensations included.

    Checking the compiled text alone let `tc filter replace` through for months: the
    compiler and the grammar disagreed, and every classifier failed its preflight.
    """
    compiled = compile_update(platform, kind, name, changes)
    check = _grammar(platform)
    for command in compiled.commands + compiled.rollback_commands:
        check(command)


@pytest.mark.parametrize("platform, kind, name, changes, message", [
    ("linux", "interface", "eth1", {"ipv4_admin_state": "enable"}, "SR Linux subinterface setting"),
    ("vyos", "interface", "eth1.10", {"ipv4_admin_state": "enable"}, "SR Linux subinterface setting"),
    ("nokia_srlinux", "interface", "ethernet-1/3.0", {"mtu": 1500}, "mtu is set on the port"),
    ("nokia_srlinux", "interface", "ethernet-1/3.0", {"ipv4_admin_state": "up"}, "ipv4_admin_state must be one of"),
    ("nokia_srlinux", "acl", "EDGE-IN", {"delete_entry": 10, "delete": True}, "does not combine"),
    ("nokia_srlinux", "acl", "EDGE-IN", {"delete_entry": "ten"}, "delete_entry must be an integer"),
    ("vyos", "routing", "VRF-A", {"admin_state": "enable"}, "name default"),
    ("linux", "routing", "default", {"admin_state": "enable"}, "no routing objects"),
    ("vyos", "firewall_rule", "forward", {"default_action": "reject"}, "default_action must be one of accept, drop"),
    ("linux", "qos", "eth2", {"filter": {"src": "192.168.10.0/24", "dst": "198.51.100.0/24", "flowid": "1:10"}}, "not both"),
    ("linux", "qos", "eth2", {"filter": {"src": "192.168.10.7/24", "flowid": "1:10"}}, "IPv4 prefix"),
    ("linux", "qos", "eth2", {"delete_filter": {}}, "needs the prio"),
])
def test_the_new_words_are_refused_where_they_mean_nothing(platform, kind, name, changes, message):
    with pytest.raises(UnsupportedUpdate, match=message):
        compile_update(platform, kind, name, changes)
