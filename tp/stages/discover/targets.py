"""What the Targets page shows (stage D): discovered profiles under data/targets/, each field's
value, source, layer and probe, counts, exclusions, discovery runs, and the `--no-conventions`
comparison at the same commit (discover/001 D4)."""

import json

import yaml

from tp.core import artifacts, db, events, runner
from tp.core.profile import CONCERNS
from tp.stages.discover.stage import files, split

VERIFIED = {"probed", "repo", "ai_proposed+verified", "resolver"}


def _load(slug: str, nc: bool = False) -> dict | None:
    path = files(slug, nc)[0]
    return yaml.safe_load(path.read_text()) if path.exists() else None


def fields(p: dict) -> list[dict]:
    """One row per provenance sub-key: concern, key, value, source, layer, probe."""
    out = []
    for c in CONCERNS:
        f = p[c]
        for k, pv in (f.get("provenance") or {}).items():
            out.append({"concern": c, "key": k, "value": f["value"].get(k, f["value"]["strategy"]
                                                                       if k == "strategy" else None),
                        "source": pv["source"], "layer": pv["layer"], "probe": pv["probe"]})  # fmt: skip
    return out


def counts(p: dict) -> dict:
    rows = fields(p)
    return {"verified": sum(r["source"] in VERIFIED for r in rows),
            "clarify": sum(r["source"] == "clarify" for r in rows),
            "override": sum(r["source"] == "override" for r in rows),
            "layers": {lay: sum(r["layer"] == lay for r in rows)
                       for lay in ("convention", "algorithm", "exploration", "clarify")}}  # fmt: skip


def runs(slug: str) -> list[dict]:
    """Discovery runs for this target, newest first, with probes, model calls, cost and time."""
    out = []
    for r in runner.list_runs(500):
        if runner.stages(r["id"]) != ["discover"]:
            continue
        opt = runner.heads(r["id"]).get("run_options")
        o = artifacts.get(opt).body if opt else {}
        url, ref = split(o.get("target", ""))
        if url.rstrip("/").rsplit("/", 1)[-1].lower().removesuffix(".git") != slug:
            continue
        evs = events.since(r["id"])
        cost = [e["payload"]["usd"] for e in evs if e["kind"] == "llm_cost"]
        t = [e["at"] for e in evs if e["kind"] in ("stage_running", "stage_succeeded",
                                                   "stage_failed", "stage_awaiting_review")]  # fmt: skip
        ex = r["executions"] if "executions" in r else runner.get_run(r["id"])["executions"]
        outs = next((e["outputs"] for e in reversed(ex) if e["stage"] == "discover"), {})
        probes = len(artifacts.get(outs["discovery_probes"]).body["probes"]) \
            if "discovery_probes" in outs else None  # fmt: skip
        out.append({"run": r["name"], "id": r["id"], "status": r["status"], "mode": r["mode"],
                    "ref": ref, "no_conventions": bool(o.get("no_conventions")),
                    "model_calls": len(cost), "cost_usd": round(sum(cost), 5), "probes": probes,
                    "seconds": _seconds(t[0], t[-1]) if len(t) > 1 else None})  # fmt: skip
    return out


def _seconds(a: str, b: str) -> int:
    from datetime import datetime

    f = lambda s: datetime.fromisoformat(s)
    return int((f(b) - f(a)).total_seconds())


def list_() -> list[dict]:
    root = db.data_dir() / "targets"
    out = []
    for d in sorted(root.glob("*/profile.yaml")) if root.exists() else []:
        p = yaml.safe_load(d.read_text())
        out.append({"slug": d.parent.name, "repo": p["target"]["repo"],
                    "commit_tested": p["commit_tested"], "ref": p["reach"]["value"].get("ref"),
                    "counts": counts(p), "no_conventions": files(d.parent.name, True)[0].exists()})  # fmt: skip
    return out


def detail(slug: str) -> dict:
    p = _load(slug)
    if p is None:
        raise KeyError(slug)
    return {"slug": slug, "repo": p["target"]["repo"], "commit_tested": p["commit_tested"],
            "fields": fields(p), "counts": counts(p),
            "excluded": p["checks"]["value"].get("families", {}).get("exclude", []),
            "runs": runs(slug), "compare": compare(slug)}  # fmt: skip


def compare(slug: str) -> dict | None:
    """The two profile artifacts at the same commit, field by field (D4); None until both exist."""
    a, b = _load(slug), _load(slug, True)
    if not a or not b:
        return None
    ra = {(r["concern"], r["key"]): r for r in fields(a)}
    rb = {(r["concern"], r["key"]): r for r in fields(b)}
    rows = []
    for key in list(dict.fromkeys([*ra, *rb])):
        x, y = ra.get(key), rb.get(key)
        rows.append({"concern": key[0], "key": key[1],
                     "normal": x and {"source": x["source"], "layer": x["layer"]},
                     "no_conventions": y and {"source": y["source"], "layer": y["layer"]},
                     "same_value": bool(x and y and json.dumps(x["value"], sort_keys=True)
                                        == json.dumps(y["value"], sort_keys=True))})  # fmt: skip
    latest = {}
    for r in runs(slug):
        if r["status"] == "succeeded" and r["mode"] == "live":
            latest.setdefault("no_conventions" if r["no_conventions"] else "normal", r)
    return {"same_commit": a["commit_tested"] == b["commit_tested"], "rows": rows,
            "runs": latest}  # fmt: skip
