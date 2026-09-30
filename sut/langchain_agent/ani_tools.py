"""LangChain tools adapting the framework-independent ANI contract."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from benchmarks.platforms.containerlab import ContainerLabANI
from sut.common.ani_report import device_changes_from_ani_result, objective_from_validation, operation_summary
from sut.common.tool_schema_transport import decode_json_string_arguments


READS = frozenset({"get_topology", "get_state", "get_running_config", "get_object"})
MUTATIONS = frozenset({"update_config", "update_object", "rollback_config"})
VALIDATIONS = frozenset({"execute_validation"})


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
    filter_text: str | None = Field(default=None, description="Optional case-insensitive result filter.")


class GetTopologyArguments(PagedArguments):
    nodes: list[str] | None = Field(default=None, description="Optional reviewed node names; omit for all nodes.")


class GetStateArguments(PagedArguments):
    nodes: list[str] | None = Field(default=None, description="Optional node names; omit for all nodes.")
    views: list[Literal["interfaces", "routes", "qdisc", "system"]] | None = Field(
        default=None,
        description="Operational state categories; omit for interfaces and routes.",
    )


class GetRunningConfigArguments(PagedArguments):
    nodes: list[str] = Field(min_length=1, description="One or more node names.")
    paths: list[str] | None = Field(default=None, description="Optional datastore paths.")
    format: Literal["text", "json"] = Field(
        default="text",
        description="Text for scoped native output; JSON for whole-device structured SR Linux data.",
    )


class ConfigChange(ANIArguments):
    target: str = Field(min_length=1, description="Reviewed node name.")
    commands: list[str] = Field(min_length=1, description="Native configuration commands for this node.")
    rollback_commands: list[str] | None = Field(default=None, description="Optional compensating commands.")
    reason: str | None = Field(default=None, description="Evidence-based reason for the change.")


class GetObjectArguments(ANIArguments):
    node: str = Field(min_length=1, description="One node name, as get_topology lists it.")
    kind: Literal["interface", "route", "arp", "mac", "qos", "firewall_rule", "zone", "dhcp_pool"] = Field(
        description="What the name denotes on that node.")
    name: str = Field(min_length=1, max_length=80,
                      description="The object's name as the node writes it: ethernet-1/40, eth1, 10.10.40.0/24, "
                                  "an IPv4 or MAC address, a ruleset, zone or pool name.")


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
                                            "zone from_zone+ruleset|delete_from|add_interface|delete_interface; "
                                            "qos delete_root, htb (whole hierarchy; add delete_root to rebuild one), class, "
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
    source: str | None = None
    destination: str | None = None
    destination_ip: str | None = None
    count: int | None = Field(default=None, ge=1, le=20)
    timeout_seconds: int | None = Field(default=None, ge=1, le=10)


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


class RollbackConfigArguments(ANIArguments):
    transaction_id: str = Field(min_length=1, description="Exact transaction_id returned by update_config.")

class ExecutionBudgetExceeded(TimeoutError):
    """The SUT exhausted its public wall-clock budget."""

class RepeatedANIRequestError(RuntimeError):
    """The model repeated the same failing ANI request three times."""



@dataclass
class ANITrace:
    operations: list[dict[str, Any]] = field(default_factory=list)
    device_changes: list[dict[str, Any]] = field(default_factory=list)
    final_validation: dict[str, Any] | None = None
    failed_operations: int = 0
    unsafe_operations: int = 0


class LangChainANIAdapter:
    """Expose ANI as bounded LangChain tools without coupling ANI to LangChain.

    Large results are deterministically paged, filtered and truncated. Whenever
    truncation occurs, the complete JSON result is saved as a content-addressed
    artifact and the model-visible envelope explicitly reports its size and path.
    """

    def __init__(
        self,
        ani: ContainerLabANI,
        *,
        task: dict[str, Any],
        artifact_directory: str | Path = "reports/manual/results/artifacts",
        max_context_chars: int = 12_000,
        default_page_size: int = 20,
        deadline: float | None = None,
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
        self.trace = ANITrace()
        self._failure_signatures: dict[str, int] = {}

    def _invoke(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        offset: int = 0,
        limit: int | None = None,
        filter_text: str | None = None,
    ) -> str:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ExecutionBudgetExceeded("execution budget exhausted before ANI tool call")
        arguments = _plain(arguments)
        started = time.monotonic()
        try:
            raw = self.ani.dispatch(operation, arguments, task=self.task)
        except Exception as exc:
            raw = {"ok": False, "operation": operation, "error": str(exc)}
        elapsed = time.monotonic() - started
        category = (
            "read" if operation in READS else
            "mutation" if operation in MUTATIONS else
            "validation" if operation in VALIDATIONS else "tool"
        )
        bounded, artifact_ref = self._bounded(
            raw, offset=offset, limit=limit, filter_text=filter_text
        )
        event = operation_summary(operation, raw, elapsed, arguments)
        event["category"] = category
        event["artifact_ref"] = artifact_ref
        self.trace.operations.append(event)
        self.trace.device_changes.extend(device_changes_from_ani_result(raw))
        validation = objective_from_validation(raw)
        if validation is not None:
            self.trace.final_validation = validation
        if not raw.get("ok", False):
            self.trace.failed_operations += 1
            signature = json.dumps(
                {"operation": operation, "arguments": arguments, "error": raw.get("error")},
                sort_keys=True,
                default=str,
            )
            repeats = self._failure_signatures.get(signature, 0) + 1
            self._failure_signatures[signature] = repeats
        if any(not bool((change.get("result") or {}).get("safe", True)) for change in raw.get("changes") or [] if isinstance(change, dict)):
            self.trace.unsafe_operations += 1
        if not raw.get("ok", False) and repeats >= 3:
            raise RepeatedANIRequestError(
                f"stopped after 3 identical failed {operation} requests"
            )
        return bounded

    def _bounded(
        self,
        raw: dict[str, Any],
        *,
        offset: int,
        limit: int | None,
        filter_text: str | None,
    ) -> tuple[str, str | None]:
        limit = self.default_page_size if limit is None else max(1, min(limit, 100))
        offset = max(0, offset)
        complete = json.dumps(raw, sort_keys=True, default=str, separators=(",", ":"))
        sha256 = hashlib.sha256(complete.encode()).hexdigest()
        data = json.loads(complete)
        page_key = next((key for key in ("nodes", "records", "segments", "checks", "changes") if isinstance(data.get(key), list)), None)
        total_items = None
        if page_key:
            items = data[page_key]
            if filter_text:
                needle = filter_text.casefold()
                items = [item for item in items if needle in json.dumps(item, sort_keys=True, default=str).casefold()]
            total_items = len(items)
            data[page_key] = items[offset: offset + limit]
        rendered = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
        truncated = len(rendered) > self.max_context_chars or (
            total_items is not None and offset + limit < total_items
        )
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
        return json.dumps(envelope, sort_keys=True), artifact_ref

    def tools(self) -> list[BaseTool]:
        adapter = self

        @tool("get_topology", args_schema=GetTopologyArguments)
        def get_topology(nodes: list[str] | None = None, offset: int = 0, limit: int = 20, filter_text: str | None = None) -> str:
            """Discover reviewed topology, optionally scoped to named nodes."""
            return adapter._invoke("get_topology", {"nodes": nodes}, offset=offset, limit=limit, filter_text=filter_text)

        @tool("get_state", args_schema=GetStateArguments)
        def get_state(nodes: list[str] | None = None, views: list[str] | None = None, offset: int = 0, limit: int = 20, filter_text: str | None = None) -> str:
            """Read selected operational state views for selected nodes."""
            return adapter._invoke("get_state", {"nodes": nodes, "views": views}, offset=offset, limit=limit, filter_text=filter_text)

        @tool("get_running_config", args_schema=GetRunningConfigArguments)
        def get_running_config(nodes: list[str], paths: list[str] | None = None, format: str = "text", offset: int = 0, limit: int = 20, filter_text: str | None = None) -> str:
            """Read scoped running configuration with explicit paging and filtering."""
            return adapter._invoke("get_running_config", {"nodes": nodes, "paths": paths, "format": format}, offset=offset, limit=limit, filter_text=filter_text)

        @tool("get_object", args_schema=GetObjectArguments)
        def get_object(node: str, kind: str, name: str) -> str:
            """Read one named object on one node (small output): an interface, a route, an ARP or MAC entry, a QoS policy, a firewall ruleset, a zone or a DHCP pool."""
            return adapter._invoke("get_object", {"node": node, "kind": kind, "name": name})

        @tool("update_config", args_schema=UpdateConfigArguments)
        def update_config(changes: list[dict[str, Any]]) -> str:
            """Apply guarded native configuration changes through ANI."""
            return adapter._invoke("update_config", {"changes": changes})

        @tool("update_object", args_schema=UpdateObjectArguments)
        def update_object(node: str, kind: str, name: str, set: dict[str, Any], reason: str | None = None) -> str:
            """Change one named object on one node through attributes; the ANI writes the native commands, with the same safety checks and rollback as update_config."""
            return adapter._invoke("update_object", {"node": node, "kind": kind, "name": name, "set": set, "reason": reason})

        @tool("execute_validation", args_schema=ExecuteValidationArguments)
        def execute_validation(checks: list[dict[str, Any]]) -> str:
            """Validate public task criteria or a selected network path."""
            return adapter._invoke("execute_validation", {"checks": checks})

        @tool("rollback_config", args_schema=RollbackConfigArguments)
        def rollback_config(transaction_id: str) -> str:
            """Roll back a prior ANI transaction with recorded compensation."""
            return adapter._invoke("rollback_config", {"transaction_id": transaction_id})

        return [get_topology, get_state, get_running_config, get_object, update_config, update_object, execute_validation, rollback_config]
