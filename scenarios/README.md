# Scenarios and versioned oracles
The scenario collection contains `connectivity`, `dhcp_dns`, `filtering`, `qos`, `security`, `vlan`, `combined`, `examples`, `oracles`, `topologies`, `schemas`, `compiler`, and `generator`.
`vlan` holds the VLAN membership faults of the campus access switches (an access port in the wrong VLAN, a VLAN pruned from the uplink). `combined` is written by `generator/combine_scenarios.py`: the faults of 2 to 4 catalog scenarios injected together on disjoint blast radii, judged by oracles composed from the components' own; see `generator/README.md`.
Source scenarios declare intent, semantic selectors, fault operations, and explicit healthy, expected-degradation, repair, and preservation oracle references. The compiler binds semantics to an executable topology while keeping private bindings and commands out of A2A requests.
Oracle YAML is validated by `scenarios/schemas/oracle.schema.json`. Every probe declares type, endpoints, protocol, attempts, timeout, metrics, thresholds, aggregation, and success. Only exact `${bindings.name}` placeholders are allowed; arbitrary expressions are never evaluated. Oracles may be shared by many scenarios.
Reference-state provisioning is distinct from all four oracle phases.
```bash
.venv/bin/python -m pytest -q scenarios/tests
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/connectivity-smoke.toml
```
Academic provenance: selected Connectivity families were normalized from ideas described in Y. Zhou et al., *NetArena: Dynamic Benchmarks for AI Agents in Network Automation* (ICLR 2026). The implementation and transport here are independent.
