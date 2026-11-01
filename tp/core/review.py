"""One review queue for every human decision. Decisions are append-only, mirrored to jsonl."""

import json

from tp.core import db, events

# kind -> decision options; the UI renders buttons from this.
KINDS: dict[str, list[str]] = {
    "question": ["pick", "defer"],
    "confirm_observed": ["confirm", "reject", "out_of_scope"],
    "signoff": ["approve", "reject", "defer"],
    "factory_proposal": ["accept", "reject"],
    "unbound": ["rewrite_case", "accept_gap"],
    "equivalent_mutant": ["equivalent", "not_equivalent"],
    "triage": ["accept", "override"],
    "real_defect": ["confirm", "reject"],
}
NEEDS_REASON = {("triage", "override")}
CHOICE_DECISIONS = {("question", "pick"), ("triage", "override")}  # detail.choice = a payload choice
# Kinds whose payload may carry `lines`: each line is decided on its own (plan C5).
LINE_OPTIONS: dict[str, list[str]] = {"signoff": ["approve", "reject"]}
LINE_NEEDS_COMMENT = {"reject"}
SKIPS_DETAIL = {"defer"}  # deciding the whole item at once, no per-line/choice detail
UNATTENDED_DECISION = {
    "signoff": "defer",
    "question": "defer",
    "confirm_observed": "out_of_scope",  # unconfirmed code-derived behaviour never reaches cases
    "unbound": "accept_gap",  # an unbindable case is listed as a gap, never silently dropped
    "equivalent_mutant": "not_equivalent",  # unconfirmed: counts as a survivor (gate D11)
    "triage": "accept",  # the rules' class stands; the report marks it decided by "unattended"
}  # others fail unattended


class ReviewHandle:
    """What a stage sees: create items for its execution, read back their state."""

    def __init__(self, execution_id: int):
        self.execution_id = execution_id

    def create(self, kind: str, payload: dict) -> int:
        return create(self.execution_id, kind, payload)

    def items(self) -> list[dict]:
        return list_(execution_id=self.execution_id)


def create(execution_id: int, kind: str, payload: dict) -> int:
    """payload may hold `lines: [{id, text, ...}]` (line-level kinds) or, for `question`,
    `choices: [{id, text}]` plus an optional `recommended` choice id."""
    if kind not in KINDS:
        raise ValueError(f"unknown review kind: {kind}")
    _check_payload(kind, payload)
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO review_items (kind, stage_execution, payload, status) VALUES (?,?,?,?)",
            (kind, execution_id, json.dumps(payload), "open"),
        )
        run_id = c.execute(
            "SELECT run_id FROM stage_executions WHERE id=?", (execution_id,)
        ).fetchone()
    events.emit(
        run_id and run_id[0], execution_id, "review_created", item=cur.lastrowid, review_kind=kind
    )
    return cur.lastrowid


def list_(status: str | None = None, execution_id: int | None = None) -> list[dict]:
    q, args = "SELECT * FROM review_items WHERE 1=1", []
    if status:
        q, args = q + " AND status=?", [*args, status]
    if execution_id is not None:
        q, args = q + " AND stage_execution=?", [*args, execution_id]
    with db.connect() as c:
        rows = c.execute(q + " ORDER BY id", args).fetchall()
    return [_row(r) for r in rows]


def get(item_id: int) -> dict:
    with db.connect() as c:
        r = c.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone()
    if not r:
        raise KeyError(item_id)
    return _row(r)


def resolve(
    item_id: int,
    decision: str | None,
    by: str,
    reason: str = "",
    detail: dict | None = None,
    seconds: float | None = None,
) -> dict:
    """Record the decision. Resuming the waiting stage is the runner's job (runner.resume).

    Line items: pass detail={"lines": {id: {"decision", "comment"}}}; the item's decision is
    derived (approve only if every line is approved). Questions: decision "pick" needs
    detail={"choice": <choice id>}. seconds: time the reviewer spent on the item (UI), logged
    with the decision only, not on the item, so stages never see it.
    """
    item = get(item_id)
    if item["status"] != "open":
        raise ValueError(f"review {item_id} is already {item['status']}")
    decision, detail = _check_detail(item, decision, detail or {})
    if decision not in KINDS[item["kind"]]:
        raise ValueError(f"{decision!r} is not an option for {item['kind']}")
    if (item["kind"], decision) in NEEDS_REASON and not reason:
        raise ValueError("a reason is required for this decision")
    at = db.now()
    with db.connect() as c, c:
        c.execute("BEGIN")
        c.execute(
            "UPDATE review_items SET status='resolved', decision=?, detail=?, decided_by=?, "
            "reason=? WHERE id=?",
            (decision, json.dumps(detail), by, reason, item_id),
        )
        c.execute(
            "INSERT INTO decisions (review_item, decision, detail, decided_by, reason, at) "
            "VALUES (?,?,?,?,?,?)",
            (item_id, decision, json.dumps(detail | {"seconds": seconds}), by, reason, at),
        )
    record = {
        "review_item": item_id,
        "kind": item["kind"],
        "decision": decision,
        "detail": detail,
        "by": by,
        "reason": reason,
        "at": at,
        "seconds": seconds,
    }
    with open(db.data_dir() / "decisions.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    return get(item_id)


def _check_payload(kind: str, payload: dict) -> None:
    if "lines" in payload:
        if kind not in LINE_OPTIONS:
            raise ValueError(f"{kind} items cannot have lines")
        ids = [line["id"] for line in payload["lines"]]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("lines need unique ids and at least one line")
    if kind == "question":
        ids = [c["id"] for c in payload.get("choices", [])]
        if len(ids) < 2 or len(ids) != len(set(ids)):
            raise ValueError("a question needs at least two choices with unique ids")
        if payload.get("recommended") not in (None, *ids):
            raise ValueError("recommended must be one of the choice ids")


def _check_detail(item: dict, decision: str | None, detail: dict) -> tuple[str, dict]:
    payload, kind = item["payload"], item["kind"]
    if decision in SKIPS_DETAIL:
        return decision, {}
    if "lines" in payload:
        given = detail.get("lines", {})
        ids = {line["id"] for line in payload["lines"]}
        if set(given) != ids:
            raise ValueError(f"decide every line exactly once: missing {sorted(ids - set(given))}")
        for line_id, d in given.items():
            if d.get("decision") not in LINE_OPTIONS[kind]:
                raise ValueError(f"line {line_id}: decision must be one of {LINE_OPTIONS[kind]}")
            if d["decision"] in LINE_NEEDS_COMMENT and not d.get("comment"):
                raise ValueError(f"line {line_id}: a comment is required to {d['decision']}")
        derived = "approve" if all(d["decision"] == "approve" for d in given.values()) else "reject"
        if decision not in (None, derived):
            raise ValueError(f"decision {decision!r} contradicts the line decisions ({derived})")
        return derived, {"lines": given}
    if (kind, decision) in CHOICE_DECISIONS:
        choice = detail.get("choice")
        if choice not in {c["id"] for c in payload.get("choices", [])}:
            raise ValueError(f"{decision} needs detail.choice set to one of the choice ids")
        return decision, {"choice": choice}
    if decision is None:
        raise ValueError("a decision is required")
    return decision, {}


def _row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    d["detail"] = json.loads(d["detail"]) if d["detail"] else None
    d["options"] = KINDS[d["kind"]]
    d["line_options"] = LINE_OPTIONS.get(d["kind"], []) if "lines" in d["payload"] else []
    return d
