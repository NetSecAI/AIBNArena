"""Small domain-specific metric projections over shared oracle evaluations."""
from __future__ import annotations

from typing import Any, Mapping

from benchmarks.core.contracts import OracleEvaluation
from benchmarks.core.metrics import preservation_vacuous

# The metric a domain reports its repair rate under. One table rather than two
# lists, so a domain cannot be admitted by `for_domain` and then KeyError here.
DOMAIN_PREFIXES = {
    "connectivity": "path",
    "dhcp_dns": "provisioning",
    "filtering": "policy",
    "qos": "quality",
    "security": "control",
}


def for_domain(domain: str):
    if domain not in DOMAIN_PREFIXES:
        raise KeyError(domain)
    return lambda evaluations, sut_result: domain_metrics(domain, evaluations, sut_result)


def domain_metrics(domain: str, evaluations: Mapping[str, OracleEvaluation], sut_result: Mapping[str, Any]) -> dict[str, Any]:
    repair = evaluations.get("repair")
    preservation = evaluations.get("preservation")
    probes = tuple(repair.probes) if repair else ()
    prefix = DOMAIN_PREFIXES[domain]
    return {
        "domain": domain,
        f"{prefix}_repair_pass_rate": (
            sum(probe.passed for probe in probes) / len(probes) if probes else 0.0
        ),
        "non_regression_passed": preservation.passed if preservation else None,
        "non_regression_vacuous": preservation_vacuous(preservation),
        "sut_verified": bool(sut_result.get("verified")),
    }
