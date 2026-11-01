import { useState } from "react";
import type { ArtifactPanel, PanelModule, ReviewPanel } from "../types";

type Suspect = { file: string; line: number | null; upstream_line: number | null; text: string | null; basis: string };
type Defect = { id: string; ac: string; kind: string; class: string; cases: string[]; suspects: Suspect[];
  original_class?: string; decision?: { by: string; decision: string; reason: string } };
type Finding = { id: string; class: string; dist: string; rule: string; original_class?: string };
type Triage = { summary: Record<string, number>; findings: Finding[]; defects: Defect[];
  overrides: { defect: string; from: string; to: string; reason: string; by: string }[] };

const Suspects = ({ s }: { s: Suspect[] }) => (
  <ol style={{ margin: 0 }}>{s.map((x, i) => <li key={i}><code>{x.file}:{x.upstream_line ?? "–"}</code> <code>{x.text}</code> <span className="muted">{x.basis}</span></li>)}</ol>
);

/** Findings table (design D13; timeline and quarantine list parked). */
const TriagePanel: ArtifactPanel = ({ artifact }) => {
  const t = artifact.body as Triage;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="card row">{Object.entries(t.summary).map(([c, n]) => <span key={c} className="badge">{c} {n}</span>)}</div>
      {t.defects.map((d) => (
        <div key={d.id} className="card"><strong>{d.id}</strong> <span className="badge">{d.class}</span>
          {d.original_class && <span className="muted"> (was {d.original_class})</span>}
          {d.decision && <span className="muted"> · {d.decision.decision} by {d.decision.by}{d.decision.reason ? `: ${d.decision.reason}` : ""}</span>}
          <div className="muted">{d.cases.join(", ")}</div><Suspects s={d.suspects} /></div>
      ))}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>finding</th><th>class</th><th>dist</th><th>rule</th></tr></thead>
        <tbody>{t.findings.map((f) => <tr key={f.id}><td className="mono">{f.id}</td><td>{f.class}{f.original_class ? ` (was ${f.original_class})` : ""}</td><td>{f.dist}</td><td>{f.rule}</td></tr>)}</tbody>
      </table></div>
    </div>
  );
};

/** Accept the rules' class, or override it with a new class and a reason (J4). */
const TriageReview: ReviewPanel = ({ item, submit, busy }) => {
  const p = item.payload as { defect: string; class: string; cases: string[]; suspects: Suspect[]; choices: { id: string }[] };
  const [choice, setChoice] = useState(p.class);
  const [reason, setReason] = useState("");
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div><strong>{p.defect}</strong> classified <span className="badge">{p.class}</span> · {p.cases.join(", ")}</div>
      <Suspects s={p.suspects} />
      <div className="row">
        <button disabled={busy} onClick={() => submit({ decision: "accept" })}>Accept {p.class}</button>
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>{p.choices.map((c) => <option key={c.id}>{c.id}</option>)}</select>
        <input placeholder="reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button disabled={busy || !reason || choice === p.class}
          onClick={() => submit({ decision: "override", detail: { choice }, reason })}>Override</button>
      </div>
    </div>
  );
};

export const artifactPanels: PanelModule["artifactPanels"] = { triage: TriagePanel };
export const reviewPanels: PanelModule["reviewPanels"] = { triage: TriageReview };
