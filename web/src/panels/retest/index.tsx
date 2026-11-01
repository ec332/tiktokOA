import type { ArtifactPanel, PanelModule } from "../types";

type Row = { node: string; case_id: string; before: string; before_dist?: string; after?: string; after_dist?: string };
type Retest = { defect: string; ac: string; fix_kind: string; verdict: string; closed: boolean;
  before: { variant: string }; after: { variant: string; folder?: string; root?: string };
  tests_changed: string[]; env_diff?: string[]; fail_to_pass: Row[];
  pass_to_pass: { checked: number; regressions: string[] }; pre_existing: Row[] };

const tone = (v: string) => v === "FIXED_CONFIRMED" ? "var(--ok)" : v === "INCONCLUSIVE" ? "var(--warn)" : "var(--bad)";

/** Before/after per defect (design D13; side-by-side evidence parked). */
const RetestPanel: ArtifactPanel = ({ artifact }) => {
  const body = artifact.body as { retests: Retest[]; note?: string };
  if (!body.retests.length) return <div className="card muted">{body.note ?? "no retests"}</div>;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      {body.retests.map((r) => (
        <div key={r.defect} className="card" style={{ display: "grid", gap: 8 }}>
          <div className="row">
            <strong className="mono">{r.defect}</strong>
            <span className="badge" style={{ color: tone(r.verdict) }}>{r.verdict}{r.closed ? " · closed" : ""}</span>
            <span className="badge">{r.fix_kind}</span>
            <span className="muted mono">{r.before.variant} → {r.after.variant}</span>
          </div>
          <div className="muted">
            tests identical: {r.tests_changed.length ? `no (${r.tests_changed.join(", ")})` : "yes"} ·
            env diff: {r.env_diff?.length ? r.env_diff.join(", ") : "code/flags only"} ·
            pass→pass: {r.pass_to_pass.checked} checked, {r.pass_to_pass.regressions.length} regressed
          </div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>case</th><th>before</th><th>after</th></tr></thead>
            <tbody>
              {r.fail_to_pass.map((t) => (
                <tr key={t.node}><td className="mono">{t.case_id}</td>
                  <td>{t.before} {t.before_dist}</td><td>{t.after ?? "not run"} {t.after_dist}</td></tr>
              ))}
              {r.pre_existing.map((t) => (
                <tr key={t.node} className="muted"><td className="mono">{t.case_id} (pre-existing)</td>
                  <td>{t.before}</td><td>{t.after ?? "not run"}</td></tr>
              ))}
            </tbody>
          </table></div>
          {r.after.folder && <span className="muted mono">after run: {r.after.folder} · root {r.after.root?.slice(0, 12)}</span>}
        </div>
      ))}
    </div>
  );
};

export const artifactPanels: PanelModule["artifactPanels"] = { retest: RetestPanel };
