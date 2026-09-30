"""A2A server plumbing shared by ANI SUTs: executor, log rendering, ANI wiring."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping

from loguru import logger
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_text_message
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard

from benchmarks.platforms.containerlab import ContainerLabANI, ContainerLabEnv
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology

RUNTIME_IDENTITY_PATH = "/.well-known/sut-runtime.json"


class SelfExecuteAgentExecutor(AgentExecutor):
    """Serializes episodes and keeps the event loop free to serve the agent card."""

    def __init__(self, agent: Any, *, source: str):
        self.agent = agent
        self.source = source
        # The SUT uses synchronous provider and ContainerLab calls. Serialize
        # mutations, while keeping Uvicorn free to serve the agent card.
        self._execution_lock = asyncio.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        input_text = context.get_user_input()
        try:
            async with self._execution_lock:
                result = await asyncio.to_thread(self.agent.invoke, input_text)
            logger.info(format_result_for_log(result))
        except Exception as exc:
            logger.exception("Autonomous repair failed")
            result = json.dumps({"error": str(exc), "source": self.source})
        await event_queue.enqueue_event(new_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

def build_a2a_application(
    agent: Any,
    *,
    agent_card: AgentCard,
    source: str,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> Any:
    """Build the shared stable-SDK ASGI application for a SUT.

    Runtime metadata is deliberately separate from the standard Agent Card:
    the current A2A protobuf has no generic metadata field. The well-known
    runtime document lets an experiment launcher prove that it reached the
    process and model it just started, rather than a stale listener.
    """
    request_handler = DefaultRequestHandler(
        agent_executor=SelfExecuteAgentExecutor(agent, source=source),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    runtime_identity = {
        "schema_version": "1.0",
        "sut_identity": source,
        "process_id": os.getpid(),
        **dict(runtime_metadata or {}),
    }

    async def get_runtime_identity(_request: Any) -> JSONResponse:
        return JSONResponse(runtime_identity)

    routes = [
        Route(RUNTIME_IDENTITY_PATH, get_runtime_identity, methods=["GET"]),
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(request_handler, rpc_url="/"),
    ]
    return Starlette(routes=routes)

def build_ani(
    scenario_topology: str | Path | None,
    healthy_state: str | Path | None,
    *,
    allow_unconfigured: bool = False,
) -> ContainerLabANI | None:
    """Wire the SUT's local ANI from an abstract topology descriptor.

    A live server must name its lab explicitly. Falling through to the environment's
    historical ``clos01`` default can write an unrelated lab. Only a caller which has
    already established a non-executing mode (currently ``--dry-run``) may request the
    unconfigured ``None`` result.
    """
    if bool(scenario_topology) != bool(healthy_state):
        raise ValueError(
            "scenario_topology and healthy_state must be provided together"
        )
    if not scenario_topology:
        if allow_unconfigured:
            return None
        raise ValueError(
            "a live ANI requires explicit scenario_topology and healthy_state"
        )
    topology = CompiledContainerLabTopology.from_file(scenario_topology)
    env = ContainerLabEnv(topology.env_config(healthy_state_path=healthy_state))
    return ContainerLabANI(env, topology_document=topology.document)

def format_result_for_log(raw_result: str) -> str:
    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        return f"Autonomous repair result:\n{raw_result}"

    if not isinstance(payload, dict):
        return f"Autonomous repair result:\n{raw_result}"

    if payload.get("mode") == "self_execute":
        return format_self_execute_report(payload)

    if payload.get("error"):
        return "\n".join([
            "Autonomous repair result",
            "========================",
            "status: ERROR",
            f"error: {payload.get('error')}",
            f"source: {payload.get('source', 'n/a')}",
        ])

    if "answer" in payload:
        lines = [
            "Autonomous sanity response",
            "==========================",
            f"answer: {payload.get('answer', 'n/a')}",
        ]
        evidence = payload.get("evidence")
        if evidence:
            lines.append("evidence:")
            for item in evidence if isinstance(evidence, list) else [evidence]:
                lines.append(f"- {item}")
        return "\n".join(lines)

    return "\n".join([
        "Autonomous repair result",
        "========================",
        "mode: action",
        f"machine: {payload.get('machine', 'n/a')}",
        f"command: {payload.get('command', 'n/a')}",
        f"reason: {payload.get('reason', 'n/a')}",
    ])

def format_self_execute_report(payload: dict[str, Any]) -> str:
    device_changes = payload.get("device_changes") or []
    execution = payload.get("execution") or {}
    lines = [
        "Autonomous ANI repair result",
        "============================",
        "mode: self_execute",
        f"status: {str(payload.get('status', 'n/a')).upper()}",
        f"verified: {payload.get('verified')}",
        f"device changes: {len(device_changes)}",
        f"ani_tool_calls: {execution.get('tool_call_count', len(payload.get('ani_operations') or []))}",
    ]
    if execution.get("budget_seconds") is not None:
        lines.append(f"budget: {execution.get('budget_seconds')}s")
    if execution.get("elapsed_seconds") is not None:
        lines.append(f"elapsed: {execution.get('elapsed_seconds')}s")
    if payload.get("error"):
        lines.append(f"error: {payload.get('error')}")

    # Every tool call the model made, in order, with the arguments it chose: the
    # reads and the validations as much as the change the sections below detail.
    # What a call returned stays out -- a device's whole configuration has no
    # place in a log line -- and long arguments are cut like any value here.
    operations = [item for item in payload.get("ani_operations") or [] if isinstance(item, dict)]
    if operations:
        lines.append("")
        lines.append("Tool calls")
        lines.append("-" * 10)
    for index, item in enumerate(operations, start=1):
        arguments = json.dumps(item.get("arguments") or {}, ensure_ascii=False, default=str)
        line = (f"{index}. [{item.get('category', 'tool')}] {item.get('operation', 'n/a')} "
                f"{compact_text(arguments)} · ok={item.get('ok')}")
        if item.get("duration_seconds") is not None:
            line += f" · {float(item['duration_seconds']):.1f}s"
        if item.get("error"):
            line += f" · error: {compact_text(item['error'], 160)}"
        lines.append(line)

    for index, item in enumerate(device_changes, start=1):
        if not isinstance(item, dict):
            continue
        lines.append("")
        lines.append(f"Device change {index}")
        lines.append("-" * 14)
        action = item.get("action") or {}
        result = item.get("result") or {}
        lines.append(f"operation: {item.get('operation', 'update_config')}")
        if item.get("transaction_id"):
            lines.append(f"transaction: {item.get('transaction_id')}")
        lines.append(f"action: {action.get('machine', 'n/a')} :: {action.get('command', 'n/a')}")
        if action.get("reason"):
            lines.append(f"reason: {action.get('reason')}")
        lines.append(
            "exec: "
            f"ok={result.get('ok')}, safe={result.get('safe')}, "
            f"target={result.get('target', 'n/a')}"
        )
        if result.get("stderr"):
            lines.append(f"stderr: {compact_text(result.get('stderr'))}")
        if result.get("reason"):
            lines.append(f"exec_reason: {result.get('reason')}")
        lines.append(f"verified_after_action: {item.get('verified_after_action')}")
        objective = item.get("objective_verification")
        if isinstance(objective, dict):
            lines.append(f"objective_passed: {objective.get('passed')}")

    objective = payload.get("final_observation")
    if isinstance(objective, dict):
        lines.append("")
        lines.append(f"final_public_validation: passed={objective.get('passed')}")
    return "\n".join(lines)

def compact_text(value: Any, limit: int = 240) -> str:
    text = str(value).strip().replace("\n", " | ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
