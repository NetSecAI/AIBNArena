# Containerlab platform
This package implements the shared platform contract and ANI without depending on any agent framework.
- `platform.py`: reset, reference state, injection, cleanup, and generic probes.
- `env.py`: environment facade; `reset()` runs `containerlab destroy` then `containerlab deploy` on the testbed's `.clab.yml`.
- `executor.py`, `observer.py`, `injector.py`, `safety.py`: guarded operations.
- `compiled_topology.py`: semantic fault materialization.
- `state.py`, `fault.py`, `types.py`: reference states, faults and their restore commands, shared value types.
- `dhcp_clients.py`: the topology's DHCP clients, and how to make one ask its server again.
- `throughput.py`: throughput measurement for the shaped WAN-edge QoS scenarios.
- `object_writes.py`: `update_object`, one named object on one node changed through attributes rather than commands.
- `ani.py`: framework-neutral ANI.

Security event and configuration probes require an explicit executable collector. The platform fails when one is absent instead of fabricating a measurement.
Physical assets live in `benchmarks/testbeds/containerlab`; abstract topologies live in `scenarios/topologies`.
