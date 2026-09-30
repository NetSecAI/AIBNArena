"""Where the interface runs and how long it waits on the processes it starts."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class WebUIConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    #: The interpreter the subject and the judge are started with. The web
    #: application's own interpreter by default, so both run in the environment
    #: that could import this module.
    python: str = sys.executable
    readiness_timeout_seconds: float = 60.0
    readiness_interval_seconds: float = 1.0
    port_release_timeout_seconds: float = 10.0
    #: The directories the command line already writes to, so an episode run from
    #: here lands in the same place as one run by hand.
    result_dir: str = "reports/manual/results"
    #: Where a campaign's campaign directory goes. The same place
    #: `experiments/run_langchain_baseline.sh` writes its campaigns, and the
    #: directory `scripts/export_results.py` is pointed at afterwards; it is
    #: git-ignored, unlike the exported records beside it.
    campaign_dir: str = "reports/campaigns/runs"
    #: Where a campaign's records are laid out when it ends, one folder per
    #: model, campaign, subject and intent, by `scripts/export_results.py`.
    export_dir: str = "reports/campaigns"
    report_dir: str = "reports/manual"
    log_dir: str = "reports/manual/webui-logs"

    @classmethod
    def from_env(cls) -> "WebUIConfig":
        return cls(
            host=os.getenv("IBN_WEBUI_HOST", cls.host),
            port=int(os.getenv("IBN_WEBUI_PORT", cls.port)),
            python=os.getenv("IBN_WEBUI_PYTHON") or sys.executable,
        )

    def absolute(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else REPOSITORY / path
