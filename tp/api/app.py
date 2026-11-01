"""Thin HTTP layer: every endpoint calls one core function. Jobs run on one worker thread."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tp.core import artifacts, contract, events, review, runner

app = FastAPI(title="testplat")
worker = ThreadPoolExecutor(max_workers=1)
log = logging.getLogger("tp.api")


def submit(fn, *args):
    def report(f):
        if f.exception():
            log.error("job %s failed", fn.__name__, exc_info=f.exception())

    worker.submit(fn, *args).add_done_callback(report)


# ---- models (they drive the generated TypeScript client) ----------------------


class Execution(BaseModel):
    id: int
    run_id: int
    stage: str
    stage_version: str | None
    status: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    reason: str | None
    started: str | None
    finished: str | None


class Run(BaseModel):
    id: int
    name: str
    sut_ref: str | None
    mode: str
    stub: bool
    unattended: bool
    started: str
    status: str


class RunDetail(Run):
    executions: list[Execution]


class Requirements(BaseModel):
    kind: Literal["story", "brief"]
    text: str = Field(min_length=1)


class RunCreate(BaseModel):
    mode: str = "live"
    stub: bool = True
    unattended: bool = False
    requirements: Requirements | None = None  # a full user story or a vague brief


class Rerun(BaseModel):
    inputs: dict[str, str] = {}  # artifact type -> artifact id
    from_run: int | None = None


class ArtifactSummary(BaseModel):
    id: str
    type: str
    schema_version: int
    stage: str | None
    stage_version: str | None
    parents: list[str]
    labels: list[str]
    created_at: str


class ArtifactDetail(ArtifactSummary):
    signed: dict | None
    body: Any
    children: list[ArtifactSummary]


class ArtifactUpload(BaseModel):
    type: str
    body: Any
    schema_version: int = 1


class ReviewItem(BaseModel):
    id: int
    kind: str
    stage_execution: int
    payload: dict
    status: str
    decision: str | None
    detail: dict | None
    decided_by: str | None
    reason: str | None
    options: list[str]
    line_options: list[str]


class Resolve(BaseModel):
    decision: str | None = None  # derived from detail.lines for line-level items
    by: str
    reason: str = ""
    detail: dict | None = None
    seconds: float | None = None  # time spent on the item, measured by the UI


class Event(BaseModel):
    id: int
    run_id: int | None
    stage_execution: int | None
    kind: str
    payload: dict
    at: str


class StageInfo(BaseModel):
    name: str
    version: str
    consumes: dict[str, int]
    produces: dict[str, int]


# ---- runs ---------------------------------------------------------------------


@app.get("/stages", response_model=list[StageInfo])
def list_stages(stub: bool = True):
    stages = [contract.resolve(n, stub) for n in runner.PIPELINE]
    return [
        {"name": s.name, "version": s.version, "consumes": s.consumes, "produces": s.produces}
        for s in stages
    ]


@app.get("/runs", response_model=list[Run])
def list_runs():
    return runner.list_runs()


@app.post("/runs", response_model=Run)
def create_run(body: RunCreate):
    try:
        inputs = {}
        if body.requirements:
            req = body.requirements.model_dump()
            inputs["requirements"] = artifacts.put("requirements", req).id
        run_id = runner.start_run(body.mode, body.stub, body.unattended, inputs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    submit(runner.advance, run_id)
    return runner.get_run(run_id)


@app.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int):
    try:
        return runner.get_run(run_id)
    except KeyError:
        raise HTTPException(404, "run not found")


@app.post("/runs/{run_id}/stages/{name}/rerun", response_model=Run)
def rerun_stage(run_id: int, name: str, body: Rerun):
    get_run(run_id)
    if name not in runner.PIPELINE:
        raise HTTPException(404, "unknown stage")
    submit(runner.rerun, run_id, name, body.inputs, body.from_run)
    return runner.get_run(run_id)


@app.get("/runs/{run_id}/events/list", response_model=list[Event])
def list_events(run_id: int, after: int = 0):
    return events.since(run_id, after)


@app.get("/runs/{run_id}/events")
async def stream_events(run_id: int, request: Request):
    """SSE. Ends when the run is idle; EventSource reconnects with Last-Event-ID."""
    get_run(run_id)
    last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

    async def gen():
        nonlocal last
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            new = events.since(run_id, last)
            for e in new:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e)}\n\n"
            if not new and runner.get_run(run_id)["status"] not in ("pending", "running"):
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- artifacts ------------------------------------------------------------------


@app.get("/artifacts", response_model=list[ArtifactSummary])
def list_artifacts(type: str | None = None):
    return artifacts.list_(type)


@app.post("/artifacts", response_model=ArtifactSummary)
def upload_artifact(body: ArtifactUpload):
    """A hand-written fixture; labelled pre-generated."""
    try:
        art = artifacts.put(
            body.type, body.body, schema_version=body.schema_version, labels=["pre-generated"]
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e))
    return art.manifest


@app.get("/artifacts/{art_id}", response_model=ArtifactDetail)
def get_artifact(art_id: str):
    try:
        art = artifacts.get(art_id)
    except KeyError:
        raise HTTPException(404, "artifact not found")
    except artifacts.ArtifactConflict as e:
        raise HTTPException(409, str(e))
    return {**art.manifest, "body": art.body, "children": artifacts.children(art_id)}


# ---- reviews --------------------------------------------------------------------


@app.get("/reviews", response_model=list[ReviewItem])
def list_reviews(status: str | None = None):
    return review.list_(status=status)


@app.post("/reviews/{item_id}", response_model=ReviewItem)
def resolve_review(item_id: int, body: Resolve):
    try:
        item = review.resolve(
            item_id, body.decision, body.by, body.reason, body.detail, body.seconds
        )
    except KeyError:
        raise HTTPException(404, "review not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    submit(runner.resume, item_id)
    return item
