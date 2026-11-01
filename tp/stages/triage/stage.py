"""Stage 7 — Triage: every non-passing verdict → one classified finding with evidence; real
defects localised and sent for review. No LLM in M4. Design: docs/status/triage/001."""

import hashlib
import json
import subprocess
from pathlib import Path

from tp.core import db
from tp.core.contract import StageResult, register
from tp.stages.triage import localise, rules
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
            app_files: dict[str, str]) -> dict:  # fmt: skip
    """Everything up to the human decisions. Pure given its inputs."""
    idx = EvidenceIndex(rows, probes, logs)
    fs = rules.findings(run_records["verdicts"], idx.rows)
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
    return {"findings": fs, "defects": ds, "scores": scores,
            "evidence": {e: _excerpt(e, idx.resolve(e)) for e in sorted(cited)}}


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
    version = "1.0.0"
    consumes: dict[str, int] = {"run_records": 1, "gate_report": 1, "cases": 1, "spec": 1}  # noqa: RUF012
    produces: dict[str, int] = {"triage": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        state_path = db.data_dir() / "runs" / f"triage-exec-{ctx.review.execution_id}.json"
        if not state_path.exists():
            rr = inputs["run_records"].body
            rows, probes, logs = _records(db.data_dir() / rr["folder"], rr["root"])
            src = Path(ctx.env.code_scope()["src"]) if ctx.env else None
            read = _git_source(src) if src else (lambda rel: None)
            app = {str(p.relative_to(src)): p.read_text() for p in (src / "backend/app").rglob("*.py")} if src else {}
            state = analyse(rr, rows, probes, logs, inputs["spec"].body, read, app)
            choices = [{"id": c, "text": c} for c in rules.CLASSES]
            for d in state["defects"]:
                if d["kind"] == "real":
                    d["review"] = ctx.review.create("triage", {
                        "defect": d["id"], "class": d["class"], "ac": d["ac"], "cases": d["cases"],
                        "observed": d["observed"], "suspects": d["suspects"][:3],
                        "evidence": d["evidence"], "choices": choices})  # fmt: skip
            state_path.write_text(json.dumps(state))
            ctx.emit("triage_classified", findings=len(state["findings"]), defects=len(state["defects"]))
        state = json.loads(state_path.read_text())
        items = {i["id"]: i for i in ctx.review.items()}
        if open_ := [i for i in items.values() if i["status"] == "open"]:
            return StageResult.awaiting_review([i["id"] for i in open_])
        overrides = []
        for d in state["defects"]:
            item = items.get(d.get("review"))
            if not item:
                continue
            d["decision"] = {"by": item["decided_by"], "decision": item["decision"], "reason": item["reason"]}
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
