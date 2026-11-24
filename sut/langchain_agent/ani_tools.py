"""LangChain tools adapting the framework-independent ANI contract."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from benchmarks.platforms.containerlab import (
    MANAGEMENT_PLANE_RULE,
    SUBMIT_DIAGNOSIS_DESCRIPTION,
    SUBMIT_RESULT_DESCRIPTION,
    SUBMIT_SUMMARY_DESCRIPTION,
    ContainerLabANI,
)
from benchmarks.platforms.containerlab.ani import OBJECT_KINDS
from sut.common.ani_report import (
    SUBMISSION_OPERATION,
    device_changes_from_ani_result,
    objective_from_validation,
    operation_summary,
)
from sut.common.repeated_requests import REPEATED_REQUEST_KEY, RepeatedRequests
from sut.common.tool_schema_transport import decode_json_string_arguments


READS = frozenset({"get_topology", "get_state", "get_running_config", "get_object"})
MUTATIONS = frozenset({"update_config", "update_object", "rollback_config"})
VALIDATIONS = frozenset({"execute_validation"})
#: The reads whose answers carry each device's text, a configuration or a state view:
#: the lines of that text can be filtered and paged inside one answer.
TEXT_READS = frozenset({"get_state", "get_running_config"})

#: Arista EOS writes an interface's name in full in its configuration and short in its
#: tables: `show interfaces status`, get_state's `interfaces` view, lists Ethernet3 as
#: Et3, so a line_filter or filter_text of "Ethernet3" found nothing there (B9 live
#: check on campus_small, 2026-10-27). Both filters match an interface in either spelling.
EOS_SHORT_NAMES = {"et": "ethernet", "po": "port-channel", "ma": "management", "vl": "vlan",
                   "lo": "loopback", "vx": "vxlan", "tu": "tunnel"}
_EOS_SHORT_NAME = re.compile(r"(?<![a-z0-9-])(" + "|".join(EOS_SHORT_NAMES) + r")(?=\d)")
EITHER_SPELLING = "An interface name matches in either of Arista EOS's spellings (Ethernet3 or Et3)."

#: Said in the envelope of a text read cut at `max_context_chars`. Before it, a cut
#: answer named its size and nothing else: LangChain repaired none of the 15 episodes
#: of E16 to E18 where the fault was an ACL filter that leaf1's configuration lists
#: after about 75 000 characters of default CPM filters.
READ_FURTHER = ("line_filter keeps the lines of each device's text that contain a given string, with their line "
                "numbers; line_offset and line_limit return those lines a page at a time.")

#: One `update_config` change per write platform, as the ANI's preflight accepts it
#: (a test holds each to that platform's parser). In E17 230 of LangChain's 309
#: writes failed: 107 SR Linux lines not in the `set / <path>` form, 46 Linux commands
#: outside the modelled grammar, 6 with `commit`; in E18, 15 of 57 were reads. In E19
#: (B17) VyOS was written as paths, `delete /service/dhcp-server/...`, 7 refusals in
#: dhcp_provisioning.m2, and zone pairs backwards, `set firewall zone TRUST from
#: UNTRUST ...` for TRUST -> UNTRUST, in zone_policy_enforcement m1 and m2.
UPDATE_CONFIG_EXAMPLES: dict[str, tuple[str, list[str], str]] = {
    "nokia_srlinux": ("SR Linux", ["set / interface ethernet-1/30 admin-state enable"],
                      "every line starts with 'set /' or 'delete /'"),
    "vyos": ("VyOS", ["set interfaces ethernet eth3 address 172.16.2.254/24",
                      "set firewall zone UNTRUST from TRUST firewall name TRUST-UNTRUST"],
             "every line starts with 'set' or 'delete' followed by words, never a '/' path; a zone pair names "
             "the destination zone first: 'set firewall zone <to> from <from> firewall name <ruleset>' "
             "applies the ruleset to traffic from <from> to <to>"),
    "arista_ceos": ("Arista EOS", ["interface Ethernet3", "switchport access vlan 100"],
                    "configuration lines in order, a mode line first; no 'exit' or 'end', the next mode line "
                    "changes mode"),
    "linux": ("Linux", ["ip route replace default via 10.10.40.1 dev eth1"],
              "one 'ip link set', 'ip address', 'ip route' or 'tc' command per change"),
}

UPDATE_CONFIG_DESCRIPTION = (
    "Apply guarded native configuration changes through ANI. Send configuration lines only: the ANI opens the "
    "session and commits it, so never enter, configure, commit, exit or quit, and reads go through get_running_config, "
    "get_state and get_object, never here. One change's commands per platform: "
    + "; ".join(f"{label} {json.dumps(commands)} ({rule})"
                for label, commands, rule in UPDATE_CONFIG_EXAMPLES.values())
    + ". "
)


_PARAMETER_SCHEMAS: dict[type, dict[str, Any]] = {}


class ANIArguments(BaseModel):
    """Strict base for the exact ANI v0.1 schema shown to the model."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _decode_json_string_containers(cls, data: Any) -> Any:
        """A list or object that arrived as a JSON string is decoded before validation.

        That is how vLLM's XML tool-call parser delivers a nested argument whose
        schema it could not type (sut.common.tool_schema_transport). The contract
        is unchanged: a scalar or a malformed string fails exactly as before.
        """
        if not isinstance(data, dict):
            return data
        parameters = _PARAMETER_SCHEMAS.get(cls)
        if parameters is None:
            parameters = _PARAMETER_SCHEMAS[cls] = cls.model_json_schema()
        decoded, _ = decode_json_string_arguments(data, parameters)
        return decoded


class PagedArguments(ANIArguments):
    offset: int = Field(default=0, ge=0, description="First returned item.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum returned items.")
    filter_text: str | None = Field(default=None, description="Optional case-insensitive filter on the returned "
                                                                   "items: an item that contains it is kept whole. "
                                                                   + EITHER_SPELLING)


class TextPagedArguments(PagedArguments):
    """Paging inside each device's text, where `offset` and `limit` page over devices.

    Separate parameters rather than a second meaning of `limit`: in E16 to E18
    LangChain passed `limit`, usually 100, on 859 of its 1256 one-device reads.
    """

    line_filter: str | None = Field(default=None, description="Keep only the lines of each device's text that "
                                                                   "contain this case-insensitive text, numbered as in "
                                                                   "the whole text. " + EITHER_SPELLING)
    line_offset: int = Field(default=0, ge=0, description="First returned line of each device's text, counted "
                                                          "after line_filter.")
    line_limit: int | None = Field(default=None, ge=1, le=1000, description="Maximum returned lines of each "
                                                                             "device's text; omit for all.")


class GetTopologyArguments(PagedArguments):
    nodes: list[str] | None = Field(default=None, description="Optional reviewed node names; omit for all nodes.")


class GetStateArguments(TextPagedArguments):
    nodes: list[str] | None = Field(default=None, description="Optional node names; omit for all nodes.")
    views: list[Literal["interfaces", "routes", "qdisc", "system"]] | None = Field(
        default=None,
        description="Operational state categories; omit for interfaces and routes.",
    )


class GetRunningConfigArguments(TextPagedArguments):
    nodes: list[str] = Field(min_length=1, description="One or more node names.")
    paths: list[str] | None = Field(default=None, description="Optional configuration paths, one section each: "
                                                              "SR Linux '/interface ethernet-1/30', VyOS 'interfaces "
                                                              "ethernet eth3', Arista EOS 'interface Ethernet3'.")
    # Until 2026-11-15 this said "JSON for whole-device structured SR Linux data", which the
    # JSON read has not been since it served VyOS and Linux: on main03, a QoS class of a
    # Linux node was readable through this subject's tools only in that form.
    format: Literal["text", "json"] = Field(
        default="text",
        description="Text for scoped native output. JSON for the whole device, paths ignored: structured data "
                    "on SR Linux, VyOS and Arista EOS; on Linux the text of its ip, ss and tc commands, with tc "
                    "qdisc, class and filter for each interface.",
    )


class ConfigChange(ANIArguments):
    target: str = Field(min_length=1, description="Reviewed node name.")
    commands: list[str] = Field(min_length=1, description="Native configuration commands for this node.")
    rollback_commands: list[str] | None = Field(default=None, description="Optional compensating commands.")
    reason: str | None = Field(default=None, description="Evidence-based reason for the change.")


class GetObjectArguments(ANIArguments):
    node: str = Field(min_length=1, description="One node name, as get_topology lists it.")
    # The ANI's kinds, so a kind added there (v0.9: every part of a VyOS or EOS configuration)
    # reaches the subjects that offer this read.
    kind: Literal[OBJECT_KINDS] = Field(  # type: ignore[valid-type]
        description="What the name denotes on that node.")
    name: str = Field(min_length=1, max_length=80,
                      description="The object's name as the node writes it: ethernet-1/40, eth1, 10.10.40.0/24, "
                                  "an IPv4 or MAC address, a ruleset, zone, pool or group name, source or "
                                  "destination for a NAT, a service or system setting, a VLAN id, or the first "
                                  "word of a configuration section.")


class UpdateObjectArguments(ANIArguments):
    node: str = Field(min_length=1, description="One node name, as get_topology lists it.")
    kind: Literal["interface", "route", "routing", "acl", "dhcp_pool", "firewall_rule", "zone", "qos"] = Field(
        description="What the name denotes on that node.")
    name: str = Field(min_length=1, max_length=80,
                      description="The object's name as the node writes it: an interface or subinterface "
                                  "(ethernet-1/3, ethernet-1/3.0, eth1, eth1.10), a prefix or 'default', a routing "
                                  "instance ('default'), an ACL filter, a DHCP pool, a rule set or 'forward' (the "
                                  "forward filter), a zone.")
    set: dict[str, Any] = Field(min_length=1,
                                description="Attributes to change: interface admin_state (enable|disable; a subinterface "
                                            "name sets the subinterface), ipv4_admin_state (SR Linux subinterface), mtu, "
                                            "address (with prefix length) + address_action (add|delete; no replace: delete "
                                            "the old address, then add the new one); route next_hop|next_hop_group|delete; "
                                            "routing admin_state (SR Linux instance; VyOS IPv4 forwarding); "
                                            "acl delete+unbind_interfaces, or delete_entry (entry ids); "
                                            "dhcp_pool subnet+subnet_id/default_router/name_server/domain_name/domain_search/lease/"
                                            "static_mapping/delete_option|delete; firewall_rule rule+action|delete, default_action; "
                                            "zone (the name is the destination zone) from_zone+ruleset|delete_from|"
                                            "add_interface|delete_interface; "
                                            "qos delete_root, htb {default, rate, ceil, classes, filters} (the whole "
                                            "hierarchy: rate is the circuit's link rate, the root class 1:1, and classes "
                                            "is a list of class objects that includes the default class 1:<default>; add "
                                            "delete_root to rebuild one, as delete_root alone on an HTB root is refused), "
                                            "class {id, rate, ceil, prio, parent} (one class changed in place, its id "
                                            "written 1:<minor>, as in each object of htb.classes), "
                                            "filter {src|dst, flowid, prio}, delete_filter {prio}.")
    reason: str | None = Field(default=None, description="Evidence-based reason for the change.")


class UpdateConfigArguments(ANIArguments):
    changes: list[ConfigChange] = Field(
        min_length=1,
        description="Scoped changes. The ANI opens and commits configuration sessions.",
    )


class ValidationCheck(ANIArguments):
    type: Literal["public_success_criteria", "icmp"] = Field(
        description="Use public_success_criteria for the complete task objective, or icmp for one path."
    )
    source: str | None = Field(default=None, description="icmp: the node the pings are sent from.")
    destination: str | None = Field(default=None, description="icmp: a name the source resolves.")
    destination_ip: str | None = Field(default=None, description=(
        "icmp: an IPv4 address on the data plane; one in the lab's management network is refused."))
    count: int | None = Field(default=None, ge=1, le=20, description="icmp: pings sent, 3 when omitted.")
    timeout_seconds: int | None = Field(default=None, ge=1, le=10,
                                        description="icmp: seconds to wait for each reply, 3 when omitted.")


class ExecuteValidationArguments(ANIArguments):
    checks: list[ValidationCheck] = Field(
        min_length=1,
        description="Validation requests. Do not submit child success-criterion types directly.",
    )

def _plain(value: Any) -> Any:
    """Convert LangChain/Pydantic tool values to ANI's JSON-native contract."""
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _in_full(text: str) -> str:
    """`text` casefolded, with EOS's short interface names written in full: Et3 is ethernet3."""
    return _EOS_SHORT_NAME.sub(lambda match: EOS_SHORT_NAMES[match.group(1)], text.casefold())


def _containing(needle: str) -> Callable[[str], bool]:
    """Whether a text contains `needle`, case-insensitively, an interface name in either
    spelling: "Ethernet3" finds `Et3   connected`, "Et3" finds `interface Ethernet3`."""
    plain, full = needle.casefold(), _in_full(needle)
    return lambda text: plain in text.casefold() or full in _in_full(text)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def _select_lines(item: Any, texts: list[dict[str, Any]], line_filter: str | None, line_offset: int,
                  line_limit: int | None, *, count_only: bool = False,
                  containing: Callable[[str], Callable[[str], bool]] = _containing) -> Any:
    """`item` with each of its texts cut to the lines asked for, one entry per text in `texts`.

    A text is a string of more than one line wherever it sits: a configuration's
    stdout, a state view's. A kept line carries its number in the whole text, as
    `grep -n` writes it, so a page read after a filter lines up with it.
    """
    node = item.get("name") if isinstance(item, dict) else None
    contains = containing(line_filter) if line_filter else None

    def walk(value: Any, field: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            return {key: walk(child, (*field, str(key))) for key, child in value.items()}
        if isinstance(value, list):
            return [walk(child, (*field, str(position))) for position, child in enumerate(value)]
        if not isinstance(value, str) or "\n" not in value:
            return value
        lines = list(enumerate(value.splitlines(), start=1))
        entry: dict[str, Any] = {"node": node, "field": "/".join(field), "total": len(lines)}
        texts.append(entry)
        if count_only:
            return value
        if contains is not None:
            lines = [(number, line) for number, line in lines if contains(line)]
            entry["matching"] = len(lines)
        page = lines[line_offset:None if line_limit is None else line_offset + line_limit]
        entry["returned"] = len(page)
        return "\n".join(f"{number}:{line}" for number, line in page)

    return walk(item, ())


class RollbackConfigArguments(ANIArguments):
    transaction_id: str = Field(min_length=1, description="Exact transaction_id returned by update_config.")


class SubmitResultArguments(ANIArguments):
    status: Literal["completed", "failed"]
    summary: str = Field(min_length=1, description=SUBMIT_SUMMARY_DESCRIPTION)
    diagnosis: str | None = Field(default=None, description=SUBMIT_DIAGNOSIS_DESCRIPTION)

class ExecutionBudgetExceeded(TimeoutError):
    """The SUT exhausted its public wall-clock budget."""


@dataclass
class ANITrace:
    operations: list[dict[str, Any]] = field(default_factory=list)
    device_changes: list[dict[str, Any]] = field(default_factory=list)
    #: The last public validation, cleared by any write after it: what `verified` and
    #: `final_observation` read, as the shared loop's `latest_objective`.
    final_validation: dict[str, Any] | None = None
    failed_operations: int = 0
    unsafe_operations: int = 0
    #: The conclusion the ANI accepted through `submit_result`; the agent stops on it.
    submission: dict[str, Any] | None = None
    #: Identical failed requests, by their index in `operations`: the answers that
    #: carried the note, and each applied write that started the count again.
    repeated_requests: RepeatedRequests = field(default_factory=RepeatedRequests)
    #: The one `failed` sent back with more than half the budget left
    #: (`LangChainANIAdapter._early_failure_pushback`), or None.
    early_failure_pushback: dict[str, Any] | None = None
    #: The one `completed` sent back for want of a passing public validation since the
    #: last write (`LangChainANIAdapter._unverified_completion_pushback`), or None.
    unverified_completion_pushback: dict[str, Any] | None = None
    #: Each reply with neither a tool call nor a conclusion that was sent back to the model
    #: (`agent._nudge_on_no_action`): the operations made before it and the reply's length.
    no_action_nudges: list[dict[str, Any]] = field(default_factory=list)


class LangChainANIAdapter:
    """Expose ANI as bounded LangChain tools without coupling ANI to LangChain.

    Large results are deterministically paged, filtered and truncated. Whenever
    truncation occurs, the complete JSON result is saved as a content-addressed
    artifact and the model-visible envelope explicitly reports its size and path.
    """

    #: How `filter_text` and `line_filter` match, and what a cut text read says about
    #: reading on. A subject built on this adapter may give its own.
    containing = staticmethod(_containing)
    read_further: str | None = READ_FURTHER

    def __init__(
        self,
        ani: ContainerLabANI,
        *,
        task: dict[str, Any],
        artifact_directory: str | Path = "reports/manual/results/artifacts",
        max_context_chars: int = 12_000,
        default_page_size: int = 20,
        deadline: float | None = None,
        budget_seconds: float | None = None,
        ani_call_limit: int | None = None,
    ) -> None:
        if max_context_chars < 512:
            raise ValueError("max_context_chars must be at least 512")
        self.ani = ani
        self.task = task
        self.artifact_directory = Path(artifact_directory)
        self.max_context_chars = max_context_chars
        # The ANI cap is enforced by the ANI itself, which every call here passes
        # through, so a refusal arrives as an ordinary failed result and is recorded
        # by the same path as any other answer rather than bypassing it.
        self.ani_call_limit = ani_call_limit
        self.default_page_size = default_page_size
        self.deadline = deadline
        #: The episode's budget, whose half decides whether a `failed` is sent back.
        self.budget_seconds = budget_seconds
        self.trace = ANITrace()
        # The public validation a `completed` needs behind it: the last one, cleared by
        # any write the model sends, as the shared loop's completion gate reads it; and
        # whether one ran at all.
        self._backing_validation: dict[str, Any] | None = None
        self._validated_once = False

    def _early_failure_pushback(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Send back, once, a `failed` submitted with more than half the budget left.

        The other subjects did so since E15; this subject had no such rule. In E20 three of the four scenarios only LangChain
        failed ended on such a `failed`: disable_interface.high.m1 at 31 s, a mistaken
        call whose summary read "will use icmp validation", disable_routing.high.m1 at
        1 404 s and drop_traffic_to_from_subnet.high.m2 at 1 377 s. The next `failed` goes
        to the ANI, as does any submission once half the budget is spent. No deadline or
        no budget, no episode: nothing is sent back.
        """
        if self.deadline is None or not self.budget_seconds or self.trace.early_failure_pushback is not None:
            return None
        if str(arguments.get("status", "")).strip().lower() != "failed":
            return None
        remaining = self.deadline - time.monotonic()
        budget = self.budget_seconds
        if remaining <= budget / 2:
            return None
        self.trace.early_failure_pushback = {"elapsed_seconds": round(budget - remaining, 1),
                                             "remaining_seconds": round(remaining, 1),
                                             "budget_seconds": budget}
        return {
            "ok": False,
            "operation": SUBMISSION_OPERATION,
            "submitted": False,
            "error": (
                f"Not submitted: {int(remaining)} of your {int(budget)} seconds remain, more "
                "than half. Before concluding that no safe repair can be found, test your next "
                "hypothesis: make the minimal change, validate, and keep it or roll it back "
                "with rollback_config. If you still conclude that no safe repair can be found, "
                "submit again: it will be accepted."),
        }

    def _unverified_completion_pushback(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Send back, once, a `completed` that no passing public validation backs.

        In E19 six of this subject's failures concluded `completed` with no passing
        validation behind them (drop_traffic_to_from_subnet.high.m4,
        wrong_routing_table.high.m4, zone_policy_enforcement.high.m1, wan_shaping m1, m2
        and m4), and the episode ended there. The same submission is now sent back
        once; the next `completed` goes to
        the ANI whatever its validation. No deadline, no episode: nothing is sent back.
        """
        if self.deadline is None or self.trace.unverified_completion_pushback is not None:
            return None
        if str(arguments.get("status", "")).strip().lower() != "completed":
            return None
        if self._backing_validation is not None and self._backing_validation.get("passed"):
            return None
        if self._backing_validation is not None:
            reason = "the last execute_validation failed"
        elif self._validated_once:
            reason = "the network changed after the last execute_validation"
        else:
            reason = "no execute_validation was run"
        self.trace.unverified_completion_pushback = {
            "remaining_seconds": round(self.deadline - time.monotonic(), 1),
            "reason": reason,
        }
        return {
            "ok": False,
            "operation": SUBMISSION_OPERATION,
            "submitted": False,
            "error": (
                f"Not submitted: {reason}. A task is completed when execute_validation with "
                "type public_success_criteria passes after your last change to the network. "
                "Run it; if it fails, the repair is not done: find what still fails, or roll "
                "back with rollback_config. If you still conclude that the task is completed, "
                "submit again: it will be accepted."),
        }

    def _invoke(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        offset: int = 0,
        limit: int | None = None,
        filter_text: str | None = None,
        line_filter: str | None = None,
        line_offset: int = 0,
        line_limit: int | None = None,
    ) -> str:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ExecutionBudgetExceeded("execution budget exhausted before ANI tool call")
        arguments = _plain(arguments)
        if self.trace.submission is not None:
            # A call sent in the same turn as the submission: the episode is over, and
            # nothing reaches the network after the agent's own end (as in the shared loop).
            return json.dumps({"ok": False, "operation": operation,
                               "error": "the episode ended with submit_result; this call was not sent"})
        started = time.monotonic()
        pushback = ((self._early_failure_pushback(arguments)
                     or self._unverified_completion_pushback(arguments))
                    if operation == SUBMISSION_OPERATION else None)
        if pushback is not None:
            # Recorded below like any answer: the model's call is in the record, refused.
            raw = pushback
        else:
            try:
                raw = self.ani.dispatch(operation, arguments, task=self.task)
            except Exception as exc:
                raw = {"ok": False, "operation": operation, "error": str(exc)}
        elapsed = time.monotonic() - started
        category = (
            "read" if operation in READS else
            "mutation" if operation in MUTATIONS else
            "validation" if operation in VALIDATIONS else
            "submission" if operation == SUBMISSION_OPERATION else "tool"
        )
        if operation == SUBMISSION_OPERATION and raw.get("ok") and raw.get("submitted"):
            self.trace.submission = {key: raw.get(key) for key in ("status", "summary", "diagnosis")}
        index = len(self.trace.operations)
        changes = device_changes_from_ani_result(raw)
        if raw.get("ok", False) and operation in MUTATIONS and changes:
            # A write the ANI applied changed the network the counted requests asked
            # about, so the same request after it is a new question and the count starts
            # again. A refused write or a no-op (`changes` empty) changed nothing.
            self.trace.repeated_requests.changed(index)
        note = None
        if not raw.get("ok", False):
            self.trace.failed_operations += 1
            note = self.trace.repeated_requests.failed(operation, arguments, raw.get("error"), index)
        bounded, artifact_ref = self._bounded(
            raw, offset=offset, limit=limit, filter_text=filter_text,
            line_filter=line_filter, line_offset=line_offset, line_limit=line_limit,
            text_read=operation in TEXT_READS, validation=operation in VALIDATIONS, note=note,
        )
        event = operation_summary(operation, raw, elapsed, arguments)
        event["category"] = category
        event["artifact_ref"] = artifact_ref
        # The paging the model asked for, which the ANI never sees: without it two
        # pages of one text read as the same call. Only what differs from the defaults.
        paging = {key: value for key, value, default in (
            ("offset", offset, 0), ("limit", limit, self.default_page_size), ("filter_text", filter_text, None),
            ("line_filter", line_filter, None), ("line_offset", line_offset, 0), ("line_limit", line_limit, None),
        ) if value not in (default, None)}
        if paging:
            event["paging"] = paging
        self.trace.operations.append(event)
        self.trace.device_changes.extend(changes)
        if operation in MUTATIONS:
            # Even refused: an indeterminate write is when an older pass is least safe.
            # The record's validation goes with it, as the shared loop's does
            # (`self_execute.can_have_changed_the_network`): `verified` and
            # `final_observation` describe the network the episode ended on. Until
            # 2026-11-15 a validation passed before a last write still verified here.
            # A call `create_agent` refused before any tool ran never reaches this.
            self._backing_validation = None
            self.trace.final_validation = None
        validation = objective_from_validation(raw)
        if validation is not None:
            self.trace.final_validation = validation
            self._backing_validation = validation
            self._validated_once = True
        if any(not bool((change.get("result") or {}).get("safe", True)) for change in raw.get("changes") or [] if isinstance(change, dict)):
            self.trace.unsafe_operations += 1
        return bounded

    def _bounded(
        self,
        raw: dict[str, Any],
        *,
        offset: int,
        limit: int | None,
        filter_text: str | None,
        line_filter: str | None = None,
        line_offset: int = 0,
        line_limit: int | None = None,
        text_read: bool = False,
        validation: bool = False,
        note: str | None = None,
    ) -> tuple[str, str | None]:
        limit = self.default_page_size if limit is None else max(1, min(limit, 100))
        offset = max(0, offset)
        line_offset = max(0, line_offset)
        complete = json.dumps(raw, sort_keys=True, default=str, separators=(",", ":"))
        sha256 = hashlib.sha256(complete.encode()).hexdigest()
        if validation:
            # A validation result is the verdict the subject decides on, so it goes whole and
            # in the ANI's own order (`ok` and `passed` before the checks), as the shared
            # loop gives it. Sorted and cut at max_context_chars, a public check put both past
            # the cut whenever its ping list or a throughput criterion made it long (E21: every
            # QoS result, 310 of 322 on campus_large), and paging cannot reach them, the check
            # being one item.
            rendered = json.dumps(raw, default=str, separators=(",", ":"))
            envelope = {
                "data": rendered,
                "context": {
                    "truncated": False,
                    "complete_size_bytes": len(complete.encode()),
                    "returned_size_bytes": len(rendered.encode()),
                    "sha256": sha256,
                    "artifact_ref": None,
                    "page": {"offset": 0, "limit": None, "total_items": None},
                    "filter": None,
                },
            }
            if note is not None:
                envelope[REPEATED_REQUEST_KEY] = note
            return json.dumps(envelope, sort_keys=True), None
        data = json.loads(complete)
        page_key = next((key for key in ("nodes", "records", "segments", "checks", "changes") if isinstance(data.get(key), list)), None)
        total_items = None
        if page_key:
            items = data[page_key]
            if filter_text:
                # The whole JSON for a filter that spans a key and its value; each string
                # on its own for a short name after a line break, which the JSON hides
                # by writing it \nEt3.
                contains = self.containing(filter_text)
                items = [item for item in items
                         if contains(json.dumps(item, sort_keys=True, default=str)) or any(map(contains, _strings(item)))]
            total_items = len(items)
            data[page_key] = items[offset: offset + limit]
        texts: list[dict[str, Any]] = []
        selecting = bool(text_read and page_key and (line_filter or line_offset or line_limit is not None))
        if selecting:
            data[page_key] = [_select_lines(item, texts, line_filter, line_offset, line_limit,
                                            containing=self.containing)
                              for item in data[page_key]]
        rendered = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
        cut = len(rendered) > self.max_context_chars
        if cut and text_read and page_key and not selecting:
            # Counted before the cut, so the model knows how many lines there are to page.
            for item in data[page_key]:
                _select_lines(item, texts, None, 0, None, count_only=True)
        truncated = cut or (total_items is not None and offset + limit < total_items) or any(
            line_offset + entry["returned"] < entry.get("matching", entry["total"])
            for entry in texts if "returned" in entry)
        artifact_ref = None
        if truncated:
            self.artifact_directory.mkdir(parents=True, exist_ok=True)
            artifact = self.artifact_directory / f"ani-{sha256}.json"
            if not artifact.exists():
                artifact.write_text(json.dumps(raw, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            artifact_ref = str(artifact)
            if len(rendered) > self.max_context_chars:
                rendered = rendered[: self.max_context_chars]
        envelope = {
            "data": rendered,
            "context": {
                "truncated": truncated,
                "complete_size_bytes": len(complete.encode()),
                "returned_size_bytes": len(rendered.encode()),
                "sha256": sha256,
                "artifact_ref": artifact_ref,
                "page": {"offset": offset, "limit": limit, "total_items": total_items},
                "filter": filter_text,
            },
        }
        if texts or selecting:
            # Empty when the lines asked for have no text to come from (format json).
            envelope["context"]["lines"] = {"filter": line_filter, "offset": line_offset, "limit": line_limit,
                                            "texts": texts}
        if cut and texts and self.read_further:
            envelope["context"]["read_further"] = (
                f"This answer was cut at {self.max_context_chars} characters. " + self.read_further)
        if note is not None:
            # Beside the data, not in it, so no cut can drop it.
            envelope[REPEATED_REQUEST_KEY] = note
        return json.dumps(envelope, sort_keys=True), artifact_ref

    def tools(self) -> list[BaseTool]:
        adapter = self

        @tool("get_topology", args_schema=GetTopologyArguments)
        def get_topology(nodes: list[str] | None = None, offset: int = 0, limit: int = 20, filter_text: str | None = None) -> str:
            """Discover reviewed topology, optionally scoped to named nodes."""
            return adapter._invoke("get_topology", {"nodes": nodes}, offset=offset, limit=limit, filter_text=filter_text)

        @tool("get_state", args_schema=GetStateArguments)
        def get_state(nodes: list[str] | None = None, views: list[str] | None = None, offset: int = 0, limit: int = 20, filter_text: str | None = None, line_filter: str | None = None, line_offset: int = 0, line_limit: int | None = None) -> str:
            """Read selected operational state views for selected nodes. offset, limit and filter_text page over the nodes; line_filter, line_offset and line_limit select lines inside each node's text."""
            return adapter._invoke("get_state", {"nodes": nodes, "views": views}, offset=offset, limit=limit, filter_text=filter_text,
                                   line_filter=line_filter, line_offset=line_offset, line_limit=line_limit)

        @tool("get_running_config", args_schema=GetRunningConfigArguments)
        def get_running_config(nodes: list[str], paths: list[str] | None = None, format: str = "text", offset: int = 0, limit: int = 20, filter_text: str | None = None, line_filter: str | None = None, line_offset: int = 0, line_limit: int | None = None) -> str:
            """Read scoped running configuration with explicit paging and filtering. offset, limit and filter_text page over the nodes; line_filter, line_offset and line_limit select lines inside each node's text."""
            return adapter._invoke("get_running_config", {"nodes": nodes, "paths": paths, "format": format}, offset=offset, limit=limit, filter_text=filter_text,
                                   line_filter=line_filter, line_offset=line_offset, line_limit=line_limit)

        @tool("get_object", args_schema=GetObjectArguments)
        def get_object(node: str, kind: str, name: str) -> str:
            """Read one named object on one node (small output): an interface, a route, an ARP or MAC entry, a QoS policy, a firewall ruleset, a zone, a DHCP pool, a firewall group, a NAT direction, a service, a system setting, a VLAN or another configuration section."""
            return adapter._invoke("get_object", {"node": node, "kind": kind, "name": name})

        # The two writes carry the ANI's management-plane rule: a description is set
        # explicitly because a docstring cannot interpolate it.
        @tool("update_config", args_schema=UpdateConfigArguments,
              description=UPDATE_CONFIG_DESCRIPTION + MANAGEMENT_PLANE_RULE)
        def update_config(changes: list[dict[str, Any]]) -> str:
            return adapter._invoke("update_config", {"changes": changes})

        @tool("update_object", args_schema=UpdateObjectArguments,
              description="Change one named object on one node through attributes; the ANI writes the native "
                          "commands, with the same safety checks and rollback as update_config. Prefer it to "
                          "update_config for a DHCP pool option, a zone pair and a traffic-control hierarchy: "
                          "the ANI writes their syntax. Arista EOS switches have no objects here: change them "
                          "through update_config. "
                          + MANAGEMENT_PLANE_RULE)
        def update_object(node: str, kind: str, name: str, set: dict[str, Any], reason: str | None = None) -> str:
            return adapter._invoke("update_object", {"node": node, "kind": kind, "name": name, "set": set, "reason": reason})

        @tool("execute_validation", args_schema=ExecuteValidationArguments)
        def execute_validation(checks: list[dict[str, Any]]) -> str:
            """Validate public task criteria or a selected network path."""
            return adapter._invoke("execute_validation", {"checks": checks})

        @tool("rollback_config", args_schema=RollbackConfigArguments)
        def rollback_config(transaction_id: str) -> str:
            """Roll back a prior ANI transaction with recorded compensation."""
            return adapter._invoke("rollback_config", {"transaction_id": transaction_id})

        # The agent's end of the episode: once the ANI accepts it, the agent stops
        # before its next model call (LangChainRepairAgent's middleware).
        @tool("submit_result", args_schema=SubmitResultArguments, description=SUBMIT_RESULT_DESCRIPTION)
        def submit_result(status: str, summary: str, diagnosis: str | None = None) -> str:
            return adapter._invoke("submit_result", {"status": status, "summary": summary, "diagnosis": diagnosis})

        return [get_topology, get_state, get_running_config, get_object, update_config, update_object,
                execute_validation, rollback_config, submit_result]
