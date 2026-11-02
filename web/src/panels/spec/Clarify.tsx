import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { ReviewItem } from "../../api/client";
import { resolveItem, type Resolution } from "../../flow/resolve";

/** Stop 1 of the happy path (M8 spec D16): questions and "is this intended?" checks, inline. */
type Choice = { id: string; text: string };
const isOther = (r?: Resolution) => r?.decision === "pick" && (r.detail as { choice?: string } | null)?.choice === "other";

export function Clarify({ items, onDone }: { items: ReviewItem[]; onDone: () => void }) {
  const t0 = useRef(Date.now());
  const [picks, setPicks] = useState<Record<number, Resolution>>(() =>
    Object.fromEntries(items.filter((i) => i.kind === "question" && i.payload.recommended)
      .map((i) => [i.id, { decision: "pick", detail: { choice: i.payload.recommended as string } }])));
  const submit = useMutation({
    mutationFn: async () => {
      const secs = (Date.now() - t0.current) / 1000 / items.length;
      for (const i of items) await resolveItem(i.id, picks[i.id], secs);
    },
    onSuccess: onDone,
  });
  const set = (id: number, r: Resolution) => setPicks({ ...picks, [id]: r });
  const done = items.every((i) => picks[i.id] && !(isOther(picks[i.id]) && !(picks[i.id].detail as { text: string }).text.trim()));

  return (
    <div className="card">
      {items.map((i) => {
        const p = i.payload as Record<string, unknown>;
        const cur = picks[i.id];
        if (i.kind === "question") {
          const choices = p.choices as Choice[];
          return (
            <div key={i.id} className="q">
              <div>{p.text as string}</div>
              <div className="opts">
                {choices.filter((c) => c.id !== "other").map((c) => {
                  const on = cur?.decision === "pick" && (cur.detail as { choice: string }).choice === c.id;
                  return (
                    <button key={c.id} className={`opt ${on ? "on" : ""}`} aria-pressed={on}
                      onClick={() => set(i.id, { decision: "pick", detail: { choice: c.id } })}>
                      {c.text}{c.id === p.recommended ? " (recommended)" : ""}
                    </button>
                  );
                })}
                {choices.some((c) => c.id === "other") && (
                  <button className={`opt ${isOther(cur) ? "on" : ""}`} aria-pressed={isOther(cur)}
                    onClick={() => set(i.id, { decision: "pick", detail: { choice: "other", text: "" } })}>
                    Other…
                  </button>
                )}
                <button className={`opt ${cur?.decision === "defer" ? "on" : ""}`} onClick={() => set(i.id, { decision: "defer" })}>
                  Let the platform assume
                </button>
              </div>
              {isOther(cur) && (
                <input type="text" autoFocus placeholder="Describe what should happen, e.g. 0 to 100 inclusive is accepted; anything else returns 422"
                  value={(cur!.detail as { text: string }).text}
                  onChange={(e) => set(i.id, { decision: "pick", detail: { choice: "other", text: e.target.value } })} />
              )}
            </div>
          );
        }
        // confirm_observed: behaviour read from the code, not from the requirements
        const opts: [string, string][] = [["confirm", "Yes, intended"], ["reject", "No, that's wrong"], ["out_of_scope", "Not part of this feature"]];
        return (
          <div key={i.id} className="q">
            <div>
              <span className="muted">The code does this; is it intended? </span>
              {(p.title as string) ?? ""}{p.then ? `: ${p.then as string}` : ""}
              {p.status_code ? <span className="mono"> → {String(p.status_code)}</span> : null}
            </div>
            <div className="opts">
              {opts.map(([d, label]) => (
                <button key={d} className={`opt ${cur?.decision === d ? "on" : ""}`} aria-pressed={cur?.decision === d}
                  onClick={() => set(i.id, { decision: d })}>{label}</button>
              ))}
            </div>
          </div>
        );
      })}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="primary" disabled={!done || submit.isPending} onClick={() => submit.mutate()}>
          {submit.isPending ? "Sending…" : `Submit ${items.length} answer${items.length === 1 ? "" : "s"}`}
        </button>
        {!done && <span className="muted">Answer each one, or let the platform assume.</span>}
        {submit.error && <span className="error">{(submit.error as Error).message}</span>}
      </div>
    </div>
  );
}
