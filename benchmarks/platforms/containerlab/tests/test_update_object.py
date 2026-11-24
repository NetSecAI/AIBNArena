"""update_object through the ANI: compiled commands, one transaction, the same ledger."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.platforms.containerlab.ani import ANI_OPERATIONS, ANIRequestError, ContainerLabANI
from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
from benchmarks.platforms.containerlab.tests.test_executor_transactions import RecordingRunner
from benchmarks.platforms.containerlab.tests.test_object_rollback import SRL_RUNNING, VYOS_SHOWN
from benchmarks.platforms.containerlab.types import LabNode

LEAF = LabNode(name="leaf2", kind="nokia_srlinux", container_name="clab-test-leaf2")
FW = LabNode(name="fw-int", kind="vyos", container_name="clab-test-fw-int")
HOST = LabNode(name="web1", kind="linux", container_name="clab-test-web1")


class StateRunner(RecordingRunner):
    """Answers reads (probes) with device-like text and writes with the confirmations."""

    def __init__(self, returncode: int = 0, *, reads: dict[str, str] | None = None) -> None:
        super().__init__(returncode)
        self.reads = reads or {}

    def run(self, args, timeout_seconds=None, input_text=None):
        joined = " ".join(str(a) for a in args) + " " + (input_text or "")
        for needle, text in self.reads.items():
            if needle in joined:
                self.calls.append({"args": [str(a) for a in args], "timeout_seconds": timeout_seconds, "input_text": input_text})
                import subprocess
                return subprocess.CompletedProcess(args, 0, stdout=text, stderr="")
        return super().run(args, timeout_seconds=timeout_seconds, input_text=input_text)


SRL_PORT_UP = "ethernet-1/40 is up, speed 25G\n  IPv4 addr    : 10.10.40.1/24\n"
SRL_PORT_DOWN = "ethernet-1/40 is down, reason port-admin-disabled\n"
HOST_UP = "8805: eth1@if8804: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 9500\n    inet 10.10.40.10/24 scope global eth1\n"
ZONE = "set firewall zone DMZ interface 'eth3'\n"


def _ani(runner: RecordingRunner) -> ContainerLabANI:
    env = SimpleNamespace(
        config=SimpleNamespace(nodes={"leaf2": LEAF, "fw-int": FW, "web1": HOST}, lab_name="test"),
        executor=ContainerLabExecutor(Path("unused.yaml"), runner=runner),
    )
    return ContainerLabANI(env)


def test_the_operation_is_on_the_surface_as_a_write():
    assert "update_object" in ANI_OPERATIONS
    schema = next(s for s in ContainerLabANI.tool_schemas() if s["function"]["name"] == "update_object")
    assert schema["function"]["parameters"]["required"] == ["node", "kind", "name", "set"]
    assert schema["function"]["parameters"]["properties"]["set"]["type"] == "object"
    assert any(item["name"] == "update_object" and item["type"] == "write" for item in ContainerLabANI.tool_contract())


def test_an_sr_linux_change_is_one_candidate_with_its_compensation():
    runner = StateRunner(0, reads={"show interface ethernet-1/40": SRL_PORT_DOWN})
    result = _ani(runner).dispatch("update_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40",
                                                     "set": {"admin_state": "enable"}, "reason": "port was disabled"})
    assert result["ok"] is True and result["operation"] == "update_object"
    assert result["compiled"]["commands"] == ["set / interface ethernet-1/40 admin-state enable"]
    assert result["rollback_available"] is True
    assert result["changes"][0]["reason"] == "port was disabled"
    assert result["transaction_id"].startswith("ani_tx_")
    assert "set / interface ethernet-1/40 admin-state enable" in (runner.calls[-1]["input_text"] or "")


def test_a_linux_change_is_one_native_command_per_change():
    down = "8805: eth1@if8804: <BROADCAST,MULTICAST> mtu 9500\n"
    runner = StateRunner(0, reads={"ip address show dev eth1": down})
    result = _ani(runner).dispatch("update_object", {"node": "web1", "kind": "interface", "name": "eth1",
                                                     "set": {"admin_state": "enable", "address": "10.10.40.10/24"}})
    assert result["ok"] is True
    assert [change["commands"] for change in result["changes"]] == [["ip link set dev eth1 up"], ["ip address replace 10.10.40.10/24 dev eth1"]]
    assert result["rollback_available"] is True


def test_a_vyos_change_goes_through_the_configure_session():
    runner = StateRunner(0, reads={"show configuration commands": "set firewall zone UPLINK default-action 'drop'\n"})
    result = _ani(runner).dispatch("update_object", {"node": "fw-int", "kind": "zone", "name": "UPLINK",
                                                     "set": {"from_zone": "GUEST", "ruleset": "GUEST-TO-UPLINK"}})
    assert result["ok"] is True
    assert result["compiled"]["commands"] == ["set firewall zone UPLINK from GUEST firewall name GUEST-TO-UPLINK"]
    assert result["rollback_available"] is True


def test_a_change_without_derivable_compensation_says_so():
    """A traffic-control tree takes several commands to rebuild, and Linux applies one."""
    runner = RecordingRunner(0)
    result = _ani(runner).dispatch("update_object", {"node": "web1", "kind": "qos", "name": "eth1",
                                                     "set": {"delete_root": True}})
    assert result["ok"] is True and result["rollback_available"] is False
    assert result["compiled"]["rollback_commands"] == []
    assert "rollback_basis" not in result["compiled"]


HTB_ROOT = "qdisc htb 1: root refcnt 9 r2q 10 default 0x20 direct_packets_stat 0 direct_qlen 1000\n"
NETEM_ROOT = "qdisc netem 8001: root refcnt 2 limit 1000 delay 100ms\n"


def test_deleting_an_htb_root_alone_is_refused_and_nothing_is_sent():
    """E19 LangChain wan_shaping m1 and m4: the circuit's root removed first, never rebuilt."""
    runner = StateRunner(0, reads={"tc qdisc show dev eth2": HTB_ROOT})
    with pytest.raises(ANIRequestError, match="delete_root alone would remove the HTB hierarchy on eth2"):
        _ani(runner).dispatch("update_object", {"node": "web1", "kind": "qos", "name": "eth2",
                                                "set": {"delete_root": True}})
    assert not any("tc qdisc del" in " ".join(call["args"]) + (call["input_text"] or "")
                   for call in runner.calls), "only the read reached the device"


def test_a_root_that_is_not_an_htb_or_a_rebuild_with_it_goes_through():
    netem = StateRunner(0, reads={"tc qdisc show dev eth1": NETEM_ROOT})
    removed = _ani(netem).dispatch("update_object", {"node": "web1", "kind": "qos", "name": "eth1",
                                                     "set": {"delete_root": True}})
    assert removed["ok"] is True and removed["compiled"]["commands"] == ["tc qdisc del dev eth1 root"]

    htb = StateRunner(0, reads={"tc qdisc show dev eth2": HTB_ROOT})
    rebuilt = _ani(htb).dispatch("update_object", {"node": "web1", "kind": "qos", "name": "eth2", "set": {
        "delete_root": True,
        "htb": {"default": 20, "rate": "10mbit", "classes": [{"id": "1:20", "rate": "2mbit", "ceil": "10mbit"}]}}})
    assert rebuilt["ok"] is True and rebuilt["compiled"]["commands"][0] == "tc qdisc del dev eth2 root"


# -- compensation read from the node just before the change --------------------------


def test_an_sr_linux_route_change_is_undone_from_the_running_configuration():
    """e10 disable_routing.m1: leaf2's routes repointed, with no way back."""
    runner = StateRunner(0, reads={"info from running /": SRL_RUNNING})
    ani = _ani(runner)
    done = ani.dispatch("update_object", {"node": "leaf2", "kind": "route", "name": "10.10.30.0/24",
                                          "set": {"next_hop": "10.255.0.7"}})
    assert done["ok"] is True and done["rollback_available"] is True
    assert done["compiled"]["rollback_basis"] == "state_before_change"
    assert "set / network-instance default static-routes route 10.10.30.0/24 next-hop-group to-leaf2" in \
        done["compiled"]["rollback_commands"]

    undone = ani.dispatch("rollback_config", {"transaction_id": done["transaction_id"]})
    assert undone["ok"] is True
    sent = runner.calls[-1]["input_text"] or ""
    assert "delete / network-instance default next-hop-groups group nhg-10-10-30-0-24" in sent
    assert "set / network-instance default static-routes route 10.10.30.0/24 next-hop-group to-leaf2" in sent


def test_a_linux_route_deletion_is_undone_by_replacing_the_route_it_removed():
    """e10 wrong_routing_table.m1: app1's default route deleted, rollback refused."""
    runner = StateRunner(0, reads={"ip -o -4 route show default": "default via 10.10.40.1 dev eth1 \n"})
    ani = _ani(runner)
    done = ani.dispatch("update_object", {"node": "web1", "kind": "route", "name": "default", "set": {"delete": True}})
    assert done["ok"] is True and done["rollback_available"] is True
    assert done["compiled"]["rollback_commands"] == ["ip route replace default via 10.10.40.1 dev eth1"]

    undone = ani.dispatch("rollback_config", {"transaction_id": done["transaction_id"]})
    assert undone["ok"] is True
    assert " ".join(str(a) for a in runner.calls[-1]["args"]).endswith(
        "ip route replace default via 10.10.40.1 dev eth1")


def test_a_vyos_option_change_is_undone_from_its_configuration_commands():
    runner = StateRunner(0, reads={"show configuration commands": VYOS_SHOWN})
    done = _ani(runner).dispatch("update_object", {"node": "fw-int", "kind": "dhcp_pool", "name": "USER1",
                                                   "set": {"subnet": "10.10.10.0/24", "default_router": "10.10.10.1"}})
    assert done["ok"] is True and done["rollback_available"] is True
    assert done["compiled"]["rollback_commands"] == [
        "delete service dhcp-server shared-network-name USER1 subnet 10.10.10.0/24 option default-router",
        "set service dhcp-server shared-network-name USER1 subnet 10.10.10.0/24 option default-router '10.10.10.2'",
    ]


def test_a_compensation_the_preflight_would_refuse_is_dropped_and_the_write_goes_ahead():
    """Putting back a default route through eth0 would touch the management plane: the
    write must still happen, without a rollback, exactly as before compensations were
    derived."""
    runner = StateRunner(0, reads={"ip -o -4 route show default": "default via 172.21.0.1 dev eth0 \n"})
    done = _ani(runner).dispatch("update_object", {"node": "web1", "kind": "route", "name": "default",
                                                   "set": {"delete": True}})
    assert done["ok"] is True and done["rollback_available"] is False
    assert done["compiled"]["rollback_commands"] == []
    assert any("ip route del default" in " ".join(str(a) for a in call["args"]) for call in runner.calls)


def test_a_read_that_is_not_the_node_s_own_gives_no_compensation_and_blocks_nothing():
    # RecordingRunner answers every read with a commit acknowledgement, not a route.
    result = _ani(RecordingRunner(0)).dispatch("update_object", {"node": "web1", "kind": "route", "name": "default",
                                                                 "set": {"next_hop": "10.10.40.1"}})
    assert result["ok"] is True and result["rollback_available"] is False


def test_nothing_is_read_when_the_request_already_gives_the_compensation():
    runner = StateRunner(0, reads={"show interface ethernet-1/40": SRL_PORT_DOWN, "info from running /": SRL_RUNNING})
    result = _ani(runner).dispatch("update_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40",
                                                     "set": {"admin_state": "enable"}})
    assert result["rollback_available"] is True and result["compiled"]["rollback_basis"] == "request"
    assert not any("info from running /" in (call["input_text"] or "") for call in runner.calls)


def test_rollback_config_undoes_an_object_change():
    runner = StateRunner(0, reads={"show interface ethernet-1/40": SRL_PORT_UP})
    ani = _ani(runner)
    done = ani.dispatch("update_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "set": {"admin_state": "disable"}})
    undone = ani.dispatch("rollback_config", {"transaction_id": done["transaction_id"]})
    assert undone["ok"] is True
    assert "set / interface ethernet-1/40 admin-state enable" in (runner.calls[-1]["input_text"] or "")


def test_a_request_the_ani_cannot_form_never_reaches_a_device():
    runner = RecordingRunner(0)
    ani = _ani(runner)
    with pytest.raises(ANIRequestError, match="no attribute speed"):
        ani.dispatch("update_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "set": {"speed": "10G"}})
    with pytest.raises(ANIRequestError, match="no zone objects"):
        ani.dispatch("update_object", {"node": "web1", "kind": "zone", "name": "GUEST", "set": {"add_interface": "eth1"}})
    with pytest.raises(ANIRequestError):
        ani.dispatch("update_object", {"node": "nowhere", "kind": "interface", "name": "eth1", "set": {"mtu": 1500}})
    assert runner.calls == []


def test_a_failed_command_is_reported_like_any_write():
    runner = RecordingRunner(1)
    result = _ani(runner).dispatch("update_object", {"node": "web1", "kind": "route", "name": "default", "set": {"delete": True}})
    assert result["ok"] is False and result["operation"] == "update_object"
    assert result["changes"][0]["result"]["ok"] is False


def test_a_change_the_object_already_has_is_not_sent_and_has_no_compensation():
    runner = StateRunner(0, reads={"show interface ethernet-1/40": SRL_PORT_UP})
    result = _ani(runner).dispatch("update_object", {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "set": {"admin_state": "enable"}})
    assert result["ok"] is True and result["noop"] is True and result["changes"] == []
    assert result["transaction_id"] is None and result["rollback_available"] is False
    assert result["compiled"]["skipped"][0]["command"] == "set / interface ethernet-1/40 admin-state enable"
    assert all("commit" not in (call["input_text"] or "") for call in runner.calls), "nothing was committed"


def test_an_interface_already_in_the_zone_is_not_added_again():
    runner = StateRunner(0, reads={"show configuration commands": ZONE})
    result = _ani(runner).dispatch("update_object", {"node": "fw-int", "kind": "zone", "name": "DMZ", "set": {"add_interface": "eth3"}})
    assert result["noop"] is True
    result = _ani(runner).dispatch("update_object", {"node": "fw-int", "kind": "zone", "name": "DMZ", "set": {"delete_interface": "eth3"}})
    assert result.get("noop") is None and result["compiled"]["commands"] == ["delete firewall zone DMZ interface eth3"]


def test_a_host_link_already_up_is_left_alone_but_a_new_address_is_added():
    runner = StateRunner(0, reads={"ip address show dev eth1": HOST_UP})
    result = _ani(runner).dispatch("update_object", {"node": "web1", "kind": "interface", "name": "eth1",
                                                     "set": {"admin_state": "enable", "address": "10.10.40.11/24"}})
    assert [c["commands"] for c in result["changes"]] == [["ip address replace 10.10.40.11/24 dev eth1"]]
    assert result["compiled"]["skipped"][0]["command"] == "ip link set dev eth1 up"


def test_a_shaping_hierarchy_with_its_classifier_passes_the_linux_grammar():
    """The filters an htb object writes are the ones the Linux grammar accepts.

    They were written as `tc filter replace`, which the grammar refuses: every
    update_object that carried a classifier failed its preflight before reaching
    the device (NetRepairArena gpt-5.4, 2026-09-25).
    """
    runner = RecordingRunner(0)
    result = _ani(runner).dispatch("update_object", {
        "node": "web1", "kind": "qos", "name": "eth2",
        "set": {"htb": {"default": 20, "rate": "20mbit",
                        "classes": [{"id": "1:10", "rate": "13mbit", "ceil": "20mbit", "prio": 0},
                                    {"id": "1:20", "rate": "7mbit", "ceil": "20mbit", "prio": 1}],
                        "filters": [{"src": "192.168.10.0/24", "flowid": "1:10"}]}}})
    assert result["ok"] is True
    assert result["compiled"]["commands"][-1] == (
        "tc filter add dev eth2 parent 1: protocol ip prio 1 u32 match ip src 192.168.10.0/24 flowid 1:10")


@pytest.mark.parametrize("node, kind, name, changes, sent", [
    # a classifier on its own, on either side of the match
    ("web1", "qos", "eth2", {"filter": {"src": "192.168.10.0/24", "flowid": "1:10"}},
     "tc filter add dev eth2 parent 1: protocol ip prio 1 u32 match ip src 192.168.10.0/24 flowid 1:10"),
    ("web1", "qos", "eth2", {"filter": {"dst": "198.51.100.0/24", "flowid": "1:20", "prio": 2}},
     "tc filter add dev eth2 parent 1: protocol ip prio 2 u32 match ip dst 198.51.100.0/24 flowid 1:20"),
    ("web1", "qos", "eth2", {"delete_filter": {"prio": 1}}, "tc filter del dev eth2 parent 1: protocol ip prio 1 u32"),
    # SR Linux subinterface settings, one candidate each
    ("leaf2", "interface", "ethernet-1/40.0", {"admin_state": "enable"},
     "set / interface ethernet-1/40 subinterface 0 admin-state enable"),
    ("leaf2", "interface", "ethernet-1/40", {"ipv4_admin_state": "enable"},
     "set / interface ethernet-1/40 subinterface 0 ipv4 admin-state enable"),
    ("leaf2", "acl", "EDGE-IN", {"delete_entry": 100}, "delete / acl acl-filter EDGE-IN type ipv4 entry 100"),
    # VyOS forwarding, forward filter and vifs through the configure session
    ("fw-int", "routing", "default", {"admin_state": "enable"}, "delete system ip disable-forwarding"),
    ("fw-int", "firewall_rule", "forward", {"default_action": "accept"}, "set firewall ipv4 forward filter default-action accept"),
    ("fw-int", "firewall_rule", "forward", {"rule": 10, "delete": True}, "delete firewall ipv4 forward filter rule 10"),
])
def test_the_new_words_reach_the_device_as_native_commands(node, kind, name, changes, sent):
    runner = RecordingRunner(0)
    result = _ani(runner).dispatch("update_object", {"node": node, "kind": kind, "name": name, "set": changes})
    assert result["ok"] is True, result
    assert result["compiled"]["commands"] == [sent]
    # no derivable compensation for these: the transaction says so rather than guessing one
    assert result["rollback_available"] is False
    delivered = " ".join(" ".join(call["args"]) + " " + (call["input_text"] or "") for call in runner.calls)
    # SR Linux and Linux receive the line; VyOS receives it as the token vector of its batch
    assert sent in delivered or json.dumps(shlex.split(sent)) in delivered


def test_a_vif_is_written_as_a_vif_of_its_parent():
    """eth1.10 is VLAN 10 of eth1: `interfaces ethernet eth1.10` names nothing on VyOS."""
    runner = StateRunner(0, reads={"show interfaces ethernet eth1.10": "eth1.10@eth1: <BROADCAST,MULTICAST> mtu 1500\n"})
    result = _ani(runner).dispatch("update_object", {"node": "fw-int", "kind": "interface", "name": "eth1.10",
                                                     "set": {"admin_state": "enable"}})
    assert result["ok"] is True
    assert result["compiled"]["commands"] == ["delete interfaces ethernet eth1 vif 10 disable"]
    assert result["compiled"]["rollback_commands"] == ["set interfaces ethernet eth1 vif 10 disable"]


def test_the_forward_filter_is_read_by_the_name_it_is_written_under():
    config = ("set firewall ipv4 forward filter default-action 'drop'\n"
              "set firewall ipv4 forward filter rule 10 action 'jump'\n"
              "set firewall ipv4 name LAN-TO-WAN default-action 'drop'\n"
              "set firewall zone LAN interface 'eth1.10'\n")
    runner = StateRunner(0, reads={"show configuration commands": config})
    found = _ani(runner).dispatch("get_object", {"node": "fw-int", "kind": "firewall_rule", "name": "forward"})
    assert found["found"] is True
    assert found["output"].splitlines() == ["set firewall ipv4 forward filter default-action 'drop'",
                                            "set firewall ipv4 forward filter rule 10 action 'jump'"]
    ruleset = _ani(runner).dispatch("get_object", {"node": "fw-int", "kind": "firewall_rule", "name": "LAN-TO-WAN"})
    assert ruleset["output"] == "set firewall ipv4 name LAN-TO-WAN default-action 'drop'"


def test_a_word_one_platform_does_not_have_never_reaches_a_device():
    runner = RecordingRunner(0)
    with pytest.raises(ANIRequestError, match="SR Linux subinterface setting"):
        _ani(runner).dispatch("update_object", {"node": "web1", "kind": "interface", "name": "eth1",
                                                "set": {"ipv4_admin_state": "enable"}})
    with pytest.raises(ANIRequestError, match="no routing objects"):
        _ani(runner).dispatch("update_object", {"node": "web1", "kind": "routing", "name": "default",
                                                "set": {"admin_state": "enable"}})
    assert runner.calls == []


# --- v0.10: a VyOS route's next hop replaces the targets the route has --------------------

ROUTED = """\
set interfaces ethernet eth7 address '10.255.254.0/31'
set protocols static route 10.1.0.0/24 next-hop 192.0.2.1 distance '5'
set protocols static route 10.9.0.0/24 next-hop 10.255.254.1
"""
NEXT_HOP = {"node": "fw-int", "kind": "route", "name": "10.1.0.0/24", "set": {"next_hop": "10.255.254.1"}}


def _sent(runner) -> str:
    return " ".join(call["input_text"] or "" for call in runner.calls)


def test_a_vyos_next_hop_replaces_the_hop_the_route_had():
    """main04: `set ... next-hop` added the hop beside the fault's, and three langchain_v2
    episodes ended with both, and failed. Linux's `ip route replace` never did."""
    runner = StateRunner(0, reads={"show configuration commands": ROUTED})
    done = _ani(runner).dispatch("update_object", NEXT_HOP)
    assert done["ok"] is True
    assert done["compiled"]["commands"] == ["delete protocols static route 10.1.0.0/24 next-hop 192.0.2.1",
                                            "set protocols static route 10.1.0.0/24 next-hop 10.255.254.1"]
    # One commit; the compensation is the route's hops as they were, distance included.
    assert done["rollback_available"] is True and done["compiled"]["rollback_basis"] == "state_before_change"
    assert done["compiled"]["rollback_commands"] == [
        "delete protocols static route 10.1.0.0/24 next-hop",
        "set protocols static route 10.1.0.0/24 next-hop 192.0.2.1 distance '5'"]
    assert "10.9.0.0/24" not in _sent(runner), "another route is not touched"


def test_a_vyos_route_whose_only_hop_is_the_one_asked_for_has_nothing_to_change():
    runner = StateRunner(0, reads={"show configuration commands": ROUTED})
    done = _ani(runner).dispatch("update_object", {**NEXT_HOP, "name": "10.9.0.0/24"})
    assert done["ok"] is True and done["noop"] is True and done["changes"] == []
    assert "commit" not in _sent(runner)


def test_a_new_vyos_route_is_one_set_with_its_compensation_as_before():
    runner = StateRunner(0, reads={"show configuration commands": ROUTED})
    done = _ani(runner).dispatch("update_object", {**NEXT_HOP, "name": "10.2.0.0/24"})
    assert done["compiled"]["commands"] == ["set protocols static route 10.2.0.0/24 next-hop 10.255.254.1"]
    assert done["compiled"]["rollback_commands"] == ["delete protocols static route 10.2.0.0/24 next-hop 10.255.254.1"]
    assert done["compiled"]["rollback_basis"] == "request"


def test_a_vyos_blackhole_route_given_a_next_hop_forwards_through_it_alone():
    runner = StateRunner(0, reads={"show configuration commands":
                                   ROUTED + "set protocols static route 10.1.0.10/32 blackhole distance '1'\n"})
    done = _ani(runner).dispatch("update_object", {**NEXT_HOP, "name": "10.1.0.10/32"})
    assert done["compiled"]["commands"] == ["delete protocols static route 10.1.0.10/32 blackhole",
                                            "set protocols static route 10.1.0.10/32 next-hop 10.255.254.1"]


def test_a_vyos_next_hop_whose_route_cannot_be_read_is_refused_before_any_write():
    runner = RecordingRunner(1)
    with pytest.raises(ANIRequestError, match="could not be read"):
        _ani(runner).dispatch("update_object", NEXT_HOP)
    assert "commit" not in _sent(runner)
