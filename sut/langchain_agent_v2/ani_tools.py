"""The baseline's LangChain tools, without what coaches the model or corrects it.

Everything the model reads about a tool is here: the descriptions, and the arguments
whose wording differs from the baseline's. The calls themselves, the paging, the record
and the transport are the baseline's (`sut.langchain_agent.ani_tools`).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from benchmarks.platforms.containerlab import MANAGEMENT_PLANE_RULE, ContainerLabANI
from benchmarks.platforms.containerlab.ani import OBJECT_KINDS_TEXT
from sut.common.ani_report import operation_summary
from sut.common.repeated_requests import RepeatedRequests
from sut.langchain_agent import ani_tools as baseline
from sut.langchain_agent.ani_tools import ANIArguments, LangChainANIAdapter

TOPOLOGY_TOOL = "get_topology_simple"

#: The tools the model is handed, in this order. `get_topology_simple` stands where the
#: baseline has `get_topology`; a tool added to the baseline does not reach this subject.
TOOLS = (TOPOLOGY_TOOL, "get_state", "get_running_config", "get_object", "update_config",
         "update_object", "execute_validation", "rollback_config", "submit_result")

#: The ANI's own descriptions, where the baseline's carry advice drawn from its campaigns.
#: Five of the ANI's advise the model too, or name the completion gate this subject does
#: not have, and are written here without that.
DESCRIPTIONS = {
    **{schema["function"]["name"]: schema["function"]["description"]
       for schema in ContainerLabANI.tool_schemas()},
    TOPOLOGY_TOOL: (
        "List the lab's nodes with their kind (the platform, which sets the command syntax), "
        "and the links between their interfaces. A link has endpoint1 and endpoint2; a link "
        "listed as endpoints with more than two entries is a shared segment (a VLAN or a "
        "trunk), and one with a single entry has a peer the topology does not declare."),
    "get_state": (
        "Read live operational state, one text per node and view: interfaces (each interface's state "
        "and addresses), routes (the routing table), qdisc (traffic control; on VyOS its QoS "
        "configuration), system (the software version; on Linux the links)."),
    "get_running_config": (
        "Read the running configuration of one or more nodes. In text, each node as it prints it: VyOS "
        "`show`, Arista EOS `show running-config`, SR Linux `info from running`, Linux `ip address show` "
        "and `ip route show`. format json gives the whole device as structured data instead, on Linux "
        "with its tc qdiscs, classes and filters."),
    # The ANI's list of kinds and names, without the advice its description starts with.
    "get_object": "Read one object on one node, by name. " + OBJECT_KINDS_TEXT,
    "update_config": (
        "Apply one or more scoped native commands. Send only configuration commands: the ANI opens the "
        "configuration session and commits it for you, so do not include 'enter', 'commit', 'discard', "
        "'quit' or 'exit'. Do not include shell wrappers, sr_cli, semicolons, or destructive commands. "
        "Each change holds one target's commands: on SR Linux, VyOS and Arista EOS they are committed "
        "together or not at all; on Linux a change holds one command. Changes are applied in order, and "
        "the first that fails stops the ones after it. rollback_config can undo the write only when "
        "every change carries its rollback_commands. "
        + MANAGEMENT_PLANE_RULE),
    "execute_validation": (
        "Run active validation. Type public_success_criteria evaluates every criterion in the "
        "task's success_criteria (including a real throughput measurement under offered load "
        "where one is declared). It takes up to about a minute per call: after a change it first "
        "waits out a settle window of about ten seconds, then probes every declared pair and, for "
        "a throughput criterion, runs a twelve-second measurement. Type icmp probes one chosen path."),
    "submit_result": (
        "End the episode with your conclusion. Submitting stops the episode whatever it says. "
        "status completed means the task's goals are met; failed means they are not."),
}

#: Example arguments for each tool, shown after its description: what a call looks like,
#: not when to make it. A label names the platform or the condition a call needs. No node,
#: port or address in them exists in a lab (192.0.2.0/24 and 198.51.100.0/24 are
#: documentation prefixes no lab uses), so an example says nothing of a network under
#: repair. `update_config` shows the baseline's command forms, without the rule the
#: baseline sets beside each and without its values, which are the labs' own.
EXAMPLES: dict[str, list[tuple[str, dict[str, Any]]]] = {
    TOPOLOGY_TOOL: [("", {})],
    "get_state": [
        ("", {"nodes": ["node1"], "views": ["routes"], "line_filter": "192.0.2."}),
        ("", {"nodes": ["node1"], "views": ["interfaces"], "line_offset": 200, "line_limit": 100}),
    ],
    "get_running_config": [
        ("SR Linux", {"nodes": ["node1"], "paths": ["/ interface ethernet-1/99"]}),
        ("", {"nodes": ["node1"], "line_filter": "interface", "line_limit": 50}),
    ],
    "get_object": [
        ("", {"node": "node1", "kind": "interface", "name": "ethernet-1/99"}),
        ("", {"node": "node1", "kind": "route", "name": "192.0.2.0/24"}),
    ],
    "update_config": [
        ("SR Linux", {"changes": [{"target": "node1", "commands": ["set / interface ethernet-1/99 admin-state enable"]}]}),
        ("VyOS", {"changes": [{"target": "node1", "commands": [
            "set interfaces ethernet eth9 address 192.0.2.1/24",
            "set firewall zone ZONE1 from ZONE2 firewall name RULESET1"]}]}),
        ("Arista EOS", {"changes": [{"target": "node1", "commands": ["interface Ethernet99", "switchport access vlan 999"]}]}),
        ("Linux", {"changes": [{"target": "node1", "commands": ["ip route replace default via 192.0.2.1 dev eth9"]}]}),
    ],
    "update_object": [
        ("SR Linux", {"node": "node1", "kind": "interface", "name": "ethernet-1/99", "set": {"admin_state": "enable"}}),
        ("VyOS", {"node": "node1", "kind": "route", "name": "192.0.2.0/24", "set": {"next_hop": "198.51.100.1"}}),
        ("VyOS", {"node": "node1", "kind": "firewall_rule", "name": "RULESET1", "set": {"rule": 10, "action": "drop"}}),
    ],
    "execute_validation": [
        ("", {"checks": [{"type": "public_success_criteria"}]}),
        ("from a Linux endpoint",
         {"checks": [{"type": "icmp", "source": "node1", "destination_ip": "192.0.2.10", "count": 3}]}),
    ],
    "rollback_config": [("", {"transaction_id": "ani_tx_0123456789abcdef"})],
    "submit_result": [
        ("", {"status": "completed", "summary": "node1 had no route to 192.0.2.0/24; added it through 198.51.100.1.",
              "diagnosis": "node1, static route 192.0.2.0/24: missing."}),
        ("", {"status": "failed", "summary": "Read the routes and interfaces of node1, found no cause, changed nothing."}),
    ],
}


def with_examples(name: str) -> str:
    """The tool's description followed by its example arguments."""
    shown = "; ".join(f"{label}: {json.dumps(call)}" if label else json.dumps(call) for label, call in EXAMPLES[name])
    return f"{DESCRIPTIONS[name]} Example arguments: {shown}"


FILTER_TEXT = ("Optional case-insensitive filter on the returned items: an item that contains it "
               "is kept whole.")
LINE_FILTER = ("Keep only the lines of each device's text that contain this case-insensitive text, "
               "numbered as in the whole text.")
NODE = "One node name, as get_topology_simple lists it."


class GetTopologySimpleArguments(ANIArguments):
    """The tool takes none."""


class GetStateArguments(baseline.GetStateArguments):
    filter_text: str | None = Field(default=None, description=FILTER_TEXT)
    line_filter: str | None = Field(default=None, description=LINE_FILTER)


class GetRunningConfigArguments(baseline.GetRunningConfigArguments):
    filter_text: str | None = Field(default=None, description=FILTER_TEXT)
    line_filter: str | None = Field(default=None, description=LINE_FILTER)
    paths: list[str] | None = Field(default=None, description=(
        "Optional sections, one each: VyOS 'interfaces ethernet eth3', Arista EOS 'interface Ethernet3', "
        "SR Linux '/ interface ethernet-1/40'; omit for the whole configuration. A Linux node gives its "
        "whole text whatever the path."))


class GetObjectArguments(baseline.GetObjectArguments):
    node: str = Field(min_length=1, description=NODE)


class UpdateObjectArguments(baseline.UpdateObjectArguments):
    node: str = Field(min_length=1, description=NODE)
    set: dict[str, Any] = Field(min_length=1, description="The attributes to change and their values.")


class SubmitResultArguments(baseline.SubmitResultArguments):
    summary: str = Field(min_length=1, description="What you observed and what you changed.")


#: The arguments the baseline words differently; the others are the baseline's.
ARGUMENTS = {
    "get_state": GetStateArguments,
    "get_running_config": GetRunningConfigArguments,
    "get_object": GetObjectArguments,
    "update_object": UpdateObjectArguments,
    "submit_result": SubmitResultArguments,
}


def simple_topology(topology: dict[str, Any]) -> dict[str, Any]:
    """Node names and kinds, and the links between their interfaces, from the ANI's
    `get_topology` answer.

    The ANI places each declared port on a segment. A segment with two ports is a link;
    any other is kept as one entry listing its ports, since it is no cable: a VLAN or a
    trunk shared by several ports, or a port whose peer the descriptor does not declare.
    Roles, capabilities and segment names are dropped.
    """
    nodes = topology.get("nodes") or []
    ports: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        for port in node.get("interfaces") or []:
            if port.get("segment"):
                ports.setdefault(port["segment"], []).append({"node": node.get("name"), "interface": port.get("name")})
    return {
        "ok": True,
        "operation": TOPOLOGY_TOOL,
        "nodes": [{"name": node.get("name"), "kind": node.get("kind")} for node in nodes],
        "links": [{"endpoint1": ends[0], "endpoint2": ends[1]} if len(ends) == 2 else {"endpoints": ends}
                  for ends in ports.values()],
    }


class NoRepeatedRequestNote(RepeatedRequests):
    """Counts nothing and says nothing: a request sent again fails as it failed."""

    def failed(self, operation: str, arguments: Any, error: Any, index: int) -> str | None:
        return None

    def record(self) -> dict[str, Any]:
        return {"limit": None, "action": "none", "notes": [], "resets": []}


class PlainANIAdapter(LangChainANIAdapter):
    """The baseline's adapter with no send-back, no note and no reading of the model's filters."""

    read_further = None

    @staticmethod
    def containing(needle: str) -> Callable[[str], bool]:
        """A plain case-insensitive match: `Ethernet3` does not find `Et3`."""
        folded = needle.casefold()
        return lambda text: folded in text.casefold()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.trace.repeated_requests = NoRepeatedRequestNote()
        #: Each model call that turned older reads into stubs, for the record (`agent.KeepRecentReads`).
        self.elided_reads: list[dict[str, Any]] = []
        #: What the last model call was sent, for the diagnosis turn (`agent.RememberTranscript`).
        self.transcript: list[Any] = []

    def _early_failure_pushback(self, arguments: dict[str, Any]) -> None:
        return None

    def _unverified_completion_pushback(self, arguments: dict[str, Any]) -> None:
        return None

    def file_refusal(self, call: dict[str, Any], error: str) -> None:
        """A call `create_agent` answered with an error before any tool ran, filed as the shared
        loop files one (`agent.FileRefusedCalls`): an operation that was not dispatched, which the
        judge counts as a failed operation and as no read, write or validation."""
        result = {"ok": False, "operation": call.get("name"), "dispatched": False, "error": error}
        event = operation_summary(str(call.get("name")), result, 0.0, call.get("args"))
        event["artifact_ref"] = None
        self.trace.operations.append(event)
        self.trace.failed_operations += 1

    def _bounded(self, raw: dict[str, Any], **kwargs: Any) -> tuple[str, str | None]:
        if raw.get("operation") == "get_topology" and raw.get("ok"):
            # Whole, by the path a validation takes: the tool takes no paging argument, so
            # a page of nodes or a cut would hide part of the lab with no way to read it.
            return super()._bounded(simple_topology(raw), **{**kwargs, "validation": True})
        return super()._bounded(raw, **kwargs)

    def tools(self) -> list[BaseTool]:
        adapter = self

        @tool(TOPOLOGY_TOOL, args_schema=GetTopologySimpleArguments, description=DESCRIPTIONS[TOPOLOGY_TOOL])
        def get_topology_simple() -> str:
            # One ANI read, counted by the ANI and filed under the operation it made.
            return adapter._invoke("get_topology", {})

        offered = {item.name: item for item in super().tools()}
        offered[TOPOLOGY_TOOL] = get_topology_simple
        surface = [offered[name] for name in TOOLS]
        for item in surface:
            item.description = with_examples(item.name)
            item.args_schema = ARGUMENTS.get(item.name, item.args_schema)
        return surface
