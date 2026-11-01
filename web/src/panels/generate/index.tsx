import type { ArtifactPanel, PanelModule, ReviewHeader } from "../types";

type Test = {
  unit: string; ac?: string; file?: string; status: "BOUND" | "UNBOUND" | "ERROR";
  lock_hash?: string; attempts?: number; source?: string; reason?: string; review?: string;
  dry_run?: Record<string, string | null>;
};
type BoundTests = {
  guards: string; variant: string; signature: string; summary: Record<string, number>;
  runner: { image: string; digest: string; platform_rt: string }; tests: Test[];
};
type Attempt = {
  unit: string; attempt: number; kind: string; outcome: string; classification: string | null;
  static?: string[]; origins?: Record<string, string | null>; diff?: string; reason?: string;
};

const colour: Record<string, string> = { BOUND: "var(--ok)", UNBOUND: "var(--bad)", ERROR: "var(--warn)" };
const BINDING = "    # >>> BINDING", LOCKED = "    # >>> LOCKED", END = /^\s+# <<< (BINDING|LOCKED)/;

/** The file with the binding region and the locked block visually separated (design D11). */
function Source({ text }: { text: string }) {
  let region: "binding" | "locked" | null = null;
  const lines = text.split("\n").map((ln, i) => {
    if (ln.startsWith(BINDING)) region = "binding";
    if (ln.startsWith(LOCKED)) region = "locked";
    const r = region;
    if (END.test(ln)) region = null;
    const bg = r === "binding" ? "rgba(80,140,255,.12)" : r === "locked" ? "rgba(160,160,160,.18)" : undefined;
    return <div key={i} style={{ background: bg, whiteSpace: "pre" }}>{ln || " "}</div>;
  });
  return <div className="mono" style={{ overflowX: "auto", fontSize: 12 }}>{lines}</div>;
}

const BoundTestsPanel: ArtifactPanel = ({ artifact }) => {
  const b = artifact.body as BoundTests;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="card row">
        <strong>guards {b.guards}</strong>
        <span className="muted">variant {b.variant}</span>
        <span className="muted mono trunc" title={b.runner.digest}>runner {b.runner.image} · platform_rt {b.runner.platform_rt}</span>
        {Object.entries(b.summary).map(([k, n]) => <span key={k} className="badge" style={{ color: colour[k] }}>{k} {n}</span>)}
      </div>
      {b.tests.map((t) => (
        <details key={t.unit} className="card">
          <summary className="row">
            <span style={{ color: colour[t.status], fontWeight: 600 }}>{t.status}</span>
            <strong>{t.unit}</strong>
            <span className="muted">{t.ac} · {t.attempts ?? 0} attempt(s){t.review ? ` · review: ${t.review}` : ""}</span>
            {Object.entries(t.dry_run ?? {}).filter(([, o]) => o).map(([c, o]) => <span key={c} className="badge">{c}: {o}</span>)}
          </summary>
          {t.reason && <p className="muted">{t.reason}</p>}
          {t.lock_hash && <div className="mono trunc">lock sha256 {t.lock_hash}</div>}
          {t.source && <Source text={t.source} />}
        </details>
      ))}
    </div>
  );
};

const LedgerPanel: ArtifactPanel = ({ artifact }) => {
  const attempts = (artifact.body as { attempts: Attempt[] }).attempts;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>unit</th><th>#</th><th>kind</th><th>outcome</th><th>class</th><th>detail</th></tr></thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={`${a.unit}-${a.attempt}-${a.outcome}`}>
              <td>{a.unit}</td><td>{a.attempt}</td><td>{a.kind}</td><td>{a.outcome}</td>
              <td>{a.classification ?? ""}</td>
              <td className="mono" style={{ wordBreak: "break-all" }}>
                {a.reason}
                {a.static?.map((s) => <div key={s}>{s}</div>)}
                {Object.entries(a.origins ?? {}).filter(([, o]) => o).map(([c, o]) => <div key={c}>{c}: {o}</div>)}
                {a.diff && <details><summary className="muted">diff</summary><pre>{a.diff}</pre></details>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

/** unbound: why the case could not be bound, and the last file tried (rewrite case / accept gap). */
const UnboundHeader: ReviewHeader = ({ item }) => {
  const p = item.payload as { unit: string; reason: string; source?: string;
    failures?: { case_id: string; origin: string; error: string }[] };
  return (
    <div className="card" style={{ display: "grid", gap: 6 }}>
      <strong>{p.unit}: {p.reason}</strong>
      {p.failures?.map((f) => <div key={f.case_id} className="mono">{f.case_id} {f.origin}: {f.error.slice(0, 300)}</div>)}
      {p.source && <details><summary className="muted">last file tried</summary><Source text={p.source} /></details>}
    </div>
  );
};

export const reviewHeaders: PanelModule["reviewHeaders"] = { unbound: UnboundHeader };
export const artifactPanels: PanelModule["artifactPanels"] = { bound_tests: BoundTestsPanel, ledger: LedgerPanel };
