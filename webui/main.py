#!/usr/bin/env python3
"""Serve the launcher: `python -m webui.main`."""
from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

import uvicorn  # noqa: E402

from webui.config import WebUIConfig  # noqa: E402
from webui.orchestrator.process import next_free_port  # noqa: E402


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPOSITORY / ".env")
    except ImportError:
        pass
    config = WebUIConfig.from_env()
    # A busy port (the other checkout's interface, say) is left to its holder: the
    # interface takes the next free one, and says so before uvicorn names it.
    searched: list[str] = []
    port = next_free_port(config.port, sink=searched.append)
    if port != config.port:
        for line in searched:
            print(f"webui: {line}", file=sys.stderr)
    # Bound to the loopback address by default: this starts processes and drives
    # a lab, so it is a local tool, not a service to publish.
    uvicorn.run("webui.app:app", host=config.host, port=port, workers=1)


if __name__ == "__main__":
    main()
