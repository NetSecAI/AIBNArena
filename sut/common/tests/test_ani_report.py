"""What an operation summary keeps from the ANI's own answer.

`ani_operations` is the record a result file carries. The model sees the whole tool
result; the record sees this summary, which keeps what the call was and how it went
and never the payload a read returned.
"""
from sut.common.ani_report import operation_summary


def test_a_read_is_unchanged() -> None:
    summary = operation_summary("get_routes", {"ok": True, "routes": []}, 0.1)

    assert set(summary) == {"operation", "category", "ok", "duration_seconds",
                            "transaction_id", "error"}
