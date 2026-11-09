import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { ReviewItem } from "../../api/client";
import { Labels } from "../../components/bits";
import { resolveItem } from "../../flow/resolve";

/** Stop 2 of the happy path (M8 cases D16): tick the tests to generate; title + one sentence.
 *  Grouped by priority, then requirement; P2 starts collapsed (cases/010 P2'). */
type Line = { id: string; title?: string; summary?: string; ac?: string; text: string; priority?: string;
  score?: { score: number; parts: Record<string, number> } };
type PlannedWrite = { op: string; verdict: "create" | "own_items" | "blocked"; rule: string };
/** What runs besides these tests: read-only, not signed (DR-11, cohesion/001 D3). */
type AlsoRuns = { kind: "generic" | "ui" | "suite"; id: string; text: string; basis: string };
/** Why a line is pre-selected at its priority (autonomy/001 F7): "score 12 = suspected defect 7 + …". */
const breakdown = (s?: Line["score"]) => s && Object.keys(s.parts).length
  ? `score ${s.score} = ${Object.entries(s.parts).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" + ")}` : "";

const PRIORITY_LABEL: Record<string, string> = {
  P0: "P0 · likely defects", P1: "P1 · the feature", P2: "P2 · other checks",
};
const priorityOf = (l: Line) => l.priority ?? l.text.match(/^(P\d)\b/)?.[1] ?? "P2";

export function Approve({ item, criteria, onDone }: { item: ReviewItem; criteria: Record<string, string>; onDone: () => void }) {
  const t0 = useRef(Date.now());
  const lines = item.payload.lines as Line[];
  const [off, setOff] = useState<Set<string>>(new Set());
  const byPriority = new Map<string, Line[]>();
  for (const l of lines) byPriority.set(priorityOf(l), [...(byPriority.get(priorityOf(l)) ?? []), l]);
  const n = lines.length - off.size;
  const submit = useMutation({
    mutationFn: () => resolveItem(item.id, {
      detail: { lines: Object.fromEntries(lines.map((l) => [l.id, { decision: off.has(l.id) ? "exclude" : "approve", comment: "" }])) },
    }, (Date.now() - t0.current) / 1000),
    onSuccess: onDone,
  });
  const toggle = (ids: string[], on: boolean) => {
    const s = new Set(off);
    for (const id of ids) if (on) s.delete(id); else s.add(id);
    setOff(s);
  };
  const notes = (item.payload.attention as string[] | undefined) ?? [];
  const writes = (item.payload.planned_writes as PlannedWrite[] | undefined) ?? [];
  const also = (item.payload.also_runs as AlsoRuns[] | undefined) ?? [];
  // a test that contradicts one of your answers is never buried in the notes (cases/012 D2)
  const conflicts = notes.filter((a) => a.includes("but you decided"));
  const attention = notes.filter((a) => !conflicts.includes(a));
  return (
    <div className="card">
      {conflicts.length > 0 && (
        <div className="warn" style={{ fontSize: 13, marginBottom: 8 }}>
          <strong>{conflicts.length} test{conflicts.length === 1 ? "" : "s"} disagree with your answers.</strong> Untick them, or run again after checking the answer.
          <ul style={{ margin: "4px 0" }}>{conflicts.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}
      {attention.length > 0 && (
        <details style={{ marginBottom: 8 }}>
          <summary className="warn" style={{ cursor: "pointer", fontSize: 13 }}>{attention.length} note{attention.length === 1 ? "" : "s"} from the test design check</summary>
          <ul style={{ fontSize: 13, margin: "4px 0" }}>{attention.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </details>
      )}
      {writes.length > 0 && (
        <details style={{ marginBottom: 8 }}>
          <summary style={{ cursor: "pointer", fontSize: 13 }}>
            <strong>Planned writes on the live system</strong>{" "}
            <span className="muted">{writes.filter((w) => w.verdict !== "blocked").length} allowed, {writes.filter((w) => w.verdict === "blocked").length} blocked by safe mode</span>
          </summary>
          <ul style={{ fontSize: 13, margin: "4px 0" }}>{writes.map((w) => (
            <li key={w.op}><span className="mono">{w.op}</span>: <span className={w.verdict === "blocked" ? "warn" : ""}>{w.rule}</span></li>
          ))}</ul>
        </details>
      )}
      {also.length > 0 && (
        <details style={{ marginBottom: 8 }}>
          <summary style={{ cursor: "pointer", fontSize: 13 }}>
            <strong>Also runs</strong>{" "}
            <span className="muted">{also.length} fixed check{also.length === 1 ? "" : "s"}, not drafted by the model; listed, not signed</span>
          </summary>
          <ul style={{ fontSize: 13, margin: "4px 0" }}>{also.map((a) => (
            <li key={a.id}><Labels labels={[a.basis]} /> {a.text}</li>
          ))}</ul>
        </details>
      )}
      {[...byPriority.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([p, ls]) => {
        const ids = ls.map((l) => l.id);
        const ticked = ids.filter((id) => !off.has(id)).length;
        const groups = new Map<string, Line[]>();
        for (const l of ls) groups.set(l.ac ?? "", [...(groups.get(l.ac ?? "") ?? []), l]);
        return (
          <details key={p} open={p !== "P2"} className="group" style={{ marginBottom: 6 }}>
            <summary style={{ cursor: "pointer" }}>
              <strong>{PRIORITY_LABEL[p] ?? p}</strong>{" "}
              <span className="muted">{ticked} of {ids.length} ticked</span>{" "}
              <button style={{ marginLeft: 8, fontSize: 12 }} onClick={(e) => { e.preventDefault(); toggle(ids, ticked < ids.length); }}>
                {ticked < ids.length ? "Tick group" : "Untick group"}
              </button>
            </summary>
            {[...groups.entries()].map(([ac, gl]) => (
              <div key={ac} style={{ marginTop: 6 }}>
                <h3>{ac} <span className="muted" style={{ fontWeight: 400 }}>{criteria[ac] ?? ""}</span></h3>
                {gl.map((l) => (
                  <label key={l.id} className="tline">
                    <input type="checkbox" checked={!off.has(l.id)} onChange={() => toggle([l.id], off.has(l.id))} />
                    <span><span className="t">{l.title ?? l.text}</span>{l.summary && <span className="s"> — catches {l.summary}</span>}
                      {breakdown(l.score) && <span className="muted" style={{ fontSize: 12 }}> · {breakdown(l.score)}</span>}</span>
                  </label>
                ))}
              </div>
            ))}
          </details>
        );
      })}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="primary" disabled={n === 0 || submit.isPending} onClick={() => submit.mutate()}>
          {submit.isPending ? "Sending…" : `Generate ${n} test${n === 1 ? "" : "s"}`}
        </button>
        <button onClick={() => setOff(off.size ? new Set() : new Set(lines.map((l) => l.id)))}>{off.size ? "Tick all" : "Untick all"}</button>
        {submit.error && <span className="error">{(submit.error as Error).message}</span>}
      </div>
    </div>
  );
}
