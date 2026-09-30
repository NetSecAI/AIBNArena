# Connectivity domain
Connectivity contributes ICMP reachability semantics and path-repair metrics to the shared lifecycle. Scenarios and thresholds live under `scenarios/connectivity` and `scenarios/oracles/connectivity`.
```bash
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/connectivity-smoke.toml
```
There is no domain-specific lifecycle runner.
