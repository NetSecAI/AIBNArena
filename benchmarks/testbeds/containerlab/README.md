# Containerlab testbeds
This directory contains physical Containerlab YAML, reference states, and testbed-only assets. Scenario intent and thresholds do not live here.
Each testbed is selected by its `benchmarks/configs/testbeds/<name>.toml`: `sme01-small`, `sme01-dns`, `sme01-fw`, `sme01-qos` and `sme01-qos-greenfield` (the `sme01-qos` lab with another reference state). `sme01-vlan` has no testbed config yet. Beyond `sme01-small`, the directories materialize focused VLAN, firewall, DNS, and QoS capabilities; each lab has its own name and management subnet, so several can be deployed at once.
```bash
containerlab deploy -t benchmarks/testbeds/containerlab/sme01-small/topology.clab.yml
containerlab destroy -t benchmarks/testbeds/containerlab/sme01-small/topology.clab.yml --cleanup
```
The shared lifecycle destroys and redeploys the testbed at the start of every episode, then applies the reference state. At the end, the experiment's `cleanup` either destroys the lab or restores the reference state and leaves it up. Generated `clab-*` directories are ignored.
