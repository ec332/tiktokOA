"""Stage 8 — Report: report.json, a single static report.html, summary.md and the CLI
worklist, from artifacts only. No LLM. Design: docs/status/report/001."""

import json

from tp.core import artifacts, db
from tp.core.contract import StageResult, register
from tp.stages.report import build, render


def lineage_decisions(roots: list[str]) -> list[dict]:
    """Resolved review decisions of every execution that produced an artifact in the lineage."""
    seen, todo = set(), list(roots)
    while todo:
        aid = todo.pop()
        if aid in seen:
            continue
        seen.add(aid)
        try:
            todo += artifacts.get(aid).parents
        except KeyError:
            pass
    with db.connect() as c:
        execs = [r["id"] for r in c.execute("SELECT id, outputs FROM stage_executions").fetchall()
                 if set(json.loads(r["outputs"]).values()) & seen]  # fmt: skip
        if not execs:
            return []
        marks = ",".join("?" * len(execs))
        rows = c.execute(f"SELECT * FROM review_items WHERE status='resolved' AND stage_execution IN ({marks}) "
                         "ORDER BY id", execs).fetchall()  # fmt: skip
    return [{"review_item": r["id"], "kind": r["kind"], "decision": r["decision"], "by": r["decided_by"],
             "reason": r["reason"] or ""} for r in rows]  # fmt: skip


class ReportStage:
    name = "report"
    version = "1.0.0"
    consumes: dict[str, int] = {"spec": 1, "cases": 1, "gate_report": 1, "run_records": 1, "triage": 1}  # noqa: RUF012
    produces: dict[str, int] = {"report": 1}  # noqa: RUF012

    def run(self, ctx, inputs):
        b = {t: a.body for t, a in inputs.items()}
        labels = sorted({lbl for a in inputs.values() for lbl in a.labels})
        decisions = lineage_decisions([a.id for a in inputs.values()])
        report = build.build(b["spec"], b["cases"], b["gate_report"], b["run_records"], b["triage"],
                             decisions, labels)  # fmt: skip
        if bare := list(build.walk({k: v for k, v in report.items() if k not in ("evidence", "defects", "traceability")})):
            return StageResult.failed(f"numbers without a composition: {bare[:5]}")
        page, md, text = render.html(report), render.summary_md(report), render.cli(report)
        out = db.data_dir() / "runs" / f"report-exec-{ctx.review.execution_id}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=1))
        (out / "report.html").write_text(page)
        (out / "summary.md").write_text(md)
        ctx.emit("report_written", path=str(out / "report.html"))
        return StageResult.succeeded({"report": {"report": report, "html": page, "summary_md": md,
                                                 "cli": text, "path": str(out / "report.html")}})  # fmt: skip


STAGE = register(ReportStage())
