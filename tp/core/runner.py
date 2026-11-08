"""Pipeline runner: execution state machine, inputs from heads, stale propagation.

Head of a type in a run = output of the latest succeeded execution that produced it.
A succeeded execution goes stale when an artifact it consumed stops being a head.
Resuming after review = calling run() again on the same execution with decisions visible.
"""

import json
from pathlib import Path

from tp.core import artifacts, contract, db, events, labels, llm, review, strategies
from tp.core import profile as target_profile
from tp.core.contract import StageContext

PIPELINE = [
    "spec",
    "cases",
    "generate",
    "gate",
    "env",
    "execute",
    "triage",
    "report",
    "retest",
    "deliver",
]
DONE = ("succeeded", "stale")
# Artifact types a run itself is given -> schema version. run_options ({flows, fresh, ...}) is
# always present ({} by default), so stages may consume it without making it mandatory.
RUN_INPUTS = {"requirements": 1, "run_options": 1}


# ---- runs -------------------------------------------------------------------


def start_run(
    mode: str = "live",
    stub: bool = False,
    unattended: bool = False,
    inputs: dict[str, str] | None = None,
    options: dict | None = None,
    pipeline: list[str] | None = None,
) -> int:
    """pipeline: the stages this run holds (default PIPELINE; `tp target add` holds only
    discover, discover/001 D5). inputs: artifact type -> id given to the run itself (e.g. requirements). They are the
    outputs of an already-succeeded `input` execution, so heads() resolves them like any other.
    options: stage settings for this run (e.g. flows, fresh), stored as the run_options input."""
    llm.Gateway(mode)  # validates mode
    if unknown := set(inputs or {}) - set(RUN_INPUTS):
        raise ValueError(f"not a run input: {sorted(unknown)}")
    options = {
        **(options or {}),
        **({"unattended": True} if unattended else {}),
    }  # stages may read it
    inputs = {**(inputs or {}), "run_options": artifacts.put("run_options", options).id}
    with db.connect() as c, c:
        c.execute("BEGIN")
        run_id = c.execute(
            "INSERT INTO pipeline_runs (mode, stub, unattended, started, status) "
            "VALUES (?,?,?,?,?)",
            (mode, int(stub), int(unattended), db.now(), "pending"),
        ).lastrowid
        if inputs:
            c.execute(
                "INSERT INTO stage_executions (run_id, stage, status, outputs, started, finished) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, "input", "succeeded", json.dumps(inputs), db.now(), db.now()),
            )
        for name in pipeline or PIPELINE:
            c.execute(
                "INSERT INTO stage_executions (run_id, stage, status) VALUES (?,?,?)",
                (run_id, name, "pending"),
            )
    target = _pin_target(options) if pipeline != ["discover"] else None
    if target and not stub and (other := busy(Path(target).name)):
        raise ValueError(f"target busy: {other} holds {Path(target).name}; "
                         f"`tp cancel` it, or wait for it to finish (L3)")  # fmt: skip
    events.emit(run_id, None, "run_created", mode=mode, stub=stub)
    if target:
        events.emit(
            run_id, None, "run_target", path=target
        )  # every stage runs on it (discover/006)
    return run_id


def get_run(run_id: int) -> dict:
    with db.connect() as c:
        r = c.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            raise KeyError(run_id)
        ex = c.execute(
            "SELECT * FROM stage_executions WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
    return {**_run_row(r), "executions": [_ex_row(e) for e in ex]}


def list_runs(limit: int = 100) -> list[dict]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (limit,))
        return [_run_row(r) for r in rows.fetchall()]


def advance(run_id: int) -> dict:
    """Run every stage that can run, in pipeline order. Stops at a review or a failure."""
    _set_run(run_id, "running")
    for name in stages(run_id):
        if _cancelled(run_id):
            return get_run(run_id)
        ex = _latest(run_id, name)
        if ex["status"] in DONE:
            continue
        if ex["status"] == "awaiting_review" and _open_items(ex["id"]):
            return _finish(run_id, "awaiting_review")
        if ex["status"] == "failed":
            return _finish(run_id, "failed")
        if _execute(run_id, ex["id"])["status"] != "succeeded":
            return _finish(run_id, _latest(run_id, name)["status"])
    return _finish(run_id, "succeeded")


def rerun(
    run_id: int, stage: str, overrides: dict[str, str] | None = None, from_run: int | None = None
) -> dict:
    """New execution of one stage. Downstream is marked stale, never re-run automatically."""
    with db.connect() as c:
        ex_id = c.execute(
            "INSERT INTO stage_executions (run_id, stage, status) VALUES (?,?,?)",
            (run_id, stage, "pending"),
        ).lastrowid
    _set_run(run_id, "running")
    ex = _execute(run_id, ex_id, overrides or {}, from_run)
    _finish(run_id, _derived_status(run_id))
    return ex


def resume(item_id: int) -> dict | None:
    """After a review is resolved: continue its run if nothing else is still open."""
    item = review.get(item_id)
    ex_id = item["stage_execution"]
    with db.connect() as c:
        run_id, stage = c.execute(
            "SELECT run_id, stage FROM stage_executions WHERE id=?", (ex_id,)
        ).fetchone()
    if _open_items(ex_id):
        return None
    order = stages(run_id)
    earlier = order[: order.index(stage)] if stage in order else []
    if all(_latest(run_id, s)["status"] in DONE for s in earlier):
        return advance(run_id)
    # The stage was run on its own (tp stage run): continue just that execution, with the inputs
    # it was given (overrides aren't heads of the run).
    _set_run(run_id, "running")
    given = next(e for e in get_run(run_id)["executions"] if e["id"] == ex_id)["inputs"]
    _execute(run_id, ex_id, given)
    return _finish(run_id, _derived_status(run_id))


def heads(run_id: int) -> dict[str, str]:
    out: dict[str, str] = {}
    with db.connect() as c:
        rows = c.execute(
            "SELECT outputs FROM stage_executions WHERE run_id=? AND status IN "
            "('succeeded','stale') ORDER BY id",
            (run_id,),
        ).fetchall()
    for r in rows:
        out.update(json.loads(r["outputs"]))
    return out


def retry(run_id: int) -> dict:
    """Give each failed stage a fresh pending execution, then advance (M8: a failed run can be
    continued from the failed step; earlier outputs and decisions are kept)."""
    for name in stages(run_id):
        if _latest(run_id, name)["status"] == "failed":
            with db.connect() as c:
                c.execute("INSERT INTO stage_executions (run_id, stage, status) VALUES (?,?,?)",
                          (run_id, name, "pending"))  # fmt: skip
            events.emit(run_id, None, "stage_retried", stage=name)
    return advance(run_id)


def cancel(run_id: int) -> dict:
    """Stop a run (M8 P2). Cooperative: the stage that is running finishes; nothing after it
    starts. A cancelled run can be resumed by advancing it again."""
    _set_run(run_id, "cancelled")
    events.emit(run_id, None, "run_cancelled")
    return get_run(run_id)


def _cancelled(run_id: int) -> bool:
    with db.connect() as c:
        return c.execute("SELECT status FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()[0] \
            == "cancelled"  # fmt: skip


# ---- one execution ------------------------------------------------------------


def _pin_target(options: dict) -> str | None:
    """The target this run is for: `run_options.profile` (a path or slug, e.g. from the New run
    page), else the one TP_TARGET selects now; absolute, so a resume or retry from another
    process uses the same profile. None when nothing resolves (a discover-only run)."""
    with target_profile.selected(options.get("profile")):
        try:
            d = target_profile.target_dir()
        except target_profile.ProfileError:
            return None
    return str(d.resolve()) if (d / "profile.yaml").exists() else None


def busy(slug: str, but: int | None = None) -> str | None:
    """A run other than `but` that holds this target (L3, the rule of discover/006): running, or
    waiting for review after its env stage brought the stack up. A crashed run stays `running`
    until it is cancelled, which `GET /runs` shows (harden/001 H1). Stub runs touch no target."""
    for r in list_runs(500):
        if r["id"] == but or r["stub"] or r["status"] not in ("running", "awaiting_review"):
            continue
        if stages(r["id"]) == ["discover"] or (t := run_target(r["id"])) is None:
            continue
        if Path(t).name != slug:
            continue
        env = next((e for e in reversed(get_run(r["id"])["executions"]) if e["stage"] == "env"),
                   None)  # fmt: skip
        if r["status"] == "running" or (env and env["status"] == "succeeded"):
            return r["name"]
    return None


def run_target(run_id: int) -> str | None:
    return next((e["payload"]["path"] for e in events.since(run_id) if e["kind"] == "run_target"),
                None)  # fmt: skip


def _execute(
    run_id: int, ex_id: int, overrides: dict[str, str] | None = None, from_run: int | None = None
) -> dict:
    with target_profile.selected(run_target(run_id)):
        return _execute_one(run_id, ex_id, overrides, from_run)


def _execute_one(
    run_id: int, ex_id: int, overrides: dict[str, str] | None, from_run: int | None
) -> dict:
    run = get_run(run_id)
    if not run["stub"] and (t := run_target(run_id)) and (other := busy(Path(t).name, run_id)):
        return _fail(run_id, ex_id, f"target busy: {other} holds {Path(t).name} (L3)")
    ex = next(e for e in run["executions"] if e["id"] == ex_id)
    stage = contract.resolve(ex["stage"], stub=run["stub"])
    available = heads(from_run if from_run is not None else run_id) | (overrides or {})
    missing = [t for t in stage.consumes if t not in available]
    if missing:
        return _fail(run_id, ex_id, f"missing inputs: {missing}")
    inputs = {t: artifacts.get(available[t]) for t in stage.consumes}
    wrong = [
        f"{t} v{a.schema_version} != v{v}"
        for t, a in inputs.items()
        if (v := stage.consumes[t]) != a.schema_version
    ]
    if wrong:
        return _fail(run_id, ex_id, f"schema mismatch: {wrong}")

    _update(
        ex_id,
        status="running",
        stage_version=stage.version,
        started=db.now(),
        inputs=json.dumps({t: a.id for t, a in inputs.items()}),
    )
    events.emit(run_id, ex_id, "stage_running", stage=stage.name)
    provider = llm.echo_provider if run["stub"] else llm.anthropic_provider
    spent = sum(
        e["payload"]["usd"]
        for e in events.since(run_id)
        if e["stage_execution"] == ex_id and e["kind"] == "llm_cost"
    )  # the budget covers the whole execution, across review rounds
    gateway = llm.Gateway(
        run["mode"],
        provider,
        budget_usd=getattr(stage, "budget_usd", llm.DEFAULT_BUDGET_USD),
        spent_usd=spent,
        on_cost=lambda usd, model: events.emit(run_id, ex_id, "llm_cost", usd=usd, model=model),
    )
    ctx = StageContext(
        llm=gateway,
        review=review.ReviewHandle(ex_id),
        env=contract.SERVICES.get("env"),
        emit=lambda kind, **p: events.emit(run_id, ex_id, kind, **p),
    )
    try:  # a run with no target profile fails at the first stage that needs one, not here
        ctx.profile = target_profile.load()
        ctx.strategies = strategies.resolve(ctx.profile)
    except (target_profile.ProfileError, strategies.ResolveError) as e:
        ctx.emit("target_unresolved", reason=str(e))
    stops = _stops(run_id)
    while True:
        try:
            result = stage.run(ctx, inputs)
        except Exception as e:  # noqa: BLE001 — a crashing stage fails its execution, not the runner
            return _fail(run_id, ex_id, f"{type(e).__name__}: {e}")
        if result.status != "awaiting_review" or (stops is None and not run["unattended"]):
            break
        if not _auto_decide(ex_id, () if run["unattended"] else stops, _decisions(run_id)):
            break

    if result.status == "failed":
        return _fail(run_id, ex_id, result.reason)
    if result.status == "awaiting_review":
        _update(ex_id, status="awaiting_review")
        events.emit(run_id, ex_id, "stage_awaiting_review", items=result.review_items)
        return _ex(ex_id)

    unknown = set(result.outputs) - set(stage.produces)
    if unknown or not result.outputs:
        return _fail(run_id, ex_id, f"bad outputs: {sorted(result.outputs)}")
    parent_ids = sorted(a.id for a in inputs.values())
    own = labels.for_mode(run["mode"], run["stub"]) + list(result.labels)
    out_labels = labels.inherit([a.labels for a in inputs.values()], own)
    previous = heads(run_id)
    outputs = {
        t: artifacts.put(
            t,
            body,
            schema_version=stage.produces[t],
            stage=stage.name,
            stage_version=stage.version,
            parents=parent_ids,
            labels=out_labels,
        ).id
        for t, body in result.outputs.items()
    }
    _update(ex_id, status="succeeded", outputs=json.dumps(outputs), finished=db.now())
    events.emit(run_id, ex_id, "stage_succeeded", stage=stage.name, outputs=outputs)
    dead = {previous[t] for t in outputs if t in previous and previous[t] != outputs[t]}
    _mark_stale(run_id, dead)
    return _ex(ex_id)


def _mark_stale(run_id: int, dead: set[str]) -> None:
    while dead:
        newly: set[str] = set()
        for name in stages(run_id):
            ex = _latest(run_id, name)
            if ex["status"] == "succeeded" and dead & set(ex["inputs"].values()):
                _update(ex["id"], status="stale")
                events.emit(run_id, ex["id"], "stage_stale", stage=name)
                newly |= set(ex["outputs"].values())
        dead = newly


def _stops(run_id: int) -> tuple[str, ...] | None:
    """Focused mode (M8 skeleton D27): the review kinds that wait for a person; None = all."""
    opt = heads(run_id).get("run_options")
    stops = artifacts.get(opt).body.get("stops") if opt else None
    return None if stops is None else tuple(stops)


def _decisions(run_id: int) -> dict[str, str]:
    """A run's own defaults over UNATTENDED_DECISION (a Scan confirms code-derived behaviour,
    autonomy/001 F9)."""
    opt = heads(run_id).get("run_options")
    return (artifacts.get(opt).body.get("decisions") or {}) if opt else {}


def _auto_decide(ex_id: int, stops: tuple[str, ...] = (), decisions: dict | None = None) -> bool:
    """Apply the default decision to every open item whose kind isn't a stop. False if any item
    is left open (a stop, or a kind with no default): the execution then waits for a person."""
    by = "focused" if stops else "unattended"
    for item in _open_items(ex_id):
        decision = {**review.UNATTENDED_DECISION, **(decisions or {})}.get(item["kind"])
        if item["kind"] in stops or not decision:
            continue
        review.resolve(item["id"], decision, by=by, reason=f"{by} default")
    return not _open_items(ex_id)


# ---- helpers -------------------------------------------------------------------


def stages(run_id: int) -> list[str]:
    """The stages a run holds, in its order (PIPELINE unless it was started with its own)."""
    with db.connect() as c:
        rows = c.execute("SELECT stage FROM stage_executions WHERE run_id=? AND stage!='input' "
                         "GROUP BY stage ORDER BY MIN(id)", (run_id,)).fetchall()  # fmt: skip
    return [r[0] for r in rows]


def _open_items(ex_id: int) -> list[dict]:
    return review.list_(status="open", execution_id=ex_id)


def _latest(run_id: int, stage: str) -> dict:
    with db.connect() as c:
        r = c.execute(
            "SELECT * FROM stage_executions WHERE run_id=? AND stage=? ORDER BY id DESC LIMIT 1",
            (run_id, stage),
        ).fetchone()
    return _ex_row(r)


def _ex(ex_id: int) -> dict:
    with db.connect() as c:
        return _ex_row(c.execute("SELECT * FROM stage_executions WHERE id=?", (ex_id,)).fetchone())


def _fail(run_id: int, ex_id: int, reason: str) -> dict:
    _update(ex_id, status="failed", reason=reason, finished=db.now())
    events.emit(run_id, ex_id, "stage_failed", reason=reason)
    return _ex(ex_id)


def _update(ex_id: int, **cols) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    with db.connect() as c:
        c.execute(f"UPDATE stage_executions SET {sets} WHERE id=?", (*cols.values(), ex_id))


def _set_run(run_id: int, status: str) -> None:
    with db.connect() as c:
        c.execute("UPDATE pipeline_runs SET status=? WHERE id=?", (status, run_id))


def _finish(run_id: int, status: str) -> dict:
    if _cancelled(run_id):
        return get_run(run_id)
    _set_run(run_id, status)
    events.emit(run_id, None, "run_" + status)
    return get_run(run_id)


def _derived_status(run_id: int) -> str:
    statuses = {_latest(run_id, n)["status"] for n in stages(run_id)}
    return next((s for s in ("failed", "awaiting_review", "pending") if s in statuses), "succeeded")


def _run_row(r) -> dict:
    d = dict(r)
    d["stub"], d["unattended"] = bool(d["stub"]), bool(d["unattended"])
    d["name"] = f"r-{d['id']:04d}"
    return d


def _ex_row(r) -> dict:
    d = dict(r)
    d["inputs"], d["outputs"] = json.loads(d["inputs"]), json.loads(d["outputs"])
    return d
