import json

from tp.core import db


def emit(run_id: int | None, execution_id: int | None, kind: str, **payload) -> None:
    with db.connect() as c:
        c.execute(
            "INSERT INTO events (run_id, stage_execution, kind, payload, at) VALUES (?,?,?,?,?)",
            (run_id, execution_id, kind, json.dumps(payload), db.now()),
        )


def since(run_id: int, after: int = 0) -> list[dict]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, after)
        ).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
