"""The pages and the endpoints behind them."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel

from webui import catalog, endpoints, seeds
from webui.argv import build_sut_argv
from webui.config import REPOSITORY, WebUIConfig
from webui.endpoints import Endpoint
from webui.campaign import CampaignRequest, plan
from webui.form import EpisodeRequest, experiment_id, run_id
from webui.orchestrator import (
    MANAGER,
    CellRecord,
    CampaignRecord,
    JobRecord,
    ResumeRefused,
    RunRecord,
    preflight,
    reopen,
    resume_plan,
    run_episode,
    run_campaign,
    validate,
)
from webui.orchestrator import history
from webui.orchestrator.seed_guard import context

templates = Jinja2Templates(directory=str(REPOSITORY / "webui" / "templates"))
router = APIRouter()
config = WebUIConfig.from_env()

#: Episodes run for as long as they run; the task is held so it is not collected.
_TASKS: set[asyncio.Task] = set()


def _prepared(request: EpisodeRequest):
    try:
        return catalog.architecture(request.architecture), catalog.preset(request.experiment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class EndpointRequest(BaseModel):
    """An endpoint as the page submits it.

    `api_key` omitted means the stored one stays; an empty one clears it. The
    page cannot send back a key it was never given, so editing a name must not
    be the same thing as deleting a credential.
    """

    id: str | None = None
    name: str
    api_base: str
    models: list[str] = []
    api_key: str | None = None


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {
        "catalog": json.dumps(catalog.document()),
        "seeds": json.dumps(seeds.summaries()),
        "runs": [record.summary() for record in MANAGER.history()],
        "busy": MANAGER.busy,
    })


@router.get("/models", response_class=HTMLResponse)
async def models_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "models.html", {})


@router.get("/api/endpoints")
async def list_endpoints() -> dict[str, Any]:
    return {"endpoints": [item.public() for item in endpoints.load()]}


@router.put("/api/endpoints")
async def put_endpoint(entry: EndpointRequest) -> dict[str, Any]:
    name = entry.name.strip()
    api_base = entry.api_base.strip()
    if not name or not api_base:
        raise HTTPException(status_code=400, detail="an endpoint needs a name and a base URL")
    stored = endpoints.put(Endpoint(
        id=entry.id or endpoints.identifier(name),
        name=name,
        api_base=api_base,
        models=tuple(model.strip() for model in entry.models if model.strip()),
        api_key=entry.api_key,
    ))
    return stored.public()


@router.delete("/api/endpoints/{identifier}")
async def delete_endpoint(identifier: str) -> dict[str, Any]:
    if not endpoints.delete(identifier):
        raise HTTPException(status_code=404, detail=f"unknown endpoint: {identifier}")
    return {"deleted": identifier}


@router.get("/run/{identifier}", response_class=HTMLResponse)
async def run_page(request: Request, identifier: str) -> HTMLResponse:
    # The event stream replays the run from its first line, so the page is the
    # same whether it is opened while the episode runs or a day later.
    run = _job(identifier, RunRecord).summary()
    return templates.TemplateResponse(request, "run.html", {
        "run": run, "preset": _described(run["request"]["experiment"])})


def _described(experiment: str) -> dict[str, Any] | None:
    """The experiment file a run named, for a page that shows it the way the form
    chose it: a topology and a reference state. None once the file is gone, and
    the page then names the file."""
    try:
        return catalog.preset(experiment).as_dict()
    except ValueError:
        return None


@router.get("/api/catalog")
async def catalog_document() -> dict[str, Any]:
    return catalog.document()


@router.post("/api/validate")
async def validate_request(episode: EpisodeRequest) -> dict[str, Any]:
    """Compile what this form would run, without starting anything."""
    architecture, preset = _prepared(episode)
    try:
        preview = await asyncio.to_thread(validate, episode, preset)
        preview["sut_argv"] = build_sut_argv(episode, architecture, preset)
    except Exception as exc:  # noqa: BLE001 - a bad combination is a 400, not a 500
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc
    return preview


@router.get("/api/seeds/{seed}")
async def seed_context(seed: int) -> dict[str, Any]:
    """What this seed stands for: the context a later run is asked for by number."""
    found = context(seed)
    if found is None:
        raise HTTPException(status_code=404, detail=f"seed {seed} is not in the register")
    return found


@router.get("/api/seeds")
async def seed_register() -> dict[str, Any]:
    """Every recorded seed, newest first, as the menus offer them."""
    return {"register": seeds.REGISTER, **seeds.summaries()}


@router.post("/api/runs", status_code=201)
async def start_run(episode: EpisodeRequest) -> dict[str, Any]:
    architecture, preset = _prepared(episode)
    if episode.new_seed:
        # Drawn here rather than in the runner: the record carries the request,
        # and a request whose seed was settled later would not say what ran.
        episode.seed = seeds.reserve(seeds.load(), "episode")
        episode.new_seed = False
    identifier = run_id()
    record = RunRecord(
        id=identifier,
        request=episode,
        experiment_id=experiment_id(episode, identifier),
    )
    # Checked and started without awaiting in between, so a second submission
    # cannot slip past the check while this one is being set up.
    if MANAGER.busy:
        raise HTTPException(status_code=409, detail="a run is already in flight")
    MANAGER.create(record)
    task = asyncio.create_task(run_episode(MANAGER, config, record))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"id": record.id, "experiment_id": record.experiment_id}


@router.get("/api/runs")
async def runs() -> dict[str, Any]:
    return {"busy": MANAGER.busy, "runs": [record.summary() for record in MANAGER.history()]}


@router.get("/api/runs/{identifier}")
async def run_summary(identifier: str) -> dict[str, Any]:
    return _job(identifier, RunRecord).summary()


@router.get("/api/runs/{identifier}/events")
async def run_events(identifier: str) -> StreamingResponse:
    return _events(_job(identifier, RunRecord))


def _events(record: JobRecord) -> StreamingResponse:
    """One job's log, replayed from its first line and then followed live."""

    # A job a previous server finished has its output read back on first view.
    history.load_logs(record)

    async def stream():
        queue = MANAGER.subscribe(record)
        replay = list(record.events)
        try:
            for event in replay:
                yield _event(event)
            if record.state.finished:
                return
            while True:
                event = await queue.get()
                yield _event(event)
                if event.get("type") == "finished":
                    return
        finally:
            MANAGER.unsubscribe(record, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _job(identifier: str, expected: type) -> JobRecord:
    """The job by that name, when it is the kind the route serves.

    A run id given to a campaign route is a 404 rather than a page that half
    renders: the two records carry different fields, and their templates read
    fields the other does not have.
    """
    record = MANAGER.get(identifier)
    if not isinstance(record, expected):
        raise HTTPException(status_code=404,
                            detail=f"unknown {expected.kind}: {identifier}")
    return record


@router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "campaign.html", {
        "catalog": json.dumps(catalog.document()),
        "seeds": json.dumps(seeds.summaries()),
        "campaigns": [record.summary() for record in MANAGER.history(kind="campaign")],
        "busy": MANAGER.busy,
    })


@router.get("/campaign/{identifier}", response_class=HTMLResponse)
async def campaign_page(request: Request, identifier: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "campaign_run.html",
        {"campaign": _job(identifier, CampaignRecord).summary()})


def _settle_seeds(campaign: CampaignRequest) -> dict[str, Any]:
    """Give every scenario row a seed, drawing one where it has none.

    One per row, never one shared: a seed stands for one scenario, and the
    register refuses a number already written down for another. Each draw is
    held against the in-memory document so the next cannot repeat it before any
    of them has been recorded.
    """
    document = seeds.load()
    for selection in campaign.scenarios:
        if selection.seed is not None:
            continue
        drawn = seeds.reserve(document, "episode")
        document["episodes"][str(drawn)] = {"seed": drawn}
        selection.seed = drawn
    return document


def _planned(request: CampaignRequest):
    """The matrix this request is, refused by name when a choice does not exist."""
    try:
        cells = plan(request)
        for cell in cells:
            catalog.architecture(cell.architecture)
            catalog.preset(cell.selection.experiment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return cells


@router.post("/api/campaigns/validate")
async def validate_campaign(campaign: CampaignRequest) -> dict[str, Any]:
    """Compile every cell of the matrix. Nothing is started and no lab is touched."""
    # A row whose seed is drawn at launch has none yet, and compiling it as it
    # stands would compile the wrong instance. Free numbers stand in: reserving
    # reads the register and writes nothing, so a preview costs no seed.
    _settle_seeds(campaign)
    cells = _planned(campaign)
    try:
        await asyncio.to_thread(preflight, campaign, cells)
    except Exception as exc:  # noqa: BLE001 - a bad matrix is a 400, not a 500
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "campaign_id": campaign.campaign_id(),
        "result_dir": f"{config.campaign_dir}/{campaign.campaign_id()}",
        "cells": [cell.summary() for cell in cells],
    }


@router.post("/api/campaigns", status_code=201)
async def start_campaign(campaign: CampaignRequest) -> dict[str, Any]:
    document = _settle_seeds(campaign)
    if campaign.seed_campaign is None:
        campaign.seed_campaign = seeds.reserve(document, "campaign")
    cells = _planned(campaign)
    # Named apart from the request: the record carries both, and a request rebound to
    # its own id reached the runner as a string, which failed every launch.
    campaign_id = campaign.campaign_id()
    record = CampaignRecord(
        id=run_id(),
        request=campaign,
        campaign_id=campaign_id,
        result_dir=f"{config.campaign_dir}/{campaign_id}",
        plan=cells,
        cells=[CellRecord(cell=cell.summary()) for cell in cells],
    )
    # Checked and started without awaiting in between, so a second submission
    # cannot slip past the check while this one is being set up. One lab, so an
    # episode in flight refuses a campaign just as another campaign would.
    if MANAGER.busy:
        raise HTTPException(status_code=409, detail="a run is already in flight")
    MANAGER.create(record)
    task = asyncio.create_task(run_campaign(MANAGER, config, record))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"id": record.id, "campaign_id": record.campaign_id,
            "seed_campaign": record.request.seed_campaign,
            "seeds": [row.seed for row in record.request.scenarios],
            "cells": len(cells)}


@router.post("/api/campaigns/{identifier}/resume", status_code=201)
async def resume_campaign(identifier: str) -> dict[str, Any]:
    """Run again, in place, the cells of a finished campaign that did not end done.

    Same campaign seed, same row seeds, same directory: the cells run again face
    the faults the others faced, and the campaign's page, report and exported
    records read as one campaign rather than as a first run and its restarts.
    """
    record = _job(identifier, CampaignRecord)
    # Checked and started without awaiting in between, as a launch is.
    if MANAGER.busy:
        raise HTTPException(status_code=409, detail="a run is already in flight")
    try:
        cells = resume_plan(record)
    except ResumeRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A campaign read back from disk has its output loaded before it runs again,
    # or the page's first view would replace the resume's lines with the old ones.
    history.load_logs(record)
    reopen(MANAGER, record, cells)
    task = asyncio.create_task(run_campaign(MANAGER, config, record, rerun=cells))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"id": record.id, "campaign_id": record.campaign_id,
            "seed_campaign": record.request.seed_campaign,
            "seeds": sorted({cell.seed for cell in cells}),
            "cells": len(cells)}


@router.get("/api/campaigns")
async def campaigns() -> dict[str, Any]:
    return {"busy": MANAGER.busy,
            "campaigns": [record.summary()
                            for record in MANAGER.history(kind="campaign")]}


@router.get("/api/campaigns/{identifier}")
async def campaign_summary(identifier: str) -> dict[str, Any]:
    return _job(identifier, CampaignRecord).summary()


@router.get("/api/campaigns/{identifier}/events")
async def campaign_events(identifier: str) -> StreamingResponse:
    return _events(_job(identifier, CampaignRecord))


@router.get("/api/campaigns/{identifier}/parameters")
async def campaign_parameters(identifier: str) -> dict[str, Any]:
    """Each subject's evaluation parameters, as the campaign's report computed them.

    What the campaign's page charts once it has ended. Read from the report rather
    than computed again, so the charts and the report cannot disagree.
    """
    record = _job(identifier, CampaignRecord)
    if not record.report_path:
        raise HTTPException(status_code=404, detail="this campaign has no report yet")
    path = config.absolute(record.report_path).with_suffix(".json")
    try:
        document = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"the campaign report cannot be read: {exc}") from exc
    return {
        "contenders": [{"key": item["key"], "label": item["label"]}
                       for item in document.get("contenders") or []],
        "parameters": (document.get("comparison") or {}).get("parameters") or {},
    }


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"
