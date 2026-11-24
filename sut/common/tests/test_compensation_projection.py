"""A compensated graph apply carries its rollback inside the same envelope; the
shared report projection must count those device mutations as actions."""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from sut.common.ani_report import device_changes_from_ani_result  # noqa: E402


def _compensated_envelope():
    return {
        "ok": False, "operation": "update_config", "transaction_id": "ani_tx_1",
        "changes": [
            {"target": "wan1", "commands": ["tc class replace ..."], "result": {"ok": True}},
            {"target": "wan1", "commands": ["tc filter add ..."], "result": {"ok": False}},
        ],
        "rollback": {
            "ok": True, "operation": "rollback_config", "transaction_id": "ani_tx_1",
            "changes": [{"target": "wan1", "commands": ["tc class replace old"], "result": {"ok": True}}],
        },
        "graph_plan": {"applied": False, "rolled_back": True},
    }


def test_nested_compensation_is_projected_as_rollback_actions():
    actions = device_changes_from_ani_result(_compensated_envelope())
    assert [a["operation"] for a in actions] == ["update_config", "update_config", "rollback_config"]
    assert actions[-1]["action"]["command"] == "tc class replace old"
    assert actions[-1]["transaction_id"] == "ani_tx_1"


def test_a_rollback_key_that_is_not_a_rollback_envelope_is_ignored():
    envelope = _compensated_envelope()
    envelope["rollback"] = {"ok": True, "operation": "update_config", "changes": [
        {"target": "wan1", "commands": ["never"], "result": {"ok": True}}]}
    actions = device_changes_from_ani_result(envelope)
    assert [a["operation"] for a in actions] == ["update_config", "update_config"]
