"""Stage 7 — Triage: every non-passing verdict → one classified finding with evidence; real
defects localised and sent for review. No LLM in M4. Design: docs/status/triage/001."""

import hashlib
import json
import subprocess
from pathlib import Path

from tp.core import db, profile
from tp.core.contract import StageResult, register
from tp.stages.triage import localise, propose, rules
from tp.stages.triage.evidence import EvidenceIndex


def _records(folder: Path, root: str) -> tuple[list, list, dict]:
    """Read a sealed run folder, refusing it if any record changed (execute's write-once seal)."""
    seal = folder / "records.sha256"
    if hashlib.sha256(seal.read_bytes()).hexdigest() != root:
        raise ValueError(f"{folder}: records.sha256 does not match the run_records root hash")
    for line in seal.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        if hashlib.sha256((folder / rel).read_bytes()).hexdigest() != digest:
            raise ValueError(f"{folder}: {rel} was changed after sealing")
    rows = [json.loads(x) for x in (folder / "results.jsonl").read_text().splitlines()]
    probes = json.loads((folder / "probes.json").read_text())
    logs = {p.stem: p.read_text() for p in (folder / "logs").glob("*.log")}
    return rows, probes, logs


def _git_source(src: Path):
    def read(rel: str):
        path = src / rel
        if not path.exists():
            return None
        up = subprocess.run(["git", "-C", str(src), "show", f"HEAD:{rel}"], capture_output=True,
                            text=True, check=False)  # fmt: skip
        return path.read_text(), (up.stdout if up.returncode == 0 else "")

    return read


def analyse(run_records: dict, rows: list, probes: list, logs: dict, spec: dict, read,
            app_files: dict[str, str], openapi: dict | None = None) -> dict:  # fmt: skip
    """Everything up to the human decisions. Pure given its inputs."""
    idx = EvidenceIndex(rows, probes, logs)
    fs = rules.findings(run_records["verdicts"], idx.rows)
    _malformed(fs, idx.rows, openapi)
    _spec_side(fs, idx.rows, (spec.get("index") or {}).get("from") == "openapi")
    ds = rules.defects(fs)
    criteria = {c["id"]: c for c in spec["criteria"]}
    src = localise.Source(read)
    by_id = {f["id"]: f for f in fs}
    for d in ds:
        failing = next(idx.rows[a] for f in d["findings"] for a in by_id[f]["evidence"]
                       if idx.rows.get(a, {}).get("outcome") == "failed")  # fmt: skip
        steps = [e for e in failing.get("exchanges", []) if not e.get("setup")]
        ex = steps[-1] if steps else None
        trace = ex and ex.get("x_test_trace")
        log = idx.log(trace) if trace else None
        d["evidence"] = [failing["id"]] + ([f"{failing['id']}#{ex['step']}"] if ex else []) \
            + ([f"log:{trace}"] if log else [])  # fmt: skip
        d["observed"] = ex and {"method": ex["method"], "path": ex["path"], "query": ex["query"],
                                "json": ex.get("json"), "status": ex["status"]}  # fmt: skip
        d["failed_assertions"] = [a for a in failing.get("assertions", []) if not a["ok"]]
        d["root_cause"] = (log or {}).get("traceback", {}) and log["traceback"]["root_cause"]
        d["suspects"] = localise.suspects(failing, log, criteria.get(d["ac"]), src, app_files)
        assert all(idx.exists(e) for e in d["evidence"]), d["evidence"]  # cited ids must resolve
    cited = {e for d in ds for e in d["evidence"]}
    scores = []
    for d in ds:
        if d["kind"] == "seeded":
            sites = localise.flag_sites(app_files, d["flag"])
            r = localise.rank(d["suspects"], sites, app_files)
            scores.append({"defect": d["id"], "flag": d["flag"], "rank": r, "sites": sites,
                           **{f"top{k}": r is not None and r <= k for k in (1, 3, 5)}})  # fmt: skip
    return {
        "findings": fs,
        "defects": ds,
        "scores": scores,
        "evidence": {e: _excerpt(e, idx.resolve(e)) for e in sorted(cited)},
    }


WRONG_ASSUMPTION = ("SPEC_GAP", ("the served spec and the server agree on this status: the "
                                 "drafted assumption is wrong"))  # fmt: skip
UNDECLARED = ("SPEC_MISMATCH", ("an assumed criterion failed on a status its spec doesn't "
                                "declare: server or spec is wrong"))  # fmt: skip


def _malformed(fs: list[dict], rows: dict, openapi: dict | None) -> None:
    """A product finding whose request put a value in the path that its parameter schema
    refuses (an integer id on a UUID route), answered with a 4xx: the test is wrong, not the
    server (demo/005)."""
    for f in fs:
        if f["class"] not in ("PRODUCT_DEFECT", "INTERMITTENT_PRODUCT"):
            continue
        row = next(
            (rows[a] for a in f["evidence"] if rows.get(a, {}).get("outcome") == "failed"), {}
        )
        ex = rules.under_test(row)
        if (
            ex
            and 400 <= (ex.get("status") or 0) < 500
            and (why := rules.malformed_path(ex, openapi))
        ):
            f["original_class"], f["class"] = f["class"], "TEST_DEFECT"
            f["rule"] = rules.MALFORMED.format(why)


def _spec_side(fs: list[dict], rows: dict, from_openapi: bool) -> None:
    """checks/001 D7: an assumed criterion failing on a status its served spec declares is a
    wrong assumption, not a finding of any kind; on an undeclared one, server and spec disagree.
    Every SPEC_MISMATCH carries what the report shows: the request, status, checks and curl."""
    for f in fs:
        row = next(
            (rows[a] for a in f["evidence"] if rows.get(a, {}).get("outcome") == "failed"), {}
        )
        declared = (row.get("failure") or {}).get("declared")
        if from_openapi and f["class"] == "SPEC_GAP" and declared is not None:
            f["declared"] = declared
            f["class"], f["rule"] = WRONG_ASSUMPTION if declared else UNDECLARED
        if f["class"] == "SPEC_MISMATCH":
            ex, obs = rules.under_test(row) or {}, row.get("observed") or {}
            f["observed"] = {"request": f"{ex.get('method')} {ex.get('path')}",
                             "status": ex.get("status"),
                             "error": (row.get("failure") or {}).get("error"),
                             **{k: obs[k] for k in ("checks", "curl", "examples") if k in obs}}  # fmt: skip


def _excerpt(eid: str, ev: dict) -> dict:
    """What the report embeds for a cited id (the report reads no run folder itself)."""
    if eid.startswith("log:"):
        tb = ev["traceback"] or {}
        return {"kind": "log", "variant": ev["variant"], "line_no": ev["line_no"], "line": ev["line"],
                "traceback": (tb.get("text") or "")[-6000:]}  # fmt: skip
    if "#" in eid:
        body = json.dumps(ev.get("body"))
        return {"kind": "exchange", **{k: ev.get(k) for k in ("method", "path", "query", "json", "status",
                "x_test_trace", "traceparent")}, "body": body[:2000]}  # fmt: skip
    return {"kind": "record", **{k: ev.get(k) for k in ("case_id", "variant", "batch", "outcome", "origin")},
            "failed": [a for a in ev.get("assertions", []) if not a["ok"]],
            "error": (ev.get("failure") or {}).get("error")}  # fmt: skip


class TriageStage:
    name = "triage"
    version = (
        f"1.2.0+p{propose.prompt_hash()}"  # 1.1: fix suggestions (M8 D14); 1.2: malformed path
    )
    consumes: dict[str, int] = {"run_records": 1, "gate_report": 1, "cases": 1, "spec": 1}  # noqa: RUF012
    produces: dict[str, int] = {"triage": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        state_path = db.data_dir() / "runs" / f"triage-exec-{ctx.review.execution_id}.json"
        if not state_path.exists():
            rr = inputs["run_records"].body
            rows, probes, logs = _records(db.data_dir() / rr["folder"], rr["root"])
            where = ctx.strategies.mutation.scope()["src"] if ctx.strategies else None
            src = Path(where) if where else None  # a black-box target has no source
            read = _git_source(src) if src else (lambda rel: None)
            app = (
                {
                    str(p.relative_to(src)): p.read_text()
                    for p in (src / profile.source_dir()).rglob("*.py")
                }
                if src
                else {}
            )
            try:  # the served spec, for the malformed-path rule; without it the rule is skipped
                openapi = ctx.strategies.spec.openapi("reference") if ctx.strategies else None
            except Exception:  # noqa: BLE001  the stack may already be down
                openapi = None
            state = analyse(rr, rows, probes, logs, inputs["spec"].body, read, app, openapi)
            crit = {c["id"]: c for c in inputs["spec"].body["criteria"]}
            for d in state["defects"]:
                if d["kind"] != "real" or not src:
                    continue
                files = {s["file"]: got[1] for s in d["suspects"][:5]
                         if (got := read(s["file"])) and got[1]}  # fmt: skip
                if files:
                    d["suggestion"] = propose.suggest(ctx.llm, d, crit.get(d["ac"]), files)
                    ctx.emit("triage_fix_suggested", defect=d["id"],
                             applies=d["suggestion"]["applies"])  # fmt: skip
            choices = [{"id": c, "text": c} for c in rules.CLASSES]
            for d in state["defects"]:
                if d["kind"] == "real":
                    d["review"] = ctx.review.create("triage", {
                        "defect": d["id"], "class": d["class"], "ac": d["ac"], "cases": d["cases"],
                        "observed": d["observed"], "suspects": d["suspects"][:3],
                        "evidence": d["evidence"], "choices": choices})  # fmt: skip
            state_path.write_text(json.dumps(state))
            ctx.emit(
                "triage_classified", findings=len(state["findings"]), defects=len(state["defects"])
            )
        state = json.loads(state_path.read_text())
        items = {i["id"]: i for i in ctx.review.items()}
        if open_ := [i for i in items.values() if i["status"] == "open"]:
            return StageResult.awaiting_review([i["id"] for i in open_])
        overrides = []
        for d in state["defects"]:
            item = items.get(d.get("review"))
            if not item:
                continue
            d["decision"] = {
                "by": item["decided_by"],
                "decision": item["decision"],
                "reason": item["reason"],
            }
            if item["decision"] == "override":
                new = item["detail"]["choice"]
                overrides.append({"defect": d["id"], "from": d["class"], "to": new,
                                  "reason": item["reason"], "by": item["decided_by"],
                                  "review_item": item["id"]})  # fmt: skip
                for f in state["findings"]:
                    if f["id"] in d["findings"]:
                        f["original_class"], f["class"] = f["class"], new
                d["original_class"], d["class"] = d["class"], new
        state["overrides"] = overrides
        state["quarantine"] = rules.quarantine(state["findings"], db.now())
        state["run_records"] = inputs["run_records"].id
        state["summary"] = {}
        for f in state["findings"]:
            state["summary"][f["class"]] = state["summary"].get(f["class"], 0) + 1
        return StageResult.succeeded({"triage": state})


STAGE = register(TriageStage())
