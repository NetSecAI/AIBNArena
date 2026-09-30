"""Policy faults are judged on the permitted flows they cost.

`configuration` is declared but not yet executable: reading the zone model back is
what would catch a repair that widened the policy instead of restoring it, and
`ContainerlabPlatform.run_probe` refuses that probe type rather than answering it
from reachability it cannot infer it from.
"""

PROBE_TYPES = ("icmp", "configuration")
