"""What an operation summary keeps from the ANI's own answer.

`ani_operations` is the record a result file carries. The model sees the whole tool
result; the record sees this summary, which keeps what the call was and how it went
and never the payload a read returned.
"""
import copy

from sut.common.ani_report import operation_summary, public_task_for_model


def test_a_read_is_unchanged() -> None:
    summary = operation_summary("get_routes", {"ok": True, "routes": []}, 0.1)

    assert set(summary) == {"operation", "category", "ok", "duration_seconds",
                            "transaction_id", "error"}


#: A QoS task as the judge hands it to a subject from main04 on: the protected flow, and the
#: rate it measured on the healthy network, for the ANI's throughput check.
QOS_TASK = {
    "intent": "Under contention, traffic from host-access-1 to srv-site measures below its contractual 8 Mbps.",
    "success_criteria": {"all_of": [{"type": "observed_throughput", "min_mbps": 8}]},
    "observation": {"service_measurement": {"source": "host-access-1", "destination": "srv-site",
                                            "destination_ip": "10.1.0.10", "healthy_throughput_mbps": 7.651}},
    "execution_budget": {"wall_clock_seconds": 3600},
}


def test_the_model_reads_the_flow_but_not_the_healthy_rate_its_ani_holds_it_to() -> None:
    """In main04 the model read `healthy_throughput_mbps` as the rate under the fault in turn 1
    of all 8 QoS episodes. The ANI needs it, the model does not."""
    task = copy.deepcopy(QOS_TASK)

    public = public_task_for_model(task)

    assert public["observation"] == {"service_measurement": {
        "source": "host-access-1", "destination": "srv-site", "destination_ip": "10.1.0.10"}}
    assert "7.651" not in repr(public)
    # The task the subject hands its ANI keeps the rate.
    assert task == QOS_TASK


def test_an_observation_with_nothing_judge_only_is_shown_whole() -> None:
    task = {"intent": "repair", "observation": {"connectivity": [], "service_measurement": {"source": "a"}}}

    assert public_task_for_model(task)["observation"] == task["observation"]
