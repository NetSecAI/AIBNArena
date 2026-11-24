"""Goldens for the ANI v0.1 surface, with a fake executor so no lab is needed.

The ANI is the only channel a SUT has to the network, and the Judge advertises its
operation list to every subject. Two things therefore have to stay pinned: the
surface is exactly six operations, and `get_running_config` keeps its text output
byte-compatible now that it also speaks JSON.

Run: python benchmarks/platforms/containerlab/tests/test_ani_contract.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab import (  # noqa: E402
    ANI_OPERATIONS,
    ANIRequestError,
    ContainerLabANI,
    ContainerLabEnv,
)
from benchmarks.platforms.containerlab import ani as ani_module  # noqa: E402
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology  # noqa: E402
from benchmarks.platforms.containerlab.types import CommandResult, ConnectivityCheck, Observation  # noqa: E402

TOPOLOGY = REPO_ROOT / "scenarios/topologies/sme_leaf_spine_dmz_small.yaml"
HEALTHY = REPO_ROOT / "benchmarks/testbeds/containerlab/sme01-small/states/healthy.json"

#: Shapes sr_cli actually returns, captured from clab-sme01-small-leaf2.
SRL_JSON = {
    "info from state system information | as json": {"description": "SRLinux", "last-booted": "..."},
    "info from state platform chassis | as json": {"type": "7220 IXR-D2L", "hw-mac-address": "1a:...."},
    "info from state /interface * | as json": {"interface": [{"name": "ethernet-1/40", "admin-state": "enable"}]},
    "info from state system lldp | as json": {"admin-state": "enable", "hello-timer": 30},
    "info from state /network-instance * | as json": {"network-instance": [{"name": "default"}]},
    "info from running | as json": {"_annotate": {}, "interface": [{"name": "ethernet-1/40"}]},
}


#: What a containerlab `linux` node answers, trimmed to one interface. `tc` is keyed
#: without its `dev <iface>` tail, which the fake strips before looking a command up.
SHELL = {
    "cat /etc/os-release": 'NAME="Alpine Linux"\nID=alpine\n',
    "uname -sr": "Linux 6.8.0-124-generic\n",
    "ip -o link show": "1: lo: <LOOPBACK> ...\n2: eth1@if9: <BROADCAST,UP> state UP\n",
    "ip -o -4 addr show": "2: eth1    inet 10.10.10.10/24 scope global eth1\n",
    "ip -o -6 addr show": "2: eth1    inet6 2001:db8::10/64 scope global\n",
    "ip -o -4 route show": "default via 10.10.10.1 dev eth1\n",
    "tc qdisc show": "qdisc htb 1: root refcnt 2 default 0x20\n",
    "tc class show": "class htb 1:1 root rate 10Mbit\n",
    "tc filter show": "filter parent 1: protocol ip pref 1 u32 flowid 1:10\n",
}


def srl_set(value: str) -> str:
    """A syntactically valid harmless-looking SR Linux fixture command."""
    return f"set / system name host-name {value}"


def build_ani(*, srl_json: dict[str, object] | None = None, fail: str | None = None) -> tuple[ContainerLabANI, list[str]]:
    """An ANI whose SR Linux transport is scripted; records the commands issued."""
    topology = CompiledContainerLabTopology.from_file(TOPOLOGY)
    env = ContainerLabEnv(topology.env_config(healthy_state_path=HEALTHY))
    issued: list[str] = []
    table = SRL_JSON if srl_json is None else srl_json

    def fake_run_srl_cli(node, command, timeout_seconds=None):
        issued.append(command)
        if fail is not None and fail in command:
            return CommandResult(target=node.name, command=command, returncode=1, stderr="boom")
        if command in table:
            return CommandResult(target=node.name, command=command, returncode=0,
                                 stdout=json.dumps(table[command]))
        return CommandResult(target=node.name, command=command, returncode=0,
                             stdout="admin-state enable\n")

    def fake_run_srl_cli_batch(node, commands, timeout_seconds=None):
        # The real one wraps the batch in `enter candidate` / `commit now`; record only
        # the caller's commands, which is what the validation is about.
        issued.extend(commands)
        if fail is not None and any(fail in command for command in commands):
            return CommandResult(target=node.name, command="\n".join(commands),
                                 returncode=1, stderr="boom")
        return CommandResult(target=node.name, command="\n".join(commands), returncode=0,
                             stdout=("All changes have been committed. "
                                     "Leaving candidate mode.\n"))

    def fake_run_shell(node, command, timeout_seconds=None):
        issued.append(command)
        if fail is not None and fail in command:
            return CommandResult(target=node.name, command=command, returncode=1,
                                 stderr="boom")
        return CommandResult(target=node.name, command=command, returncode=0,
                             stdout=SHELL.get(command.split(" dev ")[0] if " dev " in command
                                              else command, ""))

    env.executor.run_srl_cli = fake_run_srl_cli
    env.executor.run_srl_cli_batch = fake_run_srl_cli_batch
    env.executor.run_shell = fake_run_shell
    env.executor.run_linux_config = fake_run_shell
    return ContainerLabANI(env, topology_document=topology.document), issued


def test_surface_is_exactly_the_six_declared_operations():
    names = {schema["function"]["name"] for schema in ContainerLabANI.tool_schemas()}
    assert names == set(ANI_OPERATIONS), names
    assert len(ContainerLabANI.tool_contract()) == len(ANI_OPERATIONS)
    # A scenario-specific repair tool would make the benchmark trivial.
    assert "clear_linux_netem" not in names
    assert "enable_srl_interface" not in names
    assert "build_knowledge_graph" not in names


def test_get_running_config_declares_the_format_option():
    schema = next(s for s in ContainerLabANI.tool_schemas() if s["function"]["name"] == "get_running_config")
    properties = schema["function"]["parameters"]["properties"]
    assert set(properties) == {"nodes", "paths", "format"}
    assert properties["format"]["enum"] == ["text", "json"]
    assert schema["function"]["parameters"]["required"] == ["nodes"]


def test_text_format_is_the_default_and_unchanged():
    ani, issued = build_ani()
    result = ani.dispatch("get_running_config", {"nodes": ["leaf2"], "paths": ["/interface ethernet-1/40"]})

    assert sorted(result) == ["nodes", "ok", "operation"], "text mode must not gain keys"
    assert result["ok"] is True
    node = result["nodes"][0]
    assert sorted(node) == ["config", "kind", "name"]
    assert node["config"][0]["path"] == "/interface ethernet-1/40"
    assert node["config"][0]["result"]["stdout"] == "admin-state enable\n"
    assert issued == ["info from running /interface ethernet-1/40"]


def test_full_container_alias_is_canonicalized_for_every_read_surface():
    ani, issued = build_ani()
    full_name = ani.env.config.nodes["leaf2"].container_name

    topology = ani.dispatch("get_topology", {"nodes": [full_name]})
    state = ani.dispatch(
        "get_state", {"nodes": [full_name], "views": ["system"]})
    running = ani.dispatch(
        "get_running_config", {"nodes": [full_name], "paths": ["/"]})

    assert topology["nodes"][0]["name"] == "leaf2"
    assert state["ok"] is True and state["nodes"][0]["name"] == "leaf2"
    assert running["ok"] is True and running["nodes"][0]["name"] == "leaf2"
    assert issued == ["show version", "info from running /"]


def test_logical_and_full_container_alias_duplicates_are_rejected_deterministically():
    ani, issued = build_ani()
    full_name = ani.env.config.nodes["leaf2"].container_name
    errors = []

    for selected in (["leaf2", full_name], [full_name, "leaf2"]):
        try:
            ani.dispatch("get_state", {"nodes": selected, "views": ["system"]})
        except ANIRequestError as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("two aliases for one node must not duplicate evidence")

    assert errors == [
        "duplicate lab nodes after alias normalization: leaf2",
        "duplicate lab nodes after alias normalization: leaf2",
    ]
    assert issued == []


def test_get_state_aggregates_nested_failures_without_losing_node_evidence():
    ani, issued = build_ani(fail="show interface brief")
    result = ani.dispatch(
        "get_state", {"nodes": ["leaf2"], "views": ["interfaces", "routes"]})

    assert result["ok"] is False
    views = result["nodes"][0]["views"]
    assert views["interfaces"]["ok"] is False
    assert views["routes"]["ok"] is True
    assert issued == [
        "show interface brief",
        "info from state network-instance default route-table ipv4-unicast",
    ]


def test_text_running_config_aggregates_nested_failures_and_keeps_all_paths():
    ani, issued = build_ani(fail="ethernet-1/40")
    result = ani.dispatch("get_running_config", {
        "nodes": ["leaf2"],
        "paths": ["/interface ethernet-1/40", "/system"],
    })

    assert result["ok"] is False
    config = result["nodes"][0]["config"]
    assert [item["path"] for item in config] == [
        "/interface ethernet-1/40", "/system"]
    assert [item["result"]["ok"] for item in config] == [False, True]
    assert issued == [
        "info from running /interface ethernet-1/40",
        "info from running /system",
    ]


def test_json_format_returns_parsed_state_and_running():
    ani, issued = build_ani()
    result = ani.dispatch("get_running_config", {"nodes": ["leaf2"], "format": "json"})

    assert result["ok"] is True
    assert result["format"] == "json"
    node = result["nodes"][0]
    assert node["ok"] is True
    assert node["error"] is None
    assert sorted(node["state"]) == ["interface", "lldp", "network_instance", "platform", "system"]
    # Parsed, not text: a consumer can index straight into it.
    assert node["state"]["interface"]["interface"][0]["name"] == "ethernet-1/40"
    assert "_annotate" in node["running"]
    assert len(issued) == 6, issued
    # The `*` matters: without it sr_cli rejects `| as json` on list nodes.
    assert "info from state /interface * | as json" in issued


def test_json_format_reports_partial_failure_without_raising():
    ani, _ = build_ani(fail="lldp")
    result = ani.dispatch("get_running_config", {"nodes": ["leaf2"], "format": "json"})

    assert result["ok"] is False
    node = result["nodes"][0]
    assert node["ok"] is False
    assert "lldp" in node["error"]
    # The views that did work are still returned.
    assert "interface" in node["state"]


def test_json_format_serves_a_linux_node():
    """A Linux node has no management API, so its structured form is what `ip` and `tc` say.

    It used to be refused outright, which left the shaped WAN edge of the QoS lab — the
    only place that lab's policy exists — unreadable to anything building a model of the
    network. `state` stays empty on purpose: there is no state datastore to mirror, and
    a consumer tells the two apart by which of the two is populated.
    """
    ani, issued = build_ani()
    result = ani.dispatch("get_running_config", {"nodes": ["web1"], "format": "json"})

    node = result["nodes"][0]
    assert result["ok"] is True, node.get("error")
    assert node["ok"] is True and node["kind"] == "linux"
    assert node["state"] == {}
    running = node["running"]
    assert running["kernel"].startswith("Linux")
    # `lo` is part of no topology and must not be described.
    assert sorted(running["interfaces"]) == ["eth1"]
    assert running["_command_ok"] == {
        "addr": True, "addr6": True, "kernel": True, "link": True,
        "os_release": True, "route": True, "sockets": True,
    }
    assert running["interfaces"]["eth1"]["qdisc"].startswith("qdisc htb")
    assert running["interfaces"]["eth1"]["_command_ok"] == {
        "qdisc": True, "class": True, "filter": True}
    assert "tc qdisc show dev eth1" in issued


def test_linux_structured_marks_failed_tc_query_as_unknown_not_vacant():
    ani, _ = build_ani(fail="tc filter show")
    result = ani.dispatch(
        "get_running_config", {"nodes": ["web1"], "format": "json"})

    interface = result["nodes"][0]["running"]["interfaces"]["eth1"]
    assert interface["filter"] == ""
    assert interface["_command_ok"] == {
        "qdisc": True, "class": True, "filter": False}


def test_json_format_rejects_a_platform_it_cannot_parse():
    """The refusal still exists; it just no longer catches VyOS and Linux."""
    import dataclasses
    ani, _ = build_ani()
    # LabNode is frozen, so the entry is replaced rather than mutated.
    ani.env.config.nodes["web1"] = dataclasses.replace(
        ani.env.config.nodes["web1"], kind="juniper_srx")
    result = ani.dispatch("get_running_config", {"nodes": ["web1"], "format": "json"})

    assert result["ok"] is False
    assert result["nodes"][0]["ok"] is False
    assert "juniper_srx" in result["nodes"][0]["error"]


def test_the_format_parameter_names_every_platform_the_json_read_serves():
    """It said "SR Linux only" while the JSON read served VyOS, Linux and Arista EOS too; on
    main03 that read was the one that showed a Linux node's QoS classes (v0.6)."""
    schema = next(s for s in ContainerLabANI.tool_schemas() if s["function"]["name"] == "get_running_config")
    text = schema["function"]["parameters"]["properties"]["format"]["description"]
    assert "only" not in text
    for platform in ("SR Linux", "VyOS", "Arista EOS", "Linux"):
        assert f"on {platform} " in text, platform
    assert "tc qdisc, class and filter for each interface" in text


def test_non_json_output_is_reported_not_raised():
    ani, _ = build_ani(srl_json={})   # every command returns plain text
    result = ani.dispatch("get_running_config", {"nodes": ["leaf2"], "format": "json"})

    assert result["ok"] is False
    assert "did not return JSON" in result["nodes"][0]["error"]


def test_unknown_format_is_rejected():
    ani, _ = build_ani()
    try:
        ani.dispatch("get_running_config", {"nodes": ["leaf2"], "format": "yaml"})
    except ANIRequestError as exc:
        assert "'text' or 'json'" in str(exc)
    else:
        raise AssertionError("an unknown format must be rejected")


def test_session_control_commands_are_rejected():
    """Observed for real: a model sent `enter candidate` … `commit now` of its own.

    `run_srl_cli_batch` appends its own `commit now`, so the model's commit closed the
    session and the appended one failed outside candidate mode. sr_cli exited 1 *after*
    committing, so the ANI reported `ok=False` for a change that had been applied and
    had repaired the network. The run then read `correctness 100%` and
    `action_execution_rate 0%` at once — two metrics corrupted in opposite directions.
    """
    ani, issued = build_ani()
    for command in ("enter candidate", "commit now", "QUIT", "discard now", "exit"):
        try:
            ani.dispatch("update_config", {
                "changes": [{"target": "leaf2", "commands": [srl_set("safe"), command]}]
            })
        except ANIRequestError as exc:
            assert "session control" in str(exc), command
        else:
            raise AssertionError(f"{command!r} must be rejected: the ANI owns the candidate session")
    assert issued == [], "a rejected batch must never reach the device"


def test_session_control_verb_cannot_hide_behind_non_space_whitespace():
    ani, issued = build_ani()
    disguised = (
        "enter\tcandidate",
        "commit\fnow",
        "discard\vnow",
    )
    for command in disguised:
        try:
            ani.dispatch("update_config", {
                "changes": [{"target": "leaf2", "commands": [command]}]
            })
        except ANIRequestError as exc:
            assert "session control" in str(exc), (repr(command), exc)
        else:
            raise AssertionError(
                f"{command!r} must not bypass the ANI-owned session boundary")
    assert issued == [], "a disguised session verb must fail before device access"


def test_embedded_newline_and_semicolon_remain_native_command_boundaries():
    ani, issued = build_ani()
    for command in (
        f"{srl_set('before-newline')}\ncommit now",
        f"{srl_set('before-semicolon')}; commit now",
    ):
        try:
            ani.dispatch("update_config", {
                "changes": [{"target": "leaf2", "commands": [command]}]
            })
        except ANIRequestError as exc:
            assert "one native command" in str(exc), (repr(command), exc)
        else:
            raise AssertionError(
                f"{command!r} must remain more than one native command")
    assert issued == [], "multi-command input must fail before device access"


def test_legitimate_configuration_commands_still_pass():
    ani, issued = build_ani()
    result = ani.dispatch("update_config", {
        "changes": [{
            "target": "leaf2",
            "commands": ["set / interface ethernet-1/40 admin-state enable"],
            "reason": "restore the access link",
        }]
    })
    assert result["ok"] is True
    assert issued == ["set / interface ethernet-1/40 admin-state enable"]
    assert result["transaction_id"], "a committed change must be rollback-addressable"


def test_every_rollback_is_validated_before_any_forward_command():
    ani, issued = build_ani()
    try:
        ani.dispatch("update_config", {"changes": [
            {
                "target": "leaf2",
                "commands": [srl_set("first-change")],
                "rollback_commands": [srl_set("undo-first")],
            },
            {
                "target": "leaf2",
                "commands": [srl_set("second-change")],
                "rollback_commands": ["commit now"],
            },
        ]})
    except ANIRequestError as exc:
        assert "session control" in str(exc)
    else:
        raise AssertionError("an invalid later rollback must reject the whole request")
    assert issued == [], "preflight failure must occur before the first device write"


def test_every_forward_and_rollback_passes_safety_before_any_write():
    unsafe_changes = [
        {
            "target": "leaf2",
            "commands": ["rm stale-config"],
            "rollback_commands": [srl_set("undo-second")],
        },
        {
            "target": "leaf2",
            "commands": [srl_set("second-change")],
            "rollback_commands": ["rm restored-config"],
        },
    ]
    for unsafe in unsafe_changes:
        ani, issued = build_ani()
        try:
            ani.dispatch("update_config", {"changes": [
                {
                    "target": "leaf2",
                    "commands": [srl_set("first-change")],
                    "rollback_commands": [srl_set("undo-first")],
                },
                unsafe,
            ]})
        except ANIRequestError as exc:
            assert "safety preflight" in str(exc)
        else:
            raise AssertionError("an unsafe later command must reject the whole request")
        assert issued == [], "safety rejection must precede every device write"


def test_vyos_backend_preflight_checks_every_group_before_any_write():
    import dataclasses

    ani, _ = build_ani()
    ani.env.config.nodes["leaf2"] = dataclasses.replace(
        ani.env.config.nodes["leaf2"], kind="vyos")
    writes: list[list[str]] = []

    def recording_vyos_batch(node, commands, timeout_seconds=None):
        writes.append(commands)
        return CommandResult(
            target=node.name, command="\n".join(commands), returncode=0)

    ani.env.executor.run_vyos_cli_batch = recording_vyos_batch
    try:
        ani.dispatch("update_config", {"changes": [
            {
                "target": "leaf2",
                "commands": ["set interfaces dummy dum1 description first"],
                "rollback_commands": ["delete interfaces dummy dum1"],
            },
            {
                "target": "leaf2",
                "commands": ["show configuration"],
                "rollback_commands": ["delete interfaces dummy dum2"],
            },
        ]})
    except ANIRequestError as exc:
        assert "must start with 'set' or 'delete'" in str(exc)
    else:
        raise AssertionError("a later non-config VyOS command must fail preflight")
    assert writes == []


def test_srl_backend_preflight_checks_every_forward_and_rollback_before_writes():
    invalid_cases = [
        {
            "target": "leaf2",
            "commands": ["show version"],
            "rollback_commands": [srl_set("undo-second")],
        },
        {
            "target": "leaf2",
            "commands": [srl_set("second-change")],
            "rollback_commands": ["sh -c 'rm -rf /tmp/x'"],
        },
    ]
    for invalid in invalid_cases:
        ani, issued = build_ani()
        try:
            ani.dispatch("update_config", {"changes": [
                {
                    "target": "leaf2",
                    "commands": [srl_set("first-change")],
                    "rollback_commands": [srl_set("undo-first")],
                },
                invalid,
            ]})
        except ANIRequestError:
            pass
        else:
            raise AssertionError("every SR Linux line must be configuration grammar")
        assert issued == []


def test_unsupported_later_platform_is_rejected_before_every_write() -> None:
    import dataclasses

    ani, issued = build_ani()
    ani.env.config.nodes["leaf2"] = dataclasses.replace(
        ani.env.config.nodes["leaf2"], kind="juniper_srx")
    try:
        ani.dispatch("update_config", {"changes": [
            {
                "target": "leaf1",
                "commands": [srl_set("first-change")],
                "rollback_commands": [srl_set("undo-first")],
            },
            {
                "target": "leaf2",
                "commands": ["set system host-name second"],
            },
        ]})
    except ANIRequestError as exc:
        assert "unsupported write platform" in str(exc)
    else:
        raise AssertionError("an unknown platform must fail whole-request preflight")
    assert issued == []


def test_vyos_cidr_configuration_path_is_preserved_as_one_token() -> None:
    import dataclasses

    ani, issued = build_ani()
    ani.env.config.nodes["leaf2"] = dataclasses.replace(
        ani.env.config.nodes["leaf2"], kind="vyos")
    seen_paths: list[list[str]] = []

    def fake_show_config(node, path, timeout_seconds=None):
        seen_paths.append(path)
        return CommandResult(target=node.name, command="show", returncode=0)

    ani.env.executor.run_vyos_show_config = fake_show_config
    result = ani.dispatch("get_running_config", {
        "nodes": ["leaf2"],
        "paths": ["/protocols/static/route/192.0.2.0/24"],
    })

    assert result["ok"] is True
    assert seen_paths == [["protocols", "static", "route", "192.0.2.0/24"]]
    assert issued == []


def test_running_config_path_rejects_line_and_cli_control_injection() -> None:
    for path in ("/interface\ncommit now", "/interface | as json", "/interface; exit"):
        ani, issued = build_ani()
        try:
            ani.dispatch("get_running_config", {"nodes": ["leaf2"], "paths": [path]})
        except ANIRequestError as exc:
            assert "CLI control" in str(exc)
        else:
            raise AssertionError(f"unsafe configuration path was accepted: {path!r}")
        assert issued == []


def test_update_stops_at_first_failed_group_and_journals_only_successes():
    ani, issued = build_ani(fail="fail-here")
    result = ani.dispatch("update_config", {"changes": [
        {
            "target": "leaf2",
            "commands": [srl_set("first-change")],
            "rollback_commands": [srl_set("undo-first")],
        },
        {
            "target": "leaf2",
            "commands": [srl_set("fail-here")],
            "rollback_commands": [srl_set("undo-failed")],
        },
        {
            "target": "leaf2",
            "commands": [srl_set("must-not-run")],
            "rollback_commands": [srl_set("undo-later")],
        },
    ]})

    assert result["ok"] is False
    assert issued == [srl_set("first-change"), srl_set("fail-here")]
    assert len(result["changes"]) == 2
    assert result["rollback_available"] is True

    rollback = ani.dispatch(
        "rollback_config", {"transaction_id": result["transaction_id"]})
    assert rollback["ok"] is True
    assert issued == [
        srl_set("first-change"), srl_set("fail-here"), srl_set("undo-first")]
    assert [item["commands"] for item in rollback["changes"]] == [
        [srl_set("undo-first")]]


def test_executor_exception_marks_the_whole_transaction_indeterminate():
    ani, issued = build_ani()

    def scripted_batch(node, commands, timeout_seconds=None):
        issued.extend(commands)
        if srl_set("explode") in commands:
            raise TimeoutError("transport stopped")
        return CommandResult(target=node.name, command="\n".join(commands),
                             returncode=0)

    ani.env.executor.run_srl_cli_batch = scripted_batch
    result = ani.dispatch("update_config", {"changes": [
        {
            "target": "leaf2",
            "commands": [srl_set("first-change")],
            "rollback_commands": [srl_set("undo-first")],
        },
        {
            "target": "leaf2",
            "commands": [srl_set("explode")],
            "rollback_commands": [srl_set("undo-explode")],
        },
        {"target": "leaf2", "commands": [srl_set("must-not-run")]},
    ]})

    assert result["ok"] is False
    assert issued == [srl_set("first-change"), srl_set("explode")]
    failed = result["changes"][-1]["result"]
    assert failed["returncode"] == 1 and "TimeoutError" in failed["reason"]
    assert result["rollback_available"] is False
    assert result["indeterminate"] is True
    issued_before_rollback = list(issued)
    rollback = ani.dispatch(
        "rollback_config", {"transaction_id": result["transaction_id"]})
    assert rollback["ok"] is False and rollback["changes"] == []
    assert rollback["indeterminate"] is True
    assert "indeterminate" in rollback["error"]
    assert issued == issued_before_rollback


def test_srl_nothing_to_commit_is_a_determinate_no_change():
    # Deleting a route that is not there: SR Linux answers "Nothing to commit". Until
    # 2026-09-20 that was indeterminate, which blocked rollback of the transaction and
    # of the node's earlier transaction (E4: 63 such outcomes). It is a no-op.
    ani, issued = build_ani()
    first = ani.dispatch("update_config", {"changes": [
        {"target": "leaf2", "commands": [srl_set("first")],
         "rollback_commands": [srl_set("undo-first")]},
    ]})
    assert first["ok"] is True and first["rollback_available"] is True

    from benchmarks.platforms.containerlab.executor import NoChangeExecutionError

    def noop_batch(node, commands, timeout_seconds=None):
        # what the executor raises on "Nothing to commit" (test_executor_transactions)
        issued.extend(commands)
        raise NoChangeExecutionError(
            f"SR Linux answered 'Nothing to commit': the node is already in the requested "
            f"state, nothing was written (node {node.name}, {len(commands)} command(s))")
    ani.env.executor.run_srl_cli_batch = noop_batch
    second = ani.dispatch("update_config", {"changes": [
        {"target": "leaf2", "commands": [srl_set("already-there")],
         "rollback_commands": [srl_set("undo-already-there")]},
        {"target": "leaf2", "commands": [srl_set("never-run")]},
    ]})
    assert second["ok"] is False
    assert second["indeterminate"] is False
    assert second["rollback_available"] is False
    change = second["changes"][0]
    assert change["noop"] is True and change["result"]["ok"] is False
    assert "already in the requested state" in change["result"]["reason"]
    assert len(second["changes"]) == 1 and srl_set("never-run") not in issued

    # the no-op did not become the node's head: the first transaction still rolls back
    ani.env.executor.run_srl_cli_batch = build_ani()[0].env.executor.run_srl_cli_batch
    noop_rollback = ani.dispatch("rollback_config", {"transaction_id": second["transaction_id"]})
    assert noop_rollback["ok"] is True and noop_rollback["changes"] == []
    assert "nothing to roll back" in noop_rollback["note"]
    first_rollback = ani.dispatch("rollback_config", {"transaction_id": first["transaction_id"]})
    assert first_rollback["ok"] is True, first_rollback
    assert [c["commands"] for c in first_rollback["changes"]] == [[srl_set("undo-first")]]


def test_rollback_of_a_transaction_that_applied_nothing_is_a_no_op():
    # The device refused the only change, so nothing was applied: the rollback must say
    # so instead of asking for rollback_commands the agent had supplied (E5 dhcp m1).
    ani, issued = build_ani(fail="refused")
    update = ani.dispatch("update_config", {"changes": [
        {"target": "leaf2", "commands": [srl_set("refused")],
         "rollback_commands": [srl_set("undo-refused")]},
    ]})
    assert update["ok"] is False and update["indeterminate"] is False
    assert update["rollback_available"] is False

    before = list(issued)
    rollback = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert rollback["ok"] is True and rollback["changes"] == []
    assert "nothing to roll back" in rollback["note"]
    assert "error" not in rollback
    assert issued == before


def test_partial_rollback_is_neither_advertised_nor_executed():
    ani, issued = build_ani()
    update = ani.dispatch("update_config", {"changes": [
        {"target": "leaf2", "commands": [srl_set("no-undo")]},
        {"target": "leaf2", "commands": [srl_set("with-undo")],
         "rollback_commands": [srl_set("undo-second")]},
    ]})
    assert update["ok"] is True
    assert update["rollback_available"] is False

    before = list(issued)
    rollback = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert rollback["ok"] is False and rollback["changes"] == []
    assert "no complete rollback" in rollback["error"]
    assert issued == before


def test_linux_multicommand_forward_or_rollback_is_refused_before_writes():
    cases = [
        {
            "target": "web1",
            "commands": [
                "ip link set eth1 up",
                "ip route replace default via 192.0.2.1",
            ],
            "rollback_commands": ["ip link set eth1 down"],
        },
        {
            "target": "web1",
            "commands": ["ip link set eth1 up"],
            "rollback_commands": [
                "ip link set eth1 down",
                "ip route delete default",
            ],
        },
    ]
    for change in cases:
        ani, issued = build_ani()
        try:
            ani.dispatch("update_config", {"changes": [change]})
        except ANIRequestError as exc:
            assert "no native multi-command transaction" in str(exc)
        else:
            raise AssertionError("a Linux command list cannot claim batch atomicity")
        assert issued == []


def test_linux_cisco_route_syntax_is_rejected_before_any_write():
    submitted_in_past_runs = [
        "ip route 10.10.20.0 255.255.255.0 10.255.0.4",
        "ip route 10.10.40.0/24 10.255.0.5",
        "ip route 10.10.30.0/24 10.255.0.2",
        "ip route 10.10.10.0/24 10.255.0.2",
    ]
    for command in submitted_in_past_runs:
        ani, issued = build_ani()
        try:
            ani.dispatch("update_config", {"changes": [
                {
                    "target": "web1",
                    "commands": ["ip link set eth1 up"],
                    "rollback_commands": ["ip link set eth1 down"],
                },
                {"target": "web1", "commands": [command]},
            ]})
        except ANIRequestError as exc:
            assert "unsupported Linux configuration grammar" in str(exc)
        else:
            raise AssertionError(f"Cisco syntax crossed the Linux ANI: {command}")
        assert issued == [], "all backend grammar must be checked before writes"


def test_supported_linux_configuration_grammar_still_executes():
    commands = [
        "ip link set eth1 up",
        "ip address replace 192.0.2.10/24 dev eth1",
        "ip route replace default via 192.0.2.1",
        "ip route replace 198.51.100.0/24 nexthop via 192.0.2.1",
        ("tc class replace dev eth1 parent 1: classid 1:10 htb rate 8mbit "
         "ceil 10mbit prio 1 burst 1600b cburst 1700b"),
        ("tc filter add dev eth1 parent 1: protocol ip prio 1 u32 "
         "match ip src 10.10.10.0/24 flowid 1:10"),
        "tc filter del dev eth1 parent 1: protocol ip prio 1 u32",
    ]
    for command in commands:
        ani, issued = build_ani()
        result = ani.dispatch("update_config", {"changes": [{
            "target": "web1", "commands": [command],
        }]})
        assert result["ok"] is True, (command, result)
        assert issued == [command]


def test_rollback_stops_at_its_first_failed_group():
    ani, issued = build_ani()
    update = ani.dispatch("update_config", {"changes": [
        {"target": "leaf2", "commands": [srl_set("one")],
         "rollback_commands": [srl_set("undo-one")]},
        {"target": "leaf2", "commands": [srl_set("two")],
         "rollback_commands": [srl_set("undo-two")]},
        {"target": "leaf2", "commands": [srl_set("three")],
         "rollback_commands": [srl_set("undo-three")]},
    ]})

    def failing_rollback(node, commands, timeout_seconds=None):
        issued.extend(commands)
        return CommandResult(
            target=node.name,
            command="\n".join(commands),
            returncode=1 if srl_set("undo-two") in commands else 0,
        )

    ani.env.executor.run_srl_cli_batch = failing_rollback
    rollback = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert rollback["ok"] is False
    assert [item["commands"] for item in rollback["changes"]] == [
        [srl_set("undo-three")], [srl_set("undo-two")]]
    assert srl_set("undo-one") not in issued, \
        "rollback dependencies must stop after failure"

    def successful_retry(node, commands, timeout_seconds=None):
        issued.extend(commands)
        return CommandResult(
            target=node.name, command="\n".join(commands), returncode=0)

    ani.env.executor.run_srl_cli_batch = successful_retry
    retry = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert [item["commands"] for item in retry["changes"]] == [
        [srl_set("undo-two")], [srl_set("undo-one")]]
    assert retry["ok"] is True

    issued_after_completion = list(issued)
    repeated = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert repeated["ok"] is True and repeated["changes"] == []
    assert issued == issued_after_completion


def test_stale_rollback_cannot_overwrite_a_later_transaction() -> None:
    ani, issued = build_ani()
    first = ani.dispatch("update_config", {"changes": [{
        "target": "leaf2",
        "commands": [srl_set("first")],
        "rollback_commands": [srl_set("original")],
    }]})
    second = ani.dispatch("update_config", {"changes": [{
        "target": "leaf2",
        "commands": [srl_set("second")],
        "rollback_commands": [srl_set("first")],
    }]})

    before = list(issued)
    stale = ani.dispatch(
        "rollback_config", {"transaction_id": first["transaction_id"]})
    assert stale["ok"] is False and stale["changes"] == []
    assert "newer transaction" in stale["error"]
    assert issued == before

    latest = ani.dispatch(
        "rollback_config", {"transaction_id": second["transaction_id"]})
    assert latest["ok"] is True
    restored_first = ani.dispatch(
        "rollback_config", {"transaction_id": first["transaction_id"]})
    assert restored_first["ok"] is True
    assert issued[-2:] == [srl_set("first"), srl_set("original")]


def test_indeterminate_rollback_cannot_be_blindly_retried():
    ani, issued = build_ani()
    update = ani.dispatch("update_config", {"changes": [{
        "target": "leaf2",
        "commands": [srl_set("change")],
        "rollback_commands": [srl_set("undo-change")],
    }]})

    def timeout_after_dispatch(node, commands, timeout_seconds=None):
        issued.extend(commands)
        raise TimeoutError("commit acknowledgement was lost")

    ani.env.executor.run_srl_cli_batch = timeout_after_dispatch
    first = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert first["ok"] is False
    assert first["indeterminate"] is True
    assert first["changes"][0]["result"]["returncode"] == 1
    assert "indeterminate" in first["changes"][0]["result"]["reason"]

    calls_after_timeout = list(issued)
    second = ani.dispatch(
        "rollback_config", {"transaction_id": update["transaction_id"]})
    assert second["ok"] is False and second["changes"] == []
    assert second["indeterminate"] is True
    assert "indeterminate" in second["error"]
    assert issued == calls_after_timeout


def _scripted_validation(ani, *healthy_flags):
    """Drive `execute_validation` over a scripted sequence of observations.

    Returns the result plus the sleeps the ANI asked for, so a test can assert on the
    settle without spending it. `time.monotonic` is left alone: a settle computed from
    the real clock is what production runs, and the stamp is microseconds old here.
    """
    observations = iter(healthy_flags)
    slept: list[float] = []

    def fake_observe():
        healthy = next(observations)
        return Observation(
            lab_name="sme01-small",
            nodes=[],
            connectivity=[ConnectivityCheck(
                source="user1", destination="web1", destination_ip="10.10.40.10",
                success=healthy, packet_loss_percent=0.0 if healthy else 100.0, output="",
            )],
        )

    ani.env.observe = fake_observe
    original_sleep = ani_module.time.sleep
    ani_module.time.sleep = slept.append
    try:
        result = ani.dispatch(
            "execute_validation",
            {"checks": [{"type": "public_success_criteria"}]},
            task={"success_criteria": {"all_of": [{"type": "lab_connectivity"}]}},
        )
    finally:
        ani_module.time.sleep = original_sleep
    return result, slept


def _apply_a_change(ani):
    ani.dispatch("update_config", {
        "changes": [{
            "target": "leaf2",
            "commands": ["set / interface ethernet-1/40 admin-state enable"],
        }]
    })


def test_validation_after_a_change_settles_before_probing():
    """The regression this settle exists for.

    Observed on connectivity.disable_interface.m1: the SUT re-enabled the disabled
    access port — the correct repair — then validated immediately. Routing had not
    followed yet, the probe read `Destination Net Unreachable`, and the agent responded
    by disabling a healthy static route on the other leaf. The Judge, which polls, scored
    the network as repaired while the SUT reported `status=failed`.
    """
    ani, _ = build_ani()
    _apply_a_change(ani)
    result, slept = _scripted_validation(ani, True)
    assert result["ok"] is True
    assert slept and slept[0] > 0, "a validation must not probe the data plane a change has not reached"
    assert slept[0] <= ani_module.VALIDATION_SETTLE_SECONDS


def test_a_change_that_has_not_converged_is_re_observed_once():
    ani, _ = build_ani()
    _apply_a_change(ani)
    result, slept = _scripted_validation(ani, False, True)
    assert result["ok"] is True, "a slightly slow reconvergence is not a failed repair"
    assert slept[1:] == [ani_module.VALIDATION_RETRY_INTERVAL_SECONDS]


def test_a_failed_repair_still_reports_failure():
    """The retry may not launder a change that did not work: both observations count."""
    ani, _ = build_ani()
    _apply_a_change(ani)
    result, _ = _scripted_validation(ani, False, False)
    assert result["ok"] is False
    assert result["checks"][0]["checks"][0]["connectivity"][0]["success"] is False


def test_validation_without_a_change_neither_settles_nor_retries():
    """An unchanged network measures the same twice; spending the budget on it is waste."""
    ani, _ = build_ani()
    result, slept = _scripted_validation(ani, False)
    assert result["ok"] is False
    assert slept == []


def test_each_change_earns_one_settle_not_one_per_validation():
    ani, _ = build_ani()
    _apply_a_change(ani)
    _scripted_validation(ani, True)
    _, slept = _scripted_validation(ani, False)
    assert slept == [], "the settle belongs to the change, not to every later validation"


# --------------------------------------------------------------------------- #
# Offered tools: capabilities the platform announces but does not implement
# --------------------------------------------------------------------------- #


def test_unknown_operation_is_rejected():
    ani, _ = build_ani()
    try:
        ani.dispatch("build_knowledge_graph", {})
    except ANIRequestError as exc:
        assert "unsupported ANI operation" in str(exc)
    else:
        raise AssertionError("the ANI must not answer operations it does not declare")


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a test runner reports, it does not raise
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


def test_a_criterion_type_sent_as_a_check_type_is_redirected_to_the_gate():
    """The task prints ``{"type": "observed_throughput", ...}`` and the check schema has
    a ``type`` slot, so models copy one into the other. The rejection has to say where
    the type belongs and which call measures it, or the turn is a dead end."""
    ani, _issued = build_ani()
    declared = {"success_criteria": {"all_of": [{"type": "observed_throughput", "min_mbps": 8}]}}
    for task, phrase in ((declared, "from your task's success_criteria"),
                         ({}, "is a success-criterion type, not a check type")):
        try:
            ani.execute_validation([{"type": "observed_throughput"}], task=task)
        except ANIRequestError as exc:
            message = str(exc)
        else:
            raise AssertionError("a criterion type must not be accepted as a check type")
        assert message.startswith("unsupported validation check type: observed_throughput")
        assert "public_success_criteria, icmp" in message
        assert phrase in message, message
        assert "execute_validation with type public_success_criteria" in message
    assert "your task's" not in message  # the undeclared case never claims the task said so


def test_an_unknown_check_type_lists_the_supported_ones_without_a_false_redirect():
    ani, _issued = build_ani()
    try:
        ani.execute_validation([{"type": "throughput"}], task={})
    except ANIRequestError as exc:
        message = str(exc)
    else:
        raise AssertionError("an unknown check type must be rejected")
    assert message.startswith("unsupported validation check type: throughput")
    assert "public_success_criteria, icmp" in message
    assert "success-criterion type" not in message


def test_the_criterion_mirror_matches_the_evaluator_branches():
    """PUBLIC_SUCCESS_CRITERION_TYPES is an explicit mirror of evaluate_success_criteria."""
    # Behavioral: every declared type is evaluated (whatever its verdict), and a
    # foreign type is refused by name; the constant is the evaluator's gate, not a copy.
    ani, _issued = build_ani()
    ani.env.observe = lambda: Observation(lab_name="sme01-small", nodes=[], connectivity=[])
    for kind in ani_module.PUBLIC_SUCCESS_CRITERION_TYPES:
        result = ani.evaluate_success_criteria({"success_criteria": {"all_of": [{"type": kind}]}})
        error = str(result["checks"][0].get("error") or "")
        assert not error.startswith("unsupported public success criterion"), (kind, error)
    result = ani.evaluate_success_criteria({"success_criteria": {"all_of": [{"type": "lab_throughput"}]}})
    assert result["checks"][0]["error"] == "unsupported public success criterion: lab_throughput"
    schema = next(tool for tool in ContainerLabANI.tool_schemas()
                  if tool["function"]["name"] == "execute_validation")
    enum = schema["function"]["parameters"]["properties"]["checks"]["items"]["properties"]["type"]["enum"]
    assert tuple(enum) == ani_module.VALIDATION_CHECK_TYPES


def test_an_unsupported_criterion_is_named_in_its_result():
    ani, _issued = build_ani()
    result = ani.evaluate_success_criteria({"success_criteria": {"all_of": [{"type": "bogus"}]}})
    assert result["passed"] is False
    assert result["checks"][0]["error"] == "unsupported public success criterion: bogus"


def test_execute_validation_description_teaches_the_gate_and_its_cost():
    schema = next(tool for tool in ContainerLabANI.tool_schemas()
                  if tool["function"]["name"] == "execute_validation")
    description = schema["function"]["description"]
    assert "every criterion" in description
    assert "completion gate" in description
    assert "settle window" in description and "about a minute" in description
    # The judge advertises the shorter contract inside every task payload; it has to
    # teach the same gate, or the model meets the confusion before any schema does.
    contract = next(tool for tool in ContainerLabANI.tool_contract()
                    if tool["name"] == "execute_validation")
    assert "every declared criterion" in contract["description"]
    assert "completion gate" in contract["description"]


if __name__ == "__main__":
    raise SystemExit(main())


def test_the_ani_budget_bounds_every_operation_that_reaches_it() -> None:
    """The cap is enforced where the operations pass, not where the model asks.

    A subject tool answers one model call by reading the devices and writing them, and
    those operations come through here without passing the subject's loop. A cap that
    counted only the model's own calls would leave that path unbounded while the record
    claimed the budget was spent once.
    """
    ani, _issued = build_ani()
    ani.call_budget = 2

    first = ani.dispatch("get_topology", {})
    second = ani.dispatch("get_topology", {})
    third = ani.dispatch("get_topology", {})

    assert first.get("ok") is not False and second.get("ok") is not False
    assert third["ok"] is False
    assert third["dispatched"] is False
    assert "ANI interaction limit reached: 2 operation(s) allowed" in third["error"]
    assert ani.budget_state() == {"limit": 2, "used": 2, "refused": 1, "reached": True}


def test_without_a_budget_the_ani_dispatches_every_operation() -> None:
    ani, _issued = build_ani()
    for _ in range(5):
        assert ani.dispatch("get_topology", {}).get("ok") is not False
    assert ani.budget_state() == {"limit": None, "used": 5, "refused": 0, "reached": False}


# --- submit_result (v0.4): the agent's own end of the episode ---------------------------

def test_submit_result_records_the_conclusion_as_declared() -> None:
    """Not a check: a completion with no validation behind it is still recorded as
    submitted. Whether it holds is the judge's oracles' to say, from the network."""
    ani, issued = build_ani()
    result = ani.dispatch("submit_result", {"status": "Completed ", "summary": " enabled leaf1 e1-1 ",
                                            "diagnosis": "leaf1 ethernet-1/1 admin-down"})
    assert result == {"ok": True, "operation": "submit_result", "submitted": True, "status": "completed",
                      "summary": "enabled leaf1 e1-1", "diagnosis": "leaf1 ethernet-1/1 admin-down"}
    assert [(item["status"], item["summary"]) for item in ani.submissions] == [("completed", "enabled leaf1 e1-1")]
    assert issued == [], "submitting touches no device"


def test_submit_result_refuses_what_is_not_a_conclusion() -> None:
    ani, _issued = build_ani()
    for arguments, message in (({"status": "done", "summary": "x"}, "status must be one of"),
                               ({"summary": "x"}, "status must be one of"),
                               ({"status": "failed", "summary": "  "}, "summary must state")):
        try:
            ani.dispatch("submit_result", arguments)
        except ANIRequestError as exc:
            assert message in str(exc), exc
        else:
            raise AssertionError(f"submit_result accepted {arguments}")
    assert ani.submissions == [], "a refused submission submits nothing"
    without_diagnosis = ani.dispatch("submit_result", {"status": "failed", "summary": "no safe repair",
                                                       "diagnosis": " "})
    assert without_diagnosis["diagnosis"] is None


def test_submit_result_is_accepted_past_the_budget_and_not_counted() -> None:
    ani, _issued = build_ani()
    ani.call_budget = 1
    ani.dispatch("get_topology", {})
    refused = ani.dispatch("get_topology", {})
    assert "submit_result is still accepted" in refused["error"]
    submitted = ani.dispatch("submit_result", {"status": "failed", "summary": "budget spent"})
    assert submitted["submitted"] is True
    assert ani.budget_state() == {"limit": 1, "used": 1, "refused": 1, "reached": True}


def test_submit_result_is_declared_with_its_schema() -> None:
    contract = {item["name"]: item for item in ContainerLabANI.tool_contract()}
    assert contract["submit_result"]["type"] == "submit"
    schema = next(s["function"] for s in ContainerLabANI.tool_schemas() if s["function"]["name"] == "submit_result")
    assert schema["parameters"]["required"] == ["status", "summary"]
    assert schema["parameters"]["properties"]["status"]["enum"] == ["completed", "failed"]
    assert "stops the episode" in schema["description"]


def _icmp_criterion(**extra) -> dict:
    return {"success_criteria": {"all_of": [{
        "type": "observed_icmp_service", "source": "finance1", "destination": "app1",
        "destination_ip": "10.10.50.10", "max_packet_loss_percent": 0, **extra}]}}


def test_the_public_icmp_criterion_sends_the_repair_oracles_thirty_pings() -> None:
    """Three loss-free pings passed a path still corrupting 31 % of its packets a third
    of the time (E16 link_impairment.m2). The criterion sends the count it
    declares, past the 20-ping cap of an agent's own check, five a second."""
    ani, issued = build_ani()
    ani.evaluate_success_criteria(_icmp_criterion(count=30))
    ani.evaluate_success_criteria(_icmp_criterion())
    pings = [command for command in issued if command.startswith("ping")]
    assert pings == ["ping -q -c 30 -i 0.2 -W 3 10.10.50.10"] * 2, pings
    # A count past the criterion's own cap fails that criterion by name, without
    # aborting the call.
    result = ani.evaluate_success_criteria(_icmp_criterion(count=ani_module.PUBLIC_ICMP_MAX_COUNT + 1))
    assert result["passed"] is False
    assert "icmp count must be 1..50" in result["checks"][0]["error"]


def test_an_agents_own_icmp_check_keeps_its_cap_and_pace() -> None:
    ani, issued = build_ani()
    check = {"type": "icmp", "source": "finance1", "destination_ip": "10.10.50.10"}
    # The pace is the caller's, not the check's: a key the schema does not declare
    # changes nothing.
    ani.execute_validation([{**check, "count": 5, "interval_seconds": 0.01}], task={})
    assert [command for command in issued if command.startswith("ping")] == [
        "ping -q -c 5 -W 3 10.10.50.10"]
    try:
        ani.execute_validation([{**check, "count": 21}], task={})
    except ANIRequestError as exc:
        assert "1..20" in str(exc)
    else:
        raise AssertionError("an agent's icmp check must stay capped at 20 pings")


def _ani_whose_ping_answers(stdout: str = "", stderr: str = "", returncode: int = 0):
    """An ANI whose ping commands get this answer; the commands issued."""
    ani, issued = build_ani()

    def ping(node, command, timeout_seconds=None):
        issued.append(command)
        return CommandResult(target=node.name, command=command, returncode=returncode,
                             stdout=stdout, stderr=stderr)

    ani.env.executor.run_shell = ping
    return ani, issued


def test_an_icmp_check_from_a_node_without_ping_says_that_nothing_was_sent() -> None:
    """A node with no ping program answered `passed: false, packet_loss_percent: 100.0`
    although no packet left it (main04, 16 checks from the two DNS nodes). The check now
    says that it did not run."""
    ani, _ = _ani_whose_ping_answers(
        stderr='OCI runtime exec failed: exec failed: unable to start container process: '
               'exec: "ping": executable file not found in $PATH: unknown', returncode=126)
    answer = ani.execute_validation(
        [{"type": "icmp", "source": "finance1", "destination_ip": "10.10.50.10"}], task={})
    check = answer["checks"][0]
    assert answer["ok"] is False and check["passed"] is False
    assert check["packet_loss_percent"] is None, check
    assert "no ping program" in check["error"] and "finance1" in check["error"], check
    # A ping that ran and lost everything is still a measured loss.
    ani, _ = _ani_whose_ping_answers(
        stdout="PING 10.10.50.10 (10.10.50.10): 56 data bytes\n\n--- 10.10.50.10 ping statistics ---\n"
               "3 packets transmitted, 0 packets received, 100% packet loss\n", returncode=1)
    check = ani.execute_validation(
        [{"type": "icmp", "source": "finance1", "destination_ip": "10.10.50.10"}], task={})["checks"][0]
    assert check["passed"] is False and check["packet_loss_percent"] == 100.0 and "error" not in check


def test_an_icmp_check_to_the_management_network_is_refused_or_does_not_pass() -> None:
    """A node name resolves to the node's management address, so a ping to it passed
    whatever the network under repair did, and a typed management address did the same
    (main04, 36 such passes). A typed one is refused before it is sent. A name is sent,
    and does not pass when ping reports that it resolved into the management network."""
    from benchmarks.platforms.containerlab.management import management_networks

    ani, issued = _ani_whose_ping_answers()
    network = management_networks(ani.env.config.topology_path)[0]
    managed = str(next(network.hosts()))
    try:
        ani.execute_validation([{"type": "icmp", "source": "finance1", "destination_ip": managed}], task={})
    except ANIRequestError as exc:
        assert "management network" in str(exc) and managed in str(exc)
    else:
        raise AssertionError("a ping to a management address must be refused")
    assert issued == [], issued

    ani, _ = _ani_whose_ping_answers(
        stdout=f"PING app1 ({managed}): 56 data bytes\n\n--- app1 ping statistics ---\n"
               "3 packets transmitted, 3 packets received, 0% packet loss\n"
               "round-trip min/avg/max = 0.1/0.2/0.3 ms\n")
    check = ani.execute_validation(
        [{"type": "icmp", "source": "finance1", "destination_ip": "app1"}], task={})["checks"][0]
    assert check["passed"] is False and check["resolved_ip"] == managed, check
    assert "management network" in check["error"], check

    # A name the lab's own DNS resolves to an address of the network under repair passes.
    ani, _ = _ani_whose_ping_answers(
        stdout="PING app1.lab (10.10.50.10): 56 data bytes\n\n--- app1.lab ping statistics ---\n"
               "3 packets transmitted, 3 packets received, 0% packet loss\n"
               "round-trip min/avg/max = 0.1/0.2/0.3 ms\n")
    check = ani.execute_validation(
        [{"type": "icmp", "source": "finance1", "destination_ip": "app1.lab"}], task={})["checks"][0]
    assert check["passed"] is True and check["packet_loss_percent"] == 0.0, check


def test_an_icmp_destination_reaches_the_shell_as_one_word() -> None:
    ani, issued = _ani_whose_ping_answers()
    ani.execute_validation(
        [{"type": "icmp", "source": "finance1", "destination_ip": "10.10.50.10; reboot"}], task={})
    assert issued == ["ping -q -c 3 -W 3 '10.10.50.10; reboot'"], issued


# --- prime_learned_tables (v0.11): the judge's priming, for a subject's own code ---------

def test_prime_learned_tables_runs_the_judges_priming_and_writes_nothing() -> None:
    """A subject's own code calls it before it reads the learned tables again, as the judge
    primes before the subject: each Linux node forgets each data-plane next hop, then pings
    it; each leased client asks its resolver (none here)."""
    ani, issued = build_ani()
    linux = [name for name, node in ani.env.config.nodes.items() if node.kind == "linux"]
    assert linux, "the fixture lab has Linux nodes"

    result = ani.dispatch("prime_learned_tables", {})

    assert result == {"ok": True, "operation": "prime_learned_tables",
                      "gateway_pings": len(linux), "resolver_queries": 0}
    assert issued.count("ip neigh flush to 10.10.10.1") == len(linux)
    assert issued.count("ping -c 1 -W 1 10.10.10.1") == len(linux)
    assert set(issued) == {"ip -o -4 route show", "ip neigh flush to 10.10.10.1",
                           "ping -c 1 -W 1 10.10.10.1", "cat /etc/resolv.conf"}, "traffic, no configuration"
    assert ani.budget_state()["used"] == 1


def test_prime_learned_tables_is_offered_to_no_model_and_spends_the_cap() -> None:
    names = {schema["function"]["name"] for schema in ContainerLabANI.tool_schemas()}
    assert ani_module.SUBJECT_OPERATIONS == ("prime_learned_tables",)
    assert not set(ani_module.SUBJECT_OPERATIONS) & names
    assert not set(ani_module.SUBJECT_OPERATIONS) & set(ANI_OPERATIONS)
    ani, issued = build_ani()
    ani.call_budget = 1
    ani.dispatch("get_topology", {})
    refused = ani.dispatch("prime_learned_tables", {})
    assert refused["ok"] is False and refused["dispatched"] is False
    assert "ping -c 1 -W 1 10.10.10.1" not in issued
