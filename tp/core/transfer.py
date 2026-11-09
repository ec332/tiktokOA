"""Transfer and comparison across implementations of one spec (P4, docs/status/domains/001).

`start` makes a run on target B whose spec, cases and bound tests are run A's own artifacts,
same ids and so same hashes (DR-5). It starts at the gate, which does its K runs on B (D1).
`matrix` lays the runs' verdicts out case by implementation and applies the witness rule
(DR-6, D9): a case that fails on some implementations is a SPEC_MISMATCH on each that fails,
and the ones that pass are its witnesses; one that fails on all goes to a person."""

import json
from pathlib import Path

from tp.core import artifacts, db, events, runner
from tp.stages.triage.rules import under_test

SEALED = ("spec", "cases", "generate")  # their outputs are what transfers: spec, cases, tests
FAIL, PASS = ("CONSISTENT_FAIL", "TIMEOUT"), "PASS"  # the definite verdicts (D9)


def seed(run_id: int, executions: dict[str, dict]) -> None:
    """Fill the run's pending execution of each stage with an execution record ({inputs, outputs,
    stage_version}) as if it had run: its outputs become heads like any other (runner.heads)."""
    with db.connect() as c, c:
        for stage, ex in executions.items():
            row = c.execute("SELECT id FROM stage_executions WHERE run_id=? AND stage=? AND "
                            "status='pending' ORDER BY id LIMIT 1", (run_id, stage)).fetchone()  # fmt: skip
            if row is None:
                raise ValueError(f"r-{run_id:04d} has no pending {stage} execution")
            c.execute("UPDATE stage_executions SET status='succeeded', stage_version=?, inputs=?, "
                      "outputs=?, started=?, finished=? WHERE id=?",
                      (ex.get("stage_version"), json.dumps(ex["inputs"]), json.dumps(ex["outputs"]),
                       db.now(), db.now(), row[0]))  # fmt: skip


def start(run_a: int, target: str) -> int:
    """A new run on `target` (a profile path or slug) with run A's sealed artifacts."""
    a = runner.get_run(run_a)
    done = {e["stage"]: e for e in a["executions"] if e["status"] == "succeeded"}  # the latest
    if missing := [s for s in SEALED if s not in done]:
        raise ValueError(f"{a['name']} has no succeeded {', '.join(missing)}: nothing sealed to "
                         f"transfer")  # fmt: skip
    heads = runner.heads(run_a)
    options = artifacts.get(heads["run_options"]).body | {"profile": target}
    inputs = {"requirements": heads["requirements"]} if "requirements" in heads else {}
    run_b = runner.start_run(a["mode"], a["stub"], a["unattended"], inputs, options)
    seed(run_b, {s: done[s] for s in SEALED})
    events.emit(run_b, None, "run_transferred", from_run=a["name"],
                bound_tests=done["generate"]["outputs"]["bound_tests"])  # fmt: skip
    return run_b


# ---- comparison --------------------------------------------------------------------------------


def _column(run_id: int) -> dict:
    name = f"r-{run_id:04d}"
    heads = runner.heads(run_id)
    if "run_records" not in heads:
        raise ValueError(f"{name} has no run records: compare needs an executed run")
    rr = artifacts.get(heads["run_records"]).body
    target = runner.run_target(run_id)
    recs: dict[str, dict] = {}
    results = db.data_dir() / rr["folder"] / "results.jsonl"
    for line in results.read_text().splitlines() if results.exists() else []:
        r = json.loads(line)
        if r["variant"] == "reference" and r["lane"] == "main" and r.get("status") == "RAN":
            recs.setdefault(r["case_id"], r)  # the first attempt that ran shows the answer
    return {"run": name, "target": Path(target).name if target else "?",
            "bound_tests": heads.get("bound_tests"),
            "verdicts": {v["case_id"]: v for v in rr["verdicts"] if v["variant"] == "reference"},
            "records": recs}  # fmt: skip


def _answer(rec: dict | None) -> str:
    if rec is None:
        return "no record"
    ex = under_test(rec) or {}
    error = ((rec.get("failure") or {}).get("error") or "").splitlines()
    return f"{ex.get('status', '-')} {rec['outcome']}" + (f": {error[0][:160]}" if error else "")


def classify(cells: dict[str, str | None]) -> tuple[str, list[str], list[str]]:
    """{column: verdict or None} -> (class, failing columns, witnesses). The witness rule (DR-6)."""
    fail = [c for c, v in cells.items() if v in FAIL]
    ok = [c for c, v in cells.items() if v == PASS]
    if fail and ok:
        return "SPEC_MISMATCH", fail, ok
    if fail:
        return "TO_PERSON", fail, []  # fails wherever it ran: a suspect test or spec
    return ("AGREE" if len(ok) == len(cells) else "INCOMPLETE"), [], ok


def matrix(run_ids: list[int]) -> dict:
    cols = [_column(r) for r in run_ids]
    if len({c["bound_tests"] for c in cols}) != 1:
        raise ValueError("the runs ran different bound tests: "
                         + ", ".join(f"{c['run']}={c['bound_tests']}" for c in cols)
                         + " (DR-5 compares the same sealed tests)")  # fmt: skip
    rows = []
    for case in sorted({k for c in cols for k in c["verdicts"]}):
        cells = {c["run"]: (c["verdicts"].get(case) or {}).get("verdict") for c in cols}
        cls, fail, witnesses = classify(cells)
        row = {"case": case, "cells": cells, "class": cls, "failing": fail,
               "witnesses": witnesses}  # fmt: skip
        if fail:
            rec = next((c["records"].get(case) for c in cols if c["records"].get(case)), None)
            ex = under_test(rec) if rec else None
            row["request"] = f"{ex['method']} {ex['path']}" if ex else None
            row["answers"] = {c["run"]: _answer(c["records"].get(case)) for c in cols}
        rows.append(row)
    return {"bound_tests": cols[0]["bound_tests"],
            "columns": [{k: c[k] for k in ("run", "target")} for c in cols], "rows": rows}  # fmt: skip


def markdown(m: dict) -> str:
    head = [f"{c['target']} ({c['run']})" for c in m["columns"]]
    out = [f"Bound tests: `{m['bound_tests']}` (the same on every run)", "",
           "| Case | " + " | ".join(head) + " | Witness rule |",
           "|---" * (len(head) + 2) + "|"]  # fmt: skip
    for r in m["rows"]:
        cls = r["class"] + (
            f" on {', '.join(r['failing'])}" if r["class"] == "SPEC_MISMATCH" else ""
        )
        if r["witnesses"] and r["failing"]:
            cls += f"; witness {', '.join(r['witnesses'])}"
        cells = " | ".join(v or "-" for v in r["cells"].values())
        out.append(f"| `{r['case']}` | {cells} | {cls} |")
    counts: dict[str, int] = {}
    for r in m["rows"]:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    out += ["", "Rows: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    for r in (r for r in m["rows"] if r["failing"]):
        out += ["", f"**`{r['case']}`** ({r['class']}): request `{r['request']}`"]
        out += [f"- {run}: {ans}" for run, ans in r["answers"].items()]
    return "\n".join(out) + "\n"
