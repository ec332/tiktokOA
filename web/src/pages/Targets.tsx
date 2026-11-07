import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { api, ok } from "../api/client";
import { Loading, Status } from "../components/bits";

/* Shapes of GET /targets and GET /targets/{slug} (tp/stages/discover/targets.py). */
type Counts = { verified: number; clarify: number; override: number; layers: Record<string, number> };
type Target = { slug: string; repo: string; commit_tested: string; ref: string | null; counts: Counts; no_conventions: boolean };
type FieldRow = { concern: string; key: string; value: unknown; source: string; layer: string; probe: number | null };
type DiscRun = { run: string; id: number; status: string; mode: string; ref: string | null; no_conventions: boolean;
  model_calls: number; cost_usd: number; probes: number | null; seconds: number | null };
type Cmp = { same_commit: boolean; runs: Record<string, DiscRun>;
  rows: { concern: string; key: string; normal: { source: string; layer: string } | null;
    no_conventions: { source: string; layer: string } | null; same_value: boolean }[] };
type Detail = { slug: string; repo: string; commit_tested: string; fields: FieldRow[]; counts: Counts;
  excluded: { op: string; reason: string }[]; runs: DiscRun[]; compare: Cmp | null };

const SOURCE_TONE: Record<string, string> = { probed: "ok", repo: "ok", resolver: "", "ai_proposed+verified": "warn", clarify: "warn", override: "bad" };

function short(v: unknown): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s === undefined ? "—" : s;
}

/** Targets (stage D): discovered profiles; every field with its source, layer and evidence. */
export function TargetsPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["targets"], refetchInterval: 5000,
    queryFn: async () => (await ok(api.GET("/targets"))) as unknown as Target[] });
  const [sel, setSel] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [nc, setNc] = useState(false);
  const add = useMutation({
    mutationFn: () => ok(api.POST("/targets", { body: { target: url, no_conventions: nc } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["targets"] }),
  });
  const slug = sel ?? list.data?.[0]?.slug ?? null;
  return (
    <div className="page">
      <h1>Targets</h1>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (url.trim()) add.mutate(); }}>
        <input className="grow" aria-label="Repo URL" placeholder="https://github.com/org/repo[@tag]"
          value={url} onChange={(e) => setUrl(e.target.value)} />
        <label className="muted"><input type="checkbox" checked={nc} onChange={(e) => setNc(e.target.checked)} /> no conventions</label>
        <button className="primary" disabled={!url.trim() || add.isPending}>Discover</button>
      </form>
      {add.data ? <p className="muted">Started <Link to="/runs/$runId" params={{ runId: String(add.data.id) }}>{add.data.name}</Link>; setup questions appear on the run.</p> : null}
      {add.error ? <p className="pill bad">{String(add.error)}</p> : null}
      {!list.data ? <Loading error={list.error} /> : list.data.length === 0 ? (
        <p className="muted">No targets yet. Discover one from its repo URL.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Target</th><th>Tag</th><th>Commit tested</th><th>Verified</th><th>Clarify</th><th>Override</th><th>Layers (conv / alg / expl)</th></tr></thead>
            <tbody>
              {list.data.map((t) => (
                <tr key={t.slug} aria-selected={t.slug === slug} style={t.slug === slug ? { fontWeight: 600 } : undefined}>
                  <td><button className="linkish" onClick={() => setSel(t.slug)}>{t.slug}</button></td>
                  <td className="mono">{t.ref ?? "—"}</td>
                  <td className="mono" title={t.commit_tested}>{t.commit_tested.slice(0, 10)}</td>
                  <td>{t.counts.verified}</td><td>{t.counts.clarify}</td><td>{t.counts.override}</td>
                  <td className="mono">{t.counts.layers.convention} / {t.counts.layers.algorithm} / {t.counts.layers.exploration}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {slug ? <TargetDetail slug={slug} /> : null}
    </div>
  );
}

function TargetDetail({ slug }: { slug: string }) {
  const q = useQuery({ queryKey: ["target", slug], refetchInterval: 5000,
    queryFn: async () => (await ok(api.GET("/targets/{slug}", { params: { path: { slug } } }))) as unknown as Detail });
  const [cmp, setCmp] = useState(false);
  if (!q.data) return <Loading error={q.error} />;
  const d = q.data;
  return (
    <section className="page">
      <h2 className="row"><span className="grow">{d.slug}</span>
        <label className="muted"><input type="checkbox" checked={cmp} disabled={!d.compare}
          onChange={(e) => setCmp(e.target.checked)} /> compare with --no-conventions</label>
      </h2>
      <p className="muted"><a href={d.repo}>{d.repo}</a> · commit_tested <span className="mono">{d.commit_tested}</span></p>
      {cmp && d.compare ? <Compare c={d.compare} /> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Field</th><th>Value</th><th>Source</th><th>Layer</th><th>Evidence</th></tr></thead>
            <tbody>
              {d.fields.map((f) => (
                <tr key={`${f.concern}.${f.key}`}>
                  <td className="mono">{f.concern}.{f.key}</td>
                  <td><span className="trunc mono" style={{ maxWidth: 340 }} title={short(f.value)}>{short(f.value)}</span></td>
                  <td><span className={`pill ${SOURCE_TONE[f.source] ?? ""}`}>{f.source}</span></td>
                  <td className="muted">{f.layer}</td>
                  <td className="mono muted">{f.probe === null ? "—" : `probe#${f.probe}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3>Excluded operations</h3>
      {d.excluded.length === 0 ? <p className="muted">None.</p> : (
        <ul>{d.excluded.map((e) => <li key={e.op}><span className="mono">{e.op}</span>: {e.reason}</li>)}</ul>
      )}
      <h3>Discovery runs</h3>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Run</th><th>Mode</th><th>Conventions</th><th>Status</th><th>Probes</th><th>Model calls</th><th>Cost</th><th>Time</th></tr></thead>
          <tbody>
            {d.runs.map((r) => (
              <tr key={r.id}>
                <td><Link to="/runs/$runId" params={{ runId: String(r.id) }}>{r.run}</Link></td>
                <td>{r.mode}</td><td>{r.no_conventions ? "off" : "on"}</td><td><Status status={r.status} /></td>
                <td>{r.probes ?? "—"}</td><td>{r.model_calls}</td><td>${r.cost_usd.toFixed(4)}</td>
                <td>{r.seconds === null ? "—" : `${r.seconds}s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Compare({ c }: { c: Cmp }) {
  const sl = (x: { source: string; layer: string } | null) => (x ? `${x.source} · ${x.layer}` : "—");
  const n = c.runs.normal, x = c.runs.no_conventions;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Field</th><th>Normal (source · layer)</th><th>No conventions (source · layer)</th><th>Same value</th></tr></thead>
        <tbody>
          {c.rows.map((r) => (
            <tr key={`${r.concern}.${r.key}`}>
              <td className="mono">{r.concern}.{r.key}</td>
              <td>{sl(r.normal)}</td><td>{sl(r.no_conventions)}</td>
              <td>{r.same_value ? <span className="pill ok">same</span> : <span className="pill warn">differs</span>}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          {[["Normal", n], ["No conventions", x]].map(([label, r]) => {
            const run = r as DiscRun | undefined;
            return (
              <tr key={label as string}>
                <td className="muted">{label as string}</td>
                <td colSpan={3} className="muted">{run ? `${run.run}: ${run.probes ?? "?"} probes · ${run.model_calls} model calls · $${run.cost_usd.toFixed(4)} · ${run.seconds ?? "?"}s` : "no live run"}</td>
              </tr>
            );
          })}
          <tr><td className="muted" colSpan={4}>{c.same_commit ? "Both profiles are at the same commit." : "The two profiles are at different commits."}</td></tr>
        </tfoot>
      </table>
    </div>
  );
}
