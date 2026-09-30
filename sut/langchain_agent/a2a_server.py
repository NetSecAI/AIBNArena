#!/usr/bin/env python3
"""Expose the LangChain baseline through the repository's shared A2A server."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import uvicorn
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sut.common import build_a2a_application, build_ani, optional_bool, resolve_endpoint  # noqa: E402
from sut.langchain_agent import LangChainAgentConfig, LangChainRepairAgent  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LangChain ANI baseline over A2A")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--card-url")
    parser.add_argument("--model")
    parser.add_argument("--api-base")
    parser.add_argument("--api-key")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-context-chars", type=int)
    parser.add_argument("--artifact-directory")
    parser.add_argument("--max-execution-seconds", type=float)
    parser.add_argument("--recursion-limit", type=int)
    parser.add_argument(
        "--ani-call-limit", type=int, default=None,
        help="How many ANI calls this subject may make in one episode; "
             "a call past the cap is refused, never dispatched.",
    )
    parser.add_argument(
        "--min-retry-tokens", type=int, default=None,
        help="Smallest output reservation a provider context rejection may be retried "
             "with; omit to keep halving down to one token, which is the default.",
    )
    parser.add_argument(
        "--tool-result-chars", type=int, default=None,
        help="Cut one tool result to this many characters before the model sees it, "
             "spilling the whole result to --artifact-directory; omit to send results whole.",
    )
    parser.add_argument(
        "--context-budget-chars", type=int, default=None,
        help="Most the serialized transcript may be when the model is called; superseded "
             "reads are elided first and the episode ends by name if that is not enough. "
             "Omit for no bound, which is the default.",
    )
    parser.add_argument(
        "--max-consecutive-rejections", type=int, default=None,
        help="End the episode after this many completion claims in a row the gate refused; "
             "omit to keep refusing until the budget runs out, which is the default.",
    )
    parser.add_argument(
        "--prompt-variant",
        default=None,
        help="System prompt to run with: 'default' is the one this subject ships, "
             "any other name is a file under its prompts/ directory.",
    )
    parser.add_argument(
        "--enable-thinking", choices=["true", "false"], default=None,
        help="Ask the model family to think or not; omit to send nothing and run the "
             "model in the mode it serves by default.",
    )
    parser.add_argument(
        "--debug-trace", choices=["off", "summary", "full"], default=None,
        help="SUT-side audit trace level; omit to keep the environment's setting.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scenario-topology")
    parser.add_argument("--healthy-state")
    args = parser.parse_args()
    if bool(args.scenario_topology) != bool(args.healthy_state):
        parser.error("--scenario-topology and --healthy-state must be supplied together")
    return args


def main() -> None:
    args = parse_args()
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    env_config = LangChainAgentConfig.from_env()
    api_base, api_key = resolve_endpoint(args.api_base, args.api_key, env_config)
    config = LangChainAgentConfig(
        model=args.model or env_config.model,
        api_base=api_base,
        api_key=api_key,
        temperature=args.temperature if args.temperature is not None else env_config.temperature,
        max_tokens=args.max_tokens if args.max_tokens is not None else env_config.max_tokens,
        dry_run=args.dry_run or env_config.dry_run,
        self_execute=env_config.self_execute,
        # The command line wins and LANGCHAIN_AGENT_ENABLE_THINKING is the fallback,
        # and nothing else: a second path nobody sends is how a run file's condition
        # and the record come to disagree.
        enable_thinking=(optional_bool(args.enable_thinking) if args.enable_thinking is not None
                         else env_config.enable_thinking),
        # Read from the environment the launcher exports: without these three the
        # reasoning condition a run file states is dropped on the way to the model.
        thinking_style=env_config.thinking_style,
        thinking_directive=env_config.thinking_directive,
        thinking_effort=env_config.thinking_effort,
        debug_trace=args.debug_trace if args.debug_trace is not None else env_config.debug_trace,
        # From the environment only (the launcher exports IBN_SUT_TRACE_DIRECTORY).
        # Dropped here, trace_ref was null in every record this subject wrote.
        trace_directory=env_config.trace_directory,
        prompt_variant=args.prompt_variant or env_config.prompt_variant,
        ani_call_limit=(args.ani_call_limit if args.ani_call_limit is not None
                        else env_config.ani_call_limit),
        min_retry_tokens=(args.min_retry_tokens if args.min_retry_tokens is not None
                          else env_config.min_retry_tokens),
        tool_result_chars=(args.tool_result_chars if args.tool_result_chars is not None
                           else env_config.tool_result_chars),
        context_budget_chars=(args.context_budget_chars if args.context_budget_chars is not None
                              else env_config.context_budget_chars),
        max_consecutive_rejections=(args.max_consecutive_rejections
                                    if args.max_consecutive_rejections is not None
                                    else env_config.max_consecutive_rejections),
        max_execution_seconds=args.max_execution_seconds if args.max_execution_seconds is not None else env_config.max_execution_seconds,
        max_context_chars=args.max_context_chars if args.max_context_chars is not None else env_config.max_context_chars,
        artifact_directory=args.artifact_directory or env_config.artifact_directory,
        recursion_limit=args.recursion_limit if args.recursion_limit is not None else env_config.recursion_limit,
        # From the environment only (LANGCHAIN_AGENT_TOOL_CHOICE): there is no flag.
        # Left out until 2026-09-24, it was read and discarded, and every run bound "auto".
        tool_choice=env_config.tool_choice,
        request_extra_body=env_config.request_extra_body,
    )
    url = args.card_url or f"http://{args.host}:{args.port}/"
    if not config.dry_run and not config.api_key:
        raise SystemExit(
            "An API key is required. Set LLM_API_KEY (or LANGCHAIN_AGENT_API_KEY) "
            "in .env, pass --api-key, or start with --dry-run."
        )

    card = AgentCard(
        name="LangChain ANI Baseline",
        description="Tool-using baseline for intent-based network repair",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=url)],
        version=LangChainRepairAgent.version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(id="ani_network_repair", name="ANI Network Repair", description="Inspect, repair and validate a network through ANI", tags=["network", "repair", "langchain", "ani"])],
    )
    try:
        ani = build_ani(args.scenario_topology, args.healthy_state,
                        allow_unconfigured=config.dry_run)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    app = build_a2a_application(
        LangChainRepairAgent(config, ani),
        agent_card=card,
        source=LangChainRepairAgent.identity,
        runtime_metadata={
            "sut_version": LangChainRepairAgent.version,
            "configured_model": config.model,
        },
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
