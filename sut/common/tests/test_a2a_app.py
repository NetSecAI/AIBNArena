from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCH_ROOT = Path(__file__).resolve().parents[3]
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from sut.common.a2a_app import build_ani


def test_live_ani_requires_an_explicit_topology_pair() -> None:
    with pytest.raises(ValueError, match="live ANI requires explicit"):
        build_ani(None, None)


def test_dry_run_may_remain_unconfigured_without_selecting_a_default_lab() -> None:
    assert build_ani(None, None, allow_unconfigured=True) is None


@pytest.mark.parametrize(
    ("topology", "healthy"),
    [("topology.yaml", None), (None, "healthy.json")],
)
def test_partial_topology_binding_is_always_rejected(
    topology: str | None, healthy: str | None
) -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        build_ani(topology, healthy, allow_unconfigured=True)


def test_the_report_lists_every_tool_call_with_the_arguments_the_model_chose() -> None:
    from sut.common.a2a_app import format_self_execute_report

    report = format_self_execute_report({
        "status": "completed", "verified": True, "device_changes": [],
        "ani_operations": [
            {"operation": "get_topology", "category": "read", "ok": True,
             "duration_seconds": 0.0001, "arguments": {"nodes": None}},
            {"operation": "get_state", "category": "read", "ok": True, "duration_seconds": 3.25,
             "arguments": {"nodes": ["leaf2"], "views": ["routes", "interfaces"]}},
            {"operation": "execute_validation", "category": "validation", "ok": False,
             "duration_seconds": 30.4, "arguments": {"checks": [{"type": "public_success_criteria"}]}},
            {"operation": "update_object", "category": "mutation", "ok": False,
             "duration_seconds": 1.0, "arguments": {"target": "leaf2", "path": "x" * 400},
             "error": "refused"},
        ],
    })
    lines = report.splitlines()
    start = lines.index("Tool calls")
    assert lines[start + 2:start + 5] == [
        '1. [read] get_topology {"nodes": null} · ok=True · 0.0s',
        '2. [read] get_state {"nodes": ["leaf2"], "views": ["routes", "interfaces"]} · ok=True · 3.2s',
        '3. [validation] execute_validation {"checks": [{"type": "public_success_criteria"}]} · ok=False · 30.4s',
    ]
    # A long argument is cut, and an error is named.
    assert lines[start + 5].startswith('4. [mutation] update_object {"target": "leaf2", "path": "xxx')
    assert lines[start + 5].endswith(" · ok=False · 1.0s · error: refused")
    assert len(lines[start + 5]) < 330
