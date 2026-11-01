"""Pipeline runner: execution state machine, inputs from heads, stale propagation.

Head of a type in a run = output of the latest succeeded execution that produced it.
A succeeded execution goes stale when an artifact it consumed stops being a head.
Resuming after review = calling run() again on the same execution with decisions visible.
"""

import json

from tp.core import artifacts, contract, db, events, labels, llm, review
from tp.core.contract import StageContext

PIPELINE = ["spec", "cases", "generate", "gate", "env", "execute", "triage", "report", "retest"]
DONE = ("succeeded", "stale")


# ---- runs -------------------------------------------------------------------


def start_run(mode: str = "live", stub: bool = False, unattended: bool = False) -> int:
    llm.Gateway(mode)  # validates mode
    with db.connect() as c, c:
        c.execute("BEGIN")
        run_id = c.execute(
            "INSERT INTO pipeline_runs (mode, stub, unattended, started, status) "
            "VALUES (?,?,?,?,?)",
            (mode, int(stub), int(unattended), db.now(), "pending"),
        ).lastrowid
        for name in PIPELINE:
            c.execute(
                "INSERT INTO stage_executions (run_id, stage, status) VALUES (?,?,?)",
                (run_id, name, "pending"),
            )
    events.emit(run_id, None, "run_created", mode=mode, stub=stub)
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
    for name in PIPELINE:
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
    with db.connect() as c:
        run_id = c.execute(
            "SELECT run_id FROM stage_executions WHERE id=?", (item["stage_execution"],)
        ).fetchone()[0]
    if _open_items(item["stage_execution"]):
        return None
    return advance(run_id)


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


# ---- one execution ------------------------------------------------------------


def _execute(
    run_id: int, ex_id: int, overrides: dict[str, str] | None = None, from_run: int | None = None
) -> dict:
    run = get_run(run_id)
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
    provider = llm.echo_provider if run["stub"] else llm._no_provider
    ctx = StageContext(
        llm=llm.Gateway(run["mode"], provider),
        review=review.ReviewHandle(ex_id),
        env=contract.SERVICES.get("env"),
        emit=lambda kind, **p: events.emit(run_id, ex_id, kind, **p),
    )
    while True:
        try:
            result = stage.run(ctx, inputs)
        except Exception as e:  # noqa: BLE001 — a crashing stage fails its execution, not the runner
            return _fail(run_id, ex_id, f"{type(e).__name__}: {e}")
        if result.status != "awaiting_review" or not run["unattended"]:
            break
        if not _auto_decide(ex_id):
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
        for name in PIPELINE:
            ex = _latest(run_id, name)
            if ex["status"] == "succeeded" and dead & set(ex["inputs"].values()):
                _update(ex["id"], status="stale")
                events.emit(run_id, ex["id"], "stage_stale", stage=name)
                newly |= set(ex["outputs"].values())
        dead = newly


def _auto_decide(ex_id: int) -> bool:
    """Unattended mode: apply the default decision; False if some item has no default."""
    for item in _open_items(ex_id):
        decision = review.UNATTENDED_DECISION.get(item["kind"])
        if not decision:
            return False
        review.resolve(item["id"], decision, by="unattended", reason="unattended default")
    return True


# ---- helpers -------------------------------------------------------------------


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
    _set_run(run_id, status)
    events.emit(run_id, None, "run_" + status)
    return get_run(run_id)


def _derived_status(run_id: int) -> str:
    statuses = {_latest(run_id, n)["status"] for n in PIPELINE}
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
