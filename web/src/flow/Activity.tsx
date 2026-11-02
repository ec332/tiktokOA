import { useEffect, useRef, useState } from "react";
import type { Event, Execution } from "../api/client";
import { clock } from "./time";

/** The run page's live activity log (skeleton/019): every event, in plain words, on the right.
 *  Collapsible; follows new events while scrolled to the bottom. */

type P = Record<string, unknown>;
const n = (v: unknown) => (typeof v === "number" ? v : Number(v));
const counts = (p: P) => Object.entries(p).map(([k, v]) => `${v} ${k.toLowerCase().replace(/_/g, " ")}`).join(", ");

/** What each stage does, for the "Now" line and stage-start entries. */
export const STAGE_DOES: Record<string, string> = {
  input: "Recording what you asked for",
  spec: "Reading your text and the focused code; writing requirements and questions (AI)",
  cases: "Designing test cases from the requirements (AI; edge values from fixed calculators)",
  generate: "Writing test code, then statically checking and dry-running it (AI, checked by fixed code)",
  gate: "Running each test several times, in shuffled order and against planted bugs (fixed code)",
  env: "Starting the stack for your branch (fixed code)",
  execute: "Running the tests against the real stack and recording every request (fixed code)",
  triage: "Classifying each failure, locating defects, suggesting fixes (fixed code + AI)",
  report: "Building the report from the records (fixed code)",
  retest: "Re-running the same tests on the fix (fixed code)",
};

/** One plain line per progress event; null for kinds the step lines skip. */
export function say(e: Event): string | null {
  const p = e.payload as P;
  switch (e.kind) {
    case "spec_focused": return `Focused on ${p.routes} routes in ${p.files} files`;
    case "spec_investigated": return `Read the code: ${p.observed} behaviours, ${p.findings} findings`;
    case "spec_answer_reused": return `Reused an earlier answer (${p.key})`;
    case "spec_frozen": return `${p.criteria} requirements written down`;
    case "cases_drafted": return `${p.units} tests designed`;
    case "cases_acceptance_redraft": return `Re-drafting a normal-case test for ${p.criteria} requirement(s)`;
    case "cases_signed": case "cases_unsigned": return `${p.approved} of ${p.lines} tests approved`;
    case "runner_rebuilding": return "Rebuilding the test runner for your branch (about a minute)";
    case "runner_rebuilt": return "Test runner ready";
    case "stage_retried": return `Retrying ${p.stage}`;
    case "bindings_written": return `Writing test code for ${p.units} tests`;
    case "dry_run": return `Checking the test code (round ${p.round})`;
    case "tests_bound": return `Test code ready: ${counts(p)}`;
    case "env_up": return "Stack is up";
    case "gate_cache": return "Gate: same tests and code as an earlier run; reusing its measurements";
    case "gate_stability_run": return p.shuffled ? `Stability run ${p.run} of ${p.of}, in shuffled order` : `Stability run ${p.run} of ${p.of}`;
    case "gate_stability": return `${p.stable_passing} of ${p.nodes} tests pass every time on your branch`;
    case "gate_mutants_generating": return "Planting small code changes (mutants) in the changed lines and handlers";
    case "gate_linking": return `Finding which tests reach each of ${p.mutants} mutants`;
    case "gate_matrix_start": return `Running the tests against ${p.jobs} mutants`;
    case "gate_matrix": return `Mutation check: ${p.mutants} code changes, ${p.jobs} runs`;
    case "gate_v1": case "gate_done": return `Gate: ${counts(p)}`;
    case "execute_variant": return `Running the admitted tests on ${p.variant === "reference" ? "your branch" : p.variant}`;
    case "execute_sealed": return "Results recorded and sealed";
    case "triage_classified": return `${p.defects} defects among ${p.findings} failing results`;
    case "triage_fix_suggested": return `Fix suggested for ${p.defect}`;
    case "retest_running": return `Retesting ${p.defect} on ${String(p.after).startsWith("fixed:") ? "the suggested fix" : "your new commit"}`;
    case "retest_verdict": return `Retest of ${p.defect}: ${p.verdict}`;
    case "report_written": return "Report ready";
    case "stage_failed": return `Failed: ${p.reason}`;
    default: return null;
  }
}

/** The log line: `say`, plus the lifecycle and AI-call events the step lines leave out. */
function describe(e: Event): { text: string; tone?: string } {
  const p = e.payload as P;
  switch (e.kind) {
    case "run_created": return { text: "Run created" };
    case "stage_running": return { text: `Started ${p.stage}: ${STAGE_DOES[String(p.stage)] ?? ""}`, tone: "start" };
    case "stage_succeeded": return { text: `Finished ${p.stage}`, tone: "ok" };
    case "stage_awaiting_review": return { text: `Waiting for you (${Array.isArray(p.items) ? p.items.length : ""} item${Array.isArray(p.items) && p.items.length === 1 ? "" : "s"})`, tone: "warn" };
    case "stage_stale": return { text: `${p.stage}: output is out of date and will be rebuilt` };
    case "stage_failed": return { text: `Failed: ${p.reason}`, tone: "bad" };
    case "llm_cost": return { text: `AI call · ${String(p.model ?? "").replace(/^claude-/, "")} · $${n(p.usd).toFixed(4)}`, tone: "ai" };
    case "run_cancelled": return { text: "Cancel requested: stops after the current step", tone: "warn" };
    case "review_created": return { text: `Prepared ${REVIEW_KIND[String(p.review_kind)] ?? String(p.review_kind).replace(/_/g, " ")} for you` };
  }
  if (e.kind.startsWith("run_")) return { text: `Run ${e.kind.slice(4).replace(/_/g, " ")}`, tone: e.kind === "run_failed" ? "bad" : "ok" };
  const s = say(e);
  if (s) return { text: s };
  const raw = JSON.stringify(e.payload);
  return { text: `${e.kind}${raw && raw !== "{}" ? ` ${raw.length > 80 ? raw.slice(0, 80) + "…" : raw}` : ""}` };
}

const REVIEW_KIND: Record<string, string> = {
  question: "a question", confirm_observed: "an \"is this intended?\" check", signoff: "the test list to approve",
  triage: "a triage decision", real_defect: "a defect decision",
};

/** Consecutive events of the same kind (AI calls, prepared questions) shown as one line. */
const GROUPED = new Set(["llm_cost", "review_created"]);
type Line = { first: Event; events: Event[] };
function group(events: Event[]): Line[] {
  const out: Line[] = [];
  for (const e of events) {
    const prev = out[out.length - 1];
    if (prev && GROUPED.has(e.kind) && prev.first.kind === e.kind && prev.first.stage_execution === e.stage_execution) prev.events.push(e);
    else out.push({ first: e, events: [e] });
  }
  return out;
}
function describeLine(l: Line): { text: string; tone?: string } {
  const d = describe(l.first);
  if (l.events.length === 1) return d;
  if (l.first.kind === "llm_cost") {
    const usd = l.events.reduce((s, e) => s + n((e.payload as P).usd), 0);
    const model = String((l.first.payload as P).model ?? "").replace(/^claude-/, "");
    return { text: `${l.events.length} AI calls · ${model} · $${usd.toFixed(4)}`, tone: "ai" };
  }
  return { text: `Prepared ${l.events.length} items for you to review` };
}

function since(iso?: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

const KEY = "tp.activity.open";
function remembered(): boolean {
  try { return localStorage.getItem(KEY) !== "0"; } catch { return true; }
}

export function Activity({ events, executions }: { events: Event[]; executions: Execution[] }) {
  const [open, setOpen] = useState(remembered);
  const [follow, setFollow] = useState(true);
  const [raw, setRaw] = useState<number | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const stageOf = new Map(executions.map((x) => [x.id, x.stage]));
  const running = executions.filter((x) => x.status === "running");
  const last = events[events.length - 1];

  const toggle = () => {
    setOpen((o) => { try { localStorage.setItem(KEY, o ? "0" : "1"); } catch { /* per-viewer convenience only */ } return !o; });
  };
  useEffect(() => {
    if (open && follow && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [events.length, open, follow]);

  if (!open) {
    return (
      <aside className="activity closed">
        <button onClick={toggle} aria-expanded={false} aria-label="Show activity" title="Show activity">
          <span>Activity</span>{running.length > 0 && <span className="live" aria-hidden />}
        </button>
      </aside>
    );
  }
  return (
    <aside className="activity" aria-label="Activity">
      <div className="activity-head">
        <strong className="grow">Activity</strong>
        <span className="muted" style={{ fontSize: 12 }}>{events.length} events</span>
        <button onClick={toggle} aria-expanded aria-label="Hide activity" title="Hide activity">⇥</button>
      </div>
      {running.map((x) => (
        <div key={x.id} className="activity-now">
          <span className="live" aria-hidden />
          <div>
            <div><strong>Now: {x.stage}</strong> <span className="muted">· {since(x.started)}</span></div>
            <div className="muted">{STAGE_DOES[x.stage] ?? ""}</div>
            {last && <div className="muted" style={{ fontSize: 11 }}>last update {since(last.at)} ago</div>}
          </div>
        </div>
      ))}
      <div className="activity-list" ref={list} onScroll={(ev) => {
        const el = ev.currentTarget;
        setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
      }}>
        {events.length === 0 && <p className="muted">Nothing yet.</p>}
        {group(events).map((l) => {
          const e = l.first;
          const d = describeLine(l);
          const stage = e.stage_execution ? stageOf.get(e.stage_execution) : undefined;
          const lastAt = l.events[l.events.length - 1].at;
          return (
            <div key={e.id} className={`activity-line ${d.tone ?? ""}`}>
              <button className="linkish" onClick={() => setRaw(raw === e.id ? null : e.id)} aria-expanded={raw === e.id}>
                <span className="mono muted">{clock(lastAt)}</span>
                {stage && <span className="tag">{stage}</span>}
                <span className="text">{d.text}</span>
              </button>
              {raw === e.id && <pre className="patch">{l.events.map((x) => `${clock(x.at)} ${x.kind} ${JSON.stringify(x.payload)}`).join("\n")}</pre>}
            </div>
          );
        })}
      </div>
      {!follow && (
        <button className="activity-jump" onClick={() => { setFollow(true); }}>Jump to latest ↓</button>
      )}
    </aside>
  );
}
