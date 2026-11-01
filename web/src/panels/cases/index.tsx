import type { ArtifactPanel, PanelModule, ReviewHeader } from "../types";

type Point = { id: string; value: number; expected: { value: string } };
type Row = { id: string; when: Record<string, string>; expected: { value: string } };
type Unit = {
  id: string; ac: string; flow: string; priority: string; technique: string; purpose: string;
  defect_hypothesis: string; preconditions: string[]; review_needed: boolean; quality: string;
  expected?: { value: unknown; oracle: string }; points?: Point[]; rows?: Row[];
  boundary?: { field: string; limit: number; inclusive: boolean }; notes?: string[];
};
type NotTested = { criterion: string; reason: string };
type Cases = {
  flows: string[]; cases: Unit[]; attention: string[]; not_tested: NotTested[];
  deferred: (Unit & { deferred: string })[];
  signoff: { signed: boolean; hash: string | null; lines: number; approved: number; carried_forward: string[] };
};

const show = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

function Attention({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="card" style={{ borderColor: "var(--warn)" }}>
      <strong>Attention ({items.length})</strong>
      <ul>{items.map((a) => <li key={a}>{a}</li>)}</ul>
    </div>
  );
}

function Worked({ id, field, points }: { id: string; field: string; points: Point[] }) {
  return (
    <div className="row mono">
      <span>{id}</span> <code>{field}</code>
      {points.map((p) => <span key={p.id} className="badge">{p.value} → {p.expected.value}</span>)}
    </div>
  );
}

/** Context above the sign-off lines: attention first, worked examples, not tested (plan). */
const SignoffHeader: ReviewHeader = ({ item }) => {
  const p = item.payload as { attention?: string[]; worked_examples?: { id: string; field: string; points: Point[] }[];
    not_tested?: NotTested[]; round?: number };
  if (!p.attention && !p.worked_examples) return null; // e.g. the cases stub's plain sign-off
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <Attention items={p.attention ?? []} />
      {!!p.worked_examples?.length && (
        <div className="card"><strong>Worked examples</strong>
          {p.worked_examples.map((w) => <Worked key={w.id} {...w} />)}
        </div>
      )}
      {!!p.not_tested?.length && (
        <details><summary className="muted">Not tested ({p.not_tested.length})</summary>
          <ul>{p.not_tested.map((n) => <li key={n.criterion}>{n.criterion}: {n.reason}</li>)}</ul>
        </details>
      )}
      {(p.round ?? 1) > 1 && <p className="muted">Round {p.round}: only the lines rejected last time.</p>}
    </div>
  );
};

/** cases: sign-off state, attention, cases by flow with their oracle, deferred and not tested. */
const CasesPanel: ArtifactPanel = ({ artifact }) => {
  const c = artifact.body as Cases;
  const s = c.signoff;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="row">
        <strong>{s.signed ? `Signed ${s.hash?.slice(0, 12)}` : "Unsigned"}</strong>
        <span className="muted">{s.approved}/{s.lines} lines approved · {s.carried_forward.length} carried forward</span>
      </div>
      <Attention items={c.attention} />
      {c.flows.map((flow) => (
        <div key={flow}>
          <h2>{flow}</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>id</th><th>prio</th><th>technique</th><th>case</th><th>expected</th></tr></thead>
              <tbody>
                {c.cases.filter((u) => u.flow === flow).map((u) => (
                  <tr key={u.id}>
                    <td className="mono">{u.id}<div className="muted">{u.ac}</div></td>
                    <td>{u.priority}</td>
                    <td>{u.technique}{u.quality === "revised" && <div className="muted">revised</div>}</td>
                    <td>
                      {u.purpose}
                      <div className="muted">catches: {u.defect_hypothesis}</div>
                      {u.notes?.map((n) => <div key={n} className="muted">note: {n}</div>)}
                    </td>
                    <td className="mono">
                      {u.points && <Worked id="" field={u.boundary!.field} points={u.points} />}
                      {u.rows?.map((r) => <div key={r.id}>{Object.values(r.when).join(" / ")} → {r.expected.value}</div>)}
                      {u.expected && <>{show(u.expected.value)} <span className="muted">[{u.expected.oracle}]</span></>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <details><summary className="muted">Not tested ({c.not_tested.length + c.deferred.length})</summary>
        <ul>
          {c.not_tested.map((n) => <li key={n.criterion}>{n.criterion}: {n.reason}</li>)}
          {c.deferred.map((d, i) => <li key={i}>{d.ac} ({d.technique}): deferred, {d.deferred}</li>)}
        </ul>
      </details>
    </div>
  );
};

const SignoffMd: ArtifactPanel = ({ artifact }) => (
  <pre className="card mono" style={{ whiteSpace: "pre-wrap" }}>{(artifact.body as { markdown: string }).markdown}</pre>
);

export const artifactPanels: PanelModule["artifactPanels"] = { cases: CasesPanel, signoff_md: SignoffMd };
export const reviewHeaders: PanelModule["reviewHeaders"] = { signoff: SignoffHeader };
