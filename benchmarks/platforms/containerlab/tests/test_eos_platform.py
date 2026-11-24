"""Arista EOS (`arista_ceos`) as a containerlab platform: sessions, reads, the ANI.

Every write is one named configuration session: the lines are staged, EOS's own
refusals and the session diff decide, and only a clean session with a difference is
committed. These tests script what `Cli` answers; the live behaviour they encode
(a session survives between two `Cli` runs, `end` is refused after `commit`, a
refused line is printed with `%`) was read off cEOS 4.36.1F.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.platforms.containerlab.ani import ANIRequestError, ContainerLabANI
from benchmarks.platforms.containerlab.executor import (
    ContainerLabExecutor,
    IndeterminateExecutionError,
    NoChangeExecutionError,
)
from benchmarks.platforms.containerlab.state import LabState, StateCommand
from benchmarks.platforms.containerlab.types import LabNode

NODE = LabNode(name="agg1", kind="arista_ceos", container_name="clab-test-agg1")
DIFF = ("--- system:/running-config\n+++ session:/ani-x-session-config\n"
        " interface Ethernet3\n-   switchport access vlan 100\n+   switchport access vlan 110\n")


class EosRunner:
    """Answers the way `Cli` does: a staging script, a commit, an abort, a `-c` read."""

    def __init__(self, *, staged: str = DIFF, staged_rc: int = 0, commit: str = "",
                 commit_rc: int = 0, pending: bool = False, reads: dict[str, tuple[int, str]] | None = None):
        self.staged, self.staged_rc = staged, staged_rc
        self.commit, self.commit_rc = commit, commit_rc
        self.pending = pending
        self.reads = reads or {}
        self.calls: list[dict] = []

    def run(self, args, timeout_seconds=None, input_text=None):
        self.calls.append({"args": args, "input_text": input_text})
        if input_text is None:
            command = args[-1]
            if command == "show configuration sessions":
                session = self._session()
                row = f"  {session} pending" if self.pending and session else ""
                return subprocess.CompletedProcess(args, 0, stdout=f"  Name State\n{row}\n", stderr="")
            returncode, stdout = self.reads.get(command, (0, ""))
            return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
        lines = input_text.splitlines()
        if lines[1:] == ["commit"]:
            return subprocess.CompletedProcess(args, self.commit_rc, stdout=self.commit, stderr="")
        if lines[1:] == ["abort"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, self.staged_rc, stdout=self.staged, stderr="")

    def _session(self) -> str | None:
        for call in self.calls:
            if call["input_text"]:
                return call["input_text"].splitlines()[0].removeprefix("configure session ")
        return None

    def scripts(self) -> list[list[str]]:
        return [call["input_text"].splitlines() for call in self.calls if call["input_text"]]


def _executor(runner: EosRunner) -> ContainerLabExecutor:
    return ContainerLabExecutor(Path("unused.yaml"), runner=runner)


# -- the session -----------------------------------------------------------------------

def test_a_batch_is_staged_diffed_then_committed_in_one_named_session():
    runner = EosRunner()
    result = _executor(runner).run_eos_cli_batch(NODE, ["interface Ethernet3", "switchport access vlan 110"])
    assert result.ok and result.stdout == DIFF.strip()
    staged, committed = runner.scripts()
    session = staged[0]
    assert session.startswith("configure session ani-")
    # Staged without a commit, the diff asked for, the session left pending by `end`.
    assert staged[1:] == ["interface Ethernet3", "switchport access vlan 110", "show session-config diffs", "end"]
    # The commit reopens the same session; no `end` after it, EOS refuses one in exec mode.
    assert committed == [session, "commit"]
    assert all(call["args"][:4] == ["docker", "exec", "-i", "clab-test-agg1"] for call in runner.calls)


def test_a_refused_line_aborts_the_session_and_nothing_is_committed():
    runner = EosRunner(staged="% Invalid input (at token 1: 'acces') at line 3\n" + DIFF)
    result = _executor(runner).run_eos_cli_batch(NODE, ["interface Ethernet3", "switchport acces vlan 110"])
    assert not result.ok and "Invalid input" in result.stderr and "nothing was committed" in result.reason
    staged, aborted = runner.scripts()
    assert aborted == [staged[0], "abort"]


def test_a_warning_aborts_too_a_missing_interface_would_not_take_effect():
    runner = EosRunner(staged="! Interface does not exist. The configuration will not take effect until the module is inserted.\n")
    result = _executor(runner).run_eos_cli_batch(NODE, ["interface Ethernet99", "shutdown"])
    assert not result.ok and runner.scripts()[-1][1:] == ["abort"]


def test_a_note_about_the_mlag_roles_of_an_interface_is_not_a_refusal():
    """Re-applying an aggregate's reference state prints these on a healthy switch."""
    notes = ("! Vlan4094 is configured as the MLAG local interface at line 12\n"
             "! Interface Port-Channel999 is configured as the MLAG peer link at line 66\n")
    runner = EosRunner(staged=notes + DIFF)
    result = _executor(runner).run_eos_cli_batch(NODE, ["interface Port-Channel999", "description peer-link"])
    assert result.ok and runner.scripts()[-1][1:] == ["commit"]


def test_an_empty_diff_is_no_change_outside_restoration_and_in_state_inside_it():
    runner = EosRunner(staged="")
    executor = _executor(runner)
    with pytest.raises(NoChangeExecutionError) as raised:
        executor.run_eos_cli_batch(NODE, ["vlan 110"])
    assert not isinstance(raised.value, IndeterminateExecutionError)
    assert runner.scripts()[-1][1:] == ["abort"]
    with executor.idempotent_writes():
        assert executor.run_eos_cli_batch(NODE, ["vlan 110"]).ok
    assert [script[1:] for script in runner.scripts()].count(["commit"]) == 0


def test_a_refused_commit_that_left_the_session_pending_is_aborted():
    runner = EosRunner(commit="% Failed to commit: MLAG peer-link must be a port-channel\n", pending=True)
    result = _executor(runner).run_eos_cli_batch(NODE, ["mlag configuration", "peer-link Ethernet1"])
    assert not result.ok and "Failed to commit" in result.stderr
    assert runner.scripts()[-1][1:] == ["abort"]


def test_a_commit_that_failed_without_a_pending_session_is_indeterminate():
    runner = EosRunner(commit_rc=137, pending=False)
    with pytest.raises(IndeterminateExecutionError):
        _executor(runner).run_eos_cli_batch(NODE, ["interface Ethernet3", "shutdown"])


@pytest.mark.parametrize("command", [
    "configure", "configure replace flash:x", "end", "abort", "commit", "exit", "quit",
    "do bash id", "bash rm -rf /", "no username admin", "management api http-commands",
    "copy running-config startup-config", "reload now", "zerotouch cancel", "show running-config",
    "event-handler x", "interface Ethernet3 | redirect flash:x", "description a ; reload",
    "interface Ethernet3\nshutdown", "",
])
def test_lines_that_leave_the_configuration_are_refused_before_any_session(command):
    runner = EosRunner()
    result = _executor(runner).run_eos_cli_batch(NODE, [command])
    assert not result.ok and not result.safe and runner.calls == []


@pytest.mark.parametrize("command", [
    "interface Port-Channel2", "no shutdown", "default switchport", "switchport trunk allowed vlan remove 110",
    "no channel-group", "mlag 2", "description to the end of the hall", "ip address 10.0.0.1/24",
])
def test_configuration_lines_pass_the_preflight(command):
    assert ContainerLabExecutor.parse_eos_cli_batch([command]) == [command.split()]


def test_a_read_is_one_exec_command_and_the_echo_is_dropped():
    runner = EosRunner(reads={"show vlan": (0, "> show vlan\nVLAN  Name\n110   USERS\n")})
    result = _executor(runner).run_eos_cli(NODE, "show vlan")
    assert result.ok and result.stdout == "VLAN  Name\n110   USERS"
    assert runner.calls[0]["args"] == ["docker", "exec", "clab-test-agg1", "Cli", "-p", "15", "-c", "show vlan"]
    assert not _executor(runner).run_eos_cli(NODE, "show vlan\nreload").ok


def test_the_reference_state_keeps_each_switch_lines_in_order():
    state = LabState("s", "", [
        StateCommand("agg1", "eos_cli", "interface Ethernet3"),
        StateCommand("acc1", "eos_cli", "vlan 110"),
        StateCommand("agg1", "eos_cli", "switchport access vlan 110"),
        StateCommand("fw", "vyos_cli", "set system host-name fw"),
    ])
    assert state.grouped_eos_commands() == {
        "agg1": ["interface Ethernet3", "switchport access vlan 110"], "acc1": ["vlan 110"]}


# -- through the ANI -------------------------------------------------------------------

def _ani(runner: EosRunner) -> ContainerLabANI:
    config = SimpleNamespace(nodes={"agg1": NODE}, lab_name="test", topology_path=None)
    env = SimpleNamespace(config=config, executor=_executor(runner))
    return ContainerLabANI(env)


def test_an_interface_eos_does_not_have_is_not_found_rather_than_an_error():
    runner = EosRunner(reads={"show interfaces Ethernet9 status": (1, "\n% Invalid input at line 1")})
    result = _ani(runner).get_object("agg1", "interface", "Ethernet9")
    assert result["ok"] is True and result["found"] is False and "error" not in result


@pytest.mark.parametrize("kind, name, answer, found", [
    ("route", "10.9.9.0/24", {"vrfs": {"default": {"routes": {}}}}, False),
    ("route", "10.10.0.0/24", {"vrfs": {"default": {"routes": {"10.10.0.0/24": {"routeType": "connected"}}}}}, True),
    ("arp", "10.10.0.1", {"ipV4Neighbors": [], "totalEntries": 0}, False),
    ("arp", "10.10.0.1", {"ipV4Neighbors": [{"address": "10.10.0.1", "hwAddress": "aa:c1:ab:00:00:01"}]}, True),
    ("mac", "aa:c1:ab:00:00:01", {"unicastTable": {"tableEntries": []}, "multicastTable": {"tableEntries": []}}, False),
    ("mac", "aa:c1:ab:00:00:01", {"unicastTable": {"tableEntries": [{"vlanId": 110, "interface": "Port-Channel2"}]}}, True),
])
def test_route_arp_and_mac_are_read_in_json_because_the_text_always_has_headers(kind, name, answer, found):
    command = {"route": f"show ip route {name} | json", "arp": f"show ip arp {name} | json",
               "mac": f"show mac address-table address {name} | json"}[kind]
    runner = EosRunner(reads={command: (0, json.dumps(answer))})
    result = _ani(runner).get_object("agg1", kind, name)
    assert result["ok"] is True and result["found"] is found and result["command"] == command
    if found:
        assert "Port-Channel2" in result["output"] or name in result["output"] or "connected" in result["output"]
    else:
        assert result["output"] == ""


def test_a_configuration_section_is_read_by_its_heading():
    runner = EosRunner(reads={"show running-config section interface Ethernet3": (0, "interface Ethernet3\n   shutdown")})
    result = _ani(runner).get_running_config(["agg1"], ["interface Ethernet3"])
    value = result["nodes"][0]["config"][0]["result"]
    assert result["ok"] and value["command"] == "show running-config section interface Ethernet3"
    with pytest.raises(ANIRequestError):
        _ani(runner).get_running_config(["agg1"], ["interface Ethernet3 | redirect flash:x"])


def test_update_config_refuses_session_control_before_the_switch_is_reached():
    runner = EosRunner()
    for commands in (["configure", "interface Ethernet3"], ["interface Ethernet3", "do bash id"]):
        with pytest.raises(ANIRequestError):
            _ani(runner).update_config([{"target": "agg1", "commands": commands}])
    assert runner.calls == []


def test_update_config_commits_through_the_session_and_keeps_the_diff():
    runner = EosRunner()
    result = _ani(runner).update_config([{
        "target": "agg1", "commands": ["interface Ethernet3", "switchport access vlan 110"],
        "rollback_commands": ["interface Ethernet3", "switchport access vlan 100"]}])
    assert result["ok"] and result["rollback_available"]
    assert result["changes"][0]["result"]["stdout"] == DIFF.strip()
    assert [script[1:] for script in runner.scripts()][-1] == ["commit"]
