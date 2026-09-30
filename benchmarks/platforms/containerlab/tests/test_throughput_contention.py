"""The offered contention has to be seen on the wire, or the reading is void.

Live study on sme01-qos, 28 samples through the judge's own measure_throughput:
in four the UDP flood from guest1 died mid-window or never started, and every
one of those read the idle-circuit rate, which the oracle takes for a policy
that is not degraded. These tests pin what the oracle now does about it: a
fresh sink before the flood, the flood's report kept, the flood looked for
after it starts and after the sample, one retry, and a sample it did not cover
reported with no throughput at all and the flood's own last words.

The last part is what makes the guard bite: the judge thresholds throughput_mbps
and nothing else, so a `success` of False on its own was inert, and the ANI's
own validation measured with a bare client that had no guard at all.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest
import yaml

from benchmarks.core import OracleEvaluator
from benchmarks.platforms.containerlab import throughput
from benchmarks.platforms.containerlab.ani import ContainerLabANI
from benchmarks.platforms.containerlab.env import ContainerLabEnvConfig
from benchmarks.platforms.containerlab.platform import ContainerlabPlatform
from benchmarks.platforms.containerlab.throughput import measure_throughput
from benchmarks.platforms.containerlab.types import CommandResult, LabNode
from scenarios.oracle_loader import load_oracle, resolve_oracle

ROOT = Path(__file__).resolve().parents[4]
QOS_TOPOLOGY = ROOT / "scenarios/topologies/sme_wan_edge_qos.yaml"
SHAPING_ORACLES = ROOT / "scenarios/oracles/qos/shaping-repair"


SINK_RESTART = (
    "sh -c 'pkill -xf \"iperf3 -s -p 5202\" || true; "
    "setsid iperf3 -s -p 5202 >/dev/null 2>&1 </dev/null &'"
)
FLOOD_START = (
    "sh -c ': > /tmp/ani_load_5202.json; "
    "setsid iperf3 -c 203.0.113.10 -p 5202 -u -b 20M -t 46 -P 1 "
    "-J --logfile /tmp/ani_load_5202.json >/dev/null 2>&1 </dev/null &'"
)
LIVENESS = "sh -c 'pgrep -x iperf3 | wc -l'"
CLIENT = "iperf3 -c 203.0.113.10 -p 5201 -t 4 -O 2 -J"
LOG_TAIL = "sh -c 'tail -n 20 /tmp/ani_load_5202.json 2>/dev/null || true'"
STOP = "sh -c 'pkill -x iperf3 || true'"

# The last lines a flood that could not reach its sink leaves in its report.
DEAD_FLOOD_REPORT = '"error":\t"error - unable to connect to server: Connection refused"\n}'

BINDINGS = {
    "protected_source": "user1",
    "background_source": "guest1",
    "destination": "external1",
    "destination_ip": "203.0.113.10",
    "sink_ip": "203.0.113.10",
    "measurement_port": 5201,
    "load_port": 5202,
    "link_mbps": 10,
    "assured_bandwidth_mbps": 8,
}


class _Executor:
    """Answers each liveness check from a script and records everything run.

    With ``timed_out`` the measuring client never returns, the way a starved
    one does on the lab, and the exec gives up on it.
    """

    def __init__(self, counts, *, mbps=7.65, log_tail=DEAD_FLOOD_REPORT, timed_out=False,
                 client_stdout=None):
        self.counts = list(counts)
        self.mbps = mbps
        self.log_tail = log_tail
        self.timed_out = timed_out
        # When set, the measuring client prints this instead of a report: a
        # refused connection, say, which leaves nothing to parse.
        self.client_stdout = client_stdout
        self.commands: list[tuple[str, str]] = []

    def run_shell(self, node, command, timeout_seconds=None):
        self.commands.append((node.name, command))
        stdout = ""
        if command.startswith("iperf3 -c"):
            if self.timed_out:
                raise subprocess.TimeoutExpired(command, timeout_seconds or 0)
            if self.client_stdout is not None:
                stdout = self.client_stdout
            else:
                bits = self.mbps * 1_000_000
                stdout = json.dumps({"end": {"sum_received": {"bits_per_second": bits}}})
        elif command == LIVENESS:
            stdout = f"{self.counts.pop(0)}\n"
        elif command.startswith("sh -c 'tail -n"):
            stdout = self.log_tail
        return CommandResult(target=node.name, command=command, returncode=0, stdout=stdout)

    def on(self, node):
        return [command for name, command in self.commands if name == node]


def _env(executor):
    names = ["user1", "guest1", "external1", "wan1"]
    config = ContainerLabEnvConfig(
        lab_name="sme01-qos",
        nodes={name: LabNode(name, "linux", f"clab-sme01-qos-{name}") for name in names},
    )
    return SimpleNamespace(config=config, executor=executor)


@pytest.fixture
def sleeps(monkeypatch):
    """Record the settle waits instead of taking them."""
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", calls.append)
    return calls


def test_a_flood_alive_at_both_checks_is_observed_contention(sleeps):
    env = _env(_Executor(counts=[1, 1]))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)

    assert measurement["ok"] is True
    assert measurement["error"] is None
    assert measurement["contention_observed"] is True
    assert measurement["contention_checks"] == {"after_start": 1, "after_measurement": 1}
    assert measurement["contention_attempts"] == 1
    assert measurement["contention_log_tail"] is None
    assert measurement["throughput_mbps"] == 7.65
    assert measurement["unverified_throughput_mbps"] is None
    assert measurement["offered_load_mbps"] == 20
    assert measurement["offered_load_observed"] is None
    # Sink first, then the flood, one settle wait, one look, the sample, one
    # more look before the flood is stopped. Nothing else.
    assert env.executor.commands == [
        ("external1", SINK_RESTART),
        ("guest1", FLOOD_START),
        ("guest1", LIVENESS),
        ("user1", CLIENT),
        ("guest1", LIVENESS),
        ("guest1", STOP),
    ]
    assert sleeps == [2]


def test_a_flood_dead_after_start_is_restarted_once_with_its_sink(sleeps):
    env = _env(_Executor(counts=[0, 1, 1]))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)

    assert measurement["ok"] is True
    assert measurement["contention_observed"] is True
    assert measurement["contention_attempts"] == 2
    assert measurement["contention_checks"] == {"after_start": 1, "after_measurement": 1}
    assert env.executor.on("external1") == [SINK_RESTART, SINK_RESTART]
    assert env.executor.on("guest1") == [
        FLOOD_START, LIVENESS, FLOOD_START, LIVENESS, LIVENESS, STOP,
    ]
    # The sample is taken once, after the second start held.
    assert env.executor.on("user1") == [CLIENT]
    assert sleeps == [2, 2]


def test_a_flood_that_never_starts_still_measures_and_fails_closed(sleeps):
    env = _env(_Executor(counts=[0, 0, 0], mbps=9.41))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)

    # The idle-circuit reading is kept for the record, but not as a throughput:
    # the judge thresholds that field alone, and would score the idle circuit.
    assert measurement["throughput_mbps"] is None
    assert measurement["unverified_throughput_mbps"] == 9.41
    assert measurement["ok"] is False
    assert measurement["contention_observed"] is False
    assert measurement["contention_attempts"] == 2
    assert measurement["contention_checks"] == {"after_start": 0, "after_measurement": 0}
    assert measurement["contention_log_tail"] == DEAD_FLOOD_REPORT
    assert env.executor.on("external1") == [SINK_RESTART, SINK_RESTART]


def test_a_flood_dead_after_the_sample_voids_the_reading(sleeps):
    env = _env(_Executor(counts=[1, 0], mbps=9.35))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)

    assert measurement["ok"] is False
    assert "contention" in measurement["error"]
    assert "idle link" in measurement["error"]
    assert measurement["contention_observed"] is False
    assert measurement["contention_checks"] == {"after_start": 1, "after_measurement": 0}
    assert measurement["contention_attempts"] == 1
    assert measurement["contention_log_tail"] == DEAD_FLOOD_REPORT
    # The reading itself is still there for the record, and only there.
    assert measurement["throughput_mbps"] is None
    assert measurement["unverified_throughput_mbps"] == 9.35
    # The report is read before the flood is stopped; the stop stays last.
    assert env.executor.on("guest1") == [FLOOD_START, LIVENESS, LIVENESS, LOG_TAIL, STOP]


def test_a_timed_out_client_with_the_flood_dead_is_not_observed(sleeps):
    """The timeout path gets no exception from the after-measurement check.

    The flood outlives the client's grace by _LOAD_MARGIN_SECONDS, so a flood
    gone when the exec gives up was gone inside the window. after_start is 1
    and after_measurement is 0 here: a path that skipped the second check, or
    let the timeout stand in for it, would call this sample observed.
    """
    env = _env(_Executor(counts=[1, 0], timed_out=True))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)

    assert measurement["timed_out"] is True
    assert measurement["contention_checks"] == {"after_start": 1, "after_measurement": 0}
    assert measurement["contention_observed"] is False
    assert measurement["ok"] is False
    assert measurement["error"] == throughput.CONTENTION_NOT_OBSERVED
    assert measurement["throughput_mbps"] is None
    assert measurement["unverified_throughput_mbps"] == 0.0
    assert measurement["contention_log_tail"] == DEAD_FLOOD_REPORT
    # Both looks at the flood are taken, then its report, then the stop.
    assert env.executor.on("guest1") == [FLOOD_START, LIVENESS, LIVENESS, LOG_TAIL, STOP]
    # The stalled client is still cleaned off the measuring endpoint.
    assert env.executor.on("user1") == [CLIENT, STOP]


def test_a_timed_out_client_with_the_flood_alive_is_the_starved_case(sleeps):
    """Seen on the lab with no policy at all: the flood starves the client into
    its timeout. The flood was there the whole way, so the sample is observed
    and reads as zero, the fault at its worst."""
    env = _env(_Executor(counts=[1, 1], timed_out=True))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)

    assert measurement["timed_out"] is True
    assert measurement["contention_checks"] == {"after_start": 1, "after_measurement": 1}
    assert measurement["contention_observed"] is True
    assert measurement["ok"] is True
    assert measurement["error"] is None
    assert measurement["throughput_mbps"] == 0.0
    assert measurement["unverified_throughput_mbps"] is None
    assert env.executor.on("guest1") == [FLOOD_START, LIVENESS, LIVENESS, STOP]


def test_an_idle_baseline_touches_neither_sink_nor_flood(sleeps):
    env = _env(_Executor(counts=[], mbps=9.56))

    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=False)

    assert measurement["ok"] is True
    assert measurement["contention_observed"] is None
    assert measurement["contention_attempts"] == 0
    assert measurement["contention_checks"] == {"after_start": None, "after_measurement": None}
    assert measurement["contention_log_tail"] is None
    assert env.executor.commands == [("user1", CLIENT)]
    assert sleeps == []


def _probe():
    return {
        "id": "protected_flow",
        "type": "throughput",
        "measurement": {**BINDINGS, "seconds": 4, "contended": True},
    }


def test_the_platform_probe_fails_a_sample_the_flood_did_not_cover(sleeps):
    """The judge scores `success`; an idle-circuit reading must not earn it."""
    platform = ContainerlabPlatform()
    platform.env = _env(_Executor(counts=[1, 0], mbps=9.35))  # type: ignore[assignment]

    metrics = platform._throughput(_probe())

    assert metrics["success"] is False
    assert metrics["throughput_mbps"] is None
    assert metrics["unverified_throughput_mbps"] == 9.35
    assert metrics["contention_observed"] is False
    assert metrics["contention_attempts"] == 1
    # The voided sample says which check the flood failed and what it reported.
    assert metrics["contention_checks"] == {"after_start": 1, "after_measurement": 0}
    assert metrics["contention_log_tail"] == DEAD_FLOOD_REPORT


def test_the_platform_probe_bounds_the_flood_report_it_forwards(sleeps):
    long_report = "x" * 5000 + DEAD_FLOOD_REPORT
    platform = ContainerlabPlatform()
    platform.env = _env(_Executor(counts=[1, 0], log_tail=long_report))  # type: ignore[assignment]

    metrics = platform._throughput(_probe())

    assert len(metrics["contention_log_tail"]) == 2000
    # The end is kept: that is where iperf3 writes its error.
    assert metrics["contention_log_tail"].endswith(DEAD_FLOOD_REPORT)


def test_the_platform_probe_passes_a_sample_the_flood_covered(sleeps):
    platform = ContainerlabPlatform()
    platform.env = _env(_Executor(counts=[0, 1, 1], mbps=7.65))  # type: ignore[assignment]

    metrics = platform._throughput(_probe())

    assert metrics["success"] is True
    assert metrics["throughput_mbps"] == 7.65
    assert metrics["unverified_throughput_mbps"] is None
    assert metrics["contention_observed"] is True
    assert metrics["contention_attempts"] == 2
    assert metrics["contention_log_tail"] is None


def _shaping_oracle(phase):
    """One real shaping-repair oracle, resolved the way the lifecycle loads it."""
    document = load_oracle(SHAPING_ORACLES / f"{phase}-v1.yaml", expected_version="1.0.0")
    return resolve_oracle(document, BINDINGS)


@pytest.mark.parametrize(
    ("phase", "mbps"),
    [("healthy", 7.65), ("expected-degradation", 0.8), ("repair", 7.65)],
)
def test_every_shaping_phase_fails_closed_on_a_sample_the_flood_did_not_cover(
    sleeps, phase, mbps
):
    """End to end through the judge, against the shipped oracles.

    Each of them thresholds throughput_mbps and nothing else, so the probe's
    `success` never reached the verdict: with a number in that field an idle
    circuit passed healthy and repair, and a client that read low with the
    flood gone passed degradation. The same reading with the flood alive at
    both checks is a reading, and passes.
    """
    oracle = _shaping_oracle(phase)
    baseline = {"protected_flow": {"throughput_mbps": 8.9}}

    def evaluate(counts):
        platform = ContainerlabPlatform()
        platform.env = _env(_Executor(counts=counts, mbps=mbps))  # type: ignore[assignment]
        return OracleEvaluator(platform).evaluate(oracle, baseline=baseline)

    covered = evaluate([1, 1])
    assert covered.passed is True
    assert covered.probes[0].metrics["throughput_mbps"] == mbps

    voided = evaluate([1, 0])
    assert voided.passed is False
    metrics = voided.probes[0].metrics
    assert metrics["success"] is False
    assert metrics["throughput_mbps"] is None
    assert metrics["unverified_throughput_mbps"] == mbps
    assert metrics["contention_observed"] is False
    assert metrics["contention_checks"] == {"after_start": 1, "after_measurement": 0}
    assert metrics["contention_log_tail"] == DEAD_FLOOD_REPORT


SERVICE = {"source": "user1", "destination": "external1", "destination_ip": "203.0.113.10"}
TASK = {
    "observation": {"service_measurement": SERVICE},
    "success_criteria": {
        "all_of": [
            {"type": "observed_throughput", "min_mbps": 8, "under_contention": True, "seconds": 4}
        ]
    },
}


def _ani(executor):
    env = _env(executor)
    document = yaml.safe_load(QOS_TOPOLOGY.read_text(encoding="utf-8"))
    return ContainerLabANI(env, topology_document=document), env


def test_the_ani_validation_measures_with_the_runner_instrument(sleeps):
    """The SUT's own validation takes the same path as the judge: fresh sink,
    flood, both liveness checks, one retry, and the ports stay unpublished."""
    ani, env = _ani(_Executor(counts=[0, 1, 1], mbps=7.65))

    verdict = ani.evaluate_success_criteria(TASK)

    assert verdict["passed"] is True
    measurement = verdict["checks"][0]["measurement"]
    assert measurement["passed"] is True
    assert measurement["throughput_mbps"] == 7.65
    assert measurement["unverified_throughput_mbps"] is None
    assert measurement["contention_observed"] is True
    assert measurement["contention_attempts"] == 2
    assert measurement["error"] is None
    assert env.executor.on("external1") == [SINK_RESTART, SINK_RESTART]
    assert env.executor.on("guest1") == [
        FLOOD_START, LIVENESS, FLOOD_START, LIVENESS, LIVENESS, STOP,
    ]
    assert env.executor.on("user1") == [CLIENT]
    # The instrument comes from the reviewed topology and never goes back out:
    # an agent that knew the measured port could shape that port and pass.
    assert "5201" not in json.dumps(verdict)
    assert "5202" not in json.dumps(verdict)


def test_the_ani_validation_fails_closed_on_a_sample_the_flood_did_not_cover(sleeps):
    ani, env = _ani(_Executor(counts=[1, 0], mbps=9.35))

    verdict = ani.evaluate_success_criteria(TASK)

    assert verdict["passed"] is False
    check = verdict["checks"][0]
    assert check["passed"] is False
    measurement = check["measurement"]
    assert measurement["passed"] is False
    assert measurement["error"] == throughput.CONTENTION_NOT_OBSERVED
    assert measurement["throughput_mbps"] is None
    assert measurement["unverified_throughput_mbps"] == 9.35
    assert measurement["contention_observed"] is False
    assert measurement["contention_checks"] == {"after_start": 1, "after_measurement": 0}
    # The flood's report names the load port; it stays with the judge.
    assert "contention_log_tail" not in measurement
    assert "5201" not in json.dumps(verdict)
    assert "5202" not in json.dumps(verdict)
    assert env.executor.on("guest1") == [FLOOD_START, LIVENESS, LIVENESS, LOG_TAIL, STOP]


def test_a_client_without_a_report_fails_with_a_named_reason(sleeps):
    """Flood alive, client connect refused: the sample fails and says why."""
    env = _env(_Executor(
        counts=[1, 1],
        client_stdout="iperf3: error - unable to connect to server: Connection refused\n"))
    measurement = measure_throughput(env, BINDINGS, seconds=4, contended=True)
    assert measurement["ok"] is False
    assert measurement["throughput_mbps"] is None
    assert measurement["unverified_throughput_mbps"] is None
    assert measurement["contention_observed"] is True
    assert "no parseable report" in measurement["error"], measurement["error"]
    assert "Connection refused" in measurement["error"], measurement["error"]
