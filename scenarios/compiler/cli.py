from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from .compiler import ScenarioCompiler


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile generic IBN scenario YAMLs against an abstract topology YAML.")
    parser.add_argument("--topology", required=True, help="Topology YAML")
    parser.add_argument("--scenarios", default="scenarios", help="Scenario catalog root")
    parser.add_argument("--output", required=True, help="Compiled suite YAML")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--domain", action="append", choices=["connectivity", "dhcp_dns", "filtering", "qos", "security"])
    parser.add_argument("--single-method", action="store_true", help="Choose one scenario method per connectivity scenario instead of expanding all methods")
    parser.add_argument("--security-mode", action="append", choices=["exploit", "detect", "correct", "prevent"])
    parser.add_argument(
        "--samples-per-scenario",
        type=int,
        default=1,
        help="Compile multiple independently bound instances of each scenario definition.",
    )
    args = parser.parse_args()

    compiler = ScenarioCompiler.from_topology_file(args.topology, seed=args.seed)
    suite = compiler.compile_scenarios(
        args.scenarios,
        domains=set(args.domain) if args.domain else None,
        expand_methods=not args.single_method,
        security_modes=set(args.security_mode) if args.security_mode else None,
        samples_per_scenario=args.samples_per_scenario,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(suite, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"compiled {len(suite['tasks'])} tasks -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
