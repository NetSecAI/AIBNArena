#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab import ContainerLabEnv, ContainerLabEnvConfig  # noqa: E402
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology  # noqa: E402


def print_observation_diagnostics(observation, verbose: bool = False) -> bool:
    success = ContainerLabEnv.observation_is_healthy(observation)

    print(f"verify={success}")
    for check in observation.connectivity:
        status = "OK" if check.success else "FAILED"
        print(
            f"[{status}] connectivity {check.source} -> {check.destination} "
            f"({check.destination_ip}), packet_loss={check.packet_loss_percent}%"
        )
        if verbose or not check.success:
            output = check.output.strip() or "<no ping output>"
            print(output)

    if verbose:
        print("\nObservation context:")
        print(observation.to_prompt_context())

    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a healthy reference state to a ContainerLab lab.")
    parser.add_argument(
        "--state",
        default="benchmarks/testbeds/containerlab/states/healthy.json",
        help="Path to a declarative lab state JSON file.",
    )
    parser.add_argument(
        "--topology",
        help="Executable topology descriptor defining the lab, nodes, and checks.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run the adapter connectivity verification after applying the state.",
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for the healthy state to converge when --verify is set.",
    )
    parser.add_argument(
        "--verify-interval",
        type=float,
        default=2.0,
        help="Seconds between verification attempts when --verify is set.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print command output and full observation diagnostics.",
    )
    args = parser.parse_args()

    if args.topology:
        topology = CompiledContainerLabTopology.from_file(args.topology)
        config = topology.env_config(healthy_state_path=Path(args.state))
    else:
        config = ContainerLabEnvConfig(healthy_state_path=Path(args.state))
    env = ContainerLabEnv(config)
    results = env.apply_healthy_state()

    failed = [result for result in results if not result.ok]
    for result in results:
        status = "OK" if result.ok else "FAILED"
        print(f"[{status}] {result.target}: {result.command.splitlines()[0]}")
        if args.verbose and result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())

    verify_ok = True
    if args.verify:
        verify_ok, observation = env.wait_until_healthy(
            timeout_seconds=args.verify_timeout,
            interval_seconds=args.verify_interval,
        )
        print_observation_diagnostics(observation, verbose=args.verbose)

    return 1 if failed or not verify_ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
