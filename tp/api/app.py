"""Thin HTTP layer: every endpoint calls one core function. Jobs run on one worker thread."""

import asyncio
import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tp.core import artifacts, contract, events, llm, review, runner, sut

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


class RunSummary(Run):
    focused: bool  # started from the New run screen (the happy path)
    overview: str | None  # the feature text the run was given


class RunDetail(Run):
    executions: list[Execution]


class Requirements(BaseModel):
    kind: Literal["story", "brief"]
    text: str  # a blank brief is a Scan of the whole verified spec (cohesion/001 D2)


class RunRules(BaseModel):
    clarify: list[str] = []  # added after spec's rules.md, this run only (M8 D28)
    cases: list[str] = []  # added after the drafter's rules


class RunCreate(BaseModel):
    mode: str = "live"
    stub: bool = True
    unattended: bool = False
    requirements: Requirements | None = None  # a full user story or a vague brief
    flows: list[str] | None = None  # cases scope (default: the stage's own default)
    fresh: bool = False  # reuse no earlier answers or sign-offs
    # M8 happy path
    focused: bool = (
        True  # only Clarify and Sign-off wait for a person (D27; default: autonomy/001 F7)
    )
    routes: list[str] | None = None  # "METHOD /path" code focus (spec D12)
    rules: RunRules | None = None
    templates: list[str] | None = None  # enabled case templates (cases D14)
    gate: dict[str, int | bool] | None = None  # gate settings for this run (gate D22)
    profile: str | None = None  # target folder path or slug (GET /profiles); default TP_TARGET


FOCUSED_STOPS = review.FOCUSED_STOPS


class SutTarget(BaseModel):
    repo: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    base: str = Field(min_length=1)
    seeded: bool = False


class SuggestRules(BaseModel):
    overview: str = Field(min_length=1)
    routes: list[str] = []


class RetestRequest(BaseModel):
    defect: str
    mode: Literal["suggested", "mine"]


class ExportRequest(BaseModel):
    tests: list[str]  # case units (test files) to export


class DefectDecision(BaseModel):
    decision: Literal["confirm", "reject"]
    by: str = "you"
    reason: str = ""


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


def _summary(run: dict) -> dict:
    """A run plus what the run list shows: whether it is a happy-path run, and its feature."""
    with_inputs = runner.get_run(run["id"])
    given = next((e["outputs"] for e in with_inputs["executions"] if e["stage"] == "input"), {})
    opts = artifacts.get(given["run_options"]).body if "run_options" in given else {}
    req = artifacts.get(given["requirements"]).body if "requirements" in given else {}
    return {**run, "focused": "stops" in opts, "overview": req.get("text")}


@app.get("/runs", response_model=list[RunSummary])
def list_runs(focused: bool = False):
    """focused=true: only runs started from the New run screen."""
    out = [_summary(r) for r in runner.list_runs()]
    return [r for r in out if r["focused"]] if focused else out


@app.post("/runs", response_model=Run)
def create_run(body: RunCreate):
    try:
        inputs = {}
        options = {k: v for k, v in (("flows", body.flows), ("fresh", body.fresh),
                                     ("routes", body.routes), ("templates", body.templates),
                                     ("gate", body.gate), ("profile", body.profile)) if v}  # fmt: skip
        if body.requirements:
            req = body.requirements.model_dump()
            if not req["text"].strip():
                if req["kind"] == "story":
                    raise ValueError("a story needs its text; leave a brief empty for a Scan")
                # the same Scan as `tp scan` with no branch (tp/cli/watch.py:49)
                req["text"] = "Scan of the whole verified spec"
                options["scan"] = {"whole_spec": "an empty brief: the whole verified spec"}
            inputs["requirements"] = artifacts.put("requirements", req).id
        if body.profile and body.profile not in {c["path"] for c in profile_choices()} | {
            c["slug"] for c in profile_choices()
        }:
            raise ValueError(f"unknown target profile: {body.profile}")
        if body.rules and (body.rules.clarify or body.rules.cases):
            options["rules"] = body.rules.model_dump()
        if body.focused:
            options["stops"] = FOCUSED_STOPS
        run_id = runner.start_run(body.mode, body.stub, body.unattended, inputs, options)
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


class Count(BaseModel):
    n: int
    d: int | None
    ids: list[str]


class Blocked(BaseModel):
    why: str
    cases: Count


class Claim(BaseModel):
    """The claim-first header (cohesion/001 D5): what a run claims and what it rests on."""

    target: str | None
    reach: str | None
    isolation: int | None  # 0 isolated, 1 partly isolated (stand-ins), 2 live and local (DR-3)
    isolation_words: str | None
    ceiling: str | None
    ran: Count | None  # None until the gate has rows
    blocked: list[Blocked]
    basis: dict[str, Count]  # real | simulated | pre-generated | not recorded


@app.get("/runs/{run_id}/claim", response_model=Claim)
def run_claim(run_id: int):
    """The header the run page shows: the same function the report's header uses."""
    from tp.core import profile
    from tp.stages.report import build

    get_run(run_id)
    heads = runner.heads(run_id)
    body = {k: artifacts.get(heads[k]).body if k in heads else None
            for k in ("cases", "gate_report", "run_records")}  # fmt: skip
    target = runner.run_target(run_id)
    with profile.selected(target):
        facts = build.run_facts(body["run_records"])
    if not target:  # a run with no pinned target claims none
        facts["target"] = None
    strategies = (facts["target"] or {}).get("strategies", {})
    records = {r["id"]: r for r in facts["records"]}
    return build.claim(facts["target"], body["gate_report"], body["run_records"], body["cases"],
                       records, strategies)  # fmt: skip


@app.post("/runs/{run_id}/stages/{name}/rerun", response_model=Run)
def rerun_stage(run_id: int, name: str, body: Rerun):
    get_run(run_id)
    if name not in runner.PIPELINE:
        raise HTTPException(404, "unknown stage")
    submit(runner.rerun, run_id, name, body.inputs, body.from_run)
    return runner.get_run(run_id)


@app.post("/runs/{run_id}/cancel", response_model=Run)
def cancel_run(run_id: int):
    get_run(run_id)
    return runner.cancel(run_id)


@app.post("/runs/{run_id}/resume", response_model=Run)
def resume_run(run_id: int):
    """Continue a cancelled run, or retry a failed one from its failed step."""
    run = get_run(run_id)
    submit(runner.retry if run["status"] == "failed" else runner.advance, run_id)
    return runner.get_run(run_id)


@app.post("/runs/{run_id}/retest", response_model=Run)
def retest(run_id: int, body: RetestRequest):
    """M8 retest D16. suggested: build fixed:<patch artifact> from triage's suggestion.
    mine: refresh the target to the branch's new head and retest on reference."""
    heads = runner.heads(run_id)
    if "triage" not in heads:
        raise HTTPException(409, "this run has no triage yet")
    tri = artifacts.get(heads["triage"]).body
    d = next((x for x in tri["defects"] if x["id"] == body.defect), None)
    if d is None:
        raise HTTPException(404, "no such defect")
    if body.mode == "suggested":
        sug = d.get("suggestion") or {}
        if not sug.get("applies"):
            raise HTTPException(409, "this defect has no suggested fix that applies")
        fix = artifacts.put("fix_patch", {"patch": sug["patch"], "defect": d["id"],
                                          "triage": heads["triage"]})  # fmt: skip
        after = f"fixed:{fix.id}"
    else:
        t = sut.target()
        if not t:
            raise HTTPException(409, "no repo target is set")
        old = t["ref"]
        t = sut.set_target(t["repo"], t["branch"], t["base"], t.get("seeded", False))
        if t["ref"] == old:
            raise HTTPException(409, f"{t['branch']} has no new commit since {old[:7]}")
        after = "reference"
    opts = artifacts.put("run_options", {**artifacts.get(heads["run_options"]).body,
                                         "defect": d["id"], "after": after})  # fmt: skip
    submit(runner.rerun, run_id, "retest", {"run_options": opts.id})
    return runner.get_run(run_id)


def _export_inputs(run_id: int) -> tuple[dict, dict, list[dict], dict]:
    """(run, target, candidates, bound tests) for a run's export (M8 report/009)."""
    run, heads = get_run(run_id), runner.heads(run_id)
    if missing := [k for k in ("bound_tests", "gate_report", "cases", "spec") if k not in heads]:
        raise HTTPException(409, f"nothing to export yet (missing {', '.join(missing)})")
    body = {
        k: artifacts.get(heads[k]).body for k in ("bound_tests", "gate_report", "cases", "spec")
    }
    if body["spec"].get("index", {}).get("from") == "openapi":  # blackbox/006: stage F's job
        raise HTTPException(409, "exporting black-box tests is not supported yet (stage F)")
    t = sut.target()
    if not t:
        raise HTTPException(409, "no repo target is set")
    tri = artifacts.get(heads["triage"]).body if "triage" in heads else None
    cands = contract.resolve("report").export_candidates(
        body["bound_tests"], body["gate_report"], body["cases"], tri
    )
    return run, t | {"tested": body["spec"]["sut"]["ref"]}, cands, body["bound_tests"]


def _exports(run_id: int) -> list[dict]:
    """Earlier exports of this run, newest first."""
    bodies = (artifacts.get(a["id"]).body for a in artifacts.list_("test_export"))
    return [b for b in bodies if b.get("run_id") == run_id]


@app.get("/runs/{run_id}/export")
def export_preview(run_id: int):
    """What an export would write: the tests (pre-ticked = trusted) and the branch name."""
    run, t, cands, _ = _export_inputs(run_id)
    branch = contract.resolve("report").export_branch(run["name"])
    exists = bool(subprocess.run(["git", "-C", t["repo"], "branch", "--list", branch],
                                 capture_output=True, text=True, check=False).stdout.strip())  # fmt: skip
    return {"repo": t["repo"], "branch": branch, "branch_exists": exists, "ref": t["tested"],
            "from_branch": t["branch"], "tests": cands, "exports": _exports(run_id)}  # fmt: skip


@app.post("/runs/{run_id}/export")
def export_tests(run_id: int, body: ExportRequest):
    """Write the chosen tests to a new branch of the user's repo, in one commit."""
    rep = contract.resolve("report")
    run, t, cands, bound = _export_inputs(run_id)
    chosen = [c for c in cands if c["unit"] in set(body.tests)]
    if unknown := set(body.tests) - {c["unit"] for c in chosen}:
        raise HTTPException(404, f"no such tests in this run: {sorted(unknown)}")
    # UNBOUND tests carry no source; they are never export candidates anyway
    sources = {b["file"]: b["source"] for b in bound["tests"] if "source" in b}
    try:
        res = rep.export_tests(Path(t["repo"]), t["tested"], rep.export_branch(run["name"]),
                            run["name"], chosen, sources,
                            contract.SERVICES["env"].harness_files())  # fmt: skip
    except rep.ExportError as e:
        raise HTTPException(409, str(e))
    artifacts.put("test_export", {**res, "run_id": run_id, "ref": t["tested"]})
    return res


@app.post("/runs/{run_id}/defects/{defect:path}", response_model=ReviewItem)
def decide_defect(run_id: int, defect: str, body: DefectDecision):
    """M8 triage D15: confirm a defect from the report; logged in the shared review queue."""
    ex = next((e for e in get_run(run_id)["executions"]
               if e["stage"] == "triage" and e["status"] in ("succeeded", "stale")), None)  # fmt: skip
    if ex is None:
        raise HTTPException(409, "this run has no triage yet")
    item = review.create(ex["id"], "real_defect", {"defect": defect})
    return review.resolve(item, body.decision, body.by, body.reason)


@app.get("/runs/{run_id}/defects", response_model=list[ReviewItem])
def defect_decisions(run_id: int):
    ids = {e["id"] for e in get_run(run_id)["executions"]}
    return [i for i in review.list_() if i["kind"] == "real_defect" and i["stage_execution"] in ids]


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


# ---- SUT target and the New run screen (M8) -------------------------------------------

PREP: dict[str, Any] = {"state": "idle", "error": None}  # reference stack warm-up (env D18)


def _warm() -> None:
    PREP.update(state="starting", error=None)
    try:
        contract.SERVICES["env"].ensure("reference")
        PREP.update(state="ready")
    except Exception as e:  # noqa: BLE001 — shown on the SUT card
        PREP.update(state="failed", error=f"{type(e).__name__}: {e}"[:600])


@app.get("/sut")
def get_sut():
    cfg = sut.load()
    return {"name": cfg["name"], "ref": cfg["ref"], "target": cfg.get("target"), "stack": PREP}


@app.get("/sut/branches")
def sut_branches(repo: str):
    try:
        return sut.branches(repo)
    except sut.SutError as e:
        raise HTTPException(422, str(e))


@app.post("/sut/target")
def set_sut_target(body: SutTarget):
    try:
        t = sut.set_target(body.repo, body.branch, body.base, body.seeded)
    except sut.SutError as e:
        raise HTTPException(422, str(e))
    PREP.update(state="queued", error=None)
    submit(_warm)
    return {"target": t, "stack": PREP}


@app.get("/sut/routes")
def sut_routes():
    return contract.resolve("spec").routes()


@app.get("/rules/defaults")
def rule_defaults():
    gate = contract.resolve("gate")
    return {"clarify": contract.resolve("spec").default_rules(),
            "templates": contract.resolve("cases").templates(),
            "gate": {"defaults": gate.settings_defaults, "limits": gate.settings_limits}}  # fmt: skip


@app.post("/rules/suggest")
def rules_suggest(body: SuggestRules):
    gw = llm.Gateway("live", llm.anthropic_provider, budget_usd=0.05)
    try:
        return contract.resolve("spec").suggest_rules(gw, body.overview, body.routes)
    except Exception as e:  # noqa: BLE001 — suggestions are optional
        raise HTTPException(502, f"no suggestions: {type(e).__name__}: {e}"[:300])


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


# ---- targets (stage D: discovered profiles) -------------------------------------


def profile_choices() -> list[dict]:
    from tp.core import profile

    return profile.choices()


@app.get("/profiles", response_model=list[dict[str, Any]])
def list_profiles():
    """Targets a run can use: hand-written and discovered (discover/006)."""
    return profile_choices()


class TargetAdd(BaseModel):
    target: str  # a repo URL, optionally @<tag>
    no_conventions: bool = False


@app.get("/targets", response_model=list[dict[str, Any]])
def list_targets():
    from tp.stages.discover import targets

    return targets.list_()


@app.get("/targets/{slug}", response_model=dict[str, Any])
def get_target(slug: str):
    from tp.stages.discover import targets

    try:
        return targets.detail(slug)
    except KeyError:
        raise HTTPException(404, "target not found")


@app.post("/targets", response_model=Run)
def add_target(body: TargetAdd):
    """`tp target add`: a run holding only the discover stage (discover/001 D5)."""
    opts = {"target": body.target} | ({"no_conventions": True} if body.no_conventions else {})
    run_id = runner.start_run("live", options=opts | {"stops": FOCUSED_STOPS},
                              pipeline=["discover"])  # fmt: skip
    submit(runner.advance, run_id)
    return runner.get_run(run_id)


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
