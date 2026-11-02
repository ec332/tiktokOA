import type { ReviewItem } from "../api/client";
import { ReqLink, ResultLink, TestLink, unitOf } from "./nav";

/** What a finished step produced, shown when the step is expanded on the run page. */

export type Criterion = {
  id: string; title: string; given?: string; when?: string; then?: string; status_code?: number | null;
  status: string; resolution?: string;
};
export type Unit = { id: string; purpose: string; defect_hypothesis: string; ac: string; technique: string };
export type CasesBody = { cases: Unit[]; not_tested: { criterion: string; reason: string }[]; signoff: { signed: boolean; approved: number; lines: number } };
export type GateRow = { node: string; unit: string | null; case_id: string; ac: string | null; status: string; stability: string; kills: number };
export type Verdict = { node: string; case_id: string; variant: string; verdict: string; dist?: string };

const STATUS_NOTE: Record<string, string> = {
  confirmed: "confirmed", assumed: "assumed (not confirmed)", out_of_scope: "out of scope", observed: "read from the code", draft: "draft",
};

export function Requirements({ criteria }: { criteria: Criterion[] }) {
  return (
    <div className="card" style={{ display: "grid", gap: 8 }}>
      {criteria.map((c) => (
        <div key={c.id} id={`req-${c.id}`} className="anchor">
          <div className="row"><span className="mono">{c.id}</span><strong className="grow">{c.title}</strong>
            <span className={`pill ${c.status === "confirmed" ? "ok" : c.status === "out_of_scope" ? "" : "warn"}`}>{STATUS_NOTE[c.status] ?? c.status}</span></div>
          <div className="muted" style={{ fontSize: 13 }}>
            {c.given && <>Given {c.given}. </>}{c.when && <>When {c.when}. </>}{c.then && <>Then {c.then}</>}
            {c.status_code ? <span className="mono"> → {c.status_code}</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

const DECISION_LABEL: Record<string, string> = { confirm: "Yes, intended", reject: "No, that's wrong", out_of_scope: "Not part of this feature", defer: "Platform assumed" };

export function ClarifyHistory({ items }: { items: ReviewItem[] }) {
  if (!items.length) return <div className="card muted">No questions were needed.</div>;
  return (
    <div className="card" style={{ display: "grid", gap: 8 }}>
      {items.map((i) => {
        const p = i.payload as Record<string, unknown>;
        const crit = (p.criterion as string | undefined) ?? undefined;
        let q: string, a: string;
        if (i.kind === "question") {
          const choices = (p.choices as { id: string; text: string }[]) ?? [];
          const d = (i.detail ?? {}) as { choice?: string; text?: string };
          q = p.text as string;
          a = i.decision === "defer" ? `Platform assumed: ${choices.find((c) => c.id === p.recommended)?.text ?? ""}`
            : d.choice === "other" ? (d.text ?? "") : choices.find((c) => c.id === d.choice)?.text ?? (i.decision ?? "");
        } else {
          q = `The code does this; is it intended? ${p.title as string}`;
          a = DECISION_LABEL[i.decision ?? ""] ?? (i.decision ?? "");
        }
        return (
          <div key={i.id} style={{ fontSize: 13 }}>
            <div>{crit && <><ReqLink id={crit} /> · </>}{q}</div>
            <div><span className="muted">Answer ({i.decided_by}):</span> {a}</div>
          </div>
        );
      })}
    </div>
  );
}

export function ApprovedTests({ body }: { body: CasesBody }) {
  const groups = new Map<string, Unit[]>();
  for (const u of body.cases) groups.set(u.ac, [...(groups.get(u.ac) ?? []), u]);
  const excluded = body.not_tested.filter((n) => n.reason === "excluded at sign-off");
  return (
    <div className="card" style={{ display: "grid", gap: 6 }}>
      <div className="muted" style={{ fontSize: 13 }}>
        {body.signoff.signed ? `Signed: ${body.signoff.approved} tests approved` : "Not signed"}
        {excluded.length > 0 && ` · ${excluded.length} excluded at sign-off`}
      </div>
      {[...groups.entries()].map(([ac, us]) => (
        <div key={ac} className="group">
          <h3><ReqLink id={ac} /></h3>
          {us.map((u) => (
            <div key={u.id} id={`test-${u.id}`} className="anchor" style={{ fontSize: 13, padding: "2px 0" }}>
              <span className="mono muted">{u.id}</span> <span className="pill">{u.technique.replace(/_/g, " ")}</span>{" "}
              <strong style={{ fontWeight: 500 }}>{u.purpose}</strong>
              <span className="muted"> — catches {u.defect_hypothesis}</span>{" "}
              <ResultLink caseId={u.id}>results</ResultLink>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

const GATE_NOTE: Record<string, string> = {
  ADMITTED: "admitted", ADMITTED_REDUNDANT: "admitted (overlaps another test)", ADMITTED_UNMEASURED: "admitted (strength not measurable)",
  WEAK: "weak: caught no code change", FLAKY: "flaky", CANDIDATE_DEFECT: "fails on your branch: likely defect",
  SPEC_GAP: "fails, requirement assumed", REJECTED_ORACLE: "test is wrong", ERROR: "environment error",
};

export function BuildResults({ rows, verdicts, titles }: { rows: GateRow[]; verdicts: Verdict[]; titles: Record<string, string> }) {
  const onBranch = new Map(verdicts.filter((v) => v.variant === "reference").map((v) => [v.node, v]));
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Test</th><th>Gate</th><th>Stability</th><th>On your branch</th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const v = onBranch.get(r.node);
            return (
              <tr key={r.node} id={`run-${r.case_id}`} className="anchor">
                <td style={{ fontSize: 13 }}>
                  <TestLink caseId={r.case_id}>{titles[unitOf(r.case_id)] ?? r.case_id}</TestLink>
                  <div className="muted mono" style={{ fontSize: 11 }}>{r.case_id}{r.ac && <> · <ReqLink id={r.ac} /></>}</div>
                </td>
                <td style={{ fontSize: 13 }}>{GATE_NOTE[r.status] ?? r.status}{r.kills ? <span className="muted"> · {r.kills} kills</span> : null}</td>
                <td className="muted" style={{ fontSize: 12 }}>{r.stability}</td>
                <td style={{ fontSize: 13 }}>{v ? <><span className={`pill ${v.verdict === "PASS" ? "ok" : "bad"}`}>{v.verdict.replace(/_/g, " ").toLowerCase()}</span>{v.dist && <span className="muted" style={{ fontSize: 12 }}> {v.dist.split("/")[0]} of {v.dist.split("/")[1]} runs failed</span>}</> : <span className="muted">not run</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
