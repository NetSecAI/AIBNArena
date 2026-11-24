from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.platforms.containerlab import MANAGEMENT_PLANE_RULE
from sut.common.repeated_requests import REPEATED_REQUEST_KEY
from sut.langchain_agent.ani_tools import LangChainANIAdapter


class FakeANI:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def dispatch(self, operation, arguments, *, task=None):
        self.calls.append((operation, arguments, task))
        return {**self.result, "operation": operation}


def test_tools_are_langchain_wrappers_around_independent_ani(tmp_path: Path):
    ani = FakeANI({"ok": True, "nodes": [{"name": "leaf1"}]})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    result = json.loads(tools["get_topology"].invoke({"nodes": ["leaf1"]}))
    assert set(tools) == {"get_topology", "get_state", "get_running_config", "get_object", "update_config", "update_object", "execute_validation", "rollback_config", "submit_result"}
    assert ani.calls[0][0] == "get_topology"
    assert result["context"]["truncated"] is False
    assert adapter.trace.operations[0]["category"] == "read"


def test_tools_expose_the_full_strict_ani_schema(tmp_path: Path):
    adapter = LangChainANIAdapter(FakeANI({"ok": True}), task={}, artifact_directory=tmp_path)
    schemas = {item.name: item.args_schema.model_json_schema() for item in adapter.tools()}

    state_views = schemas["get_state"]["properties"]["views"]["anyOf"][0]["items"]
    assert state_views["enum"] == ["interfaces", "routes", "qdisc", "system"]

    update = schemas["update_config"]
    assert update["required"] == ["changes"]
    assert update["$defs"]["ConfigChange"]["required"] == ["target", "commands"]

    validation_type = schemas["execute_validation"]["$defs"]["ValidationCheck"]["properties"]["type"]
    assert validation_type["enum"] == ["public_success_criteria", "icmp"]
    assert all(schema["additionalProperties"] is False for schema in schemas.values())


def test_the_json_format_is_described_for_every_platform_it_serves(tmp_path: Path):
    """"JSON for whole-device structured SR Linux data" until 2026-11-15: on main03 the JSON
    read was the one read of this surface that showed a Linux node's QoS classes."""
    adapter = LangChainANIAdapter(FakeANI({"ok": True}), task={}, artifact_directory=tmp_path)
    schemas = {item.name: item.args_schema.model_json_schema() for item in adapter.tools()}
    text = schemas["get_running_config"]["properties"]["format"]["description"]
    for platform in ("SR Linux", "VyOS", "Arista EOS", "on Linux"):
        assert platform in text, platform
    assert "tc qdisc, class and filter for each interface" in text


def test_the_writes_state_the_management_plane_rule_before_the_first_call(tmp_path: Path):
    adapter = LangChainANIAdapter(FakeANI({"ok": True}), task={}, artifact_directory=tmp_path)
    descriptions = {item.name: item.description for item in adapter.tools()}
    assert MANAGEMENT_PLANE_RULE in descriptions["update_config"]
    assert MANAGEMENT_PLANE_RULE in descriptions["update_object"]
    assert descriptions["update_config"].startswith("Apply guarded native configuration changes")


def test_submit_result_is_offered_as_the_ani_offers_it(tmp_path: Path):
    from benchmarks.platforms.containerlab import ANI_OPERATIONS, ContainerLabANI

    assert "submit_result" in ANI_OPERATIONS
    adapter = LangChainANIAdapter(FakeANI({"ok": True}), task={}, artifact_directory=tmp_path)
    tool = {item.name: item for item in adapter.tools()}["submit_result"]
    ani_schema = next(schema["function"] for schema in ContainerLabANI.tool_schemas()
                      if schema["function"]["name"] == "submit_result")
    schema = tool.args_schema.model_json_schema()
    assert tool.description == ani_schema["description"]
    assert schema["required"] == ani_schema["parameters"]["required"]
    assert schema["properties"]["status"]["enum"] == ani_schema["parameters"]["properties"]["status"]["enum"]
    for name in ("summary", "diagnosis"):
        assert schema["properties"][name]["description"] == ani_schema["parameters"]["properties"][name]["description"]


def test_an_accepted_submission_is_recorded_and_a_refused_one_is_not(tmp_path: Path):
    refused = LangChainANIAdapter(FakeANI({"ok": False, "error": "bad status"}), task={}, artifact_directory=tmp_path)
    {item.name: item for item in refused.tools()}["submit_result"].invoke({"status": "failed", "summary": "x"})
    assert refused.trace.submission is None
    assert refused.trace.operations[0]["category"] == "submission"

    accepted = LangChainANIAdapter(
        FakeANI({"ok": True, "submitted": True, "status": "failed", "summary": "x", "diagnosis": None}),
        task={}, artifact_directory=tmp_path)
    {item.name: item for item in accepted.tools()}["submit_result"].invoke({"status": "failed", "summary": "x"})
    assert accepted.trace.submission == {"status": "failed", "summary": "x", "diagnosis": None}


def test_large_result_reports_truncation_and_artifact(tmp_path: Path):
    ani = FakeANI({"ok": True, "nodes": [{"name": f"node-{index}", "config": "x" * 400} for index in range(30)]})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path, max_context_chars=512, default_page_size=2)
    output = json.loads(adapter.tools()[0].invoke({"offset": 0, "limit": 2}))
    context = output["context"]
    assert context["truncated"] is True
    assert context["complete_size_bytes"] > context["returned_size_bytes"]
    assert context["page"] == {"offset": 0, "limit": 2, "total_items": 30}
    assert Path(context["artifact_ref"]).is_file()
    assert context["sha256"] in Path(context["artifact_ref"]).name
    assert adapter.trace.operations[0]["artifact_ref"] == context["artifact_ref"]


def test_operation_categories_never_call_reads_configuration_actions(tmp_path: Path):
    ani = FakeANI({"ok": True, "checks": [{"type": "public_success_criteria", "passed": True}]})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]})
    assert adapter.trace.operations[0]["category"] == "validation"
    assert adapter.trace.final_validation["passed"] is True


def _long_public_check() -> dict:
    """An E21 campus_large QoS public check: the verdict depends on items far down the result."""
    pings = [{"source": f"host-access-{index}", "destination": "web-dmz", "success": True,
              "packet_loss_percent": 0.0, "output": "64 bytes from 172.16.2.10 " * 12} for index in range(30)]
    pings.append({"source": "phone-access-7", "destination": "host-access-2", "success": False,
                  "packet_loss_percent": 100.0, "output": "Destination Host Unreachable"})
    return {"ok": False, "checks": [{"type": "public_success_criteria", "passed": False, "checks": [
        {"type": "lab_connectivity", "passed": False, "connectivity": pings},
        {"type": "observed_throughput", "passed": False,
         "measurement": {"throughput_mbps": 3.1, "circuit_egress_mbps": 9.9},
         "limits": {"min_mbps": 8.0, "enforced_floor_mbps": 6.8}},
    ]}]}


def test_a_long_validation_result_reaches_the_model_whole_with_its_verdict_first(tmp_path: Path):
    """E21: sorted and cut at max_context_chars, the verdict and the failing items of a long public
    check were past the cut, so LangChain read only passing pings and submitted completed."""
    adapter = LangChainANIAdapter(FakeANI(_long_public_check()), task={}, artifact_directory=tmp_path,
                                  max_context_chars=512)
    tools = {item.name: item for item in adapter.tools()}
    output = json.loads(tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]}))
    data = output["data"]
    assert output["context"]["truncated"] is False
    assert len(data) > 512
    assert data.startswith('{"ok":false')
    assert data.index('"passed":false') < data.index('"connectivity"')
    assert '"source":"phone-access-7"' in data and '"throughput_mbps":3.1' in data
    assert json.loads(data) == {**_long_public_check(), "operation": "execute_validation"}


def test_a_read_of_the_same_size_is_still_cut_and_paged(tmp_path: Path):
    ani = FakeANI({"ok": True, "nodes": [{"name": f"node-{index}", "config": "x" * 400} for index in range(30)]})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path, max_context_chars=512)
    output = json.loads({item.name: item for item in adapter.tools()}["get_topology"].invoke({}))
    assert output["context"]["truncated"] is True
    assert len(output["data"]) == 512


def test_a_third_identical_failed_request_is_answered_with_a_note_and_nothing_ends(tmp_path: Path):
    """Until 2026-10-30 the third ended the episode (E20)."""
    ani = FakeANI({"ok": False, "error": "invalid request"})
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path)
    tool = {item.name: item for item in adapter.tools()}["get_state"]

    answers = [json.loads(tool.invoke({"nodes": ["leaf1"], "views": ["routes"]})) for _ in range(4)]

    assert [REPEATED_REQUEST_KEY in answer for answer in answers] == [False, False, True, True]
    assert "failed 3 times" in answers[2][REPEATED_REQUEST_KEY]
    assert "failed 4 times" in answers[3][REPEATED_REQUEST_KEY]
    assert adapter.trace.failed_operations == 4
    assert len(adapter.trace.operations) == 4
    assert adapter.trace.repeated_requests.notes == [
        {"operation": "get_state", "counted": [0, 1, 2]},
        {"operation": "get_state", "counted": [0, 1, 2, 3]}]


def _leaf1_acl(lines=1500):
    """leaf1's `acl` block as E18 had it: the injected filter after the default CPM filters."""
    text = "\n".join([f"    cpm-filter ipv4-filter entry {number} {{ action {{ accept {{ }} }} }}"
                      for number in range(1, lines)]
                     + ["    acl-filter ibn-subnet-guard type ipv4 {", "        entry 10 { action { drop { } } }",
                        "    }"]) + "\n"
    return FakeANI({"ok": True, "nodes": [{"name": "leaf1", "kind": "nokia_srlinux", "config": [
        {"path": "acl", "result": {"ok": True, "stdout": text, "stderr": ""}}]}]})


def _stdout(output):
    return json.loads(output["data"])["nodes"][0]["config"][0]["result"]["stdout"]


def test_a_cut_text_read_says_how_many_lines_there_are_and_how_to_read_them(tmp_path: Path):
    adapter = LangChainANIAdapter(_leaf1_acl(), task={}, artifact_directory=tmp_path)
    read = {item.name: item for item in adapter.tools()}["get_running_config"]
    output = json.loads(read.invoke({"nodes": ["leaf1"], "paths": ["acl"]}))

    assert output["context"]["truncated"] is True
    assert "ibn-subnet-guard" not in output["data"], "the filter sits after the first 12 000 characters"
    assert output["context"]["lines"]["texts"] == [
        {"node": "leaf1", "field": "config/0/result/stdout", "total": 1502}]
    assert "line_filter" in output["context"]["read_further"]
    assert "paging" not in adapter.trace.operations[0]


def test_line_filter_keeps_the_matching_lines_with_their_numbers(tmp_path: Path):
    """E18 drop_traffic m3 and m4: the 27B sent filter_text="acl-filter" and got the CPM filters again."""
    adapter = LangChainANIAdapter(_leaf1_acl(), task={}, artifact_directory=tmp_path)
    read = {item.name: item for item in adapter.tools()}["get_running_config"]

    by_device = json.loads(read.invoke({"nodes": ["leaf1"], "paths": ["acl"], "filter_text": "acl-filter"}))
    assert "ibn-subnet-guard" not in by_device["data"], "filter_text keeps the device whole, as before"

    output = json.loads(read.invoke({"nodes": ["leaf1"], "paths": ["acl"], "line_filter": "ACL-FILTER"}))
    assert _stdout(output) == "1500:    acl-filter ibn-subnet-guard type ipv4 {"
    assert output["context"]["lines"]["texts"][0] == {
        "node": "leaf1", "field": "config/0/result/stdout", "total": 1502, "matching": 1, "returned": 1}
    assert output["context"]["truncated"] is False
    assert "read_further" not in output["context"]
    assert adapter.trace.operations[1]["paging"] == {"line_filter": "ACL-FILTER"}


def test_line_offset_and_limit_page_inside_the_text(tmp_path: Path):
    adapter = LangChainANIAdapter(_leaf1_acl(), task={}, artifact_directory=tmp_path)
    read = {item.name: item for item in adapter.tools()}["get_running_config"]

    first = json.loads(read.invoke({"nodes": ["leaf1"], "paths": ["acl"], "line_limit": 2}))
    assert _stdout(first).splitlines() == [
        "1:    cpm-filter ipv4-filter entry 1 { action { accept { } } }",
        "2:    cpm-filter ipv4-filter entry 2 { action { accept { } } }"]
    assert first["context"]["truncated"] is True, "1500 lines are left"

    last = json.loads(read.invoke({"nodes": ["leaf1"], "paths": ["acl"], "line_offset": 1499, "line_limit": 60}))
    assert _stdout(last).splitlines() == [
        "1500:    acl-filter ibn-subnet-guard type ipv4 {", "1501:        entry 10 { action { drop { } } }",
        "1502:    }"]
    assert last["context"]["truncated"] is False, "the last page leaves nothing out"
    assert adapter.trace.operations[1]["paging"] == {"line_offset": 1499, "line_limit": 60}


def test_a_device_limit_still_pages_devices_not_lines(tmp_path: Path):
    """LangChain passed limit, usually 100, on 859 of its 1256 one-device reads in E16 to E18."""
    adapter = LangChainANIAdapter(_leaf1_acl(lines=10), task={}, artifact_directory=tmp_path)
    read = {item.name: item for item in adapter.tools()}["get_running_config"]
    output = json.loads(read.invoke({"nodes": ["leaf1"], "paths": ["acl"], "limit": 100}))
    assert _stdout(output).startswith("    cpm-filter"), "the text is untouched and unnumbered"
    assert "lines" not in output["context"]
    assert adapter.trace.operations[0]["paging"] == {"limit": 100}


def test_the_topology_offers_no_line_paging(tmp_path: Path):
    adapter = LangChainANIAdapter(FakeANI({"ok": True, "nodes": []}), task={}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    assert "line_filter" not in tools["get_topology"].args
    assert {"line_filter", "line_offset", "line_limit"} <= set(tools["get_state"].args)
    assert {"line_filter", "line_offset", "line_limit"} <= set(tools["get_running_config"].args)


#: switch-access-1's `show interfaces status` on campus_small (cEOS 4.36.1F): EOS
#: writes Ethernet3 as Et3 in it.
EOS_STATUS = (
    "Port       Name                         Status       Vlan     Duplex Speed  Type            Flags Encapsulation\n"
    "Et1        Trunk to firewall            connected    trunk    full   1G     EbraTestPhyPort                   \n"
    "Et3        Access port to host-access-1 connected    100      full   1G     EbraTestPhyPort                   \n"
    "Et4        Access port to host-access-2 connected    200      full   1G     EbraTestPhyPort                   \n"
    "Ma0                                     connected    routed   a-full a-1G   10/100/1000                       \n")


def _campus_state():
    return FakeANI({"ok": True, "nodes": [
        {"name": "switch-access-1", "kind": "arista_ceos",
         "views": {"interfaces": {"ok": True, "stdout": EOS_STATUS, "stderr": ""}}},
        {"name": "firewall", "kind": "vyos",
         "views": {"interfaces": {"ok": True, "stdout": "Interface  IP Address\neth3       172.16.2.254/24\n",
                                  "stderr": ""}}}]})


def test_an_interface_name_finds_its_line_in_either_eos_spelling(tmp_path: Path):
    """B9 live check, 2026-10-27: line_filter "Ethernet3" found nothing in the status view."""
    adapter = LangChainANIAdapter(_campus_state(), task={}, artifact_directory=tmp_path)
    read = {item.name: item for item in adapter.tools()}["get_state"]

    output = json.loads(read.invoke({"views": ["interfaces"], "line_filter": "Ethernet3"}))
    switch, firewall = json.loads(output["data"])["nodes"]
    assert switch["views"]["interfaces"]["stdout"] == "3:" + EOS_STATUS.splitlines()[2]
    assert firewall["views"]["interfaces"]["stdout"] == "", "VyOS's eth3 is not Ethernet3"

    management = json.loads(read.invoke({"views": ["interfaces"], "line_filter": "management0"}))
    assert json.loads(management["data"])["nodes"][0]["views"]["interfaces"]["stdout"].startswith("5:Ma0 ")

    config = LangChainANIAdapter(FakeANI({"ok": True, "nodes": [{"name": "switch-access-1", "config": [
        {"path": "/", "result": {"ok": True, "stdout": "interface Ethernet3\n   switchport access vlan 100\n"
                                                       "interface Ethernet4\n", "stderr": ""}}]}]}),
        task={}, artifact_directory=tmp_path)
    by_short_name = json.loads({item.name: item for item in config.tools()}["get_running_config"].invoke(
        {"nodes": ["switch-access-1"], "line_filter": "Et3"}))
    assert _stdout(by_short_name) == "1:interface Ethernet3"


def test_filter_text_keeps_the_switch_whose_table_writes_the_short_name(tmp_path: Path):
    adapter = LangChainANIAdapter(_campus_state(), task={}, artifact_directory=tmp_path)
    read = {item.name: item for item in adapter.tools()}["get_state"]
    output = json.loads(read.invoke({"views": ["interfaces"], "filter_text": "Ethernet3"}))
    assert [node["name"] for node in json.loads(output["data"])["nodes"]] == ["switch-access-1"]
    assert "Et3" in output["data"], "the switch is kept whole, in its own words"


def test_only_eos_short_interface_names_are_widened():
    from sut.langchain_agent.ani_tools import _containing

    assert _containing("Port-Channel1")("Et1        in Po1")
    assert _containing("Vlan100")("Vl100      Corp SVI")
    assert not _containing("Ethernet3")("ethernet-1/3"), "an SR Linux port"
    assert not _containing("Ethernet3")("reset3"), "a word is not an interface"
    assert _containing("et3")("reset3"), "what matched before still matches"


def test_every_update_config_example_passes_its_platforms_preflight(tmp_path: Path):
    """The examples are what the model copies; one the ANI refused would teach a refusal."""
    from benchmarks.platforms.containerlab.ani import SUPPORTED_WRITE_KINDS
    from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
    from benchmarks.platforms.containerlab.safety import validate_linux_config_command
    from sut.langchain_agent.ani_tools import UPDATE_CONFIG_EXAMPLES

    assert set(UPDATE_CONFIG_EXAMPLES) == SUPPORTED_WRITE_KINDS, "one example per write platform"
    ContainerLabExecutor.parse_srl_cli_batch(UPDATE_CONFIG_EXAMPLES["nokia_srlinux"][1])
    ContainerLabExecutor.parse_vyos_cli_batch(UPDATE_CONFIG_EXAMPLES["vyos"][1])
    ContainerLabExecutor.parse_eos_cli_batch(UPDATE_CONFIG_EXAMPLES["arista_ceos"][1])
    (linux,) = UPDATE_CONFIG_EXAMPLES["linux"][1]
    validate_linux_config_command(linux)

    description = {item.name: item for item in LangChainANIAdapter(
        FakeANI({"ok": True}), task={}, artifact_directory=tmp_path).tools()}["update_config"].description
    for label, commands, _ in UPDATE_CONFIG_EXAMPLES.values():
        assert f"{label} {json.dumps(commands)}" in description
    assert "get_running_config" in description, "reads are sent to the read tools"
    assert MANAGEMENT_PLANE_RULE in description


def test_update_object_sends_arista_writes_to_update_config(tmp_path: Path):
    """The ANI compiles no object write for arista_ceos, of any kind."""
    from benchmarks.platforms.containerlab.object_writes import UnsupportedUpdate, compile_update

    for kind in ("interface", "route", "acl", "qos"):
        with pytest.raises(UnsupportedUpdate, match="arista_ceos node has no"):
            compile_update("arista_ceos", kind, "Ethernet3", {"admin_state": "enable"} if kind == "interface"
                           else {"delete": True} if kind in ("route", "acl") else {"delete_root": True})
    description = {item.name: item for item in LangChainANIAdapter(
        FakeANI({"ok": True}), task={}, artifact_directory=tmp_path).tools()}["update_object"].description
    assert "Arista EOS switches have no objects here: change them through update_config." in description


class ScriptedANI:
    """One result per call, in order: an episode's answers written out."""

    def __init__(self, *results):
        self.results = list(results)

    def dispatch(self, operation, arguments, *, task=None):
        return {**self.results.pop(0), "operation": operation}


FAILED_VALIDATION = {"ok": False, "checks": [{"type": "public_success_criteria", "passed": False}]}
APPLIED_WRITE = {"ok": True, "transaction_id": "tx-1",
                 "changes": [{"target": "web1", "commands": ["ip route add default via 10.10.40.1"],
                              "result": {"ok": True, "safe": True}}]}
VALIDATION = {"checks": [{"type": "public_success_criteria"}]}
WRITE = {"changes": [{"target": "web1", "commands": ["ip route add default via 10.10.40.1"]}]}


def test_an_applied_write_starts_the_count_again(tmp_path: Path):
    """E17 remove_ip.m2: validate, write, validate, write, validate, stopped at 1076 s of 3600."""
    ani = ScriptedANI(FAILED_VALIDATION, FAILED_VALIDATION, APPLIED_WRITE,
                      FAILED_VALIDATION, FAILED_VALIDATION, FAILED_VALIDATION)
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}

    tools["execute_validation"].invoke(VALIDATION)
    tools["execute_validation"].invoke(VALIDATION)
    tools["update_config"].invoke(WRITE)
    tools["execute_validation"].invoke(VALIDATION)
    tools["execute_validation"].invoke(VALIDATION)
    assert adapter.trace.repeated_requests.resets == [
        {"write": 2, "cleared": [{"operation": "execute_validation", "counted": [0, 1]}]}]
    assert adapter.trace.repeated_requests.notes == []
    third = json.loads(tools["execute_validation"].invoke(VALIDATION))
    assert REPEATED_REQUEST_KEY in third
    assert adapter.trace.repeated_requests.notes == [
        {"operation": "execute_validation", "counted": [3, 4, 5]}]


def test_a_refused_write_or_a_no_op_resets_nothing(tmp_path: Path):
    refused = {"ok": False, "error": "failed backend safety preflight"}
    no_op = {"ok": True, "noop": True, "transaction_id": None, "changes": []}
    ani = ScriptedANI(FAILED_VALIDATION, refused, no_op, FAILED_VALIDATION, FAILED_VALIDATION)
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}

    tools["execute_validation"].invoke(VALIDATION)
    tools["update_config"].invoke(WRITE)
    tools["update_object"].invoke({"node": "leaf1", "kind": "interface", "name": "ethernet-1/30",
                                   "set": {"admin_state": "enable"}})
    tools["execute_validation"].invoke(VALIDATION)
    tools["execute_validation"].invoke(VALIDATION)

    assert adapter.trace.repeated_requests.notes == [
        {"operation": "execute_validation", "counted": [0, 3, 4]}]
    assert adapter.trace.repeated_requests.resets == []


def test_get_object_is_a_read_of_one_named_thing(tmp_path: Path):
    ani = FakeANI({"ok": True, "node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "found": True,
                   "output": "admin-state enable"})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    result = json.loads(tools["get_object"].invoke({"node": "leaf2", "kind": "interface", "name": "ethernet-1/40"}))
    assert ani.calls[0][0] == "get_object"
    assert ani.calls[0][1] == {"node": "leaf2", "kind": "interface", "name": "ethernet-1/40"}
    assert json.loads(result["data"])["found"] is True
    assert adapter.trace.operations[0]["category"] == "read"


def test_update_object_is_a_mutation_with_its_own_arguments(tmp_path: Path):
    ani = FakeANI({"ok": True, "transaction_id": "ani_tx_1", "changes": [{"target": "leaf2", "commands": ["set / interface ethernet-1/40 admin-state enable"], "result": {"ok": True, "safe": True}}],
                   "rollback_available": True, "compiled": {"commands": ["set / interface ethernet-1/40 admin-state enable"]}})
    adapter = LangChainANIAdapter(ani, task={"intent": "repair"}, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    result = json.loads(tools["update_object"].invoke({"node": "leaf2", "kind": "interface", "name": "ethernet-1/40", "set": {"admin_state": "enable"}}))
    assert ani.calls[0][0] == "update_object"
    assert ani.calls[0][1]["set"] == {"admin_state": "enable"}
    assert json.loads(result["data"])["transaction_id"] == "ani_tx_1"
    assert adapter.trace.operations[0]["category"] == "mutation"


def test_update_object_names_every_attribute_the_ani_accepts():
    """The model sees this description, not the ANI's own: a word missing here is one it never uses.

    The two drifted apart once already -- address_action lost its values, and the model
    kept sending `replace`, which the ANI refuses (NetRepairArena gpt-5.4, 2026-09-25).
    """
    from benchmarks.platforms.containerlab.object_writes import ATTRIBUTES, OBJECT_WRITE_KINDS
    from sut.langchain_agent.ani_tools import UpdateObjectArguments

    described = UpdateObjectArguments.model_fields["set"].description
    missing = sorted(f"{kind}.{attribute}" for kind, attributes in ATTRIBUTES.items()
                     for attribute in attributes if attribute not in described)
    assert missing == []
    assert set(UpdateObjectArguments.model_fields["kind"].annotation.__args__) == set(OBJECT_WRITE_KINDS)


PASSED_VALIDATION = {"ok": True, "checks": [{"type": "public_success_criteria", "passed": True}]}
ACCEPTED = {"ok": True, "submitted": True, "status": "completed", "summary": "x", "diagnosis": None}
COMPLETED = {"status": "completed", "summary": "x"}


def test_a_completed_is_sent_back_when_a_write_follows_the_passing_validation(tmp_path: Path):
    """E19 wan_shaping m1: the circuit's root deleted, then `completed`. A pass the
    network has moved on from backs nothing; the next submission goes through."""
    import time

    ani = ScriptedANI(PASSED_VALIDATION, APPLIED_WRITE, ACCEPTED)
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path,
                                  deadline=time.monotonic() + 3600)
    tools = {item.name: item for item in adapter.tools()}
    tools["execute_validation"].invoke(VALIDATION)
    tools["update_config"].invoke(WRITE)
    tools["submit_result"].invoke(COMPLETED)
    assert adapter.trace.submission is None, "sent back, never dispatched"
    assert adapter.trace.unverified_completion_pushback["reason"] == \
        "the network changed after the last execute_validation"
    assert adapter.trace.operations[-1]["category"] == "submission"
    assert adapter.trace.operations[-1]["ok"] is False
    tools["submit_result"].invoke(COMPLETED)
    assert adapter.trace.submission == {"status": "completed", "summary": "x", "diagnosis": None}


def test_a_validated_or_undeadlined_completed_goes_straight_through(tmp_path: Path):
    import time

    validated = LangChainANIAdapter(ScriptedANI(PASSED_VALIDATION, ACCEPTED), task={},
                                    artifact_directory=tmp_path, deadline=time.monotonic() + 3600)
    tools = {item.name: item for item in validated.tools()}
    tools["execute_validation"].invoke(VALIDATION)
    tools["submit_result"].invoke(COMPLETED)
    assert validated.trace.submission is not None
    assert validated.trace.unverified_completion_pushback is None

    no_episode = LangChainANIAdapter(ScriptedANI(ACCEPTED), task={}, artifact_directory=tmp_path)
    {item.name: item for item in no_episode.tools()}["submit_result"].invoke(COMPLETED)
    assert no_episode.trace.submission is not None


FAILED = {"status": "failed", "summary": "no safe repair found"}
SUBMITTED_FAILED = {"ok": True, "submitted": True, "status": "failed", "summary": "x", "diagnosis": None}


def test_a_failed_is_sent_back_once_with_more_than_half_the_budget_left(tmp_path: Path):
    """E20 disable_interface.m1: `failed` at 31 s of 3600, never dispatched now; the
    shared loop has sent such a `failed` back since E15."""
    import time

    ani = ScriptedANI(SUBMITTED_FAILED)
    adapter = LangChainANIAdapter(ani, task={}, artifact_directory=tmp_path,
                                  deadline=time.monotonic() + 3600, budget_seconds=3600)
    tools = {item.name: item for item in adapter.tools()}
    answer = json.loads(json.loads(tools["submit_result"].invoke(FAILED))["data"])
    assert answer["submitted"] is False
    assert "more than half" in answer["error"]
    assert adapter.trace.submission is None
    assert adapter.trace.early_failure_pushback["budget_seconds"] == 3600
    tools["submit_result"].invoke(FAILED)
    assert adapter.trace.submission == {"status": "failed", "summary": "x", "diagnosis": None}


def test_a_late_or_undeadlined_failed_goes_straight_through(tmp_path: Path):
    import time

    late = LangChainANIAdapter(ScriptedANI(SUBMITTED_FAILED), task={}, artifact_directory=tmp_path,
                               deadline=time.monotonic() + 1000, budget_seconds=3600)
    {item.name: item for item in late.tools()}["submit_result"].invoke(FAILED)
    assert late.trace.submission is not None
    assert late.trace.early_failure_pushback is None

    no_episode = LangChainANIAdapter(ScriptedANI(SUBMITTED_FAILED), task={}, artifact_directory=tmp_path)
    {item.name: item for item in no_episode.tools()}["submit_result"].invoke(FAILED)
    assert no_episode.trace.submission is not None
