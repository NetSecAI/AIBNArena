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
from benchmarks.platforms.containerlab.platform import ContainerlabPlatform, ProbeExecutionError
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
    "wan_edge": "wan1",
    "shaped_interface": "eth2",
}
CIRCUIT = ("wan1", "eth2")
TX_BYTES = "cat /sys/class/net/eth2/statistics/tx_bytes"


class _Executor:
    """Answers each liveness check from a script and records everything run.

    With ``timed_out`` the measuring client never returns, the way a starved
    one does on the lab, and the exec gives up on it.
    """

    def __init__(self, counts, *, mbps=7.65, log_tail=DEAD_FLOOD_REPORT, timed_out=False,
                 client_stdout=None, egress_mbps=9.8, clock=None):
        self.counts = list(counts)
        self.mbps = mbps
        self.log_tail = log_tail
        self.timed_out = timed_out
        # When set, the measuring client prints this instead of a report: a
        # refused connection, say, which leaves nothing to parse.
        self.client_stdout = client_stdout
        # The shaped interface's counter grows at this rate on the fake clock,
        # which the client advances by its own window; None reads as no counter.
        self.egress_mbps = egress_mbps
        self.clock = clock if clock is not None else [0.0]
        self.commands: list[tuple[str, str]] = []

    def run_shell(self, node, command, timeout_seconds=None):
        self.commands.append((node.name, command))
        stdout = ""
        if command == TX_BYTES:
            if self.egress_mbps is not None:
                stdout = f"{int(self.egress_mbps * 125_000 * self.clock[0])}\n"
        elif command.startswith("iperf3 -c"):
            self.clock[0] += 6.0  # 4 s sample + 2 s warm-up
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


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock only the fake client moves, shared with the executor."""
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


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
    """One real shaping-repair oracle, resolved the way the lifecycle loads it.

    The version is the one the scenario pins, so a bump of the oracle file is
    tested at the version that will actually run.
    """
    scenario = yaml.safe_load((ROOT / "scenarios/qos/wan_shaping_policy_repair.yaml").read_text())
    pinned = scenario["oracles"][phase.replace("-", "_")]["version"]
    document = load_oracle(SHAPING_ORACLES / f"{phase}-v1.yaml", expected_version=pinned)
    return resolve_oracle(document, BINDINGS)


@pytest.mark.parametrize(
    ("phase", "mbps"),
    [("healthy", 7.65), ("expected-degradation", 0.8), ("repair", 7.65)],
)
def test_every_shaping_phase_fails_closed_on_a_sample_the_flood_did_not_cover(
    sleeps, clock, phase, mbps
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
        platform.env = _env(_Executor(counts=counts, mbps=mbps, clock=clock))  # type: ignore[assignment]
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


def _assured_oracle(phase):
    """One real assured_bandwidth oracle, at the version the scenario pins."""
    scenario = yaml.safe_load((ROOT / "scenarios/qos/assured_bandwidth.yaml").read_text())
    pinned = scenario["oracles"][phase.replace("-", "_")]["version"]
    document = load_oracle(ROOT / "scenarios/oracles/qos/assured-bandwidth" / f"{phase}-v1.yaml",
                           expected_version=pinned)
    return resolve_oracle(document, BINDINGS)


@pytest.mark.parametrize(("phase", "mbps"), [("expected-degradation", 0.55), ("repair", 7.65)])
def test_assured_phases_fail_closed_once_the_faults_own_flood_has_ended(sleeps, clock, phase, mbps):
    """The flood is the fault, and it runs for a fixed duration.

    E18 ran past it: the repair probe found user1 alone on the link, and a subject
    that had changed nothing passed (pistes_e19, B5). The probe now looks for the
    flood on guest1 before and after the sample, and never starts or stops it.
    """
    oracle = _assured_oracle(phase)
    baseline = {"protected_flow": {"throughput_mbps": 9.5}}

    def evaluate(counts):
        executor = _Executor(counts=counts, mbps=mbps, clock=clock)
        platform = ContainerlabPlatform()
        platform.env = _env(executor)  # type: ignore[assignment]
        return OracleEvaluator(platform).evaluate(oracle, baseline=baseline), executor

    covered, executor = evaluate([1, 1])
    assert covered.passed is True
    assert covered.probes[0].metrics["throughput_mbps"] == mbps
    assert covered.probes[0].metrics["contention_checks"] == {"before_measurement": 1, "after_measurement": 1}
    assert executor.on("guest1") == [LIVENESS, LIVENESS]

    ended, executor = evaluate([1, 0])
    assert ended.passed is False
    metrics = ended.probes[0].metrics
    assert metrics["success"] is False
    assert metrics["throughput_mbps"] is None
    assert metrics["unverified_throughput_mbps"] == mbps
    assert metrics["contention_observed"] is False
    assert metrics["contention_checks"] == {"before_measurement": 1, "after_measurement": 0}
    assert metrics["error"] == throughput.FAULT_FLOOD_NOT_OBSERVED
    # The fault's flood writes no report, and is not the oracle's to stop.
    assert metrics["contention_log_tail"] is None
    assert executor.on("guest1") == [LIVENESS, LIVENESS]


def test_the_assured_healthy_phase_looks_for_no_flood(sleeps, clock):
    """Measured before the fault is injected: there is no flood to find."""
    executor = _Executor(counts=[], mbps=9.5, clock=clock)
    platform = ContainerlabPlatform()
    platform.env = _env(executor)  # type: ignore[assignment]

    OracleEvaluator(platform).evaluate(_assured_oracle("healthy"))

    assert executor.on("guest1") == []


def test_a_flood_is_the_faults_or_the_oracles_not_both(sleeps):
    with pytest.raises(ValueError):
        measure_throughput(_env(_Executor(counts=[])), BINDINGS, seconds=4, contended=True, fault_flood=True)

    platform = ContainerlabPlatform()
    platform.env = _env(_Executor(counts=[]))  # type: ignore[assignment]
    unplaced = {key: value for key, value in BINDINGS.items() if key != "background_source"}
    for measurement in ({**BINDINGS, "seconds": 4, "contended": True, "fault_flood": True},
                        {**unplaced, "seconds": 4, "contended": False, "fault_flood": True}):
        with pytest.raises(ProbeExecutionError, match="fault_flood"):
            platform._throughput({"id": "protected_flow", "type": "throughput", "measurement": measurement})


def test_the_circuit_counter_brackets_the_sample(sleeps, clock):
    executor = _Executor(counts=[1, 1], egress_mbps=9.8, clock=clock)

    measurement = measure_throughput(
        _env(executor), BINDINGS, seconds=4, contended=True, circuit=CIRCUIT
    )

    assert measurement["circuit_egress_mbps"] == 9.8
    assert measurement["circuit_window_seconds"] == 6.0
    # Read on the shaping node just before the client and just after it, both
    # times while the flood is still offered.
    assert executor.on("wan1") == [TX_BYTES, TX_BYTES]
    order = [command for _, command in executor.commands]
    last_read = len(order) - 1 - order[::-1].index(TX_BYTES)
    assert order.index(TX_BYTES) < order.index(CLIENT) < last_read < order.index(STOP)


def test_without_a_circuit_nothing_is_read_on_the_edge(sleeps):
    """A measurement that names no circuit reads nothing on the edge: the ANI
    validation of a criterion without `max_circuit_mbps` measures as before."""
    executor = _Executor(counts=[1, 1])

    measurement = measure_throughput(_env(executor), BINDINGS, seconds=4, contended=True)

    assert executor.on("wan1") == []
    assert measurement["circuit_egress_mbps"] is None
    assert measurement["circuit_window_seconds"] is None


def test_an_unreadable_counter_reports_no_egress(sleeps, clock):
    executor = _Executor(counts=[1, 1], egress_mbps=None, clock=clock)

    measurement = measure_throughput(
        _env(executor), BINDINGS, seconds=4, contended=True, circuit=CIRCUIT
    )

    assert measurement["circuit_egress_mbps"] is None
    # The protected flow is still a reading; only the circuit is unknown.
    assert measurement["throughput_mbps"] == 7.65


@pytest.mark.parametrize(
    ("egress", "passed"),
    [
        (9.8, True),    # circuit intact: the shaper lets out its own rate
        (11.9, True),   # header and burst accounting, inside the fifth
        (16.0, False),  # e10 wan_shaping m4: root rate raised to 16 Mbit
        (28.0, False),  # e10 wan_shaping m1: root rate raised to 60 Mbit, flood and flow
        (88.0, False),  # e10 wan_shaping m3: root qdisc deleted
        (None, False),  # no counter read: no evidence the circuit holds
    ],
)
def test_the_repair_fails_when_the_circuit_was_removed_or_widened(sleeps, clock, egress, passed):
    """Measured in e10: deleting the root qdisc or raising its rate cleared the
    throughput floor, and the oracle took it for a repair. The protected flow
    clears the floor in every case here; only the circuit tells them apart."""
    oracle = _shaping_oracle("repair")
    platform = ContainerlabPlatform()
    platform.env = _env(  # type: ignore[assignment]
        _Executor(counts=[1, 1], mbps=8.9, egress_mbps=egress, clock=clock)
    )

    result = OracleEvaluator(platform).evaluate(
        oracle, baseline={"protected_flow": {"throughput_mbps": 8.9}}
    )

    assert result.passed is passed
    assert result.probes[0].metrics["throughput_mbps"] == 8.9
    assert result.probes[0].metrics["circuit_egress_mbps"] == egress


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
    # The criterion caps no circuit, so nothing is read on the edge.
    assert env.executor.on("wan1") == []
    assert measurement["circuit_egress_mbps"] is None
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


CAPPED_TASK = {
    **TASK,
    "success_criteria": {
        "all_of": [{**TASK["success_criteria"]["all_of"][0], "max_circuit_mbps": "10"}]
    },
}


@pytest.mark.parametrize(
    ("egress", "passed"),
    [
        (9.8, True),    # circuit intact
        (11.9, True),   # inside the fifth the judge allows
        (15.8, False),  # E16 wan_shaping m4: class 1:20 left at its fault rate
        (None, False),  # no counter read: no evidence the circuit holds
    ],
)
def test_the_ani_validation_caps_the_circuit_its_criterion_declares(sleeps, clock, egress, passed):
    """E16 wan_shaping m4: all three subjects put the assured class back and left
    the other at its fault rate. The protected flow read 7.65 Mbps and this check
    passed, while the shaped interface sent 15.8 Mbps against the judge's cap of 12.
    The criterion now caps the circuit as the judge does."""
    ani, env = _ani(_Executor(counts=[1, 1], mbps=7.65, egress_mbps=egress, clock=clock))

    verdict = ani.evaluate_success_criteria(CAPPED_TASK)

    check = verdict["checks"][0]
    assert check["passed"] is passed
    assert check["measurement"]["throughput_mbps"] == 7.65
    assert check["measurement"]["circuit_egress_mbps"] == egress
    assert check["limits"]["max_circuit_mbps"] == 10.0
    assert check["limits"]["enforced_circuit_ceiling_mbps"] == 12.0
    assert check["limits"]["circuit_tolerance"] == throughput.CIRCUIT_TOLERANCE
    # Read on the shaping node the topology's qos_policy names, around the sample.
    assert env.executor.on("wan1") == [TX_BYTES, TX_BYTES]


def test_the_judge_and_the_ani_cap_the_circuit_with_one_tolerance():
    """The oracles state the cap as `factor: 1.2`; the ANI reads CIRCUIT_TOLERANCE.
    An agent passed by one and failed by the other would be scored on which ran."""
    for family in ("shaping-repair", "assured-bandwidth"):
        for phase in ("healthy", "repair"):
            document = load_oracle(ROOT / "scenarios/oracles/qos" / family / f"{phase}-v1.yaml")
            caps = [threshold for probe in document["probes"] for threshold in probe["thresholds"]
                    if threshold["metric"] == "circuit_egress_mbps"]
            assert [cap["factor"] for cap in caps] == [throughput.CIRCUIT_TOLERANCE], (family, phase)


@pytest.mark.parametrize(
    ("healthy", "mbps", "floor"),
    [
        (None, 6.27, 6.8),   # no healthy rate handed over: the contract alone, as before 2026-11-15
        (6.2, 6.27, 5.27),   # main03 campus_large: a healthy shaper read 6.2 to 6.3 in 14 of 24 episodes
        (6.2, 5.2, 5.27),    # below the band of the healthy rate
        (9.4, 7.65, 6.8),    # a healthy rate above the contract leaves the contract's bar
        (9.4, 6.5, 6.8),
    ],
)
def test_the_ani_validation_holds_the_flow_to_the_repair_oracles_bar(sleeps, healthy, mbps, floor):
    """The subject's own check and the judge's repair oracle give one verdict on one sample.
    Until 2026-11-15 the check asked for the contract alone, where the oracle asks for the
    lower of the contract and the healthy rate: a network the judge called repaired could
    fail the check a success needs."""
    service = {**SERVICE, **({} if healthy is None else {"healthy_throughput_mbps": healthy})}
    ani, _env = _ani(_Executor(counts=[1, 1], mbps=mbps))

    check = ani.evaluate_success_criteria({**TASK, "observation": {"service_measurement": service}})["checks"][0]

    assert check["limits"]["enforced_floor_mbps"] == floor
    # The floor is the bar; the healthy rate itself is not repeated to the subject (v0.8).
    assert "healthy_throughput_mbps" not in check["limits"]
    assert check["passed"] is (mbps >= floor)
    if healthy is not None:
        document = load_oracle(SHAPING_ORACLES / "repair-v1.yaml")
        [bar] = [threshold for probe in document["probes"] for threshold in probe["thresholds"]
                 if threshold["metric"] == "throughput_mbps"]
        judged = OracleEvaluator._threshold_passes(
            {**bar, "value": TASK["success_criteria"]["all_of"][0]["min_mbps"]},
            {"throughput_mbps": mbps}, {"protected_flow": {"throughput_mbps": healthy}}, "protected_flow")
        assert check["passed"] is judged


def test_the_judge_and_the_ani_hold_the_flow_to_one_bar():
    """The repair oracles bound the flow by the contract and by the healthy rate, the lower of
    the two (`combine: min`), each with `factor: 0.85`; the ANI reads ASSURED_TOLERANCE for both."""
    for family in ("shaping-repair", "assured-bandwidth"):
        document = load_oracle(ROOT / "scenarios/oracles/qos" / family / "repair-v1.yaml")
        [bar] = [threshold for probe in document["probes"] for threshold in probe["thresholds"]
                 if threshold["metric"] == "throughput_mbps"]
        assert (bar["operator"], bar["combine"], bar["factor"]) == ("gte", "min", throughput.ASSURED_TOLERANCE), family
        assert bar["baseline"] == {"metric": "throughput_mbps", "factor": throughput.ASSURED_TOLERANCE}, family


def test_a_circuit_cap_without_a_declared_circuit_fails_by_name(sleeps):
    executor = _Executor(counts=[1, 1])
    ani = ContainerLabANI(_env(executor), topology_document={})

    verdict = ani.evaluate_success_criteria(CAPPED_TASK)

    assert verdict["passed"] is False
    assert "qos_policy" in verdict["checks"][0]["error"]
    assert executor.commands == []


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


@pytest.mark.parametrize("topology", ["campus_small", "campus_medium", "campus_large", "sme_wan_edge_qos"])
def test_the_ani_contention_source_is_the_judges(topology):
    """The SUT's validation floods from the endpoint the judge floods from.

    On the campus labs the first traffic generator by name is external1, which has
    no required flow to the remote site's sink: its flood never crossed the circuit,
    and the validation passed a starved policy the judge measured at 0 Mbps.
    """
    from scenarios.compiler.compiler import ScenarioCompiler

    path = ROOT / f"scenarios/topologies/{topology}.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    compiled = ScenarioCompiler(document, seed=3).compile_scenarios(str(ROOT / "scenarios"))
    task = next(t for t in compiled["tasks"] if t["id"] == "qos.wan_shaping_policy_repair.m1")
    bindings = json.loads(json.dumps(task))

    def find(node):
        if isinstance(node, dict):
            if "background_source" in node:
                return node
            return next((hit for hit in map(find, node.values()) if hit), None)
        if isinstance(node, list):
            return next((hit for hit in map(find, node) if hit), None)
        return None

    judge = find(bindings)
    names = list((document["topology"]["nodes"] or {}).keys())
    config = ContainerLabEnvConfig(
        lab_name=topology, nodes={name: LabNode(name, "linux", f"clab-{topology}-{name}") for name in names})
    ani = ContainerLabANI(SimpleNamespace(config=config, executor=None), topology_document=document)

    source = ani._contention_source(judge["protected_source"], judge["destination"])

    assert source == judge["background_source"]
