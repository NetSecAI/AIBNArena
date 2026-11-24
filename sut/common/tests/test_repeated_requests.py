"""The identical-failure rule every subject now shares (sut.common.repeated_requests)."""
from __future__ import annotations

from sut.common.repeated_requests import RepeatedRequests


def test_the_third_identical_failure_gets_the_note_and_every_one_after_it():
    rule = RepeatedRequests()
    notes = [rule.failed("get_state", {"nodes": ["leaf1"]}, "invalid request", index) for index in range(4)]

    assert [note is not None for note in notes] == [False, False, True, True]
    assert "failed 3 times" in notes[2] and "failed 4 times" in notes[3]
    assert rule.notes == [{"operation": "get_state", "counted": [0, 1, 2]},
                          {"operation": "get_state", "counted": [0, 1, 2, 3]}]


def test_a_different_argument_or_error_is_a_different_request():
    rule = RepeatedRequests()
    rule.failed("get_state", {"nodes": ["leaf1"]}, "invalid request", 0)
    rule.failed("get_state", {"nodes": ["leaf2"]}, "invalid request", 1)
    rule.failed("get_state", {"nodes": ["leaf1"]}, "another error", 2)

    assert rule.notes == []


def test_a_failed_validation_counts_by_its_request_alone():
    """A failed execute_validation has no error, only its checks."""
    rule = RepeatedRequests()
    checks = {"checks": [{"type": "public_success_criteria"}]}
    assert [rule.failed("execute_validation", checks, None, index) for index in range(3)][-1] is not None


def test_a_change_starts_the_count_again_and_the_record_names_what_it_cleared():
    rule = RepeatedRequests()
    rule.failed("execute_validation", {}, None, 0)
    rule.failed("execute_validation", {}, None, 1)
    rule.changed(2)
    rule.failed("execute_validation", {}, None, 3)
    rule.changed(4)
    rule.changed(5)  # nothing counted since 4: nothing to record

    assert rule.record() == {
        "limit": 3, "action": "note", "notes": [],
        "resets": [{"write": 2, "cleared": [{"operation": "execute_validation", "counted": [0, 1]}]},
                   {"write": 4, "cleared": [{"operation": "execute_validation", "counted": [3]}]}]}
