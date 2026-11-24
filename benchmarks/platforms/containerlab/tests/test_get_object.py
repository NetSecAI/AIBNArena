"""get_object: one named object on one node, with the narrowest command the platform has."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.platforms.containerlab.ani import (
    ANI_OPERATIONS, ANI_VERSION, OBJECT_KINDS, _OBJECT_READS, ANIRequestError, ContainerLabANI, _lines_mentioning,
)
from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
from benchmarks.platforms.containerlab.types import LabNode

LEAF = LabNode(name="leaf2", kind="nokia_srlinux", container_name="clab-test-leaf2")
FW = LabNode(name="fw-int", kind="vyos", container_name="clab-test-fw-int")
HOST = LabNode(name="web1", kind="linux", container_name="clab-test-web1")
SWITCH = LabNode(name="sw1", kind="arista_ceos", container_name="clab-test-sw1")


class _Runner:
    """Records every command and answers with a canned text per container."""

    def __init__(self, answers: dict[str, str] | None = None, returncode: int = 0,
                 failing: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self.answers = answers or {}
        self.returncode = returncode
        #: Commands that fail whatever `returncode` says, by a part of their text.
        self.failing = failing

    def run(self, args, timeout_seconds=None, input_text=None):
        self.calls.append({"args": [str(a) for a in args], "input": input_text})
        joined = " ".join(str(a) for a in args)
        out = next((text for key, text in self.answers.items() if key in joined), "")
        code = 1 if any(key in joined for key in self.failing) else self.returncode
        return subprocess.CompletedProcess(args, code, stdout=out, stderr="" if code == 0 else "no such object\n")


def _ani(runner: _Runner) -> ContainerLabANI:
    env = SimpleNamespace(
        config=SimpleNamespace(nodes={"leaf2": LEAF, "fw-int": FW, "web1": HOST, "sw1": SWITCH}, lab_name="test"),
        executor=ContainerLabExecutor(Path("unused.yaml"), runner=runner),
    )
    return ContainerLabANI(env)


def _issued(runner: _Runner, index: int = -1) -> str:
    call = runner.calls[index]
    if call["input"]:                      # sr_cli reads the command on stdin
        return call["input"].strip()
    args = call["args"]
    if "vyatta-op-cmd-wrapper" in args:
        return " ".join(args[args.index("vyatta-op-cmd-wrapper") + 1:])
    return " ".join(args[args.index(args[-1]) - 0:])  # shell: last argument is the command


def test_the_operation_is_on_the_surface_and_the_version_says_so():
    assert "get_object" in ANI_OPERATIONS
    assert ANI_VERSION == "ibn_eval.ani.v0.11"
    schema = next(s for s in ContainerLabANI.tool_schemas() if s["function"]["name"] == "get_object")
    assert schema["function"]["parameters"]["required"] == ["node", "kind", "name"]
    assert schema["function"]["parameters"]["properties"]["kind"]["enum"] == list(OBJECT_KINDS)
    assert any(item["name"] == "get_object" and item["type"] == "read" for item in ContainerLabANI.tool_contract())


def test_a_scoped_read_names_the_object_in_the_platform_command():
    runner = _Runner({"clab-test-leaf2": "    admin-state enable\n"})
    result = _ani(runner).dispatch("get_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40"})
    assert result["ok"] is True and result["found"] is True and result["mode"] == "scoped"
    assert runner.calls[-1]["input"].strip() == _OBJECT_READS["interface"]["nokia_srlinux"][1].replace("{name}", "ethernet-1/40")
    assert "admin-state enable" in result["output"]


def test_a_filter_read_keeps_only_the_lines_that_mention_the_object():
    listing = "Address   Interface   Link layer address   State\n10.10.40.10  eth3  aa:c1:ab:80:0a:d9  REACHABLE\n10.10.50.10  eth5  aa:c1:ab:0d:9d:e3  STALE\n"
    runner = _Runner({"clab-test-fw-int": listing})
    result = _ani(runner).dispatch("get_object", {"node": "fw-int", "kind": "arp", "name": "10.10.40.10"})
    assert result["mode"] == "filter" and result["found"] is True
    assert result["output"].splitlines() == ["10.10.40.10  eth3  aa:c1:ab:80:0a:d9  REACHABLE"]
    args = runner.calls[-1]["args"]
    wrapper = next(i for i, a in enumerate(args) if a.endswith("vyatta-op-cmd-wrapper"))
    assert args[wrapper + 1:] == ["show", "arp"]


def test_an_absent_object_is_found_false_not_an_error():
    runner = _Runner({"clab-test-fw-int": "10.10.50.10  eth5  aa:c1:ab:0d:9d:e3  STALE\n"})
    result = _ani(runner).dispatch("get_object", {"node": "fw-int", "kind": "arp", "name": "10.10.40.10"})
    assert result["ok"] is True and result["found"] is False and result["output"] == ""


def test_a_linux_route_lookup_uses_ip_route_get_for_an_address_and_show_for_a_prefix():
    runner = _Runner({"clab-test-web1": "10.10.40.1 dev eth1 src 10.10.40.10\n"})
    ani = _ani(runner)
    ani.dispatch("get_object", {"node": "web1", "kind": "route", "name": "10.10.40.1"})
    assert " ".join(runner.calls[-1]["args"]).endswith("ip route get 10.10.40.1")
    ani.dispatch("get_object", {"node": "web1", "kind": "route", "name": "10.10.40.0/24"})
    assert " ".join(runner.calls[-1]["args"]).endswith("ip route show 10.10.40.0/24")


HTB_ROOT = "qdisc htb 1: root refcnt 2 r2q 10 default 0x20 direct_packets_stat 0 direct_qlen 1000\n"
HTB_CLASSES = ("class htb 1:1 root rate 10Mbit ceil 10Mbit burst 1600b cburst 1600b \n"
               "class htb 1:20 parent 1:1 prio 1 rate 8Mbit ceil 10Mbit burst 1600b cburst 1600b \n")
# No line break at the end: the next command's output must not run into it.
HTB_FILTERS = ("filter parent 1: protocol ip pref 1 u32 chain 0 fh 800::800 order 2048 key ht 800 bkt 0 "
               "flowid 1:20 \n  match 0a000000/ffffff00 at 12")


def test_a_linux_qos_read_shows_the_classes_and_the_filters_one_command_at_a_time():
    """The root qdisc alone was the whole answer until v0.6: on main03 a class of the WAN
    edge's policy, where the shaping faults are, was readable through the ANI only as JSON."""
    runner = _Runner({"tc qdisc show dev eth2": HTB_ROOT, "tc class show dev eth2": HTB_CLASSES,
                      "tc filter show dev eth2": HTB_FILTERS})
    result = _ani(runner).dispatch("get_object", {"node": "web1", "kind": "qos", "name": "eth2"})

    assert result["ok"] is True and result["found"] is True and result["mode"] == "scoped"
    # Three execs of one command each: the ANI never sends a compound shell command.
    assert [call["args"][3:] for call in runner.calls] == [
        ["tc", "qdisc", "show", "dev", "eth2"], ["tc", "class", "show", "dev", "eth2"],
        ["tc", "filter", "show", "dev", "eth2"]]
    assert result["command"] == "tc qdisc show dev eth2 && tc class show dev eth2 && tc filter show dev eth2"
    assert result["output"].splitlines() == [*HTB_ROOT.splitlines(), *HTB_CLASSES.splitlines(),
                                             *HTB_FILTERS.splitlines()]


def test_a_linux_qos_read_of_an_interface_the_node_lacks_is_not_found():
    result = _ani(_Runner(returncode=1)).dispatch("get_object", {"node": "web1", "kind": "qos", "name": "eth9"})
    assert result["ok"] is False and result["found"] is False and "no such object" in result["error"]


def test_a_kind_the_platform_does_not_have_is_answered_not_raised():
    result = _ani(_Runner()).dispatch("get_object", {"node": "web1", "kind": "zone", "name": "GUEST"})
    assert result["ok"] is False and result["found"] is False and "no zone objects" in result["error"]


def test_requests_the_ani_cannot_form_are_refused_by_name():
    ani = _ani(_Runner())
    with pytest.raises(ANIRequestError, match="kind must be one of"):
        ani.dispatch("get_object", {"node": "leaf2", "kind": "bgp_neighbor", "name": "10"})
    with pytest.raises(ANIRequestError, match="name must be"):
        ani.dispatch("get_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40; reboot"})
    with pytest.raises(ANIRequestError):
        ani.dispatch("get_object", {"node": "nowhere", "kind": "interface", "name": "eth1"})


def test_a_failed_command_reports_the_nodes_words():
    result = _ani(_Runner(returncode=1)).dispatch("get_object", {"node": "leaf2", "kind": "route", "name": "10.10.40.0/24"})
    assert result["ok"] is False and result["found"] is False and "no such object" in result["error"]


def test_lines_mentioning_keeps_context_in_order_without_duplicates():
    text = "a\nb ethernet-1/40 {\nc\nd\ne ethernet-1/40 x\nf\n"
    assert _lines_mentioning(text, "ETHERNET-1/40", 1).splitlines() == ["a", "b ethernet-1/40 {", "c", "d", "e ethernet-1/40 x", "f"]
    assert _lines_mentioning(text, "zzz", 1) == ""


VYOS_CONFIG = """set firewall ipv4 name DMZ-TO-INTERNAL default-action 'drop'
set firewall ipv4 name DMZ-TO-INTERNAL rule 10 action 'accept'
set firewall ipv4 name DMZ-TO-INTERNAL rule 70 description 'web1-ping-USER1'
set firewall zone DMZ default-action 'drop'
set firewall zone DMZ from INTERNAL firewall name 'INTERNAL-TO-DMZ'
set firewall zone DMZ member interface 'eth3'
set service dhcp-server shared-network-name USER1 subnet 10.10.10.0/24 option default-router '10.10.10.1'
set service dhcp-server shared-network-name USER1 subnet 10.10.10.0/24 range 0 start '10.10.10.100'
"""


def test_a_configuration_read_keeps_one_subtree_not_every_mention():
    runner = _Runner({"clab-test-fw-int": VYOS_CONFIG})
    ani = _ani(runner)
    zone = ani.dispatch("get_object", {"node": "fw-int", "kind": "zone", "name": "DMZ"})
    assert zone["mode"] == "config" and zone["found"] is True
    assert all(line.startswith("set firewall zone DMZ ") for line in zone["output"].splitlines())
    assert len(zone["output"].splitlines()) == 3            # not the DMZ-TO-INTERNAL rule set
    pool = ani.dispatch("get_object", {"node": "fw-int", "kind": "dhcp_pool", "name": "USER1"})
    assert len(pool["output"].splitlines()) == 2            # not the rule whose description says USER1
    rules = ani.dispatch("get_object", {"node": "fw-int", "kind": "firewall_rule", "name": "DMZ-TO-INTERNAL"})
    assert len(rules["output"].splitlines()) == 3
    assert ani.dispatch("get_object", {"node": "fw-int", "kind": "zone", "name": "NOWHERE"})["found"] is False


def test_the_nodes_own_not_found_message_is_not_a_find():
    runner = _Runner({"clab-test-fw-int": "% Network not in table\n"})
    result = _ani(runner).dispatch("get_object", {"node": "fw-int", "kind": "route", "name": "10.99.99.0/24"})
    assert result["ok"] is True and result["found"] is False and "Network not in table" in result["output"]


def test_the_default_route_is_read_under_each_platforms_name_for_it():
    runner = _Runner({"clab-test-web1": "default via 10.10.40.1 dev eth1\n", "clab-test-fw-int": "Routing entry for 0.0.0.0/0\n"})
    ani = _ani(runner)
    linux = ani.dispatch("get_object", {"node": "web1", "kind": "route", "name": "default"})
    assert " ".join(runner.calls[-1]["args"]).endswith("ip route show default") and linux["found"] is True
    vyos = ani.dispatch("get_object", {"node": "fw-int", "kind": "route", "name": "default"})
    # The status read; since v0.9 the configuration read follows it.
    status = next(call["args"] for call in runner.calls
                  if "route" in call["args"] and any(a.endswith("vyatta-op-cmd-wrapper") for a in call["args"]))
    wrapper = next(i for i, a in enumerate(status) if a.endswith("vyatta-op-cmd-wrapper"))
    assert status[wrapper + 1:] == ["show", "ip", "route", "0.0.0.0/0"] and vyos["found"] is True


# --- v0.9: every part of a VyOS or Arista EOS configuration belongs to some object --------

VYOS_FULL = """set firewall global-options state-policy established action 'accept'
set firewall group address-group SRV-DNS address '10.0.3.53'
set firewall group network-group CORP network '10.0.0.0/24'
set firewall ipv4 forward filter rule 10 action 'jump'
set firewall ipv4 input filter default-action 'accept'
set firewall ipv4 name CORP-IN default-action 'drop'
set firewall ipv4 name CORP-IN rule 10 source group network-group 'CORP'
set interfaces ethernet eth1 description 'Trunk'
set interfaces ethernet eth1 offload gro
set interfaces ethernet eth1 vif 100 address '10.0.0.1/24'
set interfaces ethernet eth1 vif 100 description 'CORP gateway'
set interfaces loopback lo
set nat source rule 100 outbound-interface name 'eth6'
set nat source rule 100 translation address 'masquerade'
set pki ca clab certificate 'MIIB'
set protocols static route 10.1.0.0/24 next-hop 10.255.254.1 distance '5'
set qos interface eth1 egress 'SHAPER-1'
set qos policy shaper SHAPER-1 bandwidth '1gbit'
set qos policy shaper SHAPER-1 class 100 match CORP vif '100'
set qos policy shaper SHAPER-2 bandwidth '10mbit'
set service ntp server time1.vyos.net
set service ssh port '22'
set system host-name 'fw'
set system ip disable-forwarding
"""
VYOS_ANSWERS = {"show configuration commands": VYOS_FULL,
                "show interfaces ethernet eth1": "eth1@if9: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n",
                "show interfaces": "lo               127.0.0.1/8                        u/u\n",
                "show ip route": "Routing entry for 10.1.0.0/24\n  * 10.255.254.1, via eth7\n"}
VYOS_OBJECTS = [("interface", "eth1"), ("interface", "eth1.100"), ("interface", "lo"), ("route", "10.1.0.0/24"),
                ("qos", "eth1"), ("qos", "SHAPER-2"), ("firewall_rule", "forward"), ("firewall_rule", "input"),
                ("firewall_rule", "CORP-IN"), ("firewall_rule", "global-options"), ("firewall_group", "SRV-DNS"),
                ("firewall_group", "CORP"), ("nat", "source"), ("service", "ntp"), ("service", "ssh"),
                ("system", "host-name"), ("system", "ip"), ("config_section", "pki")]


def _lines_of(answer: dict) -> list[str]:
    return [line for field in ("output", "configuration") for line in (answer.get(field) or "").splitlines()]


def test_every_line_of_a_vyos_configuration_belongs_to_one_object():
    ani = _ani(_Runner(VYOS_ANSWERS))
    owners: dict[str, list[tuple[str, str]]] = {}
    for kind, name in VYOS_OBJECTS:
        answer = ani.dispatch("get_object", {"node": "fw-int", "kind": kind, "name": name})
        assert answer["ok"] is True and answer["found"] is True, (kind, name, answer)
        for line in _lines_of(answer):
            if line.startswith("set "):
                owners.setdefault(line, []).append((kind, name))
    assert sorted(owners) == sorted(VYOS_FULL.splitlines())
    assert {line: who for line, who in owners.items() if len(who) > 1} == {}, "each line has one owner"
    assert owners["set firewall group network-group CORP network '10.0.0.0/24'"] == [("firewall_group", "CORP")]
    assert owners["set qos policy shaper SHAPER-1 bandwidth '1gbit'"] == [("qos", "eth1")]
    assert owners["set system ip disable-forwarding"] == [("system", "ip")]


def test_a_port_and_its_vlan_subinterface_are_two_objects_each_with_its_configuration():
    runner = _Runner(VYOS_ANSWERS)
    ani = _ani(runner)
    port = ani.dispatch("get_object", {"node": "fw-int", "kind": "interface", "name": "eth1"})
    assert port["mode"] == "scoped" and port["output"].startswith("eth1@if9")
    assert port["configuration_command"] == "show configuration commands"
    assert port["configuration"].splitlines() == ["set interfaces ethernet eth1 description 'Trunk'",
                                                  "set interfaces ethernet eth1 offload gro"]
    vif = ani.dispatch("get_object", {"node": "fw-int", "kind": "interface", "name": "eth1.100"})
    assert vif["configuration"].splitlines() == ["set interfaces ethernet eth1 vif 100 address '10.0.0.1/24'",
                                                 "set interfaces ethernet eth1 vif 100 description 'CORP gateway'"]
    route = ani.dispatch("get_object", {"node": "fw-int", "kind": "route", "name": "10.1.0.0/24"})
    assert route["configuration"] == "set protocols static route 10.1.0.0/24 next-hop 10.255.254.1 distance '5'"


def test_a_configured_object_whose_status_read_fails_is_found():
    runner = _Runner(VYOS_ANSWERS, failing=("show interfaces ethernet eth1.100",))
    answer = _ani(runner).dispatch("get_object", {"node": "fw-int", "kind": "interface", "name": "eth1.100"})
    assert answer["ok"] is True and answer["found"] is True and "error" not in answer
    assert answer["returncode"] == 1 and "vif 100 address" in answer["configuration"]


def test_the_anis_own_reads_before_a_write_look_at_the_status_view_alone():
    """update_object's probes look for a marker in the status view; a configuration line must
    not answer for it, nor cost a read."""
    runner = _Runner(VYOS_ANSWERS)
    ani = _ani(runner)
    alone = ani.get_object("fw-int", "interface", "eth1", configuration=False)
    assert "configuration" not in alone
    result = ani.dispatch("update_object", {"node": "fw-int", "kind": "interface", "name": "eth1",
                                            "set": {"admin_state": "enable"}})
    assert result["noop"] is True
    assert not any("configuration" in " ".join(call["args"]) for call in runner.calls)


def test_a_vyos_qos_object_is_read_from_the_configuration():
    """`show qos shaper` printed nothing on VyOS 1.5 (main04, the firewall's two shapers)."""
    answer = _ani(_Runner(VYOS_ANSWERS)).dispatch("get_object", {"node": "fw-int", "kind": "qos", "name": "eth1"})
    assert answer["mode"] == "config" and answer["command"] == "show configuration commands"
    assert answer["output"].splitlines() == ["set qos interface eth1 egress 'SHAPER-1'",
                                             "set qos policy shaper SHAPER-1 bandwidth '1gbit'",
                                             "set qos policy shaper SHAPER-1 class 100 match CORP vif '100'"]


EOS_RUNNING = """! Command: show running-config
hostname sw1
!
spanning-tree mode mstp
!
no aaa root
!
vlan 120
   name BMS
!
vlan 130,140
   name SHARED
!
interface Ethernet1
   description Trunk to fw
   switchport trunk allowed vlan 120,130,140
   switchport mode trunk
!
ip route 0.0.0.0/0 172.20.0.1
!
no ip routing
!
end
"""
EOS_ANSWERS = {
    "show running-config interfaces Ethernet1": ("interface Ethernet1\n   description Trunk to fw\n"
                                                 "   switchport trunk allowed vlan 120,130,140\n   switchport mode trunk\n"),
    "show running-config": EOS_RUNNING,
    "show interfaces Ethernet1 status": "Port  Name         Status     Vlan\nEt1   Trunk to fw  connected  trunk\n",
    "show vlan 120": "VLAN  Name  Status  Ports\n120   BMS   active  Et1, Et3\n",
    "show vlan 999": "% VLAN 999 not found in current VLAN database\n",
    "show ip route 0.0.0.0/0 | json": '{"vrfs": {"default": {"routes": {"0.0.0.0/0": {"vias": [{"nexthopAddr": "172.20.0.1"}]}}}}}',
}
EOS_OBJECTS = [("interface", "Ethernet1"), ("vlan", "120"), ("vlan", "140"), ("route", "0.0.0.0/0"),
               ("config_section", "hostname"), ("config_section", "spanning-tree"), ("config_section", "aaa"),
               ("config_section", "ip")]


def test_every_line_of_an_eos_configuration_belongs_to_some_object():
    ani = _ani(_Runner(EOS_ANSWERS))
    seen = set()
    for kind, name in EOS_OBJECTS:
        answer = ani.dispatch("get_object", {"node": "sw1", "kind": kind, "name": name})
        assert answer["ok"] is True and answer["found"] is True, (kind, name, answer)
        seen.update(line.strip() for line in _lines_of(answer))
    expected = {line.strip() for line in EOS_RUNNING.splitlines()
                if line.strip() and not line.startswith("!") and line.strip() != "end"}
    assert expected <= seen


def test_an_eos_port_vlan_and_route_carry_their_configuration():
    ani = _ani(_Runner(EOS_ANSWERS))
    port = ani.dispatch("get_object", {"node": "sw1", "kind": "interface", "name": "Ethernet1"})
    assert "trunk" in port["output"] and "switchport trunk allowed vlan 120,130,140" in port["configuration"]
    assert port["configuration_command"] == "show running-config interfaces Ethernet1"
    vlan = ani.dispatch("get_object", {"node": "sw1", "kind": "vlan", "name": "120"})
    assert "Et1, Et3" in vlan["output"] and vlan["configuration"] == "vlan 120\n   name BMS"
    shared = ani.dispatch("get_object", {"node": "sw1", "kind": "vlan", "name": "140"})
    assert shared["output"] == "" and shared["configuration"] == "vlan 130,140\n   name SHARED" and shared["found"]
    route = ani.dispatch("get_object", {"node": "sw1", "kind": "route", "name": "default"})
    assert route["name"] == "0.0.0.0/0" and route["configuration"] == "ip route 0.0.0.0/0 172.20.0.1"
    routing = ani.dispatch("get_object", {"node": "sw1", "kind": "config_section", "name": "ip"})
    assert routing["mode"] == "section" and routing["output"].splitlines() == ["ip route 0.0.0.0/0 172.20.0.1",
                                                                               "no ip routing"]
    absent = ani.dispatch("get_object", {"node": "sw1", "kind": "vlan", "name": "999"})
    assert absent["ok"] is True and absent["found"] is False and absent["configuration"] == ""


def test_a_new_kind_the_platform_lacks_is_answered_not_raised():
    result = _ani(_Runner()).dispatch("get_object", {"node": "web1", "kind": "nat", "name": "source"})
    assert result["ok"] is False and result["found"] is False and "no nat objects" in result["error"]
    result = _ani(_Runner()).dispatch("get_object", {"node": "sw1", "kind": "system", "name": "hostname"})
    assert result["ok"] is False and "no system objects" in result["error"]


def test_get_states_qdisc_view_of_a_vyos_node_is_its_qos_configuration():
    """v0.10: `show qos shaper` printed nothing on VyOS 1.5, so a firewall's shapers were not in this view."""
    result = _ani(_Runner(VYOS_ANSWERS)).dispatch("get_state", {"nodes": ["fw-int"], "views": ["qdisc"]})
    view = result["nodes"][0]["views"]["qdisc"]
    assert result["ok"] is True and view["command"] == "show configuration commands (its set qos lines)"
    assert view["stdout"].splitlines() == [line for line in VYOS_FULL.splitlines() if line.startswith("set qos ")]
    assert len(view["stdout"].splitlines()) == 4
