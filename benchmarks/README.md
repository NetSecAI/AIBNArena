# Unified benchmark framework
`benchmarks/run.py` is the only Judge entry point. It constructs the shared `BenchmarkLifecycle` from core components, the Containerlab platform, domain metrics, and one A2A SUT client.
- `core`: contracts, lifecycle, official A2A transport, oracle evaluation, metrics, tracing, provenance, result validation, reporting.
- `domains`: Connectivity, DHCP/DNS, Filtering, QoS, and Security probe and metric semantics only.
- `platforms/containerlab`: lifecycle, execution, observation, injection, safety, topology materialization, ANI.
- `testbeds/containerlab`: physical topology and reference-state assets.
- `configs`: reusable benchmark experiment and testbed TOML; SUT runtime configuration stays with the SUT launcher.
- Results are not written here: a run writes under `reports/manual/results/` by default, a campaign under `reports/campaigns/runs/<campaign>/` (see `reports/README.md`).
```bash
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/connectivity-smoke.toml
.venv/bin/python benchmarks/run.py -c benchmarks/configs/experiments/connectivity-smoke.toml --sut-url http://127.0.0.1:8003
```
Every result validates against `core/result.schema.json` and records the discovered SUT/model parameters, A2A, Git, scenario seed and oracle versions, convergence evidence, hashes, disjoint operation counts, token usage when available, and phase durations.
