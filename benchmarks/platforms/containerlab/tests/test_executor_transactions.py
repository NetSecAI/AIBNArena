"""Atomic configuration boundaries in the containerlab executor."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab.executor import (  # noqa: E402
    ContainerLabExecutor,
    IndeterminateExecutionError,
    SRL_COMMIT_CONFIRMED,
    VYOS_CONFIG_SESSION_DRIVER,
    VYOS_DISCARD_CONFIRMED,
    VYOS_SHOW_CONFIG_DRIVER,
)
from benchmarks.platforms.containerlab.types import LabNode  # noqa: E402


class RecordingRunner:
    def __init__(self, returncode: int = 0, *, stdout: str | None = None,
                 stderr: str | None = None) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[dict] = []

    def run(self, args, timeout_seconds=None, input_text=None):
        self.calls.append({
            "args": args,
            "timeout_seconds": timeout_seconds,
            "input_text": input_text,
        })
        return subprocess.CompletedProcess(
            args, self.returncode,
            stdout=(self.stdout if self.stdout is not None else
                    (f"{SRL_COMMIT_CONFIRMED}\n" if self.returncode == 0 else "")),
            stderr=(self.stderr if self.stderr is not None else
                    ("__ANI_VYOS_COMMIT_CONFIRMED__\n" if self.returncode == 0
                     else f"{VYOS_DISCARD_CONFIRMED}\n")),
        )


NODE = LabNode(name="fw", kind="vyos", container_name="clab-test-fw")
SRL_NODE = LabNode(name="leaf", kind="nokia_srlinux", container_name="clab-test-leaf")
LINUX_NODE = LabNode(name="host", kind="linux", container_name="clab-test-host")


def _executor(returncode: int = 0, *, stdout: str | None = None,
              stderr: str | None = None
              ) -> tuple[ContainerLabExecutor, RecordingRunner]:
    runner = RecordingRunner(returncode, stdout=stdout, stderr=stderr)
    return ContainerLabExecutor(Path("unused.yaml"), runner=runner), runner


def test_vyos_batch_is_json_tokens_to_a_fixed_config_session_driver() -> None:
    executor, runner = _executor()
    commands = [
        'set interfaces dummy dum9 description "two safe words"',
        "delete interfaces dummy dum8",
    ]
    result = executor.run_vyos_cli_batch(NODE, commands)

    assert result.ok is True
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["args"][-1] == VYOS_CONFIG_SESSION_DRIVER
    assert call["args"][-2] == "-c" and call["args"][-3] == "/usr/bin/python3"
    assert json.loads(call["input_text"]) == [
        ["set", "interfaces", "dummy", "dum9", "description", "two safe words"],
        ["delete", "interfaces", "dummy", "dum8"],
    ]
    assert "two safe words" not in call["args"][-1], \
        "caller data must not become executable Python or shell source"
    assert "save_config" not in call["args"][-1]


def test_vyos_rejects_non_config_and_shell_control_before_runner() -> None:
    for command in (
        "show configuration",
        "set system host-name safe && touch /tmp/ani_escape",
    ):
        executor, runner = _executor()
        result = executor.run_vyos_cli_batch(NODE, [command])
        assert result.ok is False and result.safe is False
        assert runner.calls == []


def test_vyos_driver_failure_is_reported_as_discarded_candidate() -> None:
    executor, runner = _executor(returncode=1)
    result = executor.run_vyos_cli_batch(
        NODE, ["set interfaces dummy dum9 description test"])
    assert result.ok is False
    assert result.reason == "VyOS discarded the candidate configuration"
    assert len(runner.calls) == 1


def test_vyos_signal_without_driver_confirmation_is_indeterminate() -> None:
    executor, runner = _executor(returncode=137, stderr="Killed\n")
    try:
        executor.run_vyos_cli_batch(
            NODE, ["set interfaces dummy dum9 description test"])
    except IndeterminateExecutionError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("a killed writer cannot be reported as a definite discard")
    assert len(runner.calls) == 1


def test_backend_data_that_resembles_an_executable_is_not_rejected() -> None:
    executor, runner = _executor()
    vyos = executor.run_vyos_cli_batch(
        NODE, ["set system host-name docker",
               "set interfaces dummy dum9 description reboot"])
    assert vyos.ok is True

    srl = executor.run_srl_cli_batch(
        SRL_NODE, ["set / interface ethernet-1/1 description rm"])
    assert srl.ok is True
    assert len(runner.calls) == 2


def test_vyos_config_read_is_json_path_to_a_fixed_driver() -> None:
    executor, runner = _executor()
    path = 'firewall ipv4 name "two safe words"'
    result = executor.run_vyos_show_config(NODE, path)

    assert result.ok is True
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["args"][-1] == VYOS_SHOW_CONFIG_DRIVER
    assert call["args"][-2] == "-c" and call["args"][-3] == "/usr/bin/python3"
    assert json.loads(call["input_text"]) == [
        "firewall", "ipv4", "name", "two safe words",
    ]
    assert "two safe words" not in call["args"][-1]


def test_vyos_config_read_rejects_multiline_path_before_runner() -> None:
    executor, runner = _executor()
    result = executor.run_vyos_show_config(
        NODE, "firewall ipv4\n/bin/touch /tmp/read-became-write")

    assert result.ok is False and result.safe is False
    assert "one line" in (result.reason or "")
    assert runner.calls == []


def test_srl_batch_accepts_only_configuration_lines_and_owns_framing() -> None:
    executor, runner = _executor()
    commands = [
        "set / interface ethernet-1/1 admin-state enable",
        "delete / interface ethernet-1/2 description",
    ]
    result = executor.run_srl_cli_batch(SRL_NODE, commands)

    assert result.ok is True
    assert result.stdout == f"{SRL_COMMIT_CONFIRMED}\n"
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["args"][-1] == "sr_cli"
    assert call["input_text"] == "\n".join([
        "enter candidate", "discard stay", *commands, "commit now", "quit", "",
    ])


def test_srl_batch_without_exact_native_acknowledgement_is_indeterminate() -> None:
    executor, runner = _executor(stdout="All changes have been committed.\n")

    try:
        executor.run_srl_cli_batch(
            SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    except IndeterminateExecutionError as exc:
        assert "acknowledgement absent" in str(exc)
    else:
        raise AssertionError("process success alone must not prove an SR Linux commit")
    assert len(runner.calls) == 1


def test_srl_batch_failure_is_indeterminate_even_with_success_acknowledgement() -> None:
    executor, runner = _executor(
        returncode=1, stdout=f"{SRL_COMMIT_CONFIRMED}\n")

    try:
        executor.run_srl_cli_batch(
            SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    except IndeterminateExecutionError as exc:
        assert "return code 1" in str(exc)
        assert "acknowledgement present" in str(exc)
    else:
        raise AssertionError("a contradictory SR Linux exit status is indeterminate")
    assert len(runner.calls) == 1


def test_srl_batch_rejects_non_config_or_multiline_before_runner() -> None:
    invalid = [
        "show version",
        "sh -c 'rm -rf /tmp/x'",
        "delete /",
        "set / system name host-name safe\ncommit now",
    ]
    for command in invalid:
        executor, runner = _executor()
        result = executor.run_srl_cli_batch(SRL_NODE, [command])
        assert result.ok is False and result.safe is False, command
        assert runner.calls == [], command


def test_untrusted_linux_config_uses_positive_backend_grammar() -> None:
    executor, runner = _executor()
    rejected = executor.run_linux_config(LINUX_NODE, "sh -c 'ip link set eth1 down'")
    assert rejected.ok is False and rejected.safe is False
    assert runner.calls == []

    accepted = executor.run_linux_config(LINUX_NODE, "ip link set eth1 up")
    assert accepted.ok is True
    assert runner.calls[-1]["args"] == [
        "docker", "exec", LINUX_NODE.container_name, "ip", "link", "set", "eth1", "up",
    ]


def test_linux_positive_grammar_rejects_namespace_xdp_and_bpf_loaders() -> None:
    dangerous_or_unmodelled = (
        "ip link set eth1 netns 1",
        "ip link set eth1 xdp obj /tmp/unreviewed.o",
        "tc filter add dev eth1 parent 1: bpf obj /tmp/unreviewed.o",
    )
    for command in dangerous_or_unmodelled:
        executor, runner = _executor()
        result = executor.run_linux_config(LINUX_NODE, command)
        assert result.ok is False and result.safe is False, command
        assert runner.calls == [], command


def test_srl_batch_refusal_carries_the_refusing_output() -> None:
    """A refused batch must say what sr_cli said, not only that it was refused.

    The lifecycle records nothing but the exception text in the run result, so a
    message without the CLI's own words leaves a broken reference state looking
    like a transport failure.
    """
    executor, _runner = _executor(
        returncode=1,
        stdout="Error: Subinterface ethernet-1/30.0 does not exist\n",
        stderr="Error: commit failed\n")

    try:
        executor.run_srl_cli_batch(
            SRL_NODE, ["set / network-instance vlan20 interface ethernet-1/30.0"])
    except IndeterminateExecutionError as exc:
        assert "ethernet-1/30.0 does not exist" in str(exc)
        assert "commit failed" in str(exc)
    else:
        raise AssertionError("a refused SR Linux batch must raise")


def test_srl_refusal_names_node_count_and_keeps_the_newest_line() -> None:
    """The tail keeps the line that names the failure even when the tail is long."""
    long_early = "x" * 900
    executor, _runner = _executor(
        returncode=1, stdout=f"{long_early}\nError: the newest line\n", stderr="")
    try:
        executor.run_srl_cli_batch(SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    except IndeterminateExecutionError as exc:
        text = str(exc)
        assert "(node leaf, 1 command(s))" in text, text
        assert "Error: the newest line" in text, text
    else:
        raise AssertionError("a refused SR Linux batch must raise")


def test_vyos_refusal_carries_the_refusing_output() -> None:
    """The VyOS writer's two indeterminate raises name node, count and output too."""
    executor, _runner = _executor(
        returncode=1, stderr="ConfigSessionError: Configuration path [x] is not valid\nSet failed\n")
    try:
        executor.run_vyos_cli_batch(NODE, ["set service dhcp-server x"])
    except IndeterminateExecutionError as exc:
        text = str(exc)
        assert "without discard confirmation" in text, text
        assert "(node fw, 1 command(s))" in text, text
        assert "is not valid" in text, text
    else:
        raise AssertionError("a VyOS exit without discard confirmation must raise")


def test_srl_noop_commit_is_no_change_for_a_subject() -> None:
    from benchmarks.platforms.containerlab.executor import SRL_COMMIT_NOOP, NoChangeExecutionError

    executor, runner = _executor(stdout=f"{SRL_COMMIT_NOOP}\n")
    try:
        executor.run_srl_cli_batch(SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    except NoChangeExecutionError as exc:
        # determinate: nothing was written, so not a write and not indeterminate either
        assert "already in the requested state" in str(exc) and "Nothing to commit" in str(exc)
        assert not isinstance(exc, IndeterminateExecutionError)
    else:
        raise AssertionError("a command that changed nothing must not count as a subject's write")
    assert len(runner.calls) == 1


def test_srl_noop_commit_is_proven_only_inside_idempotent_writes() -> None:
    from benchmarks.platforms.containerlab.executor import SRL_COMMIT_NOOP

    executor, runner = _executor(stdout=f"{SRL_COMMIT_NOOP}\n")
    with executor.idempotent_writes():
        result = executor.run_srl_cli_batch(SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    assert result.ok is True and result.stdout == f"{SRL_COMMIT_NOOP}\n"
    # the block is scoped: strict again afterwards, and a real failure stays a failure inside it
    from benchmarks.platforms.containerlab.executor import NoChangeExecutionError
    try:
        executor.run_srl_cli_batch(SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    except NoChangeExecutionError:
        pass
    else:
        raise AssertionError("idempotent_writes must not leak past its block")
    failing, _ = _executor(returncode=1, stdout=f"{SRL_COMMIT_NOOP}\n")
    with failing.idempotent_writes():
        try:
            failing.run_srl_cli_batch(SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
        except IndeterminateExecutionError as exc:
            assert "return code 1" in str(exc)
        else:
            raise AssertionError("a failing exit status is indeterminate even for a no-op")
    assert len(runner.calls) == 2


LINUX_NODE = LabNode(name="user1", kind="linux", container_name="clab-test-user1")


def test_a_restore_that_deletes_an_address_already_gone_is_in_state_only_during_restoration() -> None:
    stderr = "RTNETLINK answers: Address not available\n"
    executor, runner = _executor(returncode=2, stderr=stderr)
    failed = executor.run_shell(LINUX_NODE, "ip address del 192.0.2.10/24 dev eth1")
    assert failed.ok is False and failed.returncode == 2, "outside restoration a failed delete stays a failure"
    with executor.idempotent_writes():
        result = executor.run_shell(LINUX_NODE, "ip address del 192.0.2.10/24 dev eth1")
    assert result.ok is True and "already in the requested state" in result.stdout and result.stderr == stderr
    with executor.idempotent_writes():
        other = executor.run_shell(LINUX_NODE, "ip link set eth1 up")
    assert other.ok is False, "an unrelated failure is not turned into success by the block"
    assert len(runner.calls) == 3


def test_the_already_in_state_reading_is_narrow() -> None:
    from benchmarks.platforms.containerlab.executor import _linux_already_in_state as f

    assert f("ip address del 10.0.0.1/24 dev eth1", "RTNETLINK answers: Cannot assign requested address")
    assert f("ip addr add 10.0.0.1/24 dev eth1", "RTNETLINK answers: File exists")
    assert f("ip route del default", "RTNETLINK answers: No such process")
    assert not f("ip address del 10.0.0.1/24 dev eth1", 'Cannot find device "eth1"')
    assert not f("ip link set eth1 down", "RTNETLINK answers: File exists")
    assert not f("tc qdisc del dev eth1 root", "RTNETLINK answers: No such file or directory")
