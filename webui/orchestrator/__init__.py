"""Running one episode or a matrix of them: the processes, the checks and the state."""
from __future__ import annotations

from .episode_runner import run_episode, validate
from .campaign_runner import ProvenanceError, ResumeRefused, preflight, reopen, resume_plan, run_campaign
from .readiness import ReadinessError
from .run_state import (
    MANAGER,
    CellRecord,
    CampaignRecord,
    JobRecord,
    LogSource,
    RunManager,
    RunRecord,
    RunState,
)

__all__ = [
    "MANAGER",
    "ProvenanceError",
    "ReadinessError",
    "ResumeRefused",
    "CellRecord",
    "CampaignRecord",
    "JobRecord",
    "LogSource",
    "RunManager",
    "RunRecord",
    "RunState",
    "preflight",
    "reopen",
    "resume_plan",
    "run_episode",
    "run_campaign",
    "validate",
]
