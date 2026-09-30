"""Provisioning faults are judged on the reachability they cost.

A client that never got a lease has no address, so the ICMP matrix already sees a
failed hand-off. A dns probe type is declared for the resolution half of the domain,
which needs a name to be answered rather than an address to be reached.
"""

PROBE_TYPES = ("icmp", "dns")
