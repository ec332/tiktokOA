import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { ReviewItem } from "../../api/client";
import { resolveItem } from "../../flow/resolve";

/** Stop 2 of the happy path (M8 cases D16): tick the tests to generate; title + one sentence. */
type Line = { id: string; title?: string; summary?: string; ac?: string; text: string };

export function Approve({ item, criteria, onDone }: { item: ReviewItem; criteria: Record<string, string>; onDone: () => void }) {
  const t0 = useRef(Date.now());
  const lines = item.payload.lines as Line[];
  const [off, setOff] = useState<Set<string>>(new Set());
  const groups = new Map<string, Line[]>();
  for (const l of lines) groups.set(l.ac ?? "", [...(groups.get(l.ac ?? "") ?? []), l]);
  const n = lines.length - off.size;
  const submit = useMutation({
    mutationFn: () => resolveItem(item.id, {
      detail: { lines: Object.fromEntries(lines.map((l) => [l.id, { decision: off.has(l.id) ? "exclude" : "approve", comment: "" }])) },
    }, (Date.now() - t0.current) / 1000),
    onSuccess: onDone,
  });
  const attention = (item.payload.attention as string[] | undefined) ?? [];
  return (
    <div className="card">
      {attention.length > 0 && (
        <details style={{ marginBottom: 8 }}>
          <summary className="warn" style={{ cursor: "pointer", fontSize: 13 }}>{attention.length} note{attention.length === 1 ? "" : "s"} from the test design check</summary>
          <ul style={{ fontSize: 13, margin: "4px 0" }}>{attention.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </details>
      )}
      {[...groups.entries()].map(([ac, ls]) => (
        <div key={ac} className="group">
          <h3>{ac} <span className="muted" style={{ fontWeight: 400 }}>{criteria[ac] ?? ""}</span></h3>
          {ls.map((l) => (
            <label key={l.id} className="tline">
              <input type="checkbox" checked={!off.has(l.id)} onChange={() => {
                const s = new Set(off); if (s.has(l.id)) s.delete(l.id); else s.add(l.id); setOff(s);
              }} />
              <span><span className="t">{l.title ?? l.text}</span>{l.summary && <span className="s"> — catches {l.summary}</span>}</span>
            </label>
          ))}
        </div>
      ))}
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
