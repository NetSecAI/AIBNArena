#!/usr/bin/env python3
"""Expose the LangChain RAG subject through the repository's shared A2A server."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import uvicorn
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sut.common import build_a2a_application, build_ani, optional_bool, resolve_endpoint  # noqa: E402
from sut.langchain_rag_agent import LangChainRAGAgent, LangChainRAGAgentConfig  # noqa: E402
from sut.langchain_rag_agent.agent import RAG_MODES  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the LangChain ANI baseline with retrieval over reference documents (RAG) over A2A")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8004)
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
             "a call past the cap is refused, never dispatched. Document searches are not ANI calls.",
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
    parser.add_argument(
        "--rag-directory", default=None,
        help="Directory of reference documents to retrieve from; omit for this "
             "subject's own sut/langchain_rag_agent/rag_document/.",
    )
    parser.add_argument(
        "--rag-context-directory", default=None,
        help="Directory of the documents the task-time excerpts are retrieved from; omit to "
             "use --rag-directory for both the excerpts and the search tool.",
    )
    parser.add_argument(
        "--rag-mode", choices=list(RAG_MODES), default=None,
        help="'context' appends the excerpts found for the task to its message, 'tool' gives "
             "the model a search_documents tool, 'both' (the default) does the two.",
    )
    parser.add_argument(
        "--rag-top-k", type=int, default=None,
        help="Excerpts appended to the task, and returned by a search that names no count "
             "(default 4, at most 10).",
    )
    parser.add_argument(
        "--rag-chunk-chars", type=int, default=None,
        help="Longest excerpt the documents are cut into, in characters (default 1500).",
    )
    parser.add_argument(
        "--rag-chunk-overlap", type=int, default=None,
        help="Characters two consecutive excerpts of one document share (default 200).",
    )
    parser.add_argument(
        "--rag-embedding-model", default=None,
        help="Rank by embeddings from this LiteLLM model id, served by the chat model's "
             "endpoint; omit to rank lexically (BM25), which needs no service.",
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

    env_config = LangChainRAGAgentConfig.from_env()
    api_base, api_key = resolve_endpoint(args.api_base, args.api_key, env_config)
    config = LangChainRAGAgentConfig(
        model=args.model or env_config.model,
        api_base=api_base,
        api_key=api_key,
        temperature=args.temperature if args.temperature is not None else env_config.temperature,
        max_tokens=args.max_tokens if args.max_tokens is not None else env_config.max_tokens,
        dry_run=args.dry_run or env_config.dry_run,
        self_execute=env_config.self_execute,
        # The command line wins and LANGCHAIN_RAG_AGENT_ENABLE_THINKING is the fallback,
        # and nothing else, as in the baseline's server.
        enable_thinking=(optional_bool(args.enable_thinking) if args.enable_thinking is not None
                         else env_config.enable_thinking),
        thinking_style=env_config.thinking_style,
        thinking_directive=env_config.thinking_directive,
        thinking_effort=env_config.thinking_effort,
        debug_trace=args.debug_trace if args.debug_trace is not None else env_config.debug_trace,
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
        # From the environment only (LANGCHAIN_RAG_AGENT_TOOL_CHOICE): there is no flag.
        tool_choice=env_config.tool_choice,
        request_extra_body=env_config.request_extra_body,
        rag_directory=args.rag_directory or env_config.rag_directory,
        rag_mode=args.rag_mode or env_config.rag_mode,
        rag_context_directory=args.rag_context_directory or env_config.rag_context_directory,
        rag_top_k=args.rag_top_k if args.rag_top_k is not None else env_config.rag_top_k,
        rag_chunk_chars=args.rag_chunk_chars if args.rag_chunk_chars is not None else env_config.rag_chunk_chars,
        rag_chunk_overlap=(args.rag_chunk_overlap if args.rag_chunk_overlap is not None
                           else env_config.rag_chunk_overlap),
        rag_embedding_model=args.rag_embedding_model or env_config.rag_embedding_model,
    )
    url = args.card_url or f"http://{args.host}:{args.port}/"
    if not config.dry_run and not config.api_key:
        raise SystemExit(
            "An API key is required. Set LLM_API_KEY (or LANGCHAIN_RAG_AGENT_API_KEY) "
            "in .env, pass --api-key, or start with --dry-run."
        )

    card = AgentCard(
        name="LangChain RAG ANI Agent",
        description="The LangChain tool-using baseline, with retrieval over reference documents",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=url)],
        version=LangChainRAGAgent.version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(id="ani_network_repair_rag", name="ANI Network Repair with RAG", description="Inspect, repair and validate a network through ANI, with reference documentation retrieved as needed", tags=["network", "repair", "langchain", "ani", "rag"])],
    )
    try:
        ani = build_ani(args.scenario_topology, args.healthy_state,
                        allow_unconfigured=config.dry_run)
        agent = LangChainRAGAgent(config, ani)
    except ValueError as exc:
        # An unknown RAG setting, a corpus that cannot be indexed, or a lab left
        # unnamed: all stop the server here, by name, before it answers anything.
        raise SystemExit(str(exc)) from exc
    parameters = agent.rag_parameters()
    logger.info(
        "RAG corpus {}: {} documents, {} excerpts, sha256 {}; mode {}, ranked by {}",
        parameters["rag_directory"], len(agent.corpus.files), len(agent.corpus.chunks),
        agent.corpus.sha256[:12], config.rag_mode, parameters["rag_retriever"])
    if agent.context_corpus is not None:
        logger.info(
            "RAG context corpus {}: {} documents, {} excerpts, sha256 {} (task-time excerpts only)",
            parameters["rag_context_directory"], len(agent.context_corpus.files),
            len(agent.context_corpus.chunks), agent.context_corpus.sha256[:12])
    app = build_a2a_application(
        agent,
        agent_card=card,
        source=LangChainRAGAgent.identity,
        runtime_metadata={
            "sut_version": LangChainRAGAgent.version,
            "configured_model": config.model,
        },
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
