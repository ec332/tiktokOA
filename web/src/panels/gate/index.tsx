import type { ArtifactPanel, PanelModule, ReviewHeader } from "../types";

type Row = {
  node: string; unit: string; ac: string; case_id: string; stability?: string; outcome: string;
  origin: string | null; status: string; kills?: number; unique_kills?: number;
  misses_target?: boolean; seeded?: { flag: string; killed: boolean } | null;
};
type Survivor = { mutant: string; file: string; line: number | null; class: string; criteria: string[];
  covered: boolean; equivalent?: boolean; diff: string };
type Report = {
  admit: string; rows: Row[]; summary: Record<string, number>; cache?: string;
  criteria?: Record<string, { strength: number | null; killed: number; reached_mutants: number; equivalent: number }>;
  mutants?: { total: number; killed: number; equivalent: number; not_covered: number; strength: number | null };
  survivors?: Survivor[]; timings?: Record<string, number>;
};
type Matrix = { mutants: { name: string; file: string; line: number }[]; rows: { node: string; ac: string; status: string }[];
  cells: Record<string, Record<string, string>>; note?: string };

const good = new Set(["ADMITTED", "ADMITTED_REDUNDANT", "ADMITTED_UNMEASURED"]);
const tone = (s: string) => good.has(s) ? "var(--ok)" : s === "CANDIDATE_DEFECT" ? "var(--accent)"
  : s === "WEAK" || s === "SPEC_GAP" || s === "FLAKY" ? "var(--warn)" : "var(--bad)";
const pct = (v: number | null | undefined) => v == null ? "–" : `${Math.round(v * 100)}%`;

const GateReportPanel: ArtifactPanel = ({ artifact }) => {
  const r = artifact.body as Report;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="card row">
        <strong>admit {r.admit}</strong>
        {Object.entries(r.summary).map(([s, n]) => <span key={s} className="badge" style={{ color: tone(s) }}>{s} {n}</span>)}
        {r.mutants && <span className="muted">mutants {r.mutants.killed}/{r.mutants.total} killed · {r.mutants.equivalent} equivalent · strength {pct(r.mutants.strength)}</span>}
        {r.cache && <span className="muted">cache {r.cache}</span>}
        {r.timings && <span className="muted mono">{Object.entries(r.timings).map(([k, v]) => `${k} ${v}s`).join(" · ")}</span>}
      </div>
      {r.criteria && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>criterion</th><th>strength</th><th>killed / reached</th><th>equivalent</th></tr></thead>
          <tbody>{Object.entries(r.criteria).map(([ac, c]) => (
            <tr key={ac}><td>{ac}</td><td>{pct(c.strength)}</td><td>{c.killed} / {c.reached_mutants}</td><td>{c.equivalent}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>case</th><th>criterion</th><th>status</th><th>reference</th><th>kills</th><th>seed</th></tr></thead>
        <tbody>{r.rows.map((x) => (
          <tr key={x.node}>
            <td className="mono">{x.case_id}</td><td>{x.ac}</td>
            <td style={{ color: tone(x.status), fontWeight: 600 }}>{x.status}</td>
            <td className="muted">{x.stability ?? ""} {x.outcome}{x.origin ? ` (${x.origin})` : ""}</td>
            <td>{x.kills ?? ""}{x.unique_kills ? ` (${x.unique_kills} unique)` : ""}</td>
            <td>{x.seeded ? `${x.seeded.flag}: ${x.seeded.killed ? "killed" : "MISSES_TARGET"}` : ""}</td>
          </tr>
        ))}</tbody>
      </table></div>
      {!!r.survivors?.length && (
        <details className="card"><summary><strong>Survivors ({r.survivors.length})</strong></summary>
          {r.survivors.map((s) => (
            <details key={s.mutant}><summary className="mono">
              {s.class}{s.equivalent ? " · equivalent" : ""}{s.covered ? "" : " · not covered"} — {s.file}:{s.line} {s.criteria.join(", ")}
            </summary><pre className="mono">{s.diff}</pre></details>
          ))}
        </details>
      )}
    </div>
  );
};

/** Coloured table: tests (grouped by criterion) × mutants (design D4). */
const KillMatrixPanel: ArtifactPanel = ({ artifact }) => {
  const m = artifact.body as Matrix;
  if (!m.mutants.length) return <p className="muted">{m.note ?? "No mutants."}</p>;
  const colour = (c?: string) => c === "K" ? "var(--ok)" : c === "S" ? "var(--warn)" : "transparent";
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 11 }}>
        <thead><tr><th />{m.mutants.map((x) => <th key={x.name} title={`${x.name} (${x.file}:${x.line})`} style={{ width: 8 }} />)}</tr></thead>
        <tbody>{m.rows.map((r) => (
          <tr key={r.node}>
            <td className="mono" style={{ whiteSpace: "nowrap", paddingRight: 8 }}>{r.ac} {r.node.split("[")[1]?.replace("]", "")}</td>
            {m.mutants.map((x) => {
              const c = m.cells[r.node]?.[x.name];
              return <td key={x.name} title={`${x.name}: ${c === "K" ? "killed" : c === "S" ? "setup failure" : "survived"}`}
                style={{ width: 8, height: 12, background: colour(c), border: "1px solid var(--border)" }} />;
            })}
          </tr>
        ))}</tbody>
      </table>
      <p className="muted">green = killed for the claimed reason · amber = failed in setup · empty = survived</p>
    </div>
  );
};

const EquivalentHeader: ReviewHeader = ({ item }) => {
  const p = item.payload as { mutant: string; reason: string; diff: string };
  return (
    <div className="card" style={{ display: "grid", gap: 6 }}>
      <strong className="mono">{p.mutant}</strong>
      <span>Proposed equivalent: {p.reason}</span>
      <pre className="mono">{p.diff}</pre>
    </div>
  );
};

export const artifactPanels: PanelModule["artifactPanels"] = { gate_report: GateReportPanel, kill_matrix: KillMatrixPanel };
export const reviewHeaders: PanelModule["reviewHeaders"] = { equivalent_mutant: EquivalentHeader };
