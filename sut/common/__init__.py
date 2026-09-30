"""Shared runtime for ANI v0.1 `self_execute` SUTs.

A SUT built on this package contributes its prompts and a model caller. The loop,
the report shape and the A2A plumbing are shared, which is what makes two SUTs
comparable on the same benchmark metrics.

Prompt text lives in each SUT's own `agent.py`, so an agent owns how it presents
itself, what strategy it suggests and what policy it states. The one exception is
the terminal stanza the loop has to parse: `FINAL_STATUS_CONTRACT` and
`ANSWER_CONTRACT` are shared, because a SUT that declares a different output shape
fails silently — its episodes time out with nothing to explain why.
"""
from .a2a_app import (
    SelfExecuteAgentExecutor,
    build_a2a_application,
    build_ani,
    format_result_for_log,
    format_self_execute_report,
)
from .agent_config import ModelAgentConfig, optional_bool, optional_int, resolve_endpoint
from .thinking import ThinkingUnsupported, thinking_record
from .prompt_variants import (
    DEFAULT_VARIANT,
    available_variants,
    load_prompt,
    resolve_system_prompt,
)
from .ani_report import ANI_VERSION, SELF_EXECUTE_MODE
from .litellm_backend import LiteLLMModelCaller, litellm_model_name
from .messages import ANSWER_CONTRACT, FINAL_STATUS_CONTRACT, ModelActionError
from .self_execute import (
    install_ani_budget,
    reset_ani_budget,
    SelfExecuteRuntime,
    answer_sanity_question,
    dry_run_report,
    execution_budget,
    invoke,
    load_task,
    run_self_execute,
)
from .trace import Tracer

__all__ = [
    "ANI_VERSION",
    "ANSWER_CONTRACT",
    "FINAL_STATUS_CONTRACT",
    "LiteLLMModelCaller",
    "ModelActionError",
    "DEFAULT_VARIANT",
    "ModelAgentConfig",
    "available_variants",
    "load_prompt",
    "ThinkingUnsupported",
    "resolve_system_prompt",
    "thinking_record",
    "SELF_EXECUTE_MODE",
    "SelfExecuteAgentExecutor",
    "SelfExecuteRuntime",
    "Tracer",
    "answer_sanity_question",
    "build_a2a_application",
    "build_ani",
    "dry_run_report",
    "execution_budget",
    "format_result_for_log",
    "format_self_execute_report",
    "invoke",
    "litellm_model_name",
    "load_task",
    "optional_bool",
    "optional_int",
    "resolve_endpoint",
    "run_self_execute",
]
