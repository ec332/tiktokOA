import type { ArtifactPanel, PanelModule } from "../types";

type Verdict = { node: string; case_id: string; ac: string; variant: string; verdict: string; dist: string;
  gate_status: string; max_undetected_rate?: number; escalation?: { variant: string; reference: string; escalated: boolean } };
type Records = { folder: string; root: string; k: number; variants: Record<string, { spec_hash?: string; up_error?: string }>;
  summary: Record<string, Record<string, number>>; verdicts: Verdict[] };

const tone = (v: string) => v === "PASS" ? "var(--ok)" : v === "CONSISTENT_FAIL" ? "var(--accent)"
  : v === "INTERMITTENT" ? "var(--warn)" : "var(--bad)";

/** Run grid: tests × variants with distribution chips (design D15; click-through parked). */
const RunRecordsPanel: ArtifactPanel = ({ artifact }) => {
  const r = artifact.body as Records;
  const variants = Object.keys(r.variants);
  const cell = new Map(r.verdicts.map((v) => [`${v.case_id}|${v.variant}`, v]));
  const cases = [...new Set(r.verdicts.map((v) => v.case_id))].sort();
  const power = r.verdicts.find((v) => v.max_undetected_rate !== undefined)?.max_undetected_rate;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="card row">
        <strong>K={r.k}</strong>
        {power !== undefined && <span className="badge" title="detection power at 95%">
          PASS rules out a failure rate above {Math.round(power * 100)}%</span>}
        <span className="muted mono">{r.folder} · root {r.root.slice(0, 12)}</span>
        {variants.map((v) => <span key={v} className="badge">{v}: {Object.entries(r.summary[v] ?? {}).map(([s, n]) => `${s} ${n}`).join(", ")}</span>)}
      </div>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>case</th>{variants.map((v) => <th key={v}>{v}</th>)}</tr></thead>
        <tbody>{cases.map((c) => (
          <tr key={c}><td className="mono">{c}</td>{variants.map((v) => {
            const x = cell.get(`${c}|${v}`);
            return <td key={v} title={x && (x.verdict + (x.max_undetected_rate !== undefined
              ? ` (a failure rate up to ${Math.round(x.max_undetected_rate * 100)}% could pass)` : ""))}>{x && <span className="badge" style={{ color: tone(x.verdict) }}>
              {x.dist}{x.escalation ? ` · esc ${x.escalation.variant} / ref ${x.escalation.reference}` : ""}</span>}</td>;
          })}</tr>
        ))}</tbody>
      </table></div>
    </div>
  );
};

export const artifactPanels: PanelModule["artifactPanels"] = { run_records: RunRecordsPanel };
