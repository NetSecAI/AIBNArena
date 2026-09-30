# SME01-small Containerlab testbed

This directory materializes `scenarios/topologies/sme_leaf_spine_dmz_small.yaml` as a Containerlab topology. It contains two SR Linux spines, two SR Linux leaves, routed endpoint segments, management/DMZ/external hosts, and one attacker endpoint whose data interface starts down.

## Assets

- `topology.clab.yml`: containers, images, management network, and physical links.
- `states/healthy.json`: reviewed idempotent host, interface, and route provisioning.
- `scenarios/topologies/sme_leaf_spine_dmz_small.yaml`: abstract executable nodes, links, addressing, segments, and requirements.

The Containerlab management subnet is out of band and not part of experimental data-plane oracles. Reference-state commands provision the lab; they are distinct from healthy, degradation, repair, and preservation criteria.

## Manual platform preparation

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/sme01-small/topology.clab.yml
./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json \
  --topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --verify --verify-timeout 30
```

The healthy-state script verifies the ten directed flows derived from five bidirectional requirements. A lifecycle run deploys/resets and reapplies this state itself.

## Benchmark use

```bash
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/connectivity-smoke.toml
.venv/bin/python benchmarks/run.py -c benchmarks/configs/experiments/connectivity-smoke.toml
.venv/bin/python benchmarks/run.py -c benchmarks/configs/experiments/qos-smoke.toml
```

Each experiment composes `benchmarks/configs/testbeds/sme01-small.toml`, one versioned scenario/oracle set, and one SUT config. Results are written under `reports/manual/results/`; restoration uses the canonical reference state or destroys the lab according to the experiment cleanup policy.

## Reviewed behavior

The lab has previously exercised interface/routing/address/ACL faults and QoS `tc netem` delay/corruption. Those historical live observations are preserved in `docs/validation/sme_small_restore_workflow_2026-08-11.md`; they do not substitute for a current integration run.
