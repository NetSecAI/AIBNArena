"""The ASGI application: `uvicorn webui.app:app --workers 1`.

One worker, because the state that refuses a second concurrent episode lives in
this process. A second worker would hold a second state, and a second episode
would start against the same lab.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webui.config import REPOSITORY
from webui.orchestrator import MANAGER, history
from webui.routes import config, router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The runs and campaigns a previous server left, so "Recent runs" and
    # "Recent campaigns" survive a restart.
    history.restore(MANAGER, config.absolute(config.log_dir))
    history.restore_campaigns(MANAGER, config.absolute(config.campaign_dir))
    yield


app = FastAPI(title="AIBNArena", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(REPOSITORY / "webui" / "static")), name="static")
# What the judge and the campaigns write, read-only, so a run's page can link its reports
# and a campaign page reaches every episode's. Nothing outside reports/ is served.
app.mount("/reports", StaticFiles(directory=str(REPOSITORY / "reports"), check_dir=False), name="reports")
app.include_router(router)
