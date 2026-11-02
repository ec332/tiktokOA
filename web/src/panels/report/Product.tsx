import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReviewItem } from "../../api/client";

/** The report on the run page (M8 report D12-D15). Every line comes from a record; the fix is
 *  labelled as a suggestion. Actions: confirm a defect, try the suggested fix, retest my fix. */

type Suggestion = { explanation: string; patch: string; applies: boolean; problems: string[] };
type Defect = {
  id: string; kind: string; class: string; ac: string; criterion: string;
  observed: { method: string; path: string; query?: Record<string, unknown> | string | null; status: number; json?: unknown } | null;
  requirements?: { id: string; ac: string; criterion: string }[];
  failed_assertions: { message?: string; expected?: unknown; actual?: unknown }[];
  location: { file: string; line: number | null; text: string | null; basis: string } | null;
  evidence: string[]; tests: Test[]; suggestion?: Suggestion;
};
type Test = { case: string; title: string; summary: string; ac: string; criterion: string; status?: string; reason?: string; evidence?: string[]; count?: number };
type Improve = { kind: string; title: string; detail: string; ac: string | null };
export type Product = {
  summary: Record<string, number | null>;
  defects: Defect[]; causes?: Defect[]; problematic_tests: Test[]; improve: Improve[];
  rules: { clarify: string[]; cases: string[]; templates: string[] | null; gate: Record<string, unknown> | null; routes: string[] };
};
type Retest = { defect: string; verdict?: string; after: { variant: string }; tests_changed?: boolean; reason?: string };

const STATUS_TONE: Record<string, string> = {
  PRODUCT_DEFECT: "bad", INTERMITTENT_PRODUCT: "bad", CANDIDATE_DEFECT: "bad",
  SPEC_GAP: "warn", FLAKY: "warn", FLAKY_TEST: "warn", WEAK: "warn", TEST_DEFECT: "warn",
  REJECTED_ORACLE: "warn", ERROR: "warn", ENV: "warn", DATA: "warn", INSUFFICIENT_EVIDENCE: "warn",
};
function query(q: unknown): string {
  if (!q) return "";
  if (typeof q === "string") return `?${q}`;
  const parts = Object.entries(q as Record<string, unknown>).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

const IMPROVE_LABEL: Record<string, string> = { uncovered: "No test", survivor: "Unnoticed change", assumed: "Assumed answer", spec_gap: "Open requirement" };

async function post(path: string, body: unknown) {
  const r = await fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? r.statusText);
  return b;
}

function Evidence({ ids, evidence }: { ids: string[]; evidence: Record<string, unknown> }) {
  const shown = ids.filter((i) => evidence[i]);
  if (!shown.length) return null;
  return (
    <details>
      <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>Evidence ({shown.length})</summary>
      {shown.map((i) => (
        <div key={i}><span className="mono muted">{i}</span>
          <pre className="patch">{JSON.stringify(evidence[i], null, 1)}</pre>
        </div>
      ))}
    </details>
  );
}

function DefectCard({ d, runId, evidence, decided, retests }: {
  d: Defect; runId: number; evidence: Record<string, unknown>; decided?: ReviewItem; retests: Retest[];
}) {
  const qc = useQueryClient();
  const refresh = () => { qc.invalidateQueries({ queryKey: ["defects", runId] }); qc.invalidateQueries({ queryKey: ["run", runId] }); };
  const decide = useMutation({ mutationFn: (decision: string) => post(`/runs/${runId}/defects/${d.id}`, { decision }), onSuccess: refresh });
  const retest = useMutation({ mutationFn: (mode: string) => post(`/runs/${runId}/retest`, { defect: d.id, mode }), onSuccess: refresh });
  const o = d.observed;
  const s = d.suggestion;
  const mine = retests.filter((r) => r.defect === d.id);
  return (
    <div className="card defect">
      <div className="row">
        <strong className="grow">{d.requirements && d.requirements.length > 1 ? `${d.location?.file}:${d.location?.line ?? "?"}` : d.criterion || d.ac}</strong>
        <span className="pill bad">{d.class.replace("_", " ").toLowerCase()}</span>
        {decided ? <span className={`pill ${decided.decision === "confirm" ? "bad" : "ok"}`}>{decided.decision === "confirm" ? "confirmed by you" : "not a bug (you)"}</span>
          : <span className="pill">unconfirmed</span>}
      </div>
      {d.requirements && d.requirements.length > 1 && (
        <div style={{ fontSize: 13 }}>One cause, {d.requirements.length} requirements: {d.requirements.map((r) => `${r.ac} ${r.criterion}`).join("; ")}</div>
      )}
      {o && <div className="mono" style={{ fontSize: 12.5 }}>{o.method} {o.path}{query(o.query)} → {o.status}</div>}
      {d.failed_assertions.slice(0, 2).map((a, i) => <div key={i} className="muted" style={{ fontSize: 13 }}>{a.message ?? `expected ${JSON.stringify(a.expected)}, got ${JSON.stringify(a.actual)}`}</div>)}
      {d.location && <div style={{ fontSize: 13 }}>Where: <span className="mono">{d.location.file}:{d.location.line ?? "?"}</span>{d.location.text && <span className="mono muted"> {d.location.text}</span>}</div>}
      <div style={{ fontSize: 13 }}>Found by: {[...new Set(d.tests.map((t) => t.title))].join("; ")}</div>
      {s && (
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ fontSize: 13 }}><strong>Suggested change</strong> <span className="muted">(AI suggestion{s.applies ? ", applies cleanly" : ""})</span>: {s.explanation}</div>
          {s.patch && <details><summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>Show the patch</summary><pre className="patch">{s.patch}</pre></details>}
          {s.problems.length > 0 && <div className="warn" style={{ fontSize: 13 }}>Not usable as is: {s.problems.join("; ")}</div>}
        </div>
      )}
      <Evidence ids={d.evidence} evidence={evidence} />
      {mine.map((r, i) => (
        <div key={i} style={{ fontSize: 13 }}>
          Retest on <span className="mono">{r.after.variant.startsWith("fixed:sha256") ? "the suggested fix" : r.after.variant}</span>:{" "}
          <span className={`pill ${r.verdict === "FIXED_CONFIRMED" ? "ok" : "warn"}`}>{r.verdict ?? "running"}</span>
          {r.reason && <span className="muted"> {r.reason}</span>}
        </div>
      ))}
      <div className="row">
        {!decided && <>
          <button onClick={() => decide.mutate("confirm")} disabled={decide.isPending}>Confirm defect</button>
          <button onClick={() => decide.mutate("reject")} disabled={decide.isPending}>Not a bug</button>
        </>}
        {s?.applies && <button className="primary" onClick={() => retest.mutate("suggested")} disabled={retest.isPending}>Try suggested fix</button>}
        <button onClick={() => retest.mutate("mine")} disabled={retest.isPending} title="After you commit a fix to the branch">Retest my fix</button>
        {(decide.error || retest.error) && <span className="error">{((decide.error || retest.error) as Error).message}</span>}
        {retest.isSuccess && <span className="muted">Retest started; this takes a few minutes.</span>}
      </div>
    </div>
  );
}

export function ProductReport({ product, evidence, runId, retests }: {
  product: Product; evidence: Record<string, unknown>; runId: number; retests: Retest[];
}) {
  const decisions = useQuery({
    queryKey: ["defects", runId],
    queryFn: async () => (await fetch(`/api/runs/${runId}/defects`)).json() as Promise<ReviewItem[]>,
  });
  const byDefect = new Map((decisions.data ?? []).map((i) => [i.payload.defect as string, i]));
  const s = product.summary;
  const r = product.rules;
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="strip">
        {([["Defects found", s.defects, "bad"], ["Problematic tests", s.problematic, "warn"], ["Areas to improve", s.improve, "warn"],
          ["Tests admitted", `${s.admitted}/${s.tests}`, "ok"], ["Approved at sign-off", `${s.approved ?? "–"}/${s.proposed ?? "–"}`, ""]] as const).map(([label, v, tone]) => (
          <div key={label} className="card"><b className={tone}>{v}</b><span className="muted" style={{ fontSize: 12 }}>{label}</span></div>
        ))}
      </div>

      <section>
        <h2>Defects</h2>
        {product.defects.length === 0 && <p className="muted">No test failed for a reason the rules call a product defect.</p>}
        <div style={{ display: "grid", gap: 8 }}>
          {(product.causes ?? product.defects).map((d) => <DefectCard key={d.id} d={d} runId={runId} evidence={evidence} decided={byDefect.get(d.id)} retests={retests} />)}
        </div>
      </section>

      <section>
        <h2>Problematic tests</h2>
        {product.problematic_tests.length === 0 ? <p className="muted">None.</p> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Test</th><th>Status</th><th>Why</th></tr></thead>
            <tbody>{product.problematic_tests.map((t, i) => (
              <tr key={i}>
                <td><div>{t.title}{(t.count ?? 1) > 1 && <span className="muted"> ×{t.count}</span>}</div><div className="muted" style={{ fontSize: 12 }}>{t.ac} {t.criterion}</div></td>
                <td><span className={`pill ${STATUS_TONE[t.status ?? ""] ?? ""}`}>{(t.status ?? "").replace(/_/g, " ").toLowerCase()}</span></td>
                <td style={{ fontSize: 13 }}>{t.reason}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>

      <section>
        <h2>Areas to improve</h2>
        {product.improve.length === 0 ? <p className="muted">Nothing open.</p> : (
          <div style={{ display: "grid", gap: 6 }}>
            {product.improve.map((i, n) => (
              <div key={n} className="card" style={{ display: "grid", gap: 2 }}>
                <div className="row"><span className="pill warn">{IMPROVE_LABEL[i.kind] ?? i.kind}</span><strong className="grow">{i.title}</strong></div>
                {i.kind === "survivor" ? <pre className="patch">{i.detail}</pre> : <div className="muted" style={{ fontSize: 13 }}>{i.detail}</div>}
              </div>
            ))}
          </div>
        )}
      </section>

      <details className="card">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>Rules used in this run</summary>
        <div style={{ fontSize: 13, display: "grid", gap: 4, marginTop: 8 }}>
          <div>Routes: {r.routes.length ? r.routes.join(", ") : "whole API"}</div>
          <div>Clarification rules added: {r.clarify.length ? r.clarify.join("; ") : "none (defaults only)"}</div>
          <div>Case rules added: {r.cases.length ? r.cases.join("; ") : "none"}</div>
          <div>Templates: {r.templates ? r.templates.join(", ") : "all"}</div>
          <div>Gate: <span className="mono">{JSON.stringify(r.gate)}</span></div>
        </div>
      </details>
    </div>
  );
}
