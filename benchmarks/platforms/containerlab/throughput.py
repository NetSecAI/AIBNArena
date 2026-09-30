"""Throughput measurement for the shaped WAN-edge QoS scenarios.

The link-impairment family is judged with ping: a netem delay or corruption shows
up directly as RTT and loss. The shaped families cannot be judged that way. Their
faults -- a policy removed, a class starved, a classifier deleted, an allocation
inverted -- never break reachability, so every ICMP probe keeps passing whether
the policy is intact, degraded or gone. What they change is the rate one class
gets *while the link is contended*, which means the oracle has to offer that
contention itself and measure the protected flow through it.

Two sinks are needed on the destination, and the topology names them through the
`role` of its iperf3 services: one iperf3 server runs one test at a time, so a
single port would queue the measurement behind the flood it is meant to survive.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any


# Offered contention, as a multiple of the shaped link rate. Enough to saturate
# the bottleneck several times over, so the protected class only keeps its share
# if the policy actually reserves it -- and far enough below the fabric capacity
# upstream that the flood cannot starve the protected flow before it reaches the
# shaper, which no QoS policy could repair.
_LOAD_OVERSUBSCRIPTION = 2

# iperf3 -J prints one JSON document, but a run that fails prints diagnostics
# around it. Reading from the first brace keeps the parse working in both cases.
_JSON_START_RE = re.compile(r"\{")

# How close to the contractual rate a measured flow has to land to count as
# served. iperf3 reports the receiver's goodput, so headers and the shaper's own
# burst accounting cost a few percent even on an intact policy. Measured on the
# lab against the 8 Mbit assured class: a served flow read between 7.6 and 9.4
# Mbps, a starved one 0.76. Judging on the nominal rate alone fails a correct
# policy -- an intact one measured 7.605 against a bar of 8.
#
# It lives here because the runner and the ANI validation must enforce the same
# bar: an agent judged as repaired by one and broken by the other would be
# scored on which oracle happened to run.
ASSURED_TOLERANCE = 0.85

# Seconds of TCP ramp-up left out of the reported average. Measured on the lab:
# a sample taken right after a policy change read 6.49 Mbps against an 8 Mbit
# class and 8.65 Mbps once warm -- slow-start, not a broken policy, and enough to
# fail a correct repair. Omitting two seconds puts the cold sample back at 7.65;
# omitting three changes nothing further, so two is the cheapest that works.
WARMUP_SECONDS = 2

# Grace the measuring client gets beyond its own window before the probe gives up
# on it. It is also the head-room added to the offered load, so the flood outlives
# the client in every case: measured live on 2026-09-04 with the policy removed,
# the unresponsive flood starved the client's TCP handshake, connect backed off
# for seconds, the 12 s sample overran a 17 s flood and its tail ran on an idle
# link (readings of 3 to 9.6 Mbps for a starved flow). The flood is killed as soon
# as the measurement returns, so a long window costs nothing when the client is
# quick.
_CLIENT_GRACE_SECONDS = 30
# The flood starts a few seconds before the client (sink restart, liveness check),
# so its window carries a further allowance or a client that runs to its own
# timeout finds the flood already gone (one of ten starved samples on 2026-09-04).
_LOAD_MARGIN_SECONDS = _CLIENT_GRACE_SECONDS + 10

# Seconds between starting the flood and the first look at whether it is still
# there. A UDP client that cannot reach its sink is gone within a second of
# starting; two catches that and leaves the head-room above intact.
_FLOOD_SETTLE_SECONDS = 2

# How many times sink and flood are started before the measurement goes ahead
# without them and says so.
_CONTENTION_ATTEMPTS = 2

# Lines of the flood's own report kept when it is found dead.
_LOG_TAIL_LINES = 20

# The verdict the judge fails closed on. A reading taken with nothing on the
# wire is a reading of the circuit, not of the policy: live study on sme01-qos,
# 28 samples, four of them with the flood dead mid-window or never started, and
# every one of those four read the idle-circuit rate, which the oracle took for
# a policy that was not degraded.
NO_REPORT = (
    "iperf3 returned no parseable report; the client did not complete a test: "
)


def _last_line(output: str, width: int = 200) -> str:
    """The client's last non-empty line, so a refused connection names itself."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "<no output>")[-width:]


CONTENTION_NOT_OBSERVED = (
    "offered contention was not observed for the whole window; "
    "the reading measures an idle link, not the policy"
)


def iperf3_client_command(sink_ip: str, port: int, seconds: int) -> str:
    """The measurement command, spelled once for every caller.

    The runner and the ANI validation must measure the same way or an agent could
    pass one and fail the other.
    """
    return f"iperf3 -c {sink_ip} -p {port} -t {seconds} -O {WARMUP_SECONDS} -J"


def measurement_window(seconds: int) -> int:
    """How long the flow is actually on the wire, warm-up included."""
    return seconds + WARMUP_SECONDS


def load_window(seconds: int) -> int:
    """How long contention must be offered to cover one sample end to end."""
    return measurement_window(seconds) + _LOAD_MARGIN_SECONDS


def load_mbps(bindings: dict[str, Any]) -> int:
    """The competing rate the oracle offers when it has to create contention."""
    return int(bindings["link_mbps"]) * _LOAD_OVERSUBSCRIPTION


def generates_own_contention(bindings: dict[str, Any]) -> bool:
    """Whether the fault itself is the flood.

    assured_bandwidth injects the competing traffic as its fault, so the oracle
    must not start a second one -- and must not stop it either, since stopping it
    would remove the very condition being evaluated. The shaping family degrades a
    policy instead and leaves the link idle, so contention has to be offered.
    """
    return "background_mbps" in bindings


def load_logfile(bindings: dict[str, Any]) -> str:
    """Where the flood writes its own report, on the load source."""
    return f"/tmp/ani_load_{int(bindings['load_port'])}.json"


def restart_load_sink(env: Any, bindings: dict[str, Any]) -> Any:
    """Put a fresh iperf3 server on the load port of the destination.

    An iperf3 server serves one test at a time, so a sink still holding an
    earlier flood's session refuses the next one, and the flood dies before the
    measurement starts. Restarted the way generate_healthy_state.py starts every
    sink, before each flood, so no earlier session is left to refuse it.

    Killed by full argv so only the sink on the load port goes and the
    measurement sink on the same host keeps serving. The match is exact (-x):
    the shell running this very command carries the same text in its own argv,
    and an unanchored -f kills that shell before the sink is started, and takes
    a sink on any port that merely begins with these digits with it.
    """
    destination = str(bindings["destination"])
    port = int(bindings["load_port"])
    command = (
        f"sh -c 'pkill -xf \"iperf3 -s -p {port}\" || true; "
        f"setsid iperf3 -s -p {port} >/dev/null 2>&1 </dev/null &'"
    )
    return env.executor.run_shell(
        env.config.nodes[destination], command, timeout_seconds=15
    )


def start_background_load(
    env: Any,
    bindings: dict[str, Any],
    *,
    seconds: int,
) -> Any:
    """Offer the competing flow the protected class has to survive.

    Detached exactly like the scenario's own background traffic: the measurement
    that follows has to run while this is still in flight. Its report goes to a
    file of its own rather than to /dev/null, so a flood that dies leaves its
    error text where the liveness check can pick it up. The descriptors stay
    redirected: a detached client holding the exec's pipe would keep run_shell
    waiting for the whole window.
    """
    source = str(bindings["background_source"])
    sink_ip = str(bindings["sink_ip"])
    port = int(bindings["load_port"])
    rate = load_mbps(bindings)
    logfile = load_logfile(bindings)
    command = (
        f"sh -c ': > {logfile}; setsid iperf3 -c {sink_ip} -p {port} -u -b {rate}M "
        f"-t {seconds} -P 1 -J --logfile {logfile} >/dev/null 2>&1 </dev/null &'"
    )
    return env.executor.run_shell(
        env.config.nodes[source], command, timeout_seconds=15
    )


def flood_process_count(env: Any, bindings: dict[str, Any]) -> int | None:
    """How many iperf3 processes the load source is running right now.

    The load source runs neither sink nor measurement, so any iperf3 there is
    the flood. None when the count could not be read, and the caller treats that
    as absent: an unreadable count is no evidence the flood is there.

    Known limit: this is a process-count proxy for contention, not a reading of
    the wire. A flood whose control-connection SYN is dropped sits in connect()
    for its whole backoff, alive to pgrep while offering nothing at the shaper,
    and a UDP flood whose datagrams are dropped downstream of its own host keeps
    running and keeps reporting just the same. The checks catch a flood that
    died or never started, which is what the live study saw, and no more.
    """
    source = str(bindings["background_source"])
    result = env.executor.run_shell(
        env.config.nodes[source],
        "sh -c 'pgrep -x iperf3 | wc -l'",
        timeout_seconds=15,
    )
    try:
        return int(result.stdout.strip().split()[-1])
    except (IndexError, ValueError):
        return None


def load_log_tail(env: Any, bindings: dict[str, Any]) -> str:
    """The last lines the flood wrote, so a dead one says why it died."""
    source = str(bindings["background_source"])
    result = env.executor.run_shell(
        env.config.nodes[source],
        f"sh -c 'tail -n {_LOG_TAIL_LINES} {load_logfile(bindings)} 2>/dev/null || true'",
        timeout_seconds=15,
    )
    return (result.stdout + result.stderr).strip()


def stop_background_load(env: Any, bindings: dict[str, Any]) -> Any:
    """Stop the contention this module started.

    Killed by exact name on the source alone, so a flood the *scenario* injected
    from another node keeps running: that one is a fault, not an instrument.
    """
    source = str(bindings["background_source"])
    return env.executor.run_shell(
        env.config.nodes[source],
        "sh -c 'pkill -x iperf3 || true'",
        timeout_seconds=15,
    )


def parse_iperf3_mbps(output: str) -> float | None:
    """Received rate of an `iperf3 -J` run, in Mbps.

    The receiver's sum is the honest number: the sender reports what it handed to
    the kernel, which on a shaped link is everything it was asked to send, however
    much of it the shaper then dropped.
    """
    match = _JSON_START_RE.search(output)
    if not match:
        return None
    try:
        report = json.loads(output[match.start():])
    except json.JSONDecodeError:
        return None
    end = report.get("end")
    if not isinstance(end, dict):
        return None
    summary = end.get("sum_received") or end.get("sum") or {}
    bits = summary.get("bits_per_second")
    if bits is None:
        return None
    return round(float(bits) / 1_000_000, 3)


def measure_throughput(
    env: Any,
    bindings: dict[str, Any],
    *,
    seconds: int,
    contended: bool,
) -> dict[str, Any]:
    """Measure the protected flow, optionally under offered contention.

    With ``contended`` the oracle starts the competing flow, measures through it
    and stops it again. Without it the link is idle, which is what a baseline
    before any fault looks like: it says the path can carry the rate at all, and
    separates a broken path from a broken policy.

    The flood is looked for on the load source after it starts and again as soon
    as the measurement returns. A sample the flood did not cover end to end is
    reported with no throughput at all and the flood's own last words: an
    idle-circuit reading looks exactly like a policy that is not degraded, and a
    judge that thresholds the number would score it, so the number goes out as
    ``unverified_throughput_mbps`` instead and ``throughput_mbps`` stays None.
    """
    source = str(bindings["protected_source"])
    sink_ip = str(bindings["sink_ip"])
    port = int(bindings["measurement_port"])
    offered = None
    attempts = 0
    checks: dict[str, int | None] = {"after_start": None, "after_measurement": None}
    observed = None
    log_tail = None

    if contended:
        offered = load_mbps(bindings)
        # A fresh sink, then the flood, then one look once the client has had
        # time to fail. One more round when it did, and the measurement goes
        # ahead either way so the report says what the flood did.
        while True:
            attempts += 1
            restart_load_sink(env, bindings)
            start_background_load(env, bindings, seconds=load_window(seconds))
            time.sleep(_FLOOD_SETTLE_SECONDS)
            checks["after_start"] = flood_process_count(env, bindings)
            if checks["after_start"] or attempts >= _CONTENTION_ATTEMPTS:
                break

    timed_out = False
    try:
        try:
            result = env.executor.run_shell(
                env.config.nodes[source],
                iperf3_client_command(sink_ip, port, seconds),
                timeout_seconds=measurement_window(seconds) + _CLIENT_GRACE_SECONDS,
            )
            output = (result.stdout + result.stderr).strip()
            returncode = result.returncode
            throughput = parse_iperf3_mbps(output)
        except subprocess.TimeoutExpired:
            # Seen on the lab: with no policy at all, the unresponsive flood
            # starves the measurement so thoroughly that iperf3 never finishes
            # its window. That is the fault at its worst, not a broken
            # instrument: a flow that carried nothing measurable carried
            # nothing, so it reads as zero rather than crashing the run. A sink
            # that is simply down fails fast instead, and reports no number at
            # all.
            timed_out = True
            output = f"iperf3 did not complete within {measurement_window(seconds) + _CLIENT_GRACE_SECONDS}s"
            returncode = -1
            throughput = 0.0
            # The client kept running inside the container after the exec gave
            # up, and an iperf3 server serves one test at a time: leaving it
            # there makes the *next* measurement fail to connect, which would
            # read as an unmeasurable link rather than as this one sample timing
            # out. Only the measuring endpoint is cleaned: a flood the scenario
            # injected lives elsewhere and is not ours to kill.
            env.executor.run_shell(
                env.config.nodes[source],
                "sh -c 'pkill -x iperf3 || true'",
                timeout_seconds=15,
            )
        if contended:
            # Read before the flood is stopped, or the count says nothing. The
            # timed-out client is looked at too: the flood's window outlives the
            # client's grace by _LOAD_MARGIN_SECONDS, so a flood gone now was
            # gone inside the window. A client the live flood starved into its
            # timeout read 0.0 under contention, the fault at its worst; a
            # client that timed out with the flood dead read nothing anyone can
            # score. Both checks have to see it, with no exception for either.
            checks["after_measurement"] = flood_process_count(env, bindings)
            observed = bool(checks["after_start"]) and bool(checks["after_measurement"])
            if not observed:
                log_tail = load_log_tail(env, bindings)
    finally:
        if contended:
            stop_background_load(env, bindings)

    # A reading the flood did not cover is not a reading: the idle circuit
    # measures like an intact policy, and every oracle of the shaped families
    # thresholds throughput_mbps alone, so the number has to leave that field or
    # healthy, degradation and repair would all score it. It is kept beside it,
    # unverified, so the record still says what the client saw.
    verified = observed is not False
    assured = bindings.get("assured_bandwidth_mbps")
    return {
        "kind": "throughput",
        "source": source,
        "destination": str(bindings["destination"]),
        "destination_ip": str(bindings["destination_ip"]),
        "measurement_port": port,
        "seconds": seconds,
        "warmup_seconds": WARMUP_SECONDS,
        "contended": contended,
        "offered_load_mbps": offered,
        # What the flood itself managed to send is in its report on the load
        # source; parsing the report of a flood that was killed is out of scope.
        "offered_load_observed": None,
        "contention_observed": observed,
        "contention_checks": checks,
        "contention_attempts": attempts,
        "contention_log_tail": log_tail,
        "assured_bandwidth_mbps": None if assured is None else int(assured),
        "returncode": returncode,
        "timed_out": timed_out,
        "throughput_mbps": throughput if verified else None,
        "unverified_throughput_mbps": None if verified else throughput,
        "output": output,
        "ok": verified and throughput is not None,
        "error": (
            CONTENTION_NOT_OBSERVED if not verified
            else None if throughput is not None
            else NO_REPORT + _last_line(output)
        ),
    }
