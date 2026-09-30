#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab import ContainerLabEnv  # noqa: E402
from benchmarks.platforms.containerlab.compiled_topology import (  # noqa: E402
    CompiledContainerLabTopology,
)
from benchmarks.platforms.containerlab.state import LabState, StateCommand  # noqa: E402
from benchmarks.platforms.containerlab.throughput import measure_throughput  # noqa: E402


_PACKET_LOSS_RE = re.compile(r"(?P<loss>[0-9]+(?:\.[0-9]+)?)%\s+packet loss")
_RTT_RE = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"(?P<minimum>[0-9.]+)/(?P<average>[0-9.]+)/(?P<maximum>[0-9.]+)/"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply an evaluator-private scenario restore config."
    )
    parser.add_argument("restore_config", help="Generated restore-config JSON path.")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--verify-timeout", type=float, default=30.0)
    parser.add_argument("--verify-interval", type=float, default=2.0)
    args = parser.parse_args()

    artifact_path = Path(args.restore_config)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "0.1":
        raise ValueError("restore config must use schema_version '0.1'")
    if payload.get("kind") != "containerlab_restore_config":
        raise ValueError("unsupported restore config kind")
    if not payload.get("commands"):
        raise ValueError("restore config contains no commands")
    expected_digest = payload.pop("sha256", None)
    actual_digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    if expected_digest != actual_digest:
        raise ValueError("restore config integrity check failed")

    topology = CompiledContainerLabTopology.from_file(
        payload["topology_descriptor_path"]
    )
    env = ContainerLabEnv(
        topology.env_config(healthy_state_path=payload["healthy_state_path"])
    )
    state = LabState(
        id=f"restore:{payload['scenario_instance_id']}",
        description=payload.get("description", ""),
        commands=[StateCommand(**item) for item in payload["commands"]],
    )
    results = env.apply_state(state)
    for result in results:
        status = "OK" if result.ok else "FAILED"
        print(f"[{status}] {result.target}: {result.command.splitlines()[0]}")
        if result.stderr.strip():
            print(result.stderr.strip())

    verified = True
    if args.verify and all(result.ok for result in results):
        verified, observation = env.wait_until_healthy(
            timeout_seconds=args.verify_timeout,
            interval_seconds=args.verify_interval,
        )
        for check in observation.connectivity:
            status = "OK" if check.success else "FAILED"
            print(
                f"[{status}] connectivity {check.source} -> {check.destination} "
                f"({check.destination_ip}), packet_loss={check.packet_loss_percent}%"
            )
        qos_validation = payload.get("post_restore_validation")
        if verified and qos_validation:
            qos_result = _run_post_restore_validation(env, qos_validation)
            _print_qos_after_repair(qos_validation, qos_result)
            verified = bool(qos_result["recovered"])
        print(f"verify={verified}")

    return 0 if all(result.ok for result in results) and verified else 1


def _run_post_restore_validation(
    env: ContainerLabEnv,
    validation: dict,
) -> dict:
    kind = validation.get("kind")
    if kind == "qos_assured_throughput.v1":
        return _run_throughput_validation(env, validation)
    if kind != "qos_link_impairment.v1":
        raise ValueError(
            "unsupported post-restore validation kind "
            f"{validation.get('kind')!r}"
        )
    path = validation.get("path") or {}
    probe = validation.get("probe") or {}
    source = str(path["source"])
    destination = str(path["destination"])
    destination_ip = str(path["destination_ip"])
    count = int(probe.get("count", 5))
    timeout = int(probe.get("timeout_seconds", 1))
    if count < 1 or timeout < 1:
        raise ValueError("post-restore QoS probe values must be positive")
    if source not in env.config.nodes or destination not in env.config.nodes:
        raise ValueError("post-restore QoS path references an unknown lab node")

    command_result = env.executor.run_shell(
        env.config.nodes[source],
        f"ping -q -c {count} -W {timeout} {destination_ip}",
        timeout_seconds=max(30, count * (timeout + 1) + 5),
    )
    output = (command_result.stdout + command_result.stderr).strip()
    loss_match = _PACKET_LOSS_RE.search(output)
    rtt_match = _RTT_RE.search(output)
    measurement = {
        "source": source,
        "destination": destination,
        "destination_ip": destination_ip,
        "packet_loss_percent": (
            float(loss_match.group("loss")) if loss_match else 100.0
        ),
        "rtt_min_ms": (
            float(rtt_match.group("minimum")) if rtt_match else None
        ),
        "rtt_avg_ms": (
            float(rtt_match.group("average")) if rtt_match else None
        ),
        "rtt_max_ms": (
            float(rtt_match.group("maximum")) if rtt_match else None
        ),
    }
    recovered, threshold = _qos_recovered(validation, measurement)
    return {
        "measurement": measurement,
        "recovered": recovered,
        "recovery_threshold": threshold,
    }


def _run_throughput_validation(env: ContainerLabEnv, validation: dict) -> dict:
    """Replay the measured flow after the reference restore put the policy back.

    A shaped-policy fault leaves the link reachable, so the restore can only be
    confirmed by re-measuring the rate -- and, where the fault was not itself a
    flood, by offering the contention again while measuring.
    """
    flow = validation.get("flow") or {}
    contention = validation.get("contention") or {}
    seconds = int((validation.get("probe") or {}).get("seconds") or 10)
    offered = contention.get("offered_mbps")

    bindings = {
        "protected_source": flow["source"],
        "destination": flow["destination"],
        "destination_ip": flow["sink_ip"],
        "sink_ip": flow["sink_ip"],
        "measurement_port": flow["measurement_port"],
        "background_source": contention.get("source"),
        "load_port": contention.get("load_port"),
        "assured_bandwidth_mbps": validation["assured_bandwidth_mbps"],
        # measure_throughput() derives the offered rate from the link; the
        # descriptor already carries what that came to, so keep the two equal.
        "link_mbps": int(offered) // 2 if offered else 0,
    }
    measurement = measure_throughput(
        env, bindings, seconds=seconds, contended=offered is not None
    )
    limit = float(validation["min_throughput_mbps"])
    observed = measurement.get("throughput_mbps")
    recovered = observed is not None and float(observed) >= limit
    return {
        "measurement": {
            key: measurement.get(key)
            for key in (
                "source",
                "destination",
                "destination_ip",
                "throughput_mbps",
                "contended",
                "offered_load_mbps",
            )
        },
        "recovered": recovered,
        "recovery_threshold": {"min_throughput_mbps": limit},
    }


def _qos_recovered(validation: dict, measurement: dict) -> tuple[bool, dict]:
    impairment = validation.get("impairment")
    baseline = validation.get("baseline") or {}
    loss = float(measurement["packet_loss_percent"])
    if impairment == "delay":
        baseline_rtt = baseline.get("rtt_avg_ms")
        limit = max(10.0, float(baseline_rtt or 0.0) * 3.0)
        recovered = (
            loss == 0.0
            and measurement["rtt_avg_ms"] is not None
            and float(measurement["rtt_avg_ms"]) <= limit
        )
        return recovered, {"max_rtt_avg_ms": round(limit, 3), "max_packet_loss_percent": 0.0}
    if impairment == "corruption":
        baseline_loss = float(baseline.get("packet_loss_percent") or 0.0)
        limit = max(5.0, baseline_loss + 5.0)
        return loss <= limit, {"max_packet_loss_percent": limit}
    raise ValueError(f"unsupported QoS impairment {impairment!r}")


def _print_qos_after_repair(validation: dict, result: dict) -> None:
    # A throughput descriptor names the measured flow and the contention it is
    # measured through; an impairment descriptor names the probed path.
    flow = validation.get("flow")
    path = flow or validation["path"]
    measurement = result["measurement"]
    print()
    print("QoS after repair")
    print("================")
    destination_ip = path.get("destination_ip") or path.get("sink_ip")
    print(f"path: {path['source']} -> {path['destination']} ({destination_ip})")
    if flow:
        contention = validation.get("contention") or {}
        offered = contention.get("offered_mbps")
        print(
            "measured under "
            + (f"{offered} Mbps of offered load" if offered else "the fault's own load")
            + f", assured {validation['assured_bandwidth_mbps']} Mbps"
        )
    else:
        print(f"impairment: {validation['impairment']}")
    _print_qos_measurement("baseline", validation.get("baseline"))
    _print_qos_measurement("impaired", validation.get("impaired"))
    _print_qos_measurement("after repair", measurement)
    print(f"recovery_threshold: {result['recovery_threshold']}")
    print(f"recovered={result['recovered']}")


def _print_qos_measurement(label: str, measurement: dict | None) -> None:
    if measurement is None:
        print(f"{label}: unavailable")
        return
    if "throughput_mbps" in measurement:
        observed = measurement.get("throughput_mbps")
        print(f"{label}: {'unmeasurable' if observed is None else f'{observed} Mbps'}")
        return
    print(
        f"{label}: loss={measurement.get('packet_loss_percent')}%, "
        f"rtt_avg={measurement.get('rtt_avg_ms')}ms"
    )


if __name__ == "__main__":
    raise SystemExit(main())
