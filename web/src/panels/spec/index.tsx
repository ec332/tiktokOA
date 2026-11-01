import { useState } from "react";
import type { ArtifactPanel, PanelModule } from "../types";

type Source = { kind: "requirement"; lines: string; quote: string } | { kind: "code"; file: string; line: number };
type Criterion = {
  id: string; title: string; status: string; given: string; when: string; then: string;
  status_code: number | null; human_judgement: boolean; routes: string[]; sources: Source[];
  ambiguity?: string[]; suspected_defect?: boolean; review_needed?: boolean; resolution?: string;
};
type Finding = { kind: string; summary: string; evidence: { file: string; line: number }[] };
type Spec = {
  version: number; kind: string; sut: { name: string; ref: string };
  criteria: Criterion[]; findings: Finding[]; unmapped_routes: string[];
  rejected: { reasons: string[] }[];
};

const STATUSES = ["all", "confirmed", "assumed", "observed", "draft", "out_of_scope"];

function SourceSpan({ s }: { s: Source }) {
  return s.kind === "requirement" ? (
    <span title={s.quote}><code>{s.lines}</code> <mark>“{s.quote}”</mark></span>
  ) : (
    <code>{s.file}:{s.line}</code>
  );
}

/** spec: criteria filterable by status with source spans highlighted, findings, and the
 * "behaviour with no requirement" list (plan Stage 1 UI panel). */
const SpecPanel: ArtifactPanel = ({ artifact }) => {
  const spec = artifact.body as Spec;
  const [status, setStatus] = useState("all");
  const shown = spec.criteria.filter((c) => status === "all" || c.status === status);
  const count = (s: string) => spec.criteria.filter((c) => s === "all" || c.status === s).length;

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="row">
        <strong>Spec v{spec.version}</strong>
        <span className="muted">{spec.kind} · {spec.sut.name} @ {spec.sut.ref.slice(0, 7)}</span>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          {STATUSES.map((s) => <option key={s} value={s}>{s} ({count(s)})</option>)}
        </select>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>id</th><th>status</th><th>criterion</th><th>expected</th><th>sources</th></tr></thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.id}>
                <td className="mono">{c.id}</td>
                <td>
                  {c.status}
                  {c.suspected_defect && <div className="badge unsigned">suspected defect</div>}
                  {c.review_needed && <div className="muted">review needed</div>}
                </td>
                <td>
                  <strong>{c.title}</strong>
                  <div className="muted">Given {c.given}, when {c.when}, then {c.then}</div>
                  {c.resolution && <div>Decided: {c.resolution}</div>}
                  {c.routes.map((r) => <div key={r}><code>{r}</code></div>)}
                  {!!c.ambiguity?.length && <div className="muted">ambiguity: {c.ambiguity.join(", ")}</div>}
                </td>
                <td className="mono">{c.human_judgement ? "human judgement" : c.status_code ?? "—"}</td>
                <td style={{ wordBreak: "break-word" }}>
                  {c.sources.map((s, i) => <div key={i}><SourceSpan s={s} /></div>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Findings ({spec.findings.length})</h2>
      {spec.findings.map((f, i) => (
        <div key={i} className="card">
          <strong>{f.kind.replaceAll("_", " ")}</strong>: {f.summary}
          <div className="mono muted">{f.evidence.map((e) => `${e.file}:${e.line}`).join(", ")}</div>
        </div>
      ))}

      <h2>Behaviour with no requirement ({spec.unmapped_routes.length})</h2>
      {spec.unmapped_routes.length === 0 ? <p className="muted">none</p> : (
        <ul className="mono">{spec.unmapped_routes.map((r) => <li key={r}>{r}</li>)}</ul>
      )}
      {spec.rejected.length > 0 && (
        <p className="muted">{spec.rejected.length} model output(s) rejected by SchemaCheck (see raw JSON).</p>
      )}
    </div>
  );
};

const SpecMdPanel: ArtifactPanel = ({ artifact }) => (
  <pre className="card mono" style={{ whiteSpace: "pre-wrap" }}>{(artifact.body as { markdown: string }).markdown}</pre>
);

export const artifactPanels: PanelModule["artifactPanels"] = { spec: SpecPanel, spec_md: SpecMdPanel };
