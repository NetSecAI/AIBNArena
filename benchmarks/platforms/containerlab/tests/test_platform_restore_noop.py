"""The judge's restoration accepts a device that already holds the reference (nothing to commit)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from benchmarks.platforms.containerlab.executor import SRL_COMMIT_NOOP, ContainerLabExecutor
from benchmarks.platforms.containerlab.platform import ContainerlabPlatform
from benchmarks.platforms.containerlab.tests.test_executor_transactions import SRL_NODE, RecordingRunner


class _Env:
    """An environment whose only device answers every candidate with nothing to commit."""

    def __init__(self):
        self.executor = ContainerLabExecutor(Path("unused.yaml"), runner=RecordingRunner(0, stdout=f"{SRL_COMMIT_NOOP}\n"))
        self.injected = []

    def inject(self, fault):
        self.injected.append(fault)
        return [self.executor.run_srl_cli_batch(SRL_NODE, list(fault.commands))]

    def apply_state(self, state):
        raise AssertionError("no reference state in this test")

    def stop(self):
        raise AssertionError("restore must not destroy")


def _platform(env: _Env) -> SimpleNamespace:
    return SimpleNamespace(
        env=env,
        _fault=SimpleNamespace(id="connectivity.remove_ip.1",
                               restore_commands=["set / interface ethernet-1/1 subinterface 0 ipv4 address 10.0.0.1/24"]),
        _reference_state=None,
    )


def test_restore_or_destroy_accepts_a_reference_already_in_place():
    env = _Env()
    result = ContainerlabPlatform.restore_or_destroy(_platform(env), "restore")
    assert result["ok"] is True, result
    assert env.injected[0].intent == "restore"
    assert env.executor._accept_noop_commit is False, "strict again once restoration is done"


def test_restore_fault_between_attempts_accepts_it_too():
    env = _Env()
    result = ContainerlabPlatform.restore_fault(_platform(env))
    assert result["ok"] is True, result
    assert env.executor._accept_noop_commit is False


def test_a_subject_write_that_changes_nothing_is_no_change_not_a_write():
    from benchmarks.platforms.containerlab.executor import NoChangeExecutionError

    env = _Env()
    try:
        env.executor.run_srl_cli_batch(SRL_NODE, ["set / interface ethernet-1/1 admin-state enable"])
    except NoChangeExecutionError as exc:
        assert "already in the requested state" in str(exc)
    else:
        raise AssertionError("outside restoration a no-op must not pass as a proven write")
