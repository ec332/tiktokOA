import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { PageInfo } from "../components/bits";

/** M8 happy path, screen 1: repo + branch → feature → optional rules → Start (skeleton D25). */

type Target = {
  repo: string; branch: string; base: string; ref: string; base_ref: string; subject: string;
  changed: Record<string, number[]>; warnings: string[];
};
type SutInfo = { name: string; ref: string; target: Target | null; stack: { state: string; error: string | null } };
type Choice = { slug: string; kind: "hand_written" | "discovered"; path: string; repo: string | null; commit_tested: string | null; black_box: boolean };
type Route = { method: string; path: string; file: string; line: number };
type Defaults = {
  clarify: string[]; templates: Record<string, string>;
  gate: { defaults: Gate; limits: Record<string, [number, number]> };
};
type Gate = { k: number; min_kills: number; near: number; every_criterion: boolean };
type Suggestion = { rule: string; why: string };
type Mode = "feature" | "story" | "endpoint";
const MODES: [Mode, string][] = [["feature", "Feature"], ["story", "User story"], ["endpoint", "Endpoint"]];
const MODE_HELP: Record<Mode, string> = {
  feature: "A few sentences about the feature. The platform reads the code and asks you about the gaps.",
  story: "A full user story with acceptance criteria. Each criterion becomes a requirement; the platform asks only where they are unclear.",
  endpoint: "No description: pick API endpoints. Requirements come from what the code does, and you confirm each behaviour.",
};

async function json<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? res.statusText);
  return body as T;
}
const get = <T,>(p: string) => json<T>(fetch(`/api${p}`));
const post = <T,>(p: string, b: unknown) =>
  json<T>(fetch(`/api${p}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }));

function RepoCard({ sut, onChanged }: { sut: SutInfo; onChanged: () => void }) {
  const t = sut.target;
  const [editing, setEditing] = useState(!t);
  const [repo, setRepo] = useState(t?.repo ?? "~/Desktop/full-stack-fastapi-template");
  const [branch, setBranch] = useState(t?.branch ?? "");
  const [base, setBase] = useState(t?.base ?? "");
  const branches = useQuery({
    queryKey: ["branches", repo], enabled: editing && repo.length > 1, retry: false,
    queryFn: () => get<string[]>(`/sut/branches?repo=${encodeURIComponent(repo)}`),
  });
  useEffect(() => {
    const bs = branches.data ?? [];
    if (bs.length && !bs.includes(base)) setBase(bs.includes("master") ? "master" : bs.includes("main") ? "main" : bs[0]);
    if (bs.length && !bs.includes(branch)) setBranch(bs.find((b) => b !== "master" && b !== "main") ?? bs[0]);
  }, [branches.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const use = useMutation({
    mutationFn: () => post("/sut/target", { repo, branch, base }),
    onSuccess: () => { setEditing(false); onChanged(); },
  });
  const stack = sut.stack.state;
  const files = t ? Object.keys(t.changed).length : 0;

  if (!editing && t) {
    return (
      <section className="card row" aria-label="Repository">
        <div className="grow">
          <strong>{t.repo.split("/").pop()}</strong> <span className="mono">{t.branch}</span>
          <span className="muted"> @ {t.ref.slice(0, 7)} · vs {t.base} · {files} changed file{files === 1 ? "" : "s"}</span>
          <div className="muted" style={{ fontSize: 13 }}>
            {stack === "ready" && <span className="ok">● stack healthy</span>}
            {(stack === "starting" || stack === "queued") && <span className="warn">● starting the stack (a few minutes on a new commit)…</span>}
            {stack === "failed" && <span className="bad">● stack failed: {sut.stack.error}</span>}
            {stack === "idle" && <span>● stack starts with the run</span>}
            {t.warnings.map((w) => <div key={w} className="warn">{w}</div>)}
          </div>
        </div>
        <button onClick={() => setEditing(true)}>Change</button>
      </section>
    );
  }
  return (
    <section className="card" style={{ display: "grid", gap: 10 }} aria-label="Repository">
      <strong>Repository</strong>
      <label className="field"><span>Path to a local git repo</span>
        <input id="repo" type="text" value={repo} onChange={(e) => setRepo(e.target.value)} />
      </label>
      {branches.error && <p className="error">{(branches.error as Error).message}</p>}
      <div className="row">
        <label className="field grow"><span>Branch to test</span>
          <select id="branch" value={branch} onChange={(e) => setBranch(e.target.value)}>
            {(branches.data ?? []).map((b) => <option key={b}>{b}</option>)}
          </select>
        </label>
        <label className="field grow"><span>Compared with (base)</span>
          <select id="base" value={base} onChange={(e) => setBase(e.target.value)}>
            {(branches.data ?? []).map((b) => <option key={b}>{b}</option>)}
          </select>
        </label>
      </div>
      <p className="muted" style={{ margin: 0, fontSize: 13 }}>
        The platform clones the branch into its own folder and never changes your checkout.
      </p>
      <div className="row">
        <button className="primary" disabled={!branch || !base || branch === base || use.isPending} onClick={() => use.mutate()}>
          {use.isPending ? "Cloning…" : "Use this branch"}
        </button>
        {t && <button onClick={() => setEditing(false)}>Cancel</button>}
        {use.error && <span className="error">{(use.error as Error).message}</span>}
      </div>
    </section>
  );
}

function RuleList({ title, hint, placeholder, defaults, mine, setMine, suggestions, picked, setPicked, onSuggest, suggesting, suggestError }: {
  title: string; hint: string; placeholder: string; defaults: string[]; mine: string[]; setMine: (r: string[]) => void;
  suggestions?: Suggestion[]; picked?: Set<string>; setPicked?: (s: Set<string>) => void;
  onSuggest?: () => void; suggesting?: boolean; suggestError?: string;
}) {
  const [draft, setDraft] = useState("");
  const add = () => { if (draft.trim()) { setMine([...mine, draft.trim()]); setDraft(""); } };
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 2 }}>{title}</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>{hint}</p>
      {defaults.map((r) => <div key={r} className="rule default">✓ <span>{r}</span></div>)}
      {defaults.length > 0 && <div className="muted" style={{ fontSize: 12 }}>Default rules always apply.</div>}
      {onSuggest && (
        <div style={{ marginTop: 8 }}>
          <button onClick={onSuggest} disabled={suggesting}>{suggesting ? "Suggesting…" : "Suggest rules for this feature"}</button>
          {suggestError && <span className="error"> {suggestError}</span>}
        </div>
      )}
      {suggestions?.map((s) => (
        <label key={s.rule} className="rule">
          <input type="checkbox" checked={picked!.has(s.rule)} onChange={(e) => {
            const n = new Set(picked); if (e.target.checked) n.add(s.rule); else n.delete(s.rule); setPicked!(n);
          }} />
          <span>{s.rule}<small>{s.why}</small></span>
        </label>
      ))}
      {mine.map((r, i) => (
        <div key={i} className="rule">+ <span className="grow">{r}</span>
          <button onClick={() => setMine(mine.filter((_, j) => j !== i))} aria-label={`Remove rule ${r}`}>Remove</button>
        </div>
      ))}
      <div className="row" style={{ marginTop: 6 }}>
        <input type="text" className="grow" placeholder={placeholder} value={draft}
          onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button onClick={add} disabled={!draft.trim()}>Add</button>
      </div>
    </div>
  );
}

export function NewRunPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const sut = useQuery({
    queryKey: ["sut"], queryFn: () => get<SutInfo>("/sut"),
    refetchInterval: (q) => (["starting", "queued"].includes(q.state.data?.stack.state ?? "") ? 3000 : false),
  });
  const routes = useQuery({ queryKey: ["routes", sut.data?.target?.ref], queryFn: () => get<Route[]>("/sut/routes"), enabled: !!sut.data?.target });
  const defaults = useQuery({ queryKey: ["rule-defaults"], queryFn: () => get<Defaults>("/rules/defaults") });
  // which target the run is for (discover/006): hand-written or discovered; default = the server's
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => get<Choice[]>("/profiles") });
  const [profile, setProfile] = useState<string>("");
  const choice = profiles.data?.find((c) => c.path === profile);
  const blackBox = !!choice?.black_box;

  const [overview, setOverview] = useState("");
  const [mode, setMode] = useState<Mode>("feature");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [clarify, setClarify] = useState<string[]>([]);
  const [caseRules, setCaseRules] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [templates, setTemplates] = useState<Set<string> | null>(null);
  const [gate, setGate] = useState<Gate | null>(null);
  const allTemplates = Object.keys(defaults.data?.templates ?? {});
  const tpl = templates ?? new Set(allTemplates);
  const g = gate ?? defaults.data?.gate.defaults;

  const groups = useMemo(() => {
    const m = new Map<string, Route[]>();
    for (const r of routes.data ?? []) {
      const key = r.path.replace(/^\/api\/v\d+\//, "").split("/")[0] || "/";
      m.set(key, [...(m.get(key) ?? []), r]);
    }
    return [...m.entries()];
  }, [routes.data]);

  const suggest = useMutation({
    mutationFn: () => post<Suggestion[]>("/rules/suggest", { overview: mode === "endpoint" ? `Endpoints: ${[...chosen].join(", ")}` : overview, routes: [...chosen] }),
    onSuccess: (s) => setSuggestions(s),
  });
  const start = useMutation({
    mutationFn: () => post<{ id: number }>("/runs", {
      mode: "live", stub: false, focused: true,
      requirements: mode === "endpoint"
        ? { kind: "brief", text: `Test these API endpoints as they are implemented: ${[...chosen].join(", ")}.` }
        : { kind: mode === "story" ? "story" : "brief", text: overview },
      routes: [...chosen],
      ...(profile ? { profile } : {}),
      rules: { clarify: [...suggestions.map((s) => s.rule).filter((r) => picked.has(r)), ...clarify], cases: caseRules },
      templates: templates ? [...templates] : null,
      gate: gate ?? null,
    }),
    onSuccess: (run) => { qc.invalidateQueries({ queryKey: ["runs"] }); nav({ to: "/runs/$runId", params: { runId: String(run.id) } }); },
  });

  if (!sut.data) return <p className="muted">Loading…</p>;
  // a black-box target has no repo/branch to pick and no code index: the whole API is in scope
  const hasTarget = blackBox || !!sut.data.target;
  const ready = hasTarget && (mode === "endpoint" ? !blackBox && chosen.size > 0 : overview.trim().length > 0);
  const toggle = (k: string) => { const n = new Set(chosen); if (n.has(k)) n.delete(k); else n.add(k); setChosen(n); };

  return (
    <div className="page">
      <h1>New test run</h1>
      <PageInfo>
        <p>Step 2 of the flow: describe what to test, then <strong>Start run</strong>. The run page that opens does the
          rest and stops for you only twice, at <strong>Clarify</strong> and <strong>Approve</strong>.</p>
        <h3>Target</h3>
        <ul>
          <li><strong>Default</strong> is the FastAPI template, tested from a local git repo and branch. The platform
            reads its code, plants mutations in the lines your branch changed, locates defects to a file and line, and
            can prove a fix by retest.</li>
          <li>A <strong>discovered</strong> target (added on the Targets page) is tested as a black box through its API.
            There's no branch or route picker, and the whole API is in scope.</li>
        </ul>
        <h3>Repository (the template only)</h3>
        <p>Path to a local git repo, the <em>branch to test</em> and the <em>base</em> it's compared with. The platform
          clones the branch and never changes your checkout; uncommitted changes aren't in the clone. The diff between
          base and branch is "the feature". After <strong>Use this branch</strong>, wait for the stack to show healthy.</p>
        <h3>What to test</h3>
        <ul>
          <li><strong>Feature</strong>: a few sentences. The platform turns them into requirements, reads the code for
            the rest, and asks you about gaps. The usual choice.</li>
          <li><strong>User story</strong>: a story with acceptance criteria. Each criterion becomes a requirement, so
            there are fewer questions.</li>
          <li><strong>Endpoint</strong>: only the endpoints. Requirements come from what the code does, and you confirm
            each behaviour.</li>
          <li><strong>Routes to focus on</strong> (optional): limits what's read and tested. Fewer routes is faster and
            cheaper.</li>
        </ul>
        <h3>Rules for this run (optional)</h3>
        <p>Added after the defaults, which always apply, and for this run only; the report lists them.
          <em>Clarifications</em> are points the requirements must settle (<em>Suggest rules</em> proposes general ones,
          unticked). <em>Test cases</em> lines and the four templates shape the test design. <em>Gate</em> sets how many
          times each test runs and how many planted bugs it must catch.</p>
      </PageInfo>
      <section className="card row" aria-label="Target">
        <label className="field grow"><span>Target</span>
          <select aria-label="Target" value={profile} onChange={(e) => { setProfile(e.target.value); setChosen(new Set()); if (e.target.value) setMode((m) => (m === "endpoint" ? "feature" : m)); }}>
            <option value="">Default ({sut.data.name})</option>
            {(profiles.data ?? []).map((c) => (
              <option key={c.path} value={c.path}>{c.slug} · {c.kind === "discovered" ? "discovered" : "hand-written"}{c.commit_tested ? ` @ ${c.commit_tested.slice(0, 7)}` : ""}</option>
            ))}
          </select>
        </label>
        {blackBox && <span className="muted">Black-box target: the whole API is in scope.</span>}
      </section>
      {!blackBox && <RepoCard sut={sut.data} onChanged={() => { qc.invalidateQueries({ queryKey: ["sut"] }); qc.invalidateQueries({ queryKey: ["routes"] }); }} />}

      <section className="card" style={{ display: "grid", gap: 10 }} aria-label="Feature">
        <div className="row">
          <strong className="grow">What to test</strong>
          <div className="seg" role="radiogroup" aria-label="Input mode">
            {MODES.filter(([m]) => !(blackBox && m === "endpoint")).map(([m, label]) => (
              <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>{label}</button>
            ))}
          </div>
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>{MODE_HELP[mode]}</p>
        {mode !== "endpoint" && (
          <textarea id="overview" value={overview} onChange={(e) => setOverview(e.target.value)} rows={mode === "story" ? 10 : 3}
            placeholder={mode === "story"
              ? "As an item owner I want to set a price and a discount percentage…\n\nAcceptance criteria:\n- …"
              : "Items get a price and a discount percentage; responses show the discounted price."} />
        )}
        {sut.data.target && !blackBox && (
          <div className="field"><span>{mode === "endpoint" ? "Endpoints to test (pick at least one)" : "Routes to focus on (optional: none = the whole API)"}</span>
            {routes.isLoading && <span className="muted">Reading routes…</span>}
            {groups.map(([group, rs]) => (
              <div key={group} className="row" style={{ alignItems: "baseline" }}>
                <span className="muted mono trunc" style={{ width: 110, flex: "none" }} title={group}>{group}</span>
                <div className="chips grow">
                  {rs.map((r) => {
                    const k = `${r.method} ${r.path}`;
                    return (
                      <button key={k} className={`chip ${chosen.has(k) ? "on" : ""}`} onClick={() => toggle(k)} aria-pressed={chosen.has(k)}>
                        <span className="m">{r.method}</span>{r.path.replace(/^\/api\/v\d+/, "")}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <details className="card rules">
        <summary>Rules for this run <span className="muted" style={{ fontWeight: 400 }}>(optional · {clarify.length + picked.size + caseRules.length} added)</span></summary>
        {defaults.data && g && (
          <div className="rules-grid">
            <RuleList title="Clarifications"
              hint="What the requirements must settle before tests are designed. The platform settles each point from your text or the code, and asks you when neither does."
              placeholder="e.g. Money fields: settle how amounts are rounded" defaults={defaults.data.clarify} mine={clarify} setMine={setClarify}
              suggestions={suggestions} picked={picked} setPicked={setPicked}
              onSuggest={overview.trim() || (mode === "endpoint" && chosen.size) ? () => suggest.mutate() : undefined} suggesting={suggest.isPending}
              suggestError={suggest.error ? (suggest.error as Error).message : undefined} />
            <div>
              <RuleList title="Test cases"
              hint="How to test once the requirements are settled: which inputs and scenarios every test set must include. Doesn't add requirements."
              placeholder="e.g. Every price test includes 0 and 0.01" defaults={[]} mine={caseRules} setMine={setCaseRules} />
              <div className="chips" style={{ marginTop: 8 }}>
                {allTemplates.map((t) => (
                  <label key={t} className={`chip ${tpl.has(t) ? "on" : ""}`}>
                    <input type="checkbox" hidden checked={tpl.has(t)} onChange={() => {
                      const n = new Set(tpl); if (n.has(t)) n.delete(t); else n.add(t); setTemplates(n);
                    }} />
                    {defaults.data.templates[t]}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <h2 style={{ marginTop: 0, marginBottom: 2 }}>Gate</h2>
              <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
                When a generated test's result is trusted. Fixed code, no AI. A failure counts as a defect only from a stable test on a confirmed requirement.
              </p>
              <div className="row">
                {([
                  ["k", "Runs per test", "A test must give the same result on every run plus one shuffled run, or it is FLAKY."],
                  ["min_kills", "Planted bugs a test must catch", "The gate plants small changes in the feature's code. A test that catches fewer is WEAK."],
                  ["near", "Link missed changes within N lines", "A planted change no test caught, this close to a line a requirement cites, is listed under that requirement as an unnoticed change."],
                ] as const).map(([k, label, help]) => (
                  <label key={k} className="field" title={help}><span>{label}</span>
                    <input type="number" style={{ width: 90 }} value={g[k]}
                      min={defaults.data.gate.limits[k][0]} max={defaults.data.gate.limits[k][1]}
                      onChange={(e) => setGate({ ...g, [k]: Number(e.target.value) })} />
                    <small className="muted" style={{ maxWidth: 220 }}>{help}</small>
                  </label>
                ))}
                <label className="row" style={{ fontSize: 13 }}>
                  <input type="checkbox" checked={g.every_criterion} onChange={(e) => setGate({ ...g, every_criterion: e.target.checked })} />
                  List requirements with no admitted test (under Areas to improve)
                </label>
              </div>
            </div>
          </div>
        )}
      </details>

      <div className="row">
        <button className="primary" disabled={!ready || start.isPending} onClick={() => start.mutate()}>Start run</button>
        {!hasTarget && <span className="muted">Choose a repo and branch first.</span>}
        {hasTarget && !ready && <span className="muted">{mode === "endpoint" ? "Pick at least one endpoint." : "Describe what to test."}</span>}
        {start.error && <span className="error">{(start.error as Error).message}</span>}
      </div>
    </div>
  );
}
