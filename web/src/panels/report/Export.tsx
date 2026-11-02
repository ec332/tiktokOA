import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

/** Keep the tests (M8 report/009 X1-X5): tick tests, then write them to a new branch of the
 *  user's repo. Nothing is written until the button that names the repo and branch is pressed. */

type Row = { case: string; status: string; outcome: string };
type Candidate = {
  unit: string; file: string; ac: string; priority: string; title: string;
  rows: Row[]; fails_now: string[]; trusted: boolean; why: string[];
};
type Done = { branch: string; commit: string; repo: string; path: string; tests: string[] };
type Preview = {
  repo: string; branch: string; branch_exists: boolean; ref: string; from_branch: string;
  tests: Candidate[]; exports: Done[];
};

const LABEL: Record<string, string> = { P0: "P0 · likely defects", P1: "P1 · the feature", P2: "P2 · other checks" };

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, body === undefined ? undefined
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(b.detail ?? r.statusText);
  return b as T;
}

function DoneNote({ d }: { d: Done }) {
  return (
    <div className="card" style={{ display: "grid", gap: 4 }}>
      <div><span className="pill ok">exported</span>{" "}
        {d.tests.length} test file{d.tests.length === 1 ? "" : "s"} to branch <span className="mono">{d.branch}</span>{" "}
        (<span className="mono">{d.commit.slice(0, 7)}</span>) in <span className="mono">{d.repo}</span></div>
      <div className="muted" style={{ fontSize: 13 }}>
        Run them: <span className="mono">git checkout {d.branch} && cd backend && pytest {d.path.replace(/^backend\//, "")}</span>
        {" "}(needs the database: <span className="mono">docker compose up -d db</span>). Details in <span className="mono">{d.path}/README.md</span>.
      </div>
    </div>
  );
}

export function ExportTests({ runId }: { runId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const preview = useQuery({ queryKey: ["export", runId], queryFn: () => call<Preview>(`/runs/${runId}/export`), enabled: open });
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const tests = preview.data?.tests ?? [];
  const sel = picked ?? new Set(tests.filter((t) => t.trusted).map((t) => t.unit));
  const go = useMutation({
    mutationFn: () => call<Done>(`/runs/${runId}/export`, { tests: [...sel] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["export", runId] }),
  });
  const toggle = (units: string[], on: boolean) => {
    const s = new Set(sel);
    for (const u of units) if (on) s.add(u); else s.delete(u);
    setPicked(s);
  };
  const groups = new Map<string, Candidate[]>();
  for (const t of tests) groups.set(t.priority, [...(groups.get(t.priority) ?? []), t]);
  const p = preview.data;
  return (
    <section>
      <h2>Keep the tests</h2>
      {!open ? (
        <div className="row">
          <button onClick={() => setOpen(true)}>Export tests…</button>
          <span className="muted" style={{ fontSize: 13 }}>Choose tests to commit to a new branch of your repo. Nothing is written until you confirm.</span>
        </div>
      ) : preview.isLoading ? <p className="muted">Loading the tests…</p>
        : preview.error ? <p className="error">{(preview.error as Error).message}</p>
        : p && (
          <div className="card" style={{ display: "grid", gap: 8 }}>
            {p.exports.map((d) => <DoneNote key={d.commit} d={d} />)}
            <div className="muted" style={{ fontSize: 13 }}>
              Pre-ticked: stable tests the gate admitted or that found a defect. Tests that found a defect fail until it is fixed, then guard against it.
              Tests are checked against <span className="mono">{p.ref.slice(0, 7)}</span> ({p.from_branch}); the branch starts there.
            </div>
            {[...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([prio, ts]) => {
              const n = ts.filter((t) => sel.has(t.unit)).length;
              return (
                <details key={prio} open={prio !== "P2"}>
                  <summary style={{ cursor: "pointer" }}>
                    <strong>{LABEL[prio] ?? prio}</strong> <span className="muted">{n} of {ts.length} ticked</span>
                    <button style={{ marginLeft: 8, fontSize: 12 }} onClick={(e) => { e.preventDefault(); toggle(ts.map((t) => t.unit), n < ts.length); }}>
                      {n < ts.length ? "Tick group" : "Untick group"}
                    </button>
                  </summary>
                  {ts.map((t) => (
                    <label key={t.unit} className="tline">
                      <input type="checkbox" checked={sel.has(t.unit)} onChange={() => toggle([t.unit], !sel.has(t.unit))} />
                      <span>
                        <span className="t">{t.title}</span>{" "}
                        <span className="muted mono" style={{ fontSize: 12 }}>{t.file} · {t.ac}</span>
                        {t.fails_now.length > 0 && <span className="pill bad" style={{ marginLeft: 6 }}>fails until fixed</span>}
                        {t.why.length > 0 && <span className="s"> — {t.why.join("; ")}</span>}
                      </span>
                    </label>
                  ))}
                </details>
              );
            })}
            <div className="row">
              <button className="primary" disabled={sel.size === 0 || go.isPending || p.branch_exists || go.isSuccess} onClick={() => go.mutate()}>
                {go.isPending ? "Writing…" : `Create branch ${p.branch} with ${sel.size} test file${sel.size === 1 ? "" : "s"}`}
              </button>
              <span className="muted" style={{ fontSize: 13 }}>in <span className="mono">{p.repo}</span>. Your checkout and current branch are not touched.</span>
            </div>
            {p.branch_exists && !go.isSuccess && <p className="warn" style={{ fontSize: 13 }}>Branch {p.branch} already exists. Delete or rename it to export again.</p>}
            {go.error && <p className="error">{(go.error as Error).message}</p>}
          </div>
        )}
    </section>
  );
}
