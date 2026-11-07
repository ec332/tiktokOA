import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ok, type Execution, type ReviewItem, type RunDetail } from "../api/client";
import { Approve } from "../panels/cases/Approve";
import { ProductReport, type Product } from "../panels/report/Product";
import { Clarify } from "../panels/spec/Clarify";
import { resolveItem } from "../flow/resolve";
import { ApprovedTests, BuildResults, ClarifyHistory, Requirements, type CasesBody, type Criterion, type GateRow, type Verdict } from "../flow/Details";
import { NavProvider, STEP, type Target } from "../flow/nav";
import { Activity, say } from "../flow/Activity";

function OtherDecision({ item, onDone }: { item: ReviewItem; onDone: () => void }) {
  const go = useMutation({ mutationFn: (d: string) => resolveItem(item.id, { decision: d, reason: "decided on the run page" }), onSuccess: onDone });
  return (
    <div className="card" style={{ display: "grid", gap: 6 }}>
      <span><span className="needs">Needs you</span> A {item.kind.replace(/_/g, " ")} decision is waiting.</span>
      <pre className="patch">{JSON.stringify(item.payload, null, 1).slice(0, 1200)}</pre>
      <div className="row">{item.options.map((o) => <button key={o} onClick={() => go.mutate(o)} disabled={go.isPending}>{o.replace(/_/g, " ")}</button>)}</div>
      {go.error && <span className="error">{(go.error as Error).message}</span>}
    </div>
  );
}

/** Stage D: a `setup` question (answered with text, verified by a probe when the run resumes) or
 *  discovery's "these operations answer without credentials" check (discover/006). */
function DiscoverItem({ item, onDone }: { item: ReviewItem; onDone: () => void }) {
  const p = item.payload as { question?: string; detail?: string; title?: string; then?: string;
    ops?: { op: string; description: string }[] };
  const [text, setText] = useState("");
  const go = useMutation({
    mutationFn: (d: string) => resolveItem(item.id, { decision: d, reason: "decided on the run page", ...(d === "answer" ? { detail: { text } } : {}) }),
    onSuccess: onDone,
  });
  const label: Record<string, string> = { answer: "Answer", skip: "Skip (stop discovery)", confirm: "Intended (public)", reject: "A hole (should be secured)", out_of_scope: "Not now" };
  return (
    <div className="card" style={{ display: "grid", gap: 6 }}>
      <span><span className="needs">Needs you</span> {item.kind === "setup" ? p.question : p.title}</span>
      {item.kind === "setup" ? <>
        {p.detail && <span className="muted" style={{ fontSize: 13 }}>Why: {p.detail}</span>}
        <input aria-label="Answer" value={text} onChange={(e) => setText(e.target.value)} placeholder="Your answer" />
      </> : (
        <ul style={{ margin: 0 }}>{(p.ops ?? []).map((o) => <li key={o.op}><span className="mono">{o.op}</span>{o.description && <span className="muted">: {o.description}</span>}</li>)}</ul>
      )}
      <div className="row">{item.options.map((o) => (
        <button key={o} className={o === "answer" ? "primary" : ""} onClick={() => go.mutate(o)}
          disabled={go.isPending || (o === "answer" && !text.trim())}>{label[o] ?? o.replace(/_/g, " ")}</button>
      ))}</div>
      {go.error && <span className="error">{(go.error as Error).message}</span>}
    </div>
  );
}

/** M8 run page (skeleton D26): nine stages shown as five steps; a stop becomes a card in place. */

type StepState = "pending" | "running" | "waiting" | "done" | "failed";
const DISCOVER_STEPS: { name: string; stages: string[] }[] = [{ name: "Discover the target", stages: ["discover"] }];
const STEPS: { name: string; stages: string[] }[] = [
  { name: "Understand the feature", stages: ["spec"] },
  { name: "Clarify", stages: ["spec"] },
  { name: "Design and approve tests", stages: ["cases"] },
  { name: "Build and check", stages: ["generate", "gate", "env", "execute"] },
  { name: "Report", stages: ["triage", "report"] },
];

function elapsed(from?: string | null) {
  if (!from) return "";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(from)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function useArtifact<T>(id?: string) {
  return useQuery({
    queryKey: ["artifact", id], enabled: !!id, staleTime: Infinity,
    queryFn: () => ok(api.GET("/artifacts/{art_id}", { params: { path: { art_id: id! } } })) as Promise<{ body: T }>,
  });
}

export function Flow({ run }: { run: RunDetail }) {
  const qc = useQueryClient();
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  const events = useQuery({
    queryKey: ["events", run.id],
    queryFn: () => ok(api.GET("/runs/{run_id}/events/list", { params: { path: { run_id: run.id }, query: { after: 0 } } })),
  });
  const reviews = useQuery({ queryKey: ["reviews", "open"], queryFn: () => ok(api.GET("/reviews", { params: { query: { status: "open" } } })) });
  const resolved = useQuery({ queryKey: ["reviews", "resolved"], queryFn: () => ok(api.GET("/reviews", { params: { query: { status: "resolved" } } })) });
  const [expanded, setExpanded] = useState<Set<string>>(new Set([STEP.report]));
  const [target, setTarget] = useState<Target | null>(null);
  const go = (t: Target) => { setExpanded((e) => new Set(e).add(t.step)); setTarget({ ...t }); };
  const toggle = (step: string) => setExpanded((e) => { const n = new Set(e); if (n.has(step)) n.delete(step); else n.add(step); return n; });

  const latest = new Map<string, Execution>();
  for (const e of run.executions) latest.set(e.stage, e);
  // a `tp target add` run holds only the discover stage (discover/001 D5): one step of its own
  const steps = latest.has("discover") && !latest.has("spec") ? DISCOVER_STEPS : STEPS;
  const exIds = new Set(run.executions.map((e) => e.id));
  const exStage = new Map(run.executions.map((e) => [e.id, e.stage]));
  const open = (reviews.data ?? []).filter((i) => exIds.has(i.stage_execution));
  const openFor = (stage: string) => open.filter((i) => exStage.get(i.stage_execution) === stage);
  const lastLine = (stages: string[]) => {
    const ids = new Set(run.executions.filter((e) => stages.includes(e.stage)).map((e) => e.id));
    const said = (events.data ?? []).filter((e) => e.stage_execution && ids.has(e.stage_execution)).map(say).filter(Boolean);
    return said[said.length - 1] ?? "";
  };
  const cost = (events.data ?? []).filter((e) => e.kind === "llm_cost").reduce((s, e) => s + Number((e.payload as { usd: number }).usd), 0);

  const spec = latest.get("spec");
  const specOut = useArtifact<{ criteria: Criterion[] }>(spec?.outputs.spec);
  const casesEx = latest.get("cases");
  const casesOut = useArtifact<CasesBody>(casesEx?.outputs.cases);
  const gateEx = latest.get("gate");
  const gateOut = useArtifact<{ rows: GateRow[] }>(gateEx?.outputs.gate_report);
  const execEx = latest.get("execute");
  const rrOut = useArtifact<{ verdicts: Verdict[] }>(execEx?.status === "succeeded" ? execEx.outputs.run_records : undefined);
  const titles = Object.fromEntries((casesOut.data?.body.cases ?? []).map((u) => [u.id, u.purpose]));
  const specExIds = new Set(run.executions.filter((e) => e.stage === "spec").map((e) => e.id));
  const answered = (resolved.data ?? []).filter((i) => specExIds.has(i.stage_execution) && ["question", "confirm_observed"].includes(i.kind));
  useEffect(() => {
    if (!target) return;
    const t = setTimeout(() => {
      // a test approved once may run as several points (TC-O7-2.a, .b): take the first
      const el = document.getElementById(target.anchor) ?? document.querySelector(`[id^="${CSS.escape(target.anchor)}."]`);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1800);
    }, 120);
    return () => clearTimeout(t);
  }, [target, expanded]);
  const report = latest.get("report");
  const rep = useArtifact<{ report: { product: Product; evidence: Record<string, unknown> } }>(report?.outputs.report);
  const retest = latest.get("retest");
  const ret = useArtifact<{ retests: never[] }>(retest?.status === "succeeded" ? retest.outputs.retest : undefined);
  const criteria = Object.fromEntries((specOut.data?.body.criteria ?? []).map((c) => [c.id, c.title]));
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["reviews"] });
    qc.invalidateQueries({ queryKey: ["run", run.id] });
  };

  const st = (name: string): StepState => {
    const sts = steps.find((s) => s.name === name)!.stages.map((s) => latest.get(s)?.status ?? "pending");
    if (name === "Discover the target" && openFor("discover").some((i) => i.kind === "setup")) return "waiting";
    if (name === "Understand the feature") return spec?.status === "running" ? "running" : spec?.status === "failed" ? "failed" : spec && spec.status !== "pending" ? "done" : "pending";
    if (name === "Clarify") return openFor("spec").length ? "waiting" : spec?.status === "succeeded" || spec?.status === "stale" ? "done" : "pending";
    if (name === "Design and approve tests" && openFor("cases").length) return "waiting";
    if (sts.includes("failed")) return "failed";
    if (sts.includes("running") || (sts.some((s) => s === "succeeded") && sts.includes("pending") && run.status === "running")) return "running";
    if (sts.every((s) => s === "succeeded" || s === "stale")) return "done";
    return "pending";
  };
  const needsYou = open.length > 0;
  useEffect(() => {
    const prev = document.title;
    document.title = needsYou ? `Needs you · ${run.name}` : `${run.name} · Test Platform`;
    return () => { document.title = prev; };
  }, [needsYou, run.name]);

  const cancel = useMutation({ mutationFn: () => fetch(`/api/runs/${run.id}/cancel`, { method: "POST" }), onSuccess: refresh });
  const resume = useMutation({ mutationFn: () => fetch(`/api/runs/${run.id}/resume`, { method: "POST" }), onSuccess: refresh });
  const failed = [...latest.values()].find((e) => e.status === "failed");
  /** What each finished step produced (null: nothing to show yet). */
  const details = (step: string) => {
    if (step === STEP.understand && specOut.data) return <Requirements criteria={specOut.data.body.criteria} />;
    if (step === STEP.clarify && resolved.data) return <ClarifyHistory items={answered as ReviewItem[]} />;
    if (step === STEP.design && casesOut.data) return <ApprovedTests body={casesOut.data.body} />;
    if (step === STEP.build && gateOut.data) return <BuildResults rows={gateOut.data.body.rows} verdicts={rrOut.data?.body.verdicts ?? []} titles={titles} />;
    if (step === STEP.report && rep.data?.body.report.product) return (
      <ProductReport product={rep.data.body.report.product} evidence={rep.data.body.report.evidence ?? {}}
        runId={run.id} retests={(ret.data?.body.retests ?? []) as never[]} />
    );
    return null;
  };
  // a decision the happy path doesn't expect (no stop of its own): never leave it invisible
  const other = open.filter((i) => !["question", "confirm_observed", "signoff"].includes(i.kind)
    && exStage.get(i.stage_execution) !== "discover");  // discover's items have their own cards

  return (
    <NavProvider value={go}>
    <div className="flow-layout">
    <div className="page">
      <div className="row">
        <span className="muted grow" style={{ fontSize: 13 }}>
          {cost > 0 && `AI cost so far $${cost.toFixed(3)}`}
        </span>
        {run.status === "running" && <button onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel run</button>}
        {run.status === "cancelled" && <><span className="warn">Cancelled after the current step.</span><button onClick={() => resume.mutate()}>Resume</button></>}
      </div>
      <div className="steps">
        {steps.map((s, i) => {
          const state = st(s.name);
          const runningEx = s.stages.map((n) => latest.get(n)).find((e) => e?.status === "running");
          return (
            <div key={s.name} className={`step ${state}`}>
              <span className="dot" aria-hidden>{state === "done" ? "✓" : state === "failed" ? "!" : i + 1}</span>
              <div style={{ minWidth: 0 }}>
                <div className="head">
                  {state === "done" && details(s.name) ? (
                    <button className="linkish" onClick={() => toggle(s.name)} aria-expanded={expanded.has(s.name)}>
                      <strong>{s.name}</strong> <span className="muted">{expanded.has(s.name) ? "▾" : "▸"}</span>
                    </button>
                  ) : <strong>{s.name}</strong>}
                  {state === "waiting" && <span className="needs">Needs you</span>}
                  {state === "running" && <span className="muted" style={{ fontSize: 12 }}>{elapsed(runningEx?.started)}</span>}
                </div>
                {s.name === "Discover the target" && openFor("discover").length ? (
                  <div className="body" style={{ display: "grid", gap: 8 }}>
                    {openFor("discover").map((i) => <DiscoverItem key={i.id} item={i as ReviewItem} onDone={refresh} />)}
                    {state === "done" && <div className="line">Discovered. See the profile on the <a href="/targets">Targets page</a>.</div>}
                  </div>
                ) : s.name === "Discover the target" && state === "done" ? (
                  <div className="line">Discovered. See the profile on the <a href="/targets">Targets page</a>.</div>
                ) : s.name === "Clarify" ? (
                  state === "waiting" ? <div className="body"><Clarify key={openFor("spec").map((x) => x.id).join()} items={openFor("spec") as ReviewItem[]} onDone={refresh} /></div>
                    : state === "done" && <div className="line">Answered</div>
                ) : s.name === "Design and approve tests" && state === "waiting" ? (
                  <div className="body"><Approve key={openFor("cases")[0].id} item={openFor("cases")[0] as ReviewItem} criteria={criteria} onDone={refresh} /></div>
                ) : (
                  <div className="line">{state === "failed" ? `Failed: ${s.stages.map((n) => latest.get(n)).find((e) => e?.status === "failed")?.reason ?? ""}` : lastLine(s.stages)}</div>
                )}
                {state === "done" && expanded.has(s.name) && details(s.name) && <div className="body">{details(s.name)}</div>}
              </div>
            </div>
          );
        })}
      </div>
      {failed && run.status === "failed" && (
        <div className="card row">
          <span className="error grow">The run stopped: {failed.stage} failed. {failed.reason}</span>
          <button className="primary" onClick={() => resume.mutate()} disabled={resume.isPending}>Retry from this step</button>
        </div>
      )}
      {other.map((i) => <OtherDecision key={i.id} item={i as ReviewItem} onDone={refresh} />)}
    </div>
    <Activity events={events.data ?? []} executions={run.executions} />
    </div>
    </NavProvider>
  );
}
