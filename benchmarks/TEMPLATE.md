# Adding a benchmark domain

A domain is no longer code with a lifecycle of its own. `benchmarks/run.py` drives
every domain through the same fourteen phases in `benchmarks/core/lifecycle.py`, and
what a domain contributes is a directory of scenarios, a directory of versioned
oracles, and about four lines of Python. The domain is read off the scenario id —
`filtering.zone_policy_enforcement.m1` is domain `filtering` — so there is no
`benchmark = "<name>"` key and no dispatcher to register with.

The steps below are the order the dependencies force; skipping one fails at a
different phase each time.

## 1. Widen the domain vocabulary

Five places name the domains. Miss one and the run dies late.

| File | What it gates |
|---|---|
| `scenarios/compiler/loader.py` | which directories `discover_scenarios` walks |
| `benchmarks/domains/metrics.py` | `DOMAIN_PREFIXES`: admission, and the metric name prefix |
| `scenarios/schemas/oracle.schema.json` | the `domain` enum, and any new probe `type`/`metrics` |
| `scenarios/schemas/scenario.schema.json` | the closed regex on oracle reference paths, and the selector vocabulary |
| `benchmarks/core/result.schema.json` | the `scenario.domain` enum — otherwise `write_result` rejects the artifact |

## 2. `benchmarks/domains/<name>/`

Three small files, mirroring `connectivity`:

```text
benchmarks/domains/<name>/
  __init__.py     # one docstring
  probes.py       # PROBE_TYPES = (...), declarative; nothing imports it
  README.md       # a few lines, ending "There is no domain-specific lifecycle runner."
```

There is no probe protocol for a domain to implement. The only probe contract is on
the platform — `ProbeRunner.run_probe` in `benchmarks/core/contracts.py`, satisfied by
`ContainerlabPlatform`. A new *probe type* is a branch there returning a flat metric
dict, plus entries in the oracle schema's `type` and `metrics` enums.

## 3. `scenarios/oracles/<name>/`

Four files, one per phase, all required:

```text
healthy-v1.yaml  expected-degradation-v1.yaml  repair-v1.yaml  preservation-v1.yaml
```

A domain whose families are judged differently keeps one set per family in its own
subdirectory — `scenarios/oracles/qos/assured-bandwidth/` and `.../shaping-repair/`
do, because the shaped families are measured on a rate and link impairment on loss
and latency. The four filenames stay the same inside each set.

Only exact `${bindings.name}` placeholders are allowed; partial interpolation raises.
Thresholds take an absolute bound (`value`, optionally scaled by `factor`/`offset`), a
baseline-relative one (`baseline: {metric, factor, offset}`), or both with
`combine: min|max` when neither should be exceeded.

## 4. `scenarios/<name>/*.yaml`

Ordinary scenario documents, plus the two keys the oracle regime requires:

```yaml
schema_version: "0.1"
version: "1.0.0"
oracles:
  healthy: {path: scenarios/oracles/<name>/healthy-v1.yaml, version: "1.0.0"}
  expected_degradation: {path: scenarios/oracles/<name>/expected-degradation-v1.yaml, version: "1.0.0"}
  repair: {path: scenarios/oracles/<name>/repair-v1.yaml, version: "1.0.0"}
  preservation: {path: scenarios/oracles/<name>/preservation-v1.yaml, version: "1.0.0"}
```

List the scenario in `scenarios/topology_applicability.json` unless it binds against
every topology: the catalog raises on the first one it cannot bind.

## 5. Compiler support, only if the selectors are new

- `scenarios/compiler/topology.py` — accessors answering "which node, which interface"
- `scenarios/compiler/compiler.py` — the selector dispatch, producing `bindings`
  including `target` and `affected_nodes`
- `benchmarks/platforms/containerlab/compiled_topology.py` — the semantic-operation to
  device-CLI table, and its restore counterpart

## 6. Configuration

Two composable TOMLs. The experiment says what to run:

```toml
experiment_id = "sme01-fw-zone-policy"
scenario_id = "filtering.zone_policy_enforcement.m1"
scenarios_root = "scenarios"
topology_descriptor = "scenarios/topologies/sme_leaf_firewall_dmz.yaml"
testbed_config = "benchmarks/configs/testbeds/sme01-fw.toml"
execution_budget_seconds = 400
result_dir = "reports/manual/results"
report_dir = "reports/manual"
cleanup = "restore"
seed = 1
```

The testbed says where. SUT settings are not in either file — `--sut-url` or
`$IBN_SUT_URL` only.

## 7. Check it

```bash
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/<name>-smoke.toml
```

Then add cases to `benchmarks/configs/tests/test_composition.py` and
`benchmarks/domains/tests/test_golden_scenarios.py`. The golden test is the template
for a domain acceptance test: a fake platform, canned metrics per probe id, a fake
SUT, and a real `BenchmarkLifecycle` run. No Docker.
