# DHCP/DNS domain
Provisioning faults act on the DHCP server's reservation pools: a pool that no longer serves its subnet, a default-router or name-server option pointing at nobody. The client loses the address or the resolver it was provisioned with, which the shared ICMP matrix reads directly. Scenarios and thresholds live under `scenarios/dhcp_dns` and `scenarios/oracles/dhcp_dns`.
```bash
.venv/bin/python benchmarks/run.py --validate-only -c benchmarks/configs/experiments/dhcp_dns-smoke.toml
```
There is no domain-specific lifecycle runner.
