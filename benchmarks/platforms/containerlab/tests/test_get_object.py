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


class _Runner:
    """Records every command and answers with a canned text per container."""

    def __init__(self, answers: dict[str, str] | None = None, returncode: int = 0) -> None:
        self.calls: list[dict] = []
        self.answers = answers or {}
        self.returncode = returncode

    def run(self, args, timeout_seconds=None, input_text=None):
        self.calls.append({"args": [str(a) for a in args], "input": input_text})
        joined = " ".join(str(a) for a in args)
        out = next((text for key, text in self.answers.items() if key in joined), "")
        return subprocess.CompletedProcess(args, self.returncode, stdout=out, stderr="" if self.returncode == 0 else "no such object\n")


def _ani(runner: _Runner) -> ContainerLabANI:
    env = SimpleNamespace(
        config=SimpleNamespace(nodes={"leaf2": LEAF, "fw-int": FW, "web1": HOST}, lab_name="test"),
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
    assert ANI_VERSION == "ibn_eval.ani.v0.2"
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


def test_a_kind_the_platform_does_not_have_is_answered_not_raised():
    result = _ani(_Runner()).dispatch("get_object", {"node": "web1", "kind": "zone", "name": "GUEST"})
    assert result["ok"] is False and result["found"] is False and "no zone objects" in result["error"]


def test_requests_the_ani_cannot_form_are_refused_by_name():
    ani = _ani(_Runner())
    with pytest.raises(ANIRequestError, match="kind must be one of"):
        ani.dispatch("get_object", {"node": "leaf2", "kind": "vlan", "name": "10"})
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
    wrapper = next(i for i, a in enumerate(runner.calls[-1]["args"]) if a.endswith("vyatta-op-cmd-wrapper"))
    assert runner.calls[-1]["args"][wrapper + 1:] == ["show", "ip", "route", "0.0.0.0/0"] and vyos["found"] is True
