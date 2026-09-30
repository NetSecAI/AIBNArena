"""Shared benchmark lifecycle, contracts, evaluation, metrics and reporting."""

from .a2a import A2AClient, A2ASubject
from .a2a_contracts import (
    A2A_CONTRACT_VERSION,
    SELF_EXECUTE_MODE,
    ani_contract,
    build_self_execute_request,
    parse_self_execute_report,
    require_self_execute_contract,
    self_execute_output_contract,
    device_changes_of,
    self_execute_report_has_device_change,
)
from .contracts import (
    ExperimentConfig,
    OperationCounts,
    OracleEvaluation,
    ScenarioDefinition,
    SUTResponse,
)
from .lifecycle import BenchmarkLifecycle, LifecycleDependencies, LifecycleError
from .oracle_evaluator import OracleEvaluator
from .reporting import ResultWriter
from .sut_client import A2ASUTClient

__all__ = [
    "BenchmarkLifecycle",
    "A2AClient",
    "A2ASUTClient",
    "A2ASubject",
    "A2A_CONTRACT_VERSION",
    "SELF_EXECUTE_MODE",
    "ani_contract",
    "build_self_execute_request",
    "parse_self_execute_report",
    "require_self_execute_contract",
    "self_execute_output_contract",
    "device_changes_of",
    "self_execute_report_has_device_change",
    "ExperimentConfig",
    "LifecycleDependencies",
    "LifecycleError",
    "OperationCounts",
    "OracleEvaluation",
    "OracleEvaluator",
    "ResultWriter",
    "SUTResponse",
    "ScenarioDefinition",
]
