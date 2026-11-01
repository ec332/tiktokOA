"""Immutable, content-addressed artifacts.

id = sha256 of the canonical manifest (minus id and created_at) plus the body, so the same
body with different parents, labels or stage version is a different artifact.
Stored as data/artifacts/<hex>.json = {"manifest": ..., "body": ...}, indexed in SQLite.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from tp.core import db, hashing
from tp.core import labels as labels_mod


class ArtifactConflict(Exception):
    """Stored content does not match its id (tampered or corrupted)."""


@dataclass(frozen=True)
class Artifact:
    id: str
    type: str
    schema_version: int
    stage: str | None
    stage_version: str | None
    parents: list[str]
    labels: list[str]
    created_at: str
    body: Any
    signed: dict | None = field(default=None)

    @property
    def manifest(self) -> dict:
        m = asdict(self)
        del m["body"]
        return m


def _hash_input(manifest: dict, body: Any) -> dict:
    return {**{k: v for k, v in manifest.items() if k not in ("id", "created_at")}, "body": body}


def _path(art_id: str):
    return db.data_dir() / "artifacts" / f"{art_id.removeprefix('sha256:')}.json"


def put(
    type: str,
    body: Any,
    *,
    schema_version: int = 1,
    stage: str | None = None,
    stage_version: str | None = None,
    parents: list[str] = (),
    labels: list[str] = (),
    signed: dict | None = None,
) -> Artifact:
    manifest = {
        "type": type,
        "schema_version": schema_version,
        "stage": stage,
        "stage_version": stage_version,
        "parents": sorted(parents),
        "labels": labels_mod.check(list(labels)),
        "signed": signed,
    }
    art_id = "sha256:" + hashing.sha256(_hash_input(manifest, body))
    path = _path(art_id)
    if path.exists():
        return get(art_id)  # same id twice is a no-op (get raises if the file was tampered)
    art = Artifact(id=art_id, created_at=db.now(), body=body, **manifest)
    path.write_bytes(hashing.canonical({"manifest": art.manifest, "body": body}))
    with db.connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
            (
                art.id,
                art.type,
                art.schema_version,
                art.stage,
                art.stage_version,
                json.dumps(art.parents),
                json.dumps(art.labels),
                art.created_at,
            ),
        )
    return art


def get(art_id: str) -> Artifact:
    path = _path(art_id)
    if not path.exists():
        raise KeyError(art_id)
    stored = json.loads(path.read_bytes())
    manifest, body = stored["manifest"], stored["body"]
    if (
        manifest.get("id") != art_id
        or "sha256:" + hashing.sha256(_hash_input(manifest, body)) != art_id
    ):
        raise ArtifactConflict(f"{art_id}: stored content does not match its id")
    return Artifact(body=body, **manifest)


def list_(type: str | None = None, limit: int = 200) -> list[dict]:
    with db.connect() as c:
        q, args = "SELECT * FROM artifacts", ()
        if type:
            q, args = q + " WHERE type=?", (type,)
        rows = c.execute(q + " ORDER BY created_at DESC LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def children(art_id: str) -> list[dict]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT a.* FROM artifacts a, json_each(a.parents) p WHERE p.value=?", (art_id,)
        ).fetchall()
    return [_row(r) for r in rows]


def _row(r) -> dict:
    return {**dict(r), "parents": json.loads(r["parents"]), "labels": json.loads(r["labels"])}
