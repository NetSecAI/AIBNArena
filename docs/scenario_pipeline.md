# Scenario and oracle pipeline
1. YAML in `scenarios/<domain>` declares semantic selectors and operations, not device commands.
2. `ScenarioCompiler` binds a source scenario to an executable topology.
3. `benchmarks.configs.loader` selects an instance and materializes its private fault and restoration.
4. Exact scalar oracle placeholders are resolved and schema-validated.
5. `BenchmarkLifecycle` evaluates healthy, expected-degradation, repair, and preservation phases with one generic evaluator.
The public A2A task is built from an allowlist in `scenarios/access.py`; recursive checks reject evaluator-private keys.
```bash
.venv/bin/python -m pytest -q scenarios/tests
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/connectivity-smoke.toml
```
