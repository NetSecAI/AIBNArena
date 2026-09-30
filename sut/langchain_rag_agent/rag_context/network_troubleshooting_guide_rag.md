---
title: Network Configuration Troubleshooting Guide
version: "2.0"
date: 2026-09
language: en
doc_id: network-config-troubleshooting
doc_type: troubleshooting-runbook
source: Network_Configuration_Troubleshooting_Guide.pdf
audience: [network engineers, IT support, NOC]
platforms: [vendor-neutral]
chunking: >-
  Split on level-3 headings (###). Each ### section is a self-contained chunk that
  starts with an HTML comment holding its metadata (id, type, playbook, topic, osi, related).
  Level-2 headings (##) group chunks and can be kept as parent metadata.
---

# Network Configuration Troubleshooting Guide

## About This Guide

### DOC-00 About the Network Configuration Troubleshooting Guide

<!-- chunk doc="network-config-troubleshooting" id="doc-00-about" type="overview" topic="usage" -->

This guide gives network and support engineers a repeatable, vendor-neutral way to diagnose and fix network configuration errors, from a single misconfigured host to a switched or routed network. It contains:

- a troubleshooting methodology (METHOD-01 to METHOD-03) and safe change practices (SAFE-01);
- a triage checklist and decision trees (TRIAGE-01 to TRIAGE-03);
- twenty-one troubleshooting playbooks (PB-01 to PB-21, indexed in INDEX-01 and INDEX-02);
- an incident record template and escalation criteria (DOC-01, DOC-02), and a glossary.

Each playbook is split into three parts: symptoms and likely causes, diagnostic process, and resolution and verification.

Conventions:

- The guide describes processes, not commands. Each step says what to check, where, and how to read the result. Carry it out with the command or management interface of your own platform.
- Addresses, interface names and values in examples are illustrative.

### INDEX-01 Playbook Index: Which Playbook to Use for Each Network Configuration Error

<!-- chunk doc="network-config-troubleshooting" id="index-01" type="index" topic="routing-to-playbooks" -->

Use this index to pick the playbook that matches the symptoms. When the cause is unclear, start with the quick triage decision tree (TRIAGE-02).

- **PB-01 IP Addressing & Subnet Mask Errors** (OSI L3; Hosts, servers, router interfaces): Wrong addresses or masks are among the most common configuration errors. Typical symptom: host reaches some devices on its subnet but not others.
- **PB-02 Default Gateway Misconfiguration** (OSI L3; Hosts, L3 switches, routers, FHRP): With a wrong gateway, the local subnet keeps working while everything off-subnet fails. Typical symptom: local hosts are reachable, but nothing off-subnet is.
- **PB-03 DNS Resolution Failures** (OSI L7; Clients, resolvers, DNS servers): If a destination answers by IP address but not by name, the network path is fine and the problem is name resolution. Typical symptom: a destination answers by IP address but not by name.
- **PB-04 DHCP Failures** (OSI L2-L3; Clients, access switches, relays, DHCP servers): DHCP follows the DORA exchange: Discover, Offer, Request, Acknowledge. Typical symptom: clients have no IPv4 address, or a self-assigned 169.254.x.x address.
- **PB-05 VLAN & Trunk Misconfiguration** (OSI L2; Access and distribution switches): VLAN errors isolate devices silently: the link is up, but frames land in the wrong broadcast domain or are never carried across a trunk. Typical symptom: device cannot reach its gateway, or gets an address from another subnet.
- **PB-06 Speed, Duplex & Physical-Layer Settings** (OSI L1-L2; NICs, switch ports, cabling, transceivers): Physical settings are configuration too. Typical symptom: link is up but throughput is poor and degrades under load.
- **PB-07 MTU Mismatch & Fragmentation** (OSI L2-L3; Tunnels, WAN links, firewalls, jumbo-frame segments): MTU problems let small packets through and drop large ones: pings and handshakes succeed, but transfers stall. Typical symptom: ping and TCP handshakes work, but downloads, file shares or some HTTPS pages hang.
- **PB-08 Routing Errors (Static, OSPF, BGP)** (OSI L3; Routers, L3 switches, firewalls): Routing errors affect specific destinations rather than everything. Typical symptom: specific remote subnets are unreachable while others work.
- **PB-09 ACL & Firewall Rule Errors** (OSI L3-L4; Router ACLs, firewalls, host firewalls): Filtering errors usually block a specific protocol, port or source. Typical symptom: a specific service fails while ping works, or the reverse.
- **PB-10 NAT Misconfiguration** (OSI L3; Edge routers, firewalls): NAT mistakes typically appear as “the router reaches the internet but the clients do not”, or as port forwards that never trigger. Typical symptom: the edge router reaches the internet; internal hosts do not.
- **PB-11 Spanning Tree Issues** (OSI L2; Switched network): Spanning Tree errors range from a single port shut down by a protection feature to a network-wide broadcast storm. Typical symptom: network-wide slowness or outage; very high switch CPU; activity LEDs blinking in unison.
- **PB-12 IPv6 Configuration Errors** (OSI L3; Hosts, routers, first-hop security): IPv6 depends on ICMPv6 and Router Advertisements for basic operation. Typical symptom: host has only a link-local (fe80::) address.

### INDEX-02 Playbook Index: Faults on Routers, Firewalls and Hosts (PB-13 to PB-21)

<!-- chunk doc="network-config-troubleshooting" id="index-02" type="index" topic="routing-to-playbooks" -->

These playbooks go further on configuration faults that break some paths while the rest of the network keeps working, and on performance and bandwidth guarantees. When IPv4 connectivity between endpoints is lost and the cause is unclear, start with TRIAGE-03.

- **PB-13 Interface Down or Unusable MTU**: every path through one interface fails.
- **PB-14 Routing Disabled on a Router or Host**: the local subnet works and nothing beyond it does.
- **PB-15 Traffic Dropped by an ACL or Firewall Filter**: one subnet, one direction or one protocol is blocked through a router.
- **PB-16 Missing, Wrong or Duplicate Interface Addresses**: a segment or a single host cannot reach its gateway.
- **PB-17 Static Route and Next-Hop Errors**: one remote subnet or host is unreachable, or traffic loops.
- **PB-18 DHCP Scope Errors on a Multi-Subnet Server**: the clients of one segment get no lease, or a wrong gateway or resolver.
- **PB-19 Zone-Based Firewall Policy Errors**: traffic the policy allows between two zones is blocked.
- **PB-20 Link Impairment: Added Delay, Loss or Corruption**: one application path is slow, or loses or corrupts packets.
- **PB-21 Traffic Shaping and Bandwidth Guarantees**: a protected traffic class misses its guaranteed bandwidth under contention.

## Troubleshooting Methodology

### METHOD-01 Seven-Step Network Troubleshooting Process

<!-- chunk doc="network-config-troubleshooting" id="method-01" type="methodology" topic="process" -->

A structured method avoids guesswork, shortens resolution time and makes results reproducible. Apply these seven steps to every network incident, whatever its size.

1. **Define the problem**. Goal: a precise, testable statement. Key actions: Who, what, where, since when; exact error message; what works and what does not; reproduce it.
2. **Gather information**. Goal: facts, not assumptions. Key actions: Configurations, logs, monitoring, recent changes, topology, packet captures.
3. **Form hypotheses**. Goal: a ranked list of likely causes. Key actions: Start with recent changes and the most common causes; use the OSI model to narrow the scope.
4. **Test**. Goal: confirm or eliminate one cause at a time. Key actions: Non-disruptive checks first; change one variable at a time; record each result.
5. **Implement the fix**. Goal: resolve with minimal risk. Key actions: Backup, rollback plan, change window when needed, peer review for critical devices.
6. **Verify**. Goal: prove the service is restored. Key actions: Test from the user's point of view, check for side effects, monitor for recurrence.
7. **Document**. Goal: keep the knowledge. Key actions: Root cause, fix, prevention; update diagrams, IPAM and runbooks.

### METHOD-02 Choosing a Network Troubleshooting Approach (Bottom-Up, Top-Down, Divide and Conquer)

<!-- chunk doc="network-config-troubleshooting" id="method-02" type="methodology" topic="approaches" -->

Pick the approach that fits the situation; divide and conquer is the default for most cases.

- **Bottom-up**. Use when: cause unknown, or physical changes suspected. How: Start at Layer 1 and move up the OSI model.
- **Top-down**. Use when: one application fails while others work. How: Start at the application and move down.
- **Divide and conquer**. Use when: default choice for most cases. How: Start at Layer 3 with a ping: move up if it succeeds, down if it fails.
- **Follow the path**. Use when: multi-hop or site-to-site issues. How: Trace the packet hop by hop, checking each device's forwarding decision.
- **Compare with known-good**. Use when: a similar working device or a baseline exists. How: Diff configurations, software versions and counters.
- **Swap components**. Use when: hardware or cabling suspected. How: Replace cable, port, NIC or transceiver to isolate the fault.

### METHOD-03 Mapping Network Configuration Errors to the OSI Model

<!-- chunk doc="network-config-troubleshooting" id="method-03" type="methodology" topic="osi-model" -->

Common configuration errors, first checks and matching playbooks for each OSI layer.

- **Layer 1 · Physical**. Common configuration errors: forced speed/duplex, wrong transceiver, port shut down. First checks: link LED, interface status, error counters. Playbooks: PB-06 Speed, Duplex & Physical-Layer Settings.
- **Layer 2 · Data link**. Common configuration errors: wrong VLAN, trunk allowed list, native VLAN, STP, port security. First checks: VLAN and trunk tables, MAC table. Playbooks: PB-05 VLAN & Trunk Misconfiguration; PB-11 Spanning Tree Issues.
- **Layer 3 · Network**. Common configuration errors: IP/mask, gateway, routing, NAT, MTU, IPv6 RA. First checks: address config, ping, traceroute, routing table. Playbooks: PB-01 IP Addressing & Subnet Mask Errors; PB-02 Default Gateway Misconfiguration; PB-07 MTU Mismatch & Fragmentation; PB-08 Routing Errors (Static, OSPF, BGP); PB-10 NAT Misconfiguration; PB-12 IPv6 Configuration Errors.
- **Layer 4 · Transport**. Common configuration errors: port filtering, firewall rules, service not listening. First checks: port tests, listening sockets, ACL hit counters. Playbooks: PB-09 ACL & Firewall Rule Errors.
- **Layer 5–7 · Application**. Common configuration errors: DNS, DHCP options, proxy settings. First checks: name lookups against the resolver, lease details. Playbooks: PB-03 DNS Resolution Failures; PB-04 DHCP Failures.

## Safe Change Practices

### SAFE-01 Safe Change Practices for Network Configuration Changes

<!-- chunk doc="network-config-troubleshooting" id="safe-01" type="best-practice" topic="change-management" -->

Troubleshooting often means changing configuration on production equipment. These rules reduce the risk of turning an incident into an outage.

1. **Back up before you touch anything.** Keep a copy of the running configuration, or make sure the platform keeps rollback versions.
2. **Protect your access.** When a change could cut your management session, use a change that reverts on its own unless it is confirmed (timed rollback, confirmed commit), and keep an out-of-band path available.
3. **Change one thing at a time.** Change a single variable, test, then decide. Several simultaneous changes hide the real cause and can create new faults.
4. **Check recent changes first.** A large share of incidents follows a change. Compare the running configuration with the last known-good version.
5. **Know the rollback before the change.** For every change, prepare the exact change that undoes it.
6. **Follow change management.** Record the change, obtain approval when required, and use maintenance windows for disruptive actions.
7. **Save only after verification.** Make the change permanent once the fix is verified, not before.
8. **Handle diagnostics with care.** Restrict debugging and packet captures to the traffic under study: unfiltered debugging can overload a production device.

## Quick Triage

### TRIAGE-01 First-Five-Minutes Network Triage Checklist

<!-- chunk doc="network-config-troubleshooting" id="triage-01" type="checklist" topic="triage" -->

Use the first five minutes of an incident to frame the problem before touching any configuration.

- **What exactly fails, and what error is shown?** Turns “the network is down” into a testable statement.
- **Who is affected: one user, one VLAN, one site, everyone?** Scope points to the layer and the device: host, access switch, core, WAN or provider.
- **When did it start? Did anything change?** Recent changes are the most likely cause, and they offer an immediate rollback option.
- **What still works?** IP works but not names → DNS. Local works but not remote → gateway or routing. Ping works but not the application → ACL, port or MTU.
- **Is it constant or intermittent?** Intermittent faults suggest duplex, MTU, STP, flapping links or address conflicts.
- **Can you reproduce it on demand?** A reproducible test validates each hypothesis and, later, the fix.
- **What do monitoring and logs show?** Correlated alarms (interface down, neighbor lost) often point straight at the cause.

### TRIAGE-02 Quick Triage Decision Tree: From Symptom to Playbook

<!-- chunk doc="network-config-troubleshooting" id="triage-02" type="decision-tree" topic="triage" -->

Work through the questions in order. The first failing check points to the playbooks to open; a passing check means that layer is healthy, so move to the next question.

1. **Is the physical link up?** (check: link up on both ends, port not shut down). If no: open PB-06, PB-13, or PB-11 when a protection feature shut the port. If yes: go to question 2.
2. **Does the host have a valid IP configuration?** (check: an address in the right subnet with the right prefix length, not a self-assigned 169.254.x.x address; a global IPv6 address where IPv6 is used). If no: open PB-04, PB-18, PB-01, PB-16, PB-12. If yes: go to question 3.
3. **Can the host reach its default gateway?** (check: the ARP entry for the gateway resolves). If no: open PB-02, PB-05, PB-16. If yes: go to question 4.
4. **Can the host reach a remote IP address?** (check: reachability test or path trace to a known-good remote address). If no: open PB-08, PB-14, PB-17, PB-10, PB-09, PB-15, PB-19. If yes: go to question 5.
5. **Do names resolve to the right addresses?** (check: a lookup against the configured resolver). If no: open PB-03, PB-18. If yes: go to question 6.
6. **Does the service port respond?** (check: a connection test to the port). If no: open PB-09, PB-15, or PB-07 when connections hang on large data. If yes: go to question 7.
7. **Is the problem intermittent or slow?** (check: errors, drops, flapping, latency, throughput under load). If yes: open PB-06, PB-07, PB-11, PB-20, PB-21, or PB-01 for duplicate addresses. If no: the problem is resolved; verify with the user and document it (DOC-01).

### TRIAGE-03 Restoring IPv4 Connectivity Between Endpoints: Where to Look First

<!-- chunk doc="network-config-troubleshooting" id="triage-03" type="decision-tree" topic="triage-connectivity" related="PB-13,PB-14,PB-15,PB-16,PB-17,PB-19" -->

Use this sequence when the IPv4 connectivity between affected endpoints must be restored without disrupting the healthy paths.

1. **Measure first.** Validate and read the pattern of the failing pairs:
   - every pair of one host: that host or its access link;
   - every host behind one gateway: that gateway;
   - one destination subnet from several sources: a route or a filter;
   - only ICMP: a protocol filter.
2. **Failing host**: the interface is up with its segment's MTU (PB-13); the address and prefix length fit the segment (PB-16); one default route points to the segment gateway, not a reject or discard route (PB-14, PB-17); the ARP entry for the gateway resolves.
3. **Gateway (router or firewall)**: the port and the subinterface or VLAN interface are enabled (PB-13); the gateway address is present (PB-16); routing and forwarding are enabled (PB-14); no ACL or filter drops the traffic (PB-15).
4. **Routers on the path**: an active route toward the destination and back, through the expected next hop (PB-17).
5. **Firewall between zones**: the zone pair binding, the rule order and the interface zone membership (PB-19).

Change only what the evidence implicates, then validate again: the failing pairs pass and the others are unchanged.

## Playbook PB-01: IP Addressing & Subnet Mask Errors

### PB-01 IP Addressing & Subnet Mask Errors: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-01-overview" type="playbook-symptoms-causes" playbook="PB-01" topic="ip-addressing" osi="L3" related="PB-02,PB-04,PB-12" -->

Troubleshooting playbook PB-01 IP Addressing & Subnet Mask Errors. Scope: Layer 3 · Hosts, servers, router interfaces.

Wrong addresses or masks are among the most common configuration errors. They often produce partial connectivity, which makes them easy to misdiagnose as routing or firewall problems.

**Symptoms:**

- Host reaches some devices on its subnet but not others.
- “IP address conflict” warnings, or two hosts that lose connectivity alternately.
- Host cannot reach anything even though the link is up.
- Remote subnets that overlap the local range are unreachable (common with VPNs).

**Likely causes:**

- Subnet mask too wide or too narrow for the actual segment.
- Duplicate address: a static IP set inside a DHCP scope, or a cloned VM.
- Typo in a static address, or an address belonging to another VLAN.
- Local subnet overlaps a remote or VPN subnet.

**Typical questions:** Why can a host reach some devices on its subnet but not others? How do I find which device is using a duplicate IP address? What happens when the subnet mask is too wide or too narrow?

**Keywords:** IP address, subnet mask, prefix length, duplicate IP, IP address conflict, static IP, IPAM, ARP, duplicate address detection, overlapping subnet, partial connectivity

**Related playbooks:** PB-02 Default Gateway Misconfiguration; PB-04 DHCP Failures; PB-12 IPv6 Configuration Errors.

### PB-01 IP Addressing & Subnet Mask Errors: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-01-diagnosis" type="playbook-diagnosis" playbook="PB-01" topic="ip-addressing" osi="L3" related="PB-02,PB-04,PB-12" -->

Step-by-step diagnosis for playbook PB-01 IP Addressing & Subnet Mask Errors. Follow the steps in order.

1. Read the host's complete IP configuration (address, prefix length, gateway, resolvers) and compare it with the IPAM or design record for that segment. Do the same on the router interface that serves the segment.
2. Calculate the network and broadcast addresses from the address and mask. Confirm that the gateway and the hosts the user needs fall inside that range. A mask that is **too wide** makes the host ARP for remote addresses instead of sending them to the gateway; a mask that is **too narrow** sends local traffic to the gateway.
3. Test for a duplicate address with a duplicate-address probe: ARP requests that do not claim the address. Any reply means the address is already in use. Find the second device from its MAC address in the router's ARP table, then its port in the switches' MAC tables.
4. Check whether static addresses fall inside a DHCP pool without an exclusion or reservation, and read the DHCP server's conflict records.
5. Inspect the host's ARP (neighbor) table: entries stuck in INCOMPLETE or FAILED for addresses that should be remote are a strong sign of a mask error.

### PB-01 IP Addressing & Subnet Mask Errors: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-01-resolution" type="playbook-resolution" playbook="PB-01" topic="ip-addressing" osi="L3" related="PB-02,PB-04,PB-12" -->

How to fix and verify playbook PB-01 IP Addressing & Subnet Mask Errors once the diagnostic process has identified the cause.

**Resolution:**

- Correct the address or prefix length to match the design, and update IPAM.
- Move static devices outside the dynamic range, or add DHCP exclusions / reservations.
- Reconfigure or remove the duplicate device located through the MAC table.
- For overlapping subnets, re-address one side or apply NAT on the VPN.

**Verification:**

- Ping the gateway and hosts at both ends of the subnet range.
- The ARP entry for the address shows a single, expected MAC.
- No new conflict events in host or DHCP server logs.

> **Tip:** DHCP snooping combined with Dynamic ARP Inspection and IP Source Guard prevents most address conflicts and spoofing at the access layer.

## Playbook PB-02: Default Gateway Misconfiguration

### PB-02 Default Gateway Misconfiguration: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-02-overview" type="playbook-symptoms-causes" playbook="PB-02" topic="default-gateway" osi="L3" related="PB-01,PB-05,PB-08" -->

Troubleshooting playbook PB-02 Default Gateway Misconfiguration. Scope: Layer 3 · Hosts, L3 switches, routers, FHRP.

With a wrong gateway, the local subnet keeps working while everything off-subnet fails. The error can sit on the host, in DHCP option 3, or on the router side.

**Symptoms:**

- Local hosts are reachable, but nothing off-subnet is.
- Traceroute shows no hops, or times out at the first hop.
- Only one of two redundant routers seems to work.
- Connectivity changes when a VPN connects or a second NIC is enabled.

**Likely causes:**

- Missing default gateway, or a typo in it.
- Gateway outside the host subnet (often combined with a wrong mask).
- DHCP option 3 (router) pointing to an old or wrong address.
- Router or VLAN interface down, disabled, or configured with another IP.
- The first-hop redundancy (FHRP) virtual IP differs from the gateway handed to clients.
- Several default routes with competing metrics (multi-homed host, VPN).

**Typical questions:** Why does the local subnet work but nothing off-subnet? How do I check which default gateway a host really uses? Why does traceroute stop at the first hop?

**Keywords:** default gateway, default route, 0.0.0.0/0, DHCP option 3, VLAN interface, VRRP, FHRP, route metric, first hop, multi-homed host

**Related playbooks:** PB-01 IP Addressing & Subnet Mask Errors; PB-05 VLAN & Trunk Misconfiguration; PB-08 Routing Errors (Static, OSPF, BGP).

### PB-02 Default Gateway Misconfiguration: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-02-diagnosis" type="playbook-diagnosis" playbook="PB-02" topic="default-gateway" osi="L3" related="PB-01,PB-05,PB-08" -->

Step-by-step diagnosis for playbook PB-02 Default Gateway Misconfiguration. Follow the steps in order.

1. Read the host's default route, or routes, with their metrics.
2. Test reachability of the gateway, then read the host's ARP entry for it. A missing or incomplete entry indicates a Layer 2 problem (see PB-05), or a gateway address that no router owns (PB-16). A valid entry with no reply points to the router itself or its filters.
3. On the router, confirm that the gateway interface is up with the expected address. Check the first-hop redundancy state: which router is active, and which virtual address it serves.
4. If the host has several default routes, identify which one wins (lowest metric) and whether it should exist at all.

### PB-02 Default Gateway Misconfiguration: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-02-resolution" type="playbook-resolution" playbook="PB-02" topic="default-gateway" osi="L3" related="PB-01,PB-05,PB-08" -->

How to fix and verify playbook PB-02 Default Gateway Misconfiguration once the diagnostic process has identified the cause.

**Resolution:**

- Set the correct gateway statically, or fix DHCP option 3 in the scope.
- Enable the router or VLAN interface and correct its address.
- Align the FHRP virtual IP with the gateway distributed to clients.
- Remove stale or duplicate default routes; adjust interface metrics.

**Verification:**

- Traceroute to a remote address shows the gateway as the first hop.
- Remote IPs respond, and failover to the standby router works when FHRP is used.

## Playbook PB-03: DNS Resolution Failures

### PB-03 DNS Resolution Failures: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-03-overview" type="playbook-symptoms-causes" playbook="PB-03" topic="dns" osi="L7" related="PB-04,PB-09" -->

Troubleshooting playbook PB-03 DNS Resolution Failures. Scope: Application services · Clients, resolvers, DNS servers.

If a destination answers by IP address but not by name, the network path is fine and the problem is name resolution. Always separate the two before going further.

**Symptoms:**

- A destination answers by IP address but not by name.
- “Server not found” / NXDOMAIN, or a name resolves to the wrong IP.
- Internal names fail while public names work, or the reverse.
- Long delay before each new connection starts.
- Short names fail but fully qualified names work.

**Likely causes:**

- Wrong DNS servers configured statically or via DHCP option 6.
- Resolver unreachable: UDP/TCP 53 blocked, or server down.
- Missing or incorrect DNS suffix search list.
- Stale cache, or an old entry in the hosts file.
- Missing, outdated or mistyped record on the authoritative server.
- Split-horizon mismatch, faulty conditional forwarder, or VPN overriding resolvers.

**Typical questions:** Ping works by IP address but not by name: why? When does a stale DNS cache need to be cleared? Why do internal names fail while public names resolve? Why do short names fail when fully qualified names work?

**Keywords:** DNS, name resolution, name lookup, NXDOMAIN, resolver, DNS suffix, search domain, hosts file, DNS cache, split-horizon, conditional forwarder, TCP 53, UDP 53, DHCP option 6

**Related playbooks:** PB-04 DHCP Failures; PB-09 ACL & Firewall Rule Errors.

### PB-03 DNS Resolution Failures: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-03-diagnosis" type="playbook-diagnosis" playbook="PB-03" topic="dns" osi="L7" related="PB-04,PB-09" -->

Step-by-step diagnosis for playbook PB-03 DNS Resolution Failures. Follow the steps in order.

1. Confirm the split: test the same destination by IP address and by name. If the IP test also fails, the problem is not DNS: go back to the quick triage decision tree (TRIAGE-02).
2. Identify the resolvers the client actually uses. They can differ per interface, or when a VPN is active.
3. Query the configured resolver directly, then a second or known-good resolver, and compare the answers.
4. Check that the resolver is reachable on port 53, over UDP and TCP. A timeout, rather than an error answer, usually means filtering or a dead server.
5. Inspect the local overrides: the hosts file and the local cache. Clear the cache and test again.
6. Query the authoritative server to rule out propagation or TTL issues, and check whether short names need a search suffix.

### PB-03 DNS Resolution Failures: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-03-resolution" type="playbook-resolution" playbook="PB-03" topic="dns" osi="L7" related="PB-04,PB-09" -->

How to fix and verify playbook PB-03 DNS Resolution Failures once the diagnostic process has identified the cause.

**Resolution:**

- Correct the resolver list (static configuration or DHCP option 6).
- Fix the suffix search list (DHCP option 15/119, or the host's resolver settings).
- Permit UDP **and** TCP 53 between clients and resolvers, and from resolvers to forwarders.
- Create or correct the record; fix conditional forwarders or split-horizon zones.
- Remove stale hosts-file entries and flush caches on clients and resolvers.

**Verification:**

- The name resolves to the expected address from several clients and from every resolver.
- Connection by name succeeds immediately after a cache flush.

> **Tip:** TCP 53 is not optional. Responses too large for UDP (DNSSEC, many records) fall back to TCP; blocking it causes intermittent, size-dependent failures.

## Playbook PB-04: DHCP Failures

### PB-04 DHCP Failures: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-04-overview" type="playbook-symptoms-causes" playbook="PB-04" topic="dhcp" osi="L2-L3" related="PB-01,PB-05,PB-03" -->

Troubleshooting playbook PB-04 DHCP Failures. Scope: Layers 2–3 · Clients, access switches, relays, DHCP servers.

DHCP follows the DORA exchange: Discover, Offer, Request, Acknowledge. Finding which message goes missing tells you where the fault is.

**Symptoms:**

- Clients have no IPv4 address, or a self-assigned link-local address (169.254.x.x).
- Some clients get leases while others on the same VLAN do not.
- Clients receive an unexpected subnet, gateway or DNS server.
- Leases work in existing VLANs but not in a newly created one.

**Likely causes:**

- Scope exhausted (too small, long leases, stale reservations).
- Missing or wrong DHCP relay (helper) address on the client VLAN interface.
- DHCP snooping dropping offers because the uplink is not trusted.
- Server or relay unreachable (routing, ACL, UDP 67/68 blocked).
- Rogue DHCP server (home router, test VM) answering first.
- Client port in the wrong VLAN, or blocked by port security / 802.1X.

**Typical questions:** Why does my PC get a 169.254.x.x address? Why do clients in a new VLAN get no DHCP lease? How do I detect a rogue DHCP server? How do I read a DHCP packet capture?

**Keywords:** DHCP, DORA, link-local address, 169.254, DHCP relay, scope exhausted, DHCP snooping, rogue DHCP server, lease, UDP 67, UDP 68, DHCPNAK, no IP address

**Related playbooks:** PB-01 IP Addressing & Subnet Mask Errors; PB-05 VLAN & Trunk Misconfiguration; PB-03 DNS Resolution Failures.

### PB-04 DHCP Failures: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-04-diagnosis" type="playbook-diagnosis" playbook="PB-04" topic="dhcp" osi="L2-L3" related="PB-01,PB-05,PB-03" -->

Step-by-step diagnosis for playbook PB-04 DHCP Failures. Follow the steps in order.

1. Force a lease renewal on the client and watch the result.
2. Capture the DORA exchange on the client, or on a mirrored port, and note where it stops.
3. Interpret the capture with the table below, then check the corresponding component.

   | Observation | Meaning | Check next |
   |---|---|---|
   | No Discover leaves the client | Client-side problem | DHCP client, NIC, port status |
   | Discover sent, no Offer | Request not reaching the server, or no free address | Relay address, route to the server, pool utilization |
   | Offer seen upstream but not at the client | DHCP snooping drop | Trust state of uplinks, snooping statistics |
   | Offer from an unexpected server | Rogue DHCP server | Locate its MAC in the switch table, shut the port |
   | NAK after the Request | Address not valid on this subnet | VLAN assignment, scope range, relay configuration |

4. On the relay (the client VLAN interface) and on the DHCP server, check the relay address, the pool for the segment, the active bindings and the snooping state.

### PB-04 DHCP Failures: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-04-resolution" type="playbook-resolution" playbook="PB-04" topic="dhcp" osi="L2-L3" related="PB-01,PB-05,PB-03" -->

How to fix and verify playbook PB-04 DHCP Failures once the diagnostic process has identified the cause.

**Resolution:**

- Enlarge the scope, shorten the lease time, or clean up stale reservations.
- Configure the DHCP relay address of the server on every client VLAN interface.
- Mark the uplinks toward the server and the relay ports as trusted for DHCP snooping.
- Shut down the port of a rogue server, and keep snooping enabled to block it.
- Correct the VLAN assignment or authentication policy on the client port.

**Verification:**

- The client receives a lease with correct address, mask, gateway, DNS and domain.
- The lease appears in the server binding table and in the snooping binding table.

## Playbook PB-05: VLAN & Trunk Misconfiguration

### PB-05 VLAN & Trunk Misconfiguration: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-05-overview" type="playbook-symptoms-causes" playbook="PB-05" topic="vlan-trunk" osi="L2" related="PB-02,PB-04,PB-11" -->

Troubleshooting playbook PB-05 VLAN & Trunk Misconfiguration. Scope: Layer 2 · Access and distribution switches.

VLAN errors isolate devices silently: the link is up, but frames land in the wrong broadcast domain or are never carried across a trunk.

**Symptoms:**

- Device cannot reach its gateway, or gets an address from another subnet.
- One VLAN fails between two switches while others work.
- Native VLAN mismatch messages in the logs.
- Port is up but the VLAN is inactive or not forwarding on the trunk.
- IP phones work but the PC behind them does not, or the reverse.

**Likely causes:**

- Access port assigned to the wrong VLAN.
- VLAN not created on one of the switches in the path.
- VLAN missing from the trunk allowed list, or pruned.
- Native VLAN mismatch between trunk ends.
- One side configured as access, the other as trunk (negotiation disabled).
- Voice VLAN missing, or swapped with the data VLAN.

**Typical questions:** Why does one VLAN not pass between two switches while others do? How do I fix a native VLAN mismatch? Why can redefining a trunk's allowed VLAN list cut the other VLANs? Why does a device get an address from the wrong subnet?

**Keywords:** VLAN, trunk, access port, native VLAN mismatch, allowed VLAN list, trunk negotiation, VLAN pruning, voice VLAN, MAC address table, 802.1Q

**Related playbooks:** PB-02 Default Gateway Misconfiguration; PB-04 DHCP Failures; PB-11 Spanning Tree Issues.

### PB-05 VLAN & Trunk Misconfiguration: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-05-diagnosis" type="playbook-diagnosis" playbook="PB-05" topic="vlan-trunk" osi="L2" related="PB-02,PB-04,PB-11" -->

Step-by-step diagnosis for playbook PB-05 VLAN & Trunk Misconfiguration. Follow the steps in order.

1. Check the VLAN assignment and the operational mode (access or trunk) of the host's port.
2. Confirm that the host's MAC address is learned in the expected VLAN on the access switch.
3. For each trunk on the path, check that the VLAN is allowed, active, and forwarding (not pruned or blocked by Spanning Tree).
4. Compare the mode and the native VLAN on both ends of each trunk, and look for mismatch messages in the logs.
5. Follow the MAC address switch by switch toward the gateway, with the MAC tables and the neighbor discovery information (LLDP), to find where it stops being learned.

### PB-05 VLAN & Trunk Misconfiguration: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-05-resolution" type="playbook-resolution" playbook="PB-05" topic="vlan-trunk" osi="L2" related="PB-02,PB-04,PB-11" -->

How to fix and verify playbook PB-05 VLAN & Trunk Misconfiguration once the diagnostic process has identified the cause.

**Resolution:**

- Assign the correct access VLAN to the port.
- Create the missing VLAN on every switch that must carry it.
- Add the VLAN to the allowed list of every trunk on the path.
- Match the native VLAN, and set the trunk mode explicitly on both ends.
- Configure the voice VLAN on the ports that carry phones.

**Verification:**

- The MAC is learned in the correct VLAN on every switch toward the gateway.
- The client gets an address in the right subnet and reaches its gateway.

> **Warning:** On many platforms, setting a trunk's allowed VLAN list **replaces** it and cuts every VLAN left out. Add to the list when extending a trunk; do not redefine it.

## Playbook PB-06: Speed, Duplex & Physical-Layer Settings

### PB-06 Speed, Duplex & Physical-Layer Settings: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-06-overview" type="playbook-symptoms-causes" playbook="PB-06" topic="physical-duplex" osi="L1-L2" related="PB-11,PB-07" -->

Troubleshooting playbook PB-06 Speed, Duplex & Physical-Layer Settings. Scope: Layers 1–2 · NICs, switch ports, cabling, transceivers.

Physical settings are configuration too. A duplex mismatch keeps the link up and lets pings through, so it often goes unnoticed until the link is under load.

**Symptoms:**

- Link is up but throughput is poor and degrades under load.
- Error counters rising: CRC/FCS, runts, late collisions.
- Link flaps (repeated up/down messages in the log).
- Port shut down by a protection feature, or administratively down.

**Likely causes:**

- Duplex mismatch: one side hard-coded, the other on auto-negotiation (falls back to half duplex).
- Speed forced to a value the other end or the cable does not support.
- Damaged cable, dirty fiber, or incompatible transceiver.
- Port shut down automatically by port security, BPDU guard or link-flap detection.
- Interface left administratively disabled.

**Typical questions:** The link is up but slow: what should I check? What are the signs of a duplex mismatch? How do I recover a port that a protection feature shut down?

**Keywords:** speed, duplex mismatch, auto-negotiation, half duplex, CRC errors, FCS errors, runts, late collisions, error-disabled port, link flapping, transceiver, SFP, cabling, throughput test, slow link

**Related playbooks:** PB-11 Spanning Tree Issues; PB-07 MTU Mismatch & Fragmentation.

### PB-06 Speed, Duplex & Physical-Layer Settings: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-06-diagnosis" type="playbook-diagnosis" playbook="PB-06" topic="physical-duplex" osi="L1-L2" related="PB-11,PB-07" -->

Step-by-step diagnosis for playbook PB-06 Speed, Duplex & Physical-Layer Settings. Follow the steps in order.

1. Read speed, duplex and error counters on **both** ends of the link.
2. Interpret the error pattern. Late collisions on one end and CRC errors or runts on the other are the classic signature of a duplex mismatch. CRC errors on both ends suggest cabling or optics.
3. Look for ports shut down by a protection feature, and for the reason and the log messages that triggered it.
4. For fiber links, check the optical levels and the transceiver type against what the port supports.
5. Clear the counters, generate load with a throughput test, and watch whether errors keep rising.

### PB-06 Speed, Duplex & Physical-Layer Settings: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-06-resolution" type="playbook-resolution" playbook="PB-06" topic="physical-duplex" osi="L1-L2" related="PB-11,PB-07" -->

How to fix and verify playbook PB-06 Speed, Duplex & Physical-Layer Settings once the diagnostic process has identified the cause.

**Resolution:**

- Set both ends to auto-negotiation (preferred), or hard-code identical speed and duplex on both ends.
- Replace the cable, clean fiber connectors, or use a supported transceiver.
- Fix the cause that shut the port down, then disable and re-enable the port.

**Verification:**

- Both ends report the same speed and full duplex.
- Error counters stay flat under load after clearing.
- A throughput test reaches the expected rate.

## Playbook PB-07: MTU Mismatch & Fragmentation

### PB-07 MTU Mismatch & Fragmentation: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-07-overview" type="playbook-symptoms-causes" playbook="PB-07" topic="mtu" osi="L2-L3" related="PB-08,PB-09,PB-12" -->

Troubleshooting playbook PB-07 MTU Mismatch & Fragmentation. Scope: Layers 2–3 · Tunnels, WAN links, firewalls, jumbo-frame segments.

MTU problems let small packets through and drop large ones: pings and handshakes succeed, but transfers stall. They are especially frequent on VPNs and other tunnels.

**Symptoms:**

- Ping and TCP handshakes work, but downloads, file shares or some HTTPS pages hang.
- SSH connects, then freezes when a command produces a lot of output.
- Problems appear only through a VPN, GRE/IPsec tunnel, PPPoE or VXLAN.
- OSPF neighbors stuck in EXSTART/EXCHANGE.

**Likely causes:**

- Different MTU on the two ends of a link.
- Tunnel encapsulation overhead not accounted for.
- ICMP “fragmentation needed” / ICMPv6 “packet too big” blocked, breaking Path MTU Discovery (black hole).
- Jumbo frames enabled on only part of the path.

**Typical questions:** Why do large transfers hang over a VPN while ping works? How do I find the path MTU between two hosts? Why does SSH freeze when a command prints a lot of output? Why is an OSPF neighbor stuck in EXSTART?

**Keywords:** MTU, fragmentation, Path MTU Discovery, PMTUD black hole, DF bit, don't fragment, TCP MSS clamping, jumbo frames, tunnel overhead, GRE, IPsec, VPN, PPPoE, VXLAN, ICMP type 3 code 4, ICMPv6 packet too big, EXSTART

**Related playbooks:** PB-08 Routing Errors (Static, OSPF, BGP); PB-09 ACL & Firewall Rule Errors; PB-12 IPv6 Configuration Errors.

### PB-07 MTU Mismatch & Fragmentation: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-07-diagnosis" type="playbook-diagnosis" playbook="PB-07" topic="mtu" osi="L2-L3" related="PB-08,PB-09,PB-12" -->

Step-by-step diagnosis for playbook PB-07 MTU Mismatch & Fragmentation. Follow the steps in order.

1. Probe the path with the Don't Fragment bit set. Start at 1472 bytes of payload (1472 + 28 bytes of headers = 1500) and decrease until the probe succeeds.
2. Compute the path MTU: the largest successful payload + 28 (IPv4). A path MTU trace reports it hop by hop.
3. Compare the MTU configured on each interface along the path, including tunnel interfaces.
4. Check firewalls and ACLs for rules dropping ICMP type 3 code 4 (IPv4) or ICMPv6 type 2 (IPv6).

### PB-07 MTU Mismatch & Fragmentation: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-07-resolution" type="playbook-resolution" playbook="PB-07" topic="mtu" osi="L2-L3" related="PB-08,PB-09,PB-12" -->

How to fix and verify playbook PB-07 MTU Mismatch & Fragmentation once the diagnostic process has identified the cause.

**Resolution:**

- Align the MTU on both ends of every link.
- On tunnels, lower the IP MTU and clamp the TCP MSS 40 bytes below it (for example 1400 and 1360).
- Permit ICMP type 3 code 4 and ICMPv6 type 2 through firewalls.
- Enable jumbo frames end to end, or not at all.

**Verification:**

- A DF-bit ping at the expected size succeeds end to end.
- Large transfers complete; OSPF adjacencies reach FULL.

> **Tip:** Reference values for a 1500-byte MTU: IPv4 ping payload 1472 (1500 − 20 IP − 8 ICMP); IPv6 ping payload 1452 (1500 − 40 − 8); IPv4 TCP MSS 1460.

## Playbook PB-08: Routing Errors (Static, OSPF, BGP)

### PB-08 Routing Errors (Static, OSPF, BGP): Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-08-overview" type="playbook-symptoms-causes" playbook="PB-08" topic="routing" osi="L3" related="PB-07,PB-09,PB-10" -->

Troubleshooting playbook PB-08 Routing Errors (Static, OSPF, BGP). Scope: Layer 3 · Routers, L3 switches, firewalls.

Routing errors affect specific destinations rather than everything. Always check both directions: a missing return route looks exactly like a missing forward route from the client side.

**Symptoms:**

- Specific remote subnets are unreachable while others work.
- Traceroute stops at a given router, or loops between two routers.
- Traffic works in one direction only, or sessions drop through a stateful firewall.
- Routing neighbors are down, stuck in a state, or flapping.

**Likely causes:**

- Missing static route, wrong next hop, or wrong prefix length.
- No return route on the far side.
- Unexpected route preference (longest match, administrative distance, metric).
- Prefix not advertised, not redistributed, or filtered.
- OSPF or BGP neighbor parameters mismatch.
- Asymmetric path through a stateful firewall.

**Typical questions:** Why is one specific remote subnet unreachable? Why is an OSPF neighbor stuck in INIT, 2-WAY or EXSTART? Why does a BGP session stay in the Active state? Why does traffic work in one direction only?

**Keywords:** routing, static route, return route, next hop, longest prefix match, administrative distance, OSPF adjacency, OSPF neighbor state, BGP session, BGP Idle, BGP Active, routing policy, prefix filter, redistribution, asymmetric routing, routing loop, traceroute

**Related playbooks:** PB-07 MTU Mismatch & Fragmentation; PB-09 ACL & Firewall Rule Errors; PB-10 NAT Misconfiguration.

### PB-08 Routing Errors (Static, OSPF, BGP): Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-08-diagnosis" type="playbook-diagnosis" playbook="PB-08" topic="routing" osi="L3" related="PB-07,PB-09,PB-10" -->

Step-by-step diagnosis for playbook PB-08 Routing Errors (Static, OSPF, BGP). Follow the steps in order.

1. Trace the path from both ends. Note the last responding hop and any repeating hops (loop).
2. On each hop, check which route is selected for the destination and where it points. Repeat for the return path, from the far side toward the source.
3. If a dynamic protocol should provide the route, check the neighbor state and compare it with the PB-08 tables “OSPF Adjacency States and Likely Causes” and “BGP Session States and Likely Causes”.
4. Review recent changes to routing policies, prefix filters and redistribution; compare the running configuration with the last known-good version.

### PB-08 OSPF Adjacency States and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-08-ospf" type="reference-table" playbook="PB-08" topic="routing-ospf" osi="L3" related="PB-07,PB-09,PB-10" -->

Use this table when an OSPF neighbor is missing or stuck in a state other than FULL. Part of playbook PB-08 (Routing Errors).

| State | Meaning | Likely causes |
|---|---|---|
| No neighbor listed | Hellos not exchanged or rejected | Area ID, hello/dead timers, authentication, subnet/mask, passive interface, ACL blocking IP protocol 89 |
| INIT | Hellos received in one direction only | ACL or filter in one direction, multicast issue |
| 2-WAY | Normal between two DROTHERs | Otherwise: network type mismatch between the two ends |
| EXSTART / EXCHANGE | Database exchange fails | MTU mismatch, duplicate router ID |
| FULL, route missing | Adjacency fine, route not installed | Area type (stub filtering), route filter, summarization, better route from another source |

### PB-08 BGP Session States and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-08-bgp" type="reference-table" playbook="PB-08" topic="routing-bgp" osi="L3" related="PB-07,PB-09,PB-10" -->

Use this table when a BGP session is not Established or receives no prefixes. Part of playbook PB-08 (Routing Errors).

| State | Meaning | Likely causes |
|---|---|---|
| Idle | Session not attempted or refused | Wrong neighbor IP, no route to peer, neighbor shut down |
| Active | TCP 179 not established | ACL on TCP 179, wrong session source address, missing multihop setting, wrong remote AS, MD5 password mismatch |
| Established, 0 prefixes | Session up, nothing accepted | Peer outbound policy, inbound prefix filter or routing policy, maximum-prefix limit |
| Prefix received, not used | Path selection or next hop | Next-hop reachability, local preference, administrative distance |

### PB-08 Routing Errors (Static, OSPF, BGP): Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-08-resolution" type="playbook-resolution" playbook="PB-08" topic="routing" osi="L3" related="PB-07,PB-09,PB-10" -->

How to fix and verify playbook PB-08 Routing Errors (Static, OSPF, BGP) once the diagnostic process has identified the cause.

**Resolution:**

- Add or correct the static route, including the return route on the far side.
- Align OSPF area, timers, authentication, network type and MTU; ensure unique router IDs.
- Fix the BGP neighbor address, AS number, session source address, multihop setting or password; adjust filters.
- Keep paths symmetric through stateful firewalls, or explicitly allow asymmetric flows.

**Verification:**

- The expected route is present and selected on every hop, in both directions.
- Traceroute completes from both ends; neighbor uptime keeps increasing.

## Playbook PB-09: ACL & Firewall Rule Errors

### PB-09 ACL & Firewall Rule Errors: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-09-overview" type="playbook-symptoms-causes" playbook="PB-09" topic="acl-firewall" osi="L3-L4" related="PB-08,PB-10,PB-07" -->

Troubleshooting playbook PB-09 ACL & Firewall Rule Errors. Scope: Layers 3–4 · Router ACLs, firewalls, host firewalls.

Filtering errors usually block a specific protocol, port or source. The key questions are where the packet disappears, and whether the rule that matches it is the one you expect.

**Symptoms:**

- A specific service fails while ping works, or the reverse.
- Access works from one subnet or site but not from another.
- The problem started right after a rule change.
- The connection times out rather than being refused.

**Likely causes:**

- Implicit deny at the end of the ACL or policy.
- Rule order: a broad deny above the specific permit (first match wins).
- ACL applied on the wrong interface or in the wrong direction.
- Wrong match mask, for example a subnet mask where the rule expects a wildcard mask.
- Stateless ACL without a rule for return traffic.
- Host firewall on the server, or service listening on localhost only.

**Typical questions:** Ping works but the application port does not: why? What is the difference between connection refused and timeout? How do I check whether an ACL is blocking traffic? Why does access work from one subnet but not another?

**Keywords:** ACL, access list, firewall rule, implicit deny, rule order, wildcard mask, ACL direction, hit counters, host firewall, connection refused, connection timeout, flow simulation, port test

**Related playbooks:** PB-08 Routing Errors (Static, OSPF, BGP); PB-10 NAT Misconfiguration; PB-07 MTU Mismatch & Fragmentation.

### PB-09 ACL & Firewall Rule Errors: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-09-diagnosis" type="playbook-diagnosis" playbook="PB-09" topic="acl-firewall" osi="L3-L4" related="PB-08,PB-10,PB-07" -->

Step-by-step diagnosis for playbook PB-09 ACL & Firewall Rule Errors. Follow the steps in order.

1. Test the exact protocol and port, not just ping.
2. Read the result: **refused** means the packet reached the host and nothing accepted it; **timeout** means it was silently dropped along the path or by a host firewall.
3. On the server, confirm that the service listens on the right address and port.
4. Check which ACLs are applied where, and in which direction, and watch their hit counters while reproducing the problem.
5. On firewalls, simulate the flow where the platform offers it, and read the logs for the flow.
6. Capture on both sides of the filtering device to see exactly where packets stop.

### PB-09 ACL & Firewall Rule Errors: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-09-resolution" type="playbook-resolution" playbook="PB-09" topic="acl-firewall" osi="L3-L4" related="PB-08,PB-10,PB-07" -->

How to fix and verify playbook PB-09 ACL & Firewall Rule Errors once the diagnostic process has identified the cause.

**Resolution:**

- Insert a specific permit above the conflicting deny (use sequence numbers).
- Apply the ACL to the correct interface and direction.
- Correct wildcard masks and object groups.
- Add return-traffic rules, or use stateful inspection.
- Open the port in the host firewall; bind the service to the right interface.

**Verification:**

- The connection succeeds from every intended source.
- The hit counter of the intended permit rule increments.
- No broader-than-necessary rule was introduced to “make it work”.

> **Warning:** Avoid testing with a temporary “permit any any” in production. If it is unavoidable, limit it in time, log it, and remove it as soon as the test ends.

## Playbook PB-10: NAT Misconfiguration

### PB-10 NAT Misconfiguration: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-10-overview" type="playbook-symptoms-causes" playbook="PB-10" topic="nat" osi="L3" related="PB-08,PB-09,PB-03" -->

Troubleshooting playbook PB-10 NAT Misconfiguration. Scope: Layer 3 · Edge routers, firewalls.

NAT mistakes typically appear as “the router reaches the internet but the clients do not”, or as port forwards that never trigger.

**Symptoms:**

- The edge router reaches the internet; internal hosts do not.
- Only some internal subnets get translated.
- An inbound port forward / static NAT does not respond.
- A published service works from outside but not from inside (hairpin).
- Translations fail under load (pool exhausted).

**Likely causes:**

- Interfaces not given their inside and outside NAT roles.
- NAT ACL does not match the source subnets.
- Port address translation (PAT) not enabled with a single public IP.
- Static mapping to the wrong internal IP or port.
- Upstream network has no route back to the NAT pool.
- No hairpin / NAT reflection; outside ACL blocking inbound traffic.

**Typical questions:** Why can the router reach the internet but not the clients? Why is a port forward / static NAT not working? Why is a published service unreachable from inside the network?

**Keywords:** NAT, PAT, inside interface, outside interface, port forwarding, static NAT, NAT pool exhausted, hairpin NAT, NAT reflection, split DNS, NAT translations

**Related playbooks:** PB-08 Routing Errors (Static, OSPF, BGP); PB-09 ACL & Firewall Rule Errors; PB-03 DNS Resolution Failures.

### PB-10 NAT Misconfiguration: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-10-diagnosis" type="playbook-diagnosis" playbook="PB-10" topic="nat" osi="L3" related="PB-08,PB-09,PB-03" -->

Step-by-step diagnosis for playbook PB-10 NAT Misconfiguration. Follow the steps in order.

1. Verify the interface roles (inside, outside) and the NAT rules.
2. Generate traffic from an inside host and look for its entry in the translation table.
3. If no translation appears, check that the NAT rule's match (ACL or source list) covers the source address.
4. For inbound mappings, test from an outside network and confirm that the outside ACL or firewall allows the traffic to the translated address.
5. As a last resort, debug NAT with a filter restricted to the test traffic, during a maintenance window: debug output can load the CPU.

### PB-10 NAT Misconfiguration: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-10-resolution" type="playbook-resolution" playbook="PB-10" topic="nat" osi="L3" related="PB-08,PB-09,PB-03" -->

How to fix and verify playbook PB-10 NAT Misconfiguration once the diagnostic process has identified the cause.

**Resolution:**

- Mark inside and outside interfaces correctly.
- Extend the NAT rule to all internal subnets; enable PAT when one public address is shared.
- Correct static mappings; make sure the pool is routed back to you upstream.
- Use split DNS or NAT hairpinning for inside access to published services.

**Verification:**

- Translations appear for every inside subnet and hit counts increase.
- Published services answer from an external test point.

## Playbook PB-11: Spanning Tree Issues

### PB-11 Spanning Tree Issues: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-11-overview" type="playbook-symptoms-causes" playbook="PB-11" topic="stp" osi="L2" related="PB-05,PB-06" -->

Troubleshooting playbook PB-11 Spanning Tree Issues. Scope: Layer 2 · Switched network.

Spanning Tree errors range from a single port shut down by a protection feature to a network-wide broadcast storm. During a storm, restoring service by breaking the loop comes before root-cause analysis.

**Symptoms:**

- Network-wide slowness or outage; very high switch CPU; activity LEDs blinking in unison.
- MAC flapping messages: the same MAC address seen on different ports.
- A port is shut down right after a device or small switch is plugged in.
- Traffic takes a slow or unexpected path across the network.

**Likely causes:**

- Physical loop (patch cable between two ports, unmanaged switch) with STP disabled or bypassed.
- Edge-port mode enabled on a port leading to another switch.
- BPDU guard shutting down a port that received BPDUs.
- Root bridge is not the intended core switch (default priority: lowest MAC wins).
- STP mode mismatch, or unidirectional fiber link.

**Typical questions:** The whole network is slow and switch CPU is high: what do I do? Why was a port shut down after plugging in a small switch? How do I set the root bridge? How do I stop a broadcast storm?

**Keywords:** Spanning Tree, STP, RSTP, MST, broadcast storm, switching loop, MAC flapping, BPDU guard, edge port, root bridge, bridge priority, loop guard, unidirectional link detection, storm control, topology change, high switch CPU

**Related playbooks:** PB-05 VLAN & Trunk Misconfiguration; PB-06 Speed, Duplex & Physical-Layer Settings.

### PB-11 Spanning Tree Issues: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-11-diagnosis" type="playbook-diagnosis" playbook="PB-11" topic="stp" osi="L2" related="PB-05,PB-06" -->

Step-by-step diagnosis for playbook PB-11 Spanning Tree Issues. Follow the steps in order.

1. During a storm, find the looping ports from the log messages (MAC flapping) and the interface rates, then shut one redundant link to break the loop.
2. Identify the root bridge for each VLAN and compare it with the design.
3. Check the port roles and states, and look for topology change counters that keep increasing.
4. For ports shut down by BPDU guard, confirm the cause and what is connected, with the neighbor discovery information (LLDP).

### PB-11 Spanning Tree Issues: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-11-resolution" type="playbook-resolution" playbook="PB-11" topic="stp" osi="L2" related="PB-05,PB-06" -->

How to fix and verify playbook PB-11 Spanning Tree Issues once the diagnostic process has identified the cause.

**Resolution:**

- Make the core switch root by giving it the lowest bridge priority (for example 4096).
- Enable edge-port mode and BPDU guard on edge ports only, never on switch-to-switch links.
- Remove the physical loop; enable loop guard and unidirectional link detection on fiber uplinks.
- Apply storm control on access ports to limit the impact of future loops.
- Run the same STP mode (for example RSTP or MST) on all switches.

**Verification:**

- The intended switch is root for every VLAN.
- Topology change counters stop increasing; CPU and traffic return to baseline.

## Playbook PB-12: IPv6 Configuration Errors

### PB-12 IPv6 Configuration Errors: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-12-overview" type="playbook-symptoms-causes" playbook="PB-12" topic="ipv6" osi="L3" related="PB-01,PB-07,PB-03" -->

Troubleshooting playbook PB-12 IPv6 Configuration Errors. Scope: Layer 3 · Hosts, routers, first-hop security.

IPv6 depends on ICMPv6 and Router Advertisements for basic operation. When IPv6 is half broken, dual-stack clients can also feel slow, because they try IPv6 first.

**Symptoms:**

- Host has only a link-local (fe80::) address.
- IPv6 works on some hosts or VLANs only.
- Global address present, but no default route or no DNS.
- Dual-stack clients pause before falling back to IPv4.

**Likely causes:**

- IPv6 routing not enabled on the router, or RAs suppressed on the interface.
- Prefix length other than /64 on a SLAAC segment.
- M/O flags do not match the DHCPv6 service actually deployed.
- ICMPv6 filtered (breaks Neighbor Discovery and PMTUD).
- RA Guard blocking the legitimate router, or a rogue RA source.

**Typical questions:** Why does a host only have an fe80:: address? Why does an IPv6 host have no default route? Why are dual-stack clients slow to connect?

**Keywords:** IPv6, link-local, fe80, SLAAC, Router Advertisement, RA, DHCPv6, M flag, O flag, ICMPv6, Neighbor Discovery, NDP, RA Guard, /64, RDNSS, IPv6 routing, dual-stack

**Related playbooks:** PB-01 IP Addressing & Subnet Mask Errors; PB-07 MTU Mismatch & Fragmentation; PB-03 DNS Resolution Failures.

### PB-12 IPv6 Configuration Errors: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-12-diagnosis" type="playbook-diagnosis" playbook="PB-12" topic="ipv6" osi="L3" related="PB-01,PB-07,PB-03" -->

Step-by-step diagnosis for playbook PB-12 IPv6 Configuration Errors. Follow the steps in order.

1. Check the host's IPv6 addresses and default route. The default route should point to the router's link-local address.
2. Listen for Router Advertisements on the segment.
3. On the router, confirm that IPv6 routing is enabled, and check the RA settings, the neighbor cache and the IPv6 routes on the client-facing interface.
4. Check ACLs and firewalls for ICMPv6: Neighbor Discovery (types 133–137) and Packet Too Big (type 2) must be allowed.

### PB-12 IPv6 Configuration Errors: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-12-resolution" type="playbook-resolution" playbook="PB-12" topic="ipv6" osi="L3" related="PB-01,PB-07,PB-03" -->

How to fix and verify playbook PB-12 IPv6 Configuration Errors once the diagnostic process has identified the cause.

**Resolution:**

- Enable IPv6 routing on the router; remove RA suppression on client interfaces.
- Use /64 on client segments; set the M/O flags to match the DHCPv6 design.
- Permit the required ICMPv6 types; configure RA Guard with the router port trusted.
- Provide IPv6 DNS through RDNSS (in RAs) or DHCPv6.

**Verification:**

- Hosts obtain a global address and a default route via the router link-local address.
- An IPv6 reachability test to a remote address and AAAA lookups both succeed.

## Playbook PB-13: Interface Down or Unusable MTU

### PB-13 Interface Down or Unusable MTU: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-13-overview" type="playbook-symptoms-causes" playbook="PB-13" topic="interface-state" osi="L1-L2" related="PB-06,PB-07,PB-16" -->

Troubleshooting playbook PB-13 Interface Down or Unusable MTU. Scope: Layers 1–2 · router ports and subinterfaces, firewall interfaces and VLAN interfaces, host interfaces.

An administratively disabled interface cuts the IPv4 connectivity of every endpoint behind it, while the rest of the network keeps working. The cable and the far end are fine, and only the configuration says down. An interface whose MTU is far below its segment's stays up but carries almost nothing.

**Symptoms:**

- Every path through one host or one gateway interface fails at once, while hosts on other interfaces are fine.
- The host cannot reach its gateway, and its ARP entry for the gateway is FAILED or INCOMPLETE.
- Small packets pass but normal traffic fails or stalls (MTU).

**Likely causes:**

- A port administratively disabled.
- A subinterface or VLAN interface disabled while its port stays up.
- IPv4 disabled on the subinterface, or the subinterface attached to no routing instance.
- A host interface set down.
- MTU changed on one side only, or set far below the segment's (1500 bytes on standard Ethernet).

**Keywords:** interface down, administratively disabled, admin state, subinterface, VLAN interface, link down, routing instance, MTU mismatch, low MTU

### PB-13 Interface Down or Unusable MTU: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-13-diagnosis" type="playbook-diagnosis" playbook="PB-13" topic="interface-state" osi="L1-L2" related="PB-06,PB-07,PB-16" -->

1. From the topology, find the interfaces on the failing paths: the host's data interface, its access port, and the gateway's port or VLAN interface.
2. **Host**: read the interface state (administratively up, link up) and its MTU. Compare the MTU with the other hosts of the segment.
3. **Router**: read the port's state, and the reason when it is down. Then read the subinterface: enabled, IPv4 enabled, address present, and attached to the expected routing instance.
4. **Firewall**: check the interface and the VLAN interface for a disabled setting.
5. **Compare with a working peer**: the same port on another switch, another VLAN interface on the firewall, or another host on the segment.

### PB-13 Interface Down or Unusable MTU: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-13-resolution" type="playbook-resolution" playbook="PB-13" topic="interface-state" osi="L1-L2" related="PB-06,PB-07,PB-16" -->

**Resolution:**

- Re-enable only the interface on the failing path: the port, the subinterface, IPv4 on the subinterface, or the host interface.
- Restore the MTU to the segment's value.

On many hosts, taking an interface down removes the routes through it. Bringing it up again restores the connected routes, but not always a default route that was added by hand: check the routing table afterwards and re-add the default route if it is missing (PB-14).

**Verification:**

- The interface reports up and its MTU matches the segment.
- The host's ARP entry for the gateway becomes REACHABLE.
- The validation passes for the pairs that failed, and the others are unchanged.

## Playbook PB-14: Routing Disabled on a Router or Host

### PB-14 Routing Disabled on a Router or Host: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-14-overview" type="playbook-symptoms-causes" playbook="PB-14" topic="forwarding" osi="L3" related="PB-02,PB-08,PB-15,PB-17" -->

Troubleshooting playbook PB-14 Routing Disabled on a Router or Host. Scope: Layer 3 · routing instances, IPv4 forwarding, host routing tables.

The hosts of a segment reach each other, but their IPv4 connectivity stops at the segment: nothing is routed beyond it. Or one host cannot send anything off its subnet. Interfaces and addresses are correct. What is missing is the decision to forward.

**Symptoms:**

- All hosts behind one gateway lose every off-subnet destination at once, while local traffic works.
- One host cannot reach anything outside its subnet, and its neighbours can.
- The host's programs get an immediate error instead of a timeout: network unreachable, or administratively prohibited.

**Likely causes:**

- The router's routing instance is administratively disabled.
- IPv4 forwarding is disabled on a router or firewall.
- A filter that drops everything is bound to the gateway's interfaces, or the forwarding filter drops by default with no rule accepting the traffic (PB-15).
- A host without a default route, or with a reject or discard default route (unreachable, prohibit, blackhole).
- A host whose default route points to a wrong gateway (PB-17).

**Keywords:** routing disabled, IPv4 forwarding, routing instance, forwarding filter, default action, default route missing, reject route, discard route, blackhole route, no route to host, network unreachable

### PB-14 Routing Disabled on a Router or Host: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-14-diagnosis" type="playbook-diagnosis" playbook="PB-14" topic="forwarding" osi="L3" related="PB-02,PB-08,PB-15,PB-17" -->

1. **Host**: read the routing table. A healthy host has the connected route of its subnet and one unicast default route via its gateway. A reject or discard route in place of the default stops every off-subnet packet:
   - unreachable: answers host unreachable;
   - prohibit: answers administratively prohibited;
   - blackhole: discards silently.

   Without a default route, the host has no route off its subnet.
2. **Router**: check that its routing instance is enabled. While it is disabled, nothing is routed through the router. Also check the filters bound to the gateway interfaces (PB-15).
3. **Firewall**: check that IPv4 forwarding is enabled, and that the forwarding filter does not drop by default where the design does not call for it.
4. **Compare with a healthy peer.** Another host on the segment shows the expected default route and gateway. Another router shows its routing instance enabled.

### PB-14 Routing Disabled on a Router or Host: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-14-resolution" type="playbook-resolution" playbook="PB-14" topic="forwarding" osi="L3" related="PB-02,PB-08,PB-15,PB-17" -->

**Resolution:**

- Router: re-enable the routing instance.
- Firewall: re-enable IPv4 forwarding, or remove a drop-by-default the design does not call for.
- Host: replace the missing or reject default route with a unicast default route via the segment gateway. A route replacement overwrites a special default route in one step.

**The right gateway** is the address the other hosts of the segment use as their default route. It is normally the router's address on that subnet, and never a host's own address.

On a firewall, before changing the forwarding default action, check that the zones or rulesets still enforce the intended policy (PB-19). Do not open traffic the design blocks.

**Verification:**

- The router's routing table lists the remote prefixes as active.
- The host has one unicast default route.
- The validation passes for the failing pairs.

## Playbook PB-15: Traffic Dropped by an ACL or Firewall Filter

### PB-15 Traffic Dropped by an ACL or Firewall Filter: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-15-overview" type="playbook-symptoms-causes" playbook="PB-15" topic="acl-filter" osi="L3-L4" related="PB-09,PB-14,PB-19" -->

Troubleshooting playbook PB-15 Traffic Dropped by an ACL or Firewall Filter. Scope: Layers 3–4 · router ACLs, firewall forwarding filters and rulesets. Generic filtering concepts are in PB-09.

A filter drops only what it matches, so the loss of IPv4 connectivity is selective. Traffic from or to one subnet fails in one or both directions, or only ICMP fails while TCP works. Every other flow through the same router is fine.

**Symptoms:**

- One subnet loses its paths through a gateway, while the other subnets behind the same gateway keep theirs.
- Traffic fails in one direction only: an input filter on one side, or an output filter on the other.
- Ping fails but other protocols work: an entry that matches ICMP.

**Likely causes:**

- An ACL entry with a drop action that matches the subnet as source or destination, bound in the input or output direction on the gateway's interface.
- An ACL entry with no match condition, which matches every packet.
- A firewall forwarding rule that jumps to a ruleset dropping the subnet, or a drop rule placed before the accept rules.
- A drop added for a test or an incident and never removed.

**Keywords:** ACL, filter entry, source prefix, destination prefix, drop action, input filter, output filter, interface binding, forwarding filter, jump to ruleset, firewall rule, blocked subnet, ICMP blocked, one direction

### PB-15 Traffic Dropped by an ACL or Firewall Filter: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-15-diagnosis" type="playbook-diagnosis" playbook="PB-15" topic="acl-filter" osi="L3-L4" related="PB-09,PB-14,PB-19" -->

1. Locate the gateway between the failing subnet and the rest. PB-14 rules out routing that is off entirely.
2. **ACLs on the router**: list the filters and where they are bound. A binding attaches a filter to one interface, and its direction tells which traffic the filter sees: input enters there, output leaves there. Match each drop entry's prefix with the failing subnet, and its direction with the failing direction. An entry that also matches ICMP explains a ping-only failure.
3. **Firewall rules**: follow the forwarding chain rule by rule, including the rulesets it jumps to.
   - Rules run in ascending number order, and the first match decides.
   - A ruleset ends with its default action; a return action goes back to the calling chain.
   - Find the rule whose source or destination covers the subnet with a drop action.
4. **Decide whether the filter belongs to the intended policy.** Compare it with the other gateways and with the rest of the ruleset.
   - A filter that exists only to drop one internal subnet is the fault.
   - In a filter that also carries legitimate entries, only the offending entry is.

### PB-15 Traffic Dropped by an ACL or Firewall Filter: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-15-resolution" type="playbook-resolution" playbook="PB-15" topic="acl-filter" osi="L3-L4" related="PB-09,PB-14,PB-19" -->

**Resolution:** remove only what drops the required traffic.

- A filter that exists only to drop this traffic: remove its interface binding, then the filter.
- A filter that must stay: remove only the offending entry.
- A firewall: remove the offending rule, or the jump that leads to it.

Remove the binding in the same change or before: many platforms refuse to delete a filter that an interface still references.

Do not replace a drop with a broad accept, and do not remove filters on other interfaces. The goal is to restore the intended path, not to open the router.

**Verification:**

- The validation passes for the pairs that failed, in both directions and for every protocol that was blocked.
- Traffic that the rest of the policy denies is still denied.

## Playbook PB-16: Missing, Wrong or Duplicate Interface Addresses

### PB-16 Missing, Wrong or Duplicate Interface Addresses: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-16-overview" type="playbook-symptoms-causes" playbook="PB-16" topic="addressing" osi="L3" related="PB-01,PB-02,PB-14,PB-17" -->

Troubleshooting playbook PB-16 Missing, Wrong or Duplicate Interface Addresses. Scope: Layer 3 · router and firewall interface addresses, host addresses. Generic addressing concepts are in PB-01.

An addressing fault cuts the IPv4 connectivity of exactly the endpoints that depend on the wrong address:

- a gateway without its address strands its whole segment;
- a host with a wrong address or prefix length strands only itself;
- a host holding another node's address can also disturb that node.

**Symptoms:**

- All hosts of one segment lose off-subnet access, and their ARP entries for the gateway stay FAILED or INCOMPLETE (the gateway address is missing).
- One host has no IPv4 address on its data interface, or an address from another subnet.
- One host has a /32 where its neighbours have a /24. It has no connected route, so even its gateway is unreachable.
- Two nodes answer ARP for one address, or connectivity to a server flaps (duplicate address).

**Likely causes:**

- The gateway's IPv4 address was removed from the router or firewall interface.
- A host address was deleted or mistyped, given the wrong prefix length, or copied from another host.
- An address change also removed the host's default route (see the resolution).

**Keywords:** IP address missing, no IPv4 address, wrong subnet, wrong prefix length, /32, netmask, duplicate IP, address conflict, gateway address, subinterface address

### PB-16 Missing, Wrong or Duplicate Interface Addresses: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-16-diagnosis" type="playbook-diagnosis" playbook="PB-16" topic="addressing" osi="L3" related="PB-01,PB-02,PB-14,PB-17" -->

1. **Host**: read its addresses and its routing table. Compare with the other hosts on the segment: same network, same prefix length, and a unique host part. The routing table must hold the connected route of the subnet; a /32 address has none.
2. **Work out the intended address from the design.**
   - The segment's subnet is the gateway's subnet on that link.
   - The host part is unique, and a static DHCP mapping, where one exists, names it (PB-18).
   - An address that another node of the topology already uses is never the right one.
3. **Gateway**: the router's subinterface, or the firewall's VLAN interface, must hold the gateway address, with IPv4 enabled and the interface attached to its routing instance. The gateway address is the default route of the segment's hosts.
4. **Duplicates**: look for the same address on two nodes of the topology, or an ARP entry whose MAC address is not the expected node's.

### PB-16 Missing, Wrong or Duplicate Interface Addresses: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-16-resolution" type="playbook-resolution" playbook="PB-16" topic="addressing" osi="L3" related="PB-01,PB-02,PB-14,PB-17" -->

**Resolution:**

- Gateway: put the gateway address back on the router or firewall interface, with IPv4 enabled.
- Host: remove the wrong address, add the right one, then check the default route.

The same address with another prefix length is a different address: adding the right prefix next to a wrong /32 can leave both in place, so remove the wrong entry explicitly. Removing a host's only address on a subnet also removes the routes through that subnet, the default route included. Check the routing table after every address change and re-add the default route.

**Verification:**

- The address and prefix length match the segment.
- The ARP entry for the gateway resolves and the default route is present.
- The validation passes, and no other node lost connectivity. A duplicate fixed on the wrong node only moves the fault.

## Playbook PB-17: Static Route and Next-Hop Errors

### PB-17 Static Route and Next-Hop Errors: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-17-overview" type="playbook-symptoms-causes" playbook="PB-17" topic="static-routing" osi="L3" related="PB-02,PB-08,PB-14" -->

Troubleshooting playbook PB-17 Static Route and Next-Hop Errors. Scope: Layer 3 · static routes and next hops on routers, host default gateways. Generic routing concepts are in PB-08.

Static routing errors cut the IPv4 connectivity to specific destinations: one remote subnet, or one host, becomes unreachable from some sources while the other paths work. A route can be present and active and still point the wrong way.

**Symptoms:**

- One destination subnet is unreachable from the hosts behind one router, or the return path is the broken one.
- One host inside a subnet is unreachable while its neighbours answer (a more specific route).
- Traceroute alternates between two routers until the TTL expires, or pings return Time to live exceeded (a loop).
- ICMP redirects appear, or traffic leaves through an unexpected interface.
- One host reaches only its own subnet, and its ARP entry for its gateway stays FAILED.

**Likely causes:**

- The route points to the wrong next hop: a neighbour that has no path to the destination, or that sends it back.
- A more specific route (/32) to a discard (blackhole) next hop overrides the correct /24.
- The next hop lies in no connected subnet. The route stays inactive, and traffic falls back to a less specific route or is dropped.
- Two routers point the same prefix at each other.
- A host's default route points to an address no router owns.

**Keywords:** static route, next hop, next-hop group, blackhole, discard route, longest prefix match, more specific route, host route, unresolved next hop, inactive route, routing loop, TTL exceeded, wrong egress, default gateway, ECMP

### PB-17 Static Route and Next-Hop Errors: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-17-diagnosis" type="playbook-diagnosis" playbook="PB-17" topic="static-routing" osi="L3" related="PB-02,PB-08,PB-14" -->

1. Follow the path hop by hop, in both directions. On each router, read the route for the destination, whether it is active, and what it points to: a next-hop address, a group of next hops (several make ECMP), or a discard.
2. **Compare with the working prefixes.** Routes to the other remote subnets through the same neighbour usually share one next hop, or one next-hop group. The suspect is a prefix with its own next hop, a different one, or a discard.
3. **Longest match**: look for a more specific route (/32 or a longer mask) covering the unreachable host.
4. **Next-hop resolution**: the next-hop address must lie in a connected subnet of the router, typically the neighbour's address on the link between them.
5. **Loops**: read the route for the prefix on both routers of the alternating pair. Each must point toward the destination, not at the other router.
6. **Host default gateway**: the default route must point to an address that answers ARP. The right gateway is the one the other hosts of the segment use.

### PB-17 Static Route and Next-Hop Errors: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-17-resolution" type="playbook-resolution" playbook="PB-17" topic="static-routing" osi="L3" related="PB-02,PB-08,PB-14" -->

**Resolution:** point the prefix back at a next hop that works, and remove what overrides it.

- Reuse the next hop, or next-hop group, that the working prefixes through the same neighbour use.
- Remove a more specific discard route, then any next-hop group it leaves unused.
- Give a route with an unresolvable next hop the neighbour's address on the connected link.
- **Loops**: fix the router whose route points the wrong way. If both routers were changed, fix both.
- **Host**: correct the default gateway.

Correct the node that deviates. Do not re-address a router interface to match one host's wrong gateway: that breaks every other host of the segment, and every route that points at the old address.

**Verification:**

- On each router, the route is active with the expected next hop, in both directions.
- Traceroute completes.
- The validation passes for the failing pairs, and the other pairs are unchanged.

## Playbook PB-18: DHCP Scope Errors on a Multi-Subnet Server

### PB-18 DHCP Scope Errors on a Multi-Subnet Server: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-18-overview" type="playbook-symptoms-causes" playbook="PB-18" topic="dhcp-server" osi="L2-L7" related="PB-03,PB-04,PB-16" -->

Troubleshooting playbook PB-18 DHCP Scope Errors on a Multi-Subnet Server. Scope: Layers 2–7 · DHCP servers that serve several segments, and their clients. Generic DHCP concepts (DORA, relays) are in PB-04.

A DHCP server that serves several segments fails per subnet. The clients of one segment come up without an address, or with an address but a wrong gateway or resolver, while the other segments are still served correctly. The fault is in that subnet's scope, not in the service as a whole.

**Symptoms:**

- The clients of one segment get no IPv4 lease, while the clients of the other segments do.
- The clients get an address but cannot leave their subnet: the lease advertises a default router that no router owns.
- Addresses and routing work, but names do not resolve: no name server, or a wrong one, is advertised.
- A client keeps its previous settings until its lease is renewed, so a fix shows at renewal.

**Likely causes:**

- The segment's scope is missing, or its identifier clashes with another scope on a server that requires unique identifiers.
- The router option (default gateway) is missing or wrong.
- The DNS server option is missing, or names a host that runs no resolver.
- A reservation has the wrong MAC or IP address, the range is exhausted, or the server does not listen on the segment.

**Keywords:** DHCP provisioning, DHCP server, scope, subnet, shared network, router option, default router, DNS server option, name server, domain name, lease, reservation, static mapping, range, no lease, wrong gateway, wrong DNS server, served correctly

### PB-18 DHCP Scope Errors on a Multi-Subnet Server: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-18-diagnosis" type="playbook-diagnosis" playbook="PB-18" topic="dhcp-server" osi="L2-L7" related="PB-03,PB-04,PB-16" -->

1. Read the server's configuration for every served subnet: the subnet, its identifier, the range or reservations, the router option, the DNS server option, the domain and the lease time.
2. **Compare the affected subnet with the healthy ones.** Every subnet should carry the same kinds of settings.
   - The default router is the router's own address on that segment.
   - The name server is normally the resolver the other subnets advertise, on a node that runs DNS.
   - A subnet that lacks a setting the others have, or holds a value that differs with no design reason, has the fault.
3. **Check the router's interface on the segment.** Its address gives both the subnet and the default router (PB-16).
4. **On a client**, read the lease: the address and its remaining lifetime, and the default route and resolver learned from DHCP.

### PB-18 DHCP Scope Errors on a Multi-Subnet Server: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-18-resolution" type="playbook-resolution" playbook="PB-18" topic="dhcp-server" osi="L2-L7" related="PB-03,PB-04,PB-16" -->

**Resolution:** make the subnet's scope match the design and its sibling subnets.

- Correct the router option to the segment router's address.
- Replace a wrong name server: remove it first, because a list keeps the wrong entry next to the right one.
- Recreate a missing scope completely, following the design and the sibling subnets: subnet, unique identifier where the server requires one, range or reservations, router, DNS server, domain and lease.

Change only the affected subnet. The other subnets are served correctly, and changing them re-provisions their clients.

**Verification:**

- The scope matches the design and the other subnets.
- After renewal, the clients hold an address in the subnet, a default route via the segment router, and the right resolver.
- The connectivity validation passes.

## Playbook PB-19: Zone-Based Firewall Policy Errors

### PB-19 Zone-Based Firewall Policy Errors: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-19-overview" type="playbook-symptoms-causes" playbook="PB-19" topic="zone-firewall" osi="L3-L4" related="PB-09,PB-14,PB-15" -->

Troubleshooting playbook PB-19 Zone-Based Firewall Policy Errors. Scope: Layers 3–4 · zone-based firewalls: zones, zone pairs and rulesets. Generic filtering concepts are in PB-09.

A zone-based firewall puts each interface in exactly one zone and filters traffic by zone pair. Traffic entering zone B from zone A is checked against the ruleset bound on B for traffic from A. A pair with no bound ruleset falls to the destination zone's default action, which is drop. So one broken binding cuts exactly one direction between two zones, and the policy elsewhere keeps working.

**Symptoms:**

- The hosts of one zone lose the destinations their policy allows (web, TLS, ping to another zone), while the other zones keep theirs.
- Only one direction between two zones fails.
- One VLAN or interface loses everything through the firewall, while the other interfaces of its zone work.

**Likely causes:**

- The destination zone lost the binding of the ruleset for the source zone.
- A drop rule numbered before the accept rules shadows them: the first match wins.
- The interface or VLAN interface was removed from its zone, so its traffic matches no zone pair.
- The ruleset's default action, or one of its accept rules, was changed.

**Keywords:** zone-based firewall, zone policy, zone pair, from zone, ruleset, rule order, first match, shadowed rule, default action drop, zone interface, segmentation, do not loosen the policy

### PB-19 Zone-Based Firewall Policy Errors: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-19-diagnosis" type="playbook-diagnosis" playbook="PB-19" topic="zone-firewall" osi="L3-L4" related="PB-09,PB-14,PB-15" -->

1. **Identify the source and destination zones of a failing path.** Each zone lists its interfaces, and one ruleset binding for each zone allowed to send to it.
2. **Missing binding**: the destination zone has no binding for the source zone, while the ruleset for that pair still exists. Rulesets are usually named after their pair (for example LAN-TO-WAN), so a ruleset bound nowhere is a strong sign.
3. **Missing interface**: the interface or VLAN interface of the failing hosts appears in no zone, while its siblings appear in theirs. The zone descriptions and the ruleset names show where it belongs.
4. **Shadowing rule**: read the pair's ruleset. Rules run in ascending order, so a drop rule numbered before the accept rules blocks what they allow. A missing description, or one unlike the others, is a further sign.
5. **Compare with the mirror pairs and the other zones.** In a consistent policy, bindings, rule numbering and default actions follow one pattern.

### PB-19 Zone-Based Firewall Policy Errors: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-19-resolution" type="playbook-resolution" playbook="PB-19" topic="zone-firewall" osi="L3-L4" related="PB-09,PB-14,PB-15" -->

**Resolution:** put back the one missing or broken element, never a broader accept.

- Re-bind the pair's ruleset on the destination zone.
- Put the interface back in its zone.
- Remove the drop rule that shadows the accept rules.

Do not do any of the following, even to make traffic pass:

- set a zone's or a ruleset's default action to accept;
- bind a permissive ruleset;
- move an interface into another zone.

Each would reopen the flow by loosening the segmentation that other traffic depends on.

**Verification:**

- The pair's allowed traffic passes again in the failing direction.
- A flow the policy denies, between zones with no allow rule, is still denied.
- The validation passes, and no new flow is allowed.

## Playbook PB-20: Link Impairment: Added Delay, Loss or Corruption

### PB-20 Link Impairment: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-20-overview" type="playbook-symptoms-causes" playbook="PB-20" topic="impairment" osi="L1-L3" related="PB-06,PB-07,PB-21" -->

Troubleshooting playbook PB-20 Link Impairment: Added Delay, Loss or Corruption. Scope: Layers 1–3 · interfaces and their egress queues.

An application path can work and still perform badly: every packet arrives late, or a share of packets is lost or corrupted, while routing and filtering are correct. A common configured cause is an impairment (network emulation) policy left on an interface's egress, which degrades everything leaving it.

**Symptoms:**

- The round-trip time on one path is far above comparable paths: a LAN path shows a few milliseconds at most, so hundreds of milliseconds or seconds point to added delay.
- A steady share of pings is lost on one path while the others are clean. Corrupted packets fail their checksum at the receiver and look like loss.
- Every path of one host is degraded (the impairment is on its egress), or only the paths through one interface.

**Likely causes:**

- An impairment policy left on an interface, adding delay, loss, corruption, duplication or reordering.
- A shaper whose rate is far below the traffic, which also adds delay (PB-21).
- Physical errors on the link (PB-06).

**Keywords:** expected performance, application path performing badly, latency, delay, RTT, packet loss, corruption, degraded path, network emulation, egress queueing policy

### PB-20 Link Impairment: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-20-diagnosis" type="playbook-diagnosis" playbook="PB-20" topic="impairment" osi="L1-L3" related="PB-06,PB-07,PB-21" -->

1. **Measure.** Test the degraded path from the affected host to the destination. Then measure from another host to the same destination, and on a path that performs normally. The comparison places the impairment at one host, one link or one router.
2. **Read the egress queueing policy** of every interface on the degraded path, starting with its two ends. A default queue adds nothing; an emulation policy states the delay, loss or corruption it adds.
3. **Impairments act on egress only.** A delay on host A's interface slows every packet A sends, so every path from A is affected. Look at both ends of the path, because the reply direction counts in the round-trip time.
4. **Rule out the other causes**: physical errors on the link (PB-06), and a congested shaped link (PB-21).

### PB-20 Link Impairment: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-20-resolution" type="playbook-resolution" playbook="PB-20" topic="impairment" osi="L1-L3" related="PB-06,PB-07,PB-21" -->

**Resolution:** remove the impairment policy from the interface that carries it, rather than compensating elsewhere. The interface returns to its default queue.

Remove an interface's egress policy only when it is the impairment. If the policy is an intended shaping hierarchy, removing it deletes the whole policy (PB-21).

**Verification:**

- The interface shows its default queueing policy.
- The same measurement shows no loss, and a round-trip time back in line with comparable paths (a few milliseconds on a LAN).
- The other paths from the host are unchanged or better.

## Playbook PB-21: Traffic Shaping and Bandwidth Guarantees

### PB-21 Traffic Shaping and Bandwidth Guarantees: Symptoms and Likely Causes

<!-- chunk doc="network-config-troubleshooting" id="pb-21-overview" type="playbook-symptoms-causes" playbook="PB-21" topic="qos-shaping" osi="L3" related="PB-06,PB-20" -->

Troubleshooting playbook PB-21 Traffic Shaping and Bandwidth Guarantees. Scope: QoS · hierarchical shaping on the interface that sends into a bandwidth-limited uplink.

A bandwidth guarantee only matters under contention. An idle uplink carries any flow at full speed, so connectivity checks pass even when the policy is broken. Under load, only the shaping hierarchy decides who gets what. A protected (business) traffic class receives its contractual minimum only if four elements agree:

- a root shaper on the egress interface toward the bottleneck, capped at the link rate;
- a class for the protected traffic whose guaranteed rate is at least the guarantee;
- a classifier that puts the protected traffic into that class;
- a default class for everything else, whose rate leaves room for the guarantee.

**Symptoms:**

- The protected flow measures well below its guarantee while a competing flow saturates the uplink. Idle, it is fine.
- Competing traffic takes most of the link, or the protected class is stuck at a few hundred kbit/s.

**Likely causes:**

- The class hierarchy or the whole policy was removed, leaving the bare link cap or nothing.
- The protected class's guaranteed rate or ceiling is far below the guarantee.
- The classifier for the protected traffic was deleted, so its traffic falls into the default class.
- The rates of the protected and best-effort classes are swapped.
- No policy was ever configured.

**Keywords:** QoS, traffic shaping, hierarchical shaper, bandwidth guarantee, assured bandwidth, contractual guarantee, minimum throughput, Mbps, contention, competing flow, saturates the uplink, business traffic class, shaped WAN uplink, guaranteed rate, ceiling, classifier, default class

### PB-21 Traffic Shaping and Bandwidth Guarantees: Diagnostic Process

<!-- chunk doc="network-config-troubleshooting" id="pb-21-diagnosis" type="playbook-diagnosis" playbook="PB-21" topic="qos-shaping" osi="L3" related="PB-06,PB-20" -->

1. **Find the shaped interface**: the edge's egress toward the bottleneck link. The topology gives each link's capacity. Shaping acts on egress only, so a policy on the ingress side has no effect.
2. **Read the whole hierarchy**: the root policy and its default class; every class with its guaranteed rate, ceiling and priority; every classifier with the traffic it matches and the class it sends it to. Mind the notation: some tools print class identifiers, the default class and match values in hexadecimal.
3. **Check the four elements** listed in the PB-21 symptoms:
   - the protected class's guaranteed rate is at least the guarantee;
   - a classifier sends the protected source to that class;
   - the default names the best-effort class;
   - the class rates add up to no more than the parent's rate.

### PB-21 Traffic Shaping and Bandwidth Guarantees: Resolution and Verification

<!-- chunk doc="network-config-troubleshooting" id="pb-21-resolution" type="playbook-resolution" playbook="PB-21" topic="qos-shaping" osi="L3" related="PB-06,PB-20" -->

**Resolution:** repair the element that is wrong and keep the rest of the hierarchy.

- Restore the class rates: the guarantee plus a margin for the protected class, what remains for the best-effort class, both able to borrow up to the link rate.
- Restore the classifier for the protected source.
- Point the default at the best-effort class.

Pitfalls:

- Rate units: megabits and megabytes per second differ by a factor of eight.
- Some platforms refuse to replace a root policy in place: change the classes and classifiers in place, or remove the root and build it again.
- The root usually caps the uplink at its real rate. Removing it leaves the link unshaped, and the queue then forms where no policy applies.

**Verification:**

- The classes and classifiers match the design.
- Under the same contention, the protected flow measures at or above its guarantee.
- The competing flow still passes, at the remaining rate.

### PB-21 Designing a Bandwidth Guarantee with a Hierarchical Shaper

<!-- chunk doc="network-config-troubleshooting" id="pb-21-design" type="procedure" playbook="PB-21" topic="qos-shaping" osi="L3" related="PB-21" -->

To guarantee G to one traffic class on an uplink of capacity C, while the other traffic keeps the rest:

1. Shape on the egress interface toward the bottleneck, at the link capacity or slightly below it. The queue must form in this shaper, not in the link beyond it.
2. Create one class per traffic type under a parent class at C:
   - the protected class: a guaranteed rate of at least G, plus about 10 % for headers, a ceiling of C, and the highest priority;
   - the best-effort class: a guaranteed rate of what remains, a ceiling of C, and a lower priority.

   Both classes borrow unused bandwidth up to C.
3. Classify the protected traffic on its source (or destination) prefix.
4. Point the default at the best-effort class, so that unclassified traffic is shaped too. With a default that names no class, unclassified traffic can bypass the hierarchy.

Example: with C = 20 Mbit/s and G = 12 Mbit/s, the protected class gets 13 Mbit/s guaranteed and the best-effort class 7 Mbit/s, both with a ceiling of 20 Mbit/s.

Keep the competing traffic in a class with a non-zero rate. Dropping it, or policing it to nothing, meets the guarantee but disconnects traffic that must stay connected.

## Documentation and Escalation

### DOC-01 Network Incident Record Template

<!-- chunk doc="network-config-troubleshooting" id="doc-01" type="template" topic="documentation" -->

A fix is complete only when it is documented. Good records speed up the next incident and reveal recurring configuration weaknesses. Record the following fields for every network incident:

- **Incident ID / ticket**: Reference in the ticketing system.
- **Detected / resolved**: Date and time of detection, mitigation and final resolution.
- **Reported symptoms**: Exact symptoms and error messages, as reported and as observed.
- **Scope and impact**: Users, sites, VLANs and services affected; business impact.
- **Timeline**: Key events: changes, alarms, tests performed and their results.
- **Root cause**: The configuration error and why it happened (process, tooling, human error).
- **Devices and configuration**: Devices touched; configuration before and after (diff).
- **Fix applied**: Exact changes, change reference, who applied them.
- **Verification**: Tests that prove the service is restored, from the user's point of view.
- **Prevention**: Actions to avoid recurrence: templates, validation, monitoring, training.
- **Owner and follow-up**: Person responsible for each open action, with due dates.

> **Tip:** After each incident, ask whether the error could have been caught earlier: configuration templates, pre-change validation, automated compliance checks and monitoring alerts are the most effective ways to stop the same misconfiguration from coming back.

### DOC-02 When to Escalate a Network Incident and What to Hand Over

<!-- chunk doc="network-config-troubleshooting" id="doc-02" type="procedure" topic="escalation" -->

Escalate a network incident when:

- The impact grows, or a critical service stays down beyond the agreed response time.
- The fix requires access, authority or changes outside your scope (provider, security team, another site).
- Evidence points to a hardware fault, a software bug or a security incident.
- Your list of tested hypotheses is exhausted without progress.

When escalating, hand over the problem statement, the scope, the timeline, the tests already performed with their results, captures and logs, and any changes attempted or reverted.

## Glossary

### GLOSSARY-01 Network Troubleshooting Glossary (A to L)

<!-- chunk doc="network-config-troubleshooting" id="glossary-01" type="glossary" topic="definitions" -->

- **ACL** — Access Control List: ordered permit/deny rules applied to an interface in a direction; first match wins, implicit deny at the end.
- **ARP** — Address Resolution Protocol: maps an IPv4 address to a MAC address on the local segment.
- **BGP** — Border Gateway Protocol: path-vector routing protocol running over TCP 179.
- **BPDU** — Bridge Protocol Data Unit: Spanning Tree control frame exchanged between switches.
- **BPDU guard** — Feature that shuts down an edge port when it receives a BPDU.
- **CRC / FCS error** — Frame checksum failure; points to cabling, optics or a duplex mismatch.
- **DF bit** — Don't Fragment flag in the IPv4 header; used to probe the path MTU.
- **DHCP** — Dynamic Host Configuration Protocol: hands out addresses and options (gateway, DNS, domain).
- **DORA** — Discover, Offer, Request, Acknowledge: the four-message DHCP exchange.
- **Edge port** — Spanning Tree port facing an end device, which moves straight to forwarding; never used on switch-to-switch links.
- **Error-disabled port** — Port shut down automatically by a protection feature (port security, BPDU guard, link-flap detection).
- **FHRP** — First Hop Redundancy Protocol (for example VRRP): a virtual gateway address shared between routers.
- **IPAM** — IP Address Management: the reference record of subnets and address assignments.
- **Link-local address** — Self-assigned address valid only on the local segment: 169.254.0.0/16 in IPv4 (a sign of DHCP failure), fe80::/10 in IPv6.
- **LLDP** — Link Layer Discovery Protocol: identifies directly connected neighbors.

### GLOSSARY-02 Network Troubleshooting Glossary (M to V)

<!-- chunk doc="network-config-troubleshooting" id="glossary-02" type="glossary" topic="definitions" -->

- **MSS** — TCP Maximum Segment Size; clamped on tunnels to avoid fragmentation.
- **MTU** — Maximum Transmission Unit: largest packet an interface sends without fragmentation (1500 bytes on standard Ethernet).
- **NAT / PAT** — Network / Port Address Translation; PAT shares one public IP between many hosts.
- **Native VLAN** — VLAN carried untagged on an 802.1Q trunk; must match on both ends.
- **NDP** — IPv6 Neighbor Discovery Protocol (ICMPv6 types 133-137): replaces ARP and carries Router Advertisements.
- **OSPF** — Open Shortest Path First: link-state interior routing protocol (IP protocol 89).
- **PMTUD** — Path MTU Discovery: relies on ICMP 'fragmentation needed' / ICMPv6 'packet too big' messages.
- **RA / RA Guard** — IPv6 Router Advertisement / switch feature that blocks RAs from untrusted ports.
- **SLAAC** — Stateless Address Autoconfiguration: IPv6 hosts build their address from the /64 prefix in RAs.
- **STP** — Spanning Tree Protocol (and RSTP, MST): prevents Layer 2 loops by blocking redundant paths.
- **Unidirectional link detection** — Detects fiber links that transmit in one direction only.
- **VLAN / trunk** — Virtual LAN (separate broadcast domain) / link carrying several VLANs with 802.1Q tags.
- **VLAN interface** — Layer 3 interface of a VLAN on a switch or router, often the clients' gateway.

### GLOSSARY-03 Troubleshooting Glossary: Routing Instances, Zones and Traffic Control

<!-- chunk doc="network-config-troubleshooting" id="glossary-03" type="glossary" topic="definitions" -->

- **Routing instance (VRF)** — A separate routing table; the default instance holds the global routes. Disabling it stops routing on all its interfaces.
- **Subinterface** — Logical interface on a port, often one per VLAN, that carries the IP address; it must belong to a routing instance.
- **Next-hop group** — Named set of next hops, or a discard, that static routes point to; several next hops give ECMP.
- **ACL binding** — Attachment of a filter to an interface, in the input or output direction.
- **Candidate / commit** — Edit-then-apply configuration model: changes take effect when committed.
- **Zone, zone pair, ruleset** — Interfaces grouped by trust. Traffic from zone A into zone B is filtered by the ruleset bound on B for A; a pair with no ruleset falls to the zone's default action (drop).
- **Forwarding filter** — The base filter chain a firewall applies to routed traffic.
- **DHCP scope** — What a server hands out for one subnet: range or reservations, router, DNS servers, domain, lease.
- **Queueing policy, class, classifier** — The egress queueing policy of an interface, its traffic classes, and the rules that put packets into classes.
- **Guaranteed rate, ceiling, priority, default class** — A class's assured bandwidth, its maximum when borrowing, its borrowing order, and the class for unclassified traffic.
- **Network emulation** — Egress policy that adds delay, loss, corruption, duplication or reordering.
- **Reject and discard routes** — Routes that answer unreachable or prohibited, or silently drop, instead of forwarding.
