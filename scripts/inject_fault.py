#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab import ContainerLabEnv, ContainerLabEnvConfig, FaultCatalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inject a controlled fault into the clos01 ContainerLab lab.")
    parser.add_argument(
        "fault_id",
        nargs="?",
        help="Fault id to inject. Use --list to show available faults.",
    )
    parser.add_argument(
        "--catalog",
        default="benchmarks/testbeds/containerlab/faults/mvp.json",
        help="Path to the fault catalog JSON file.",
    )
    parser.add_argument("--list", action="store_true", help="List available faults and exit.")
    parser.add_argument("--verify", action="store_true", help="Run connectivity verification after injection.")
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    catalog = FaultCatalog.from_file(catalog_path)

    if args.list:
        for fault in catalog.faults.values():
            print(f"{fault.id}: {fault.description}")
        return 0

    if not args.fault_id:
        parser.error("fault_id is required unless --list is used")

    fault = catalog.get(args.fault_id)
    config = ContainerLabEnvConfig(fault_catalog_path=catalog_path)
    env = ContainerLabEnv(config)

    print(f"Injecting fault: {fault.id}")
    print(f"Intent: {fault.intent}")
    print(f"Expected failure: {fault.expected_failure}")

    results = env.inject(fault)
    failed = [result for result in results if not result.ok]
    for result in results:
        status = "OK" if result.ok else "FAILED"
        print(f"[{status}] {result.target}: {result.command.splitlines()[0]}")
        if result.stderr.strip():
            print(result.stderr.strip())

    if args.verify:
        observation = env.observe()
        healthy = env.observation_is_healthy(observation)
        print(f"verify={healthy}")
        for check in observation.connectivity:
            status = "OK" if check.success else "FAILED"
            print(
                f"[{status}] connectivity {check.source} -> {check.destination} "
                f"({check.destination_ip}), packet_loss={check.packet_loss_percent}%"
            )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
