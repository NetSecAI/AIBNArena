# Filtering domain
Filtering faults act on the zone model itself rather than on an address list: a ruleset unbound from its pair, a deny placed ahead of the permits, an interface that left its zone. Each binds only to a pair the topology declares it accepts and that a required flow actually crosses. Scenarios and thresholds live under `scenarios/filtering` and `scenarios/oracles/filtering`.

The repair oracle is a reachability oracle and is therefore blind to a policy that became **too permissive**; see the gap recorded in `scenarios/oracles/filtering/repair-v1.yaml`.
```bash
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/filtering-smoke.toml
```
There is no domain-specific lifecycle runner.
