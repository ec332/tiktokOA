import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ok, type ReviewItem } from "../api/client";
import { Loading, Status } from "../components/bits";
import { JsonTree } from "../components/JsonTree";
import { reviewHeader, reviewPanel } from "../panels/registry";
import type { Resolution } from "../panels/types";

type Line = { id: string; text?: string; comment?: string };
type Choice = { id: string; text?: string };
type LineDecision = { decision?: string; comment: string };

function reviewer(): string {
  try { return localStorage.getItem("tp.reviewer") ?? ""; } catch { return ""; }
}

/** Generic line-level decisions: every line decided on its own, rejections need a comment. */
function LineDecisions({ item, submit, busy }: { item: ReviewItem; submit: (r: Resolution) => void; busy: boolean }) {
  const lines = item.payload.lines as Line[];
  const needsComment = new Set(["reject"]);
  const [state, setState] = useState<Record<string, LineDecision>>({});
  const set = (id: string, patch: Partial<LineDecision>) =>
    setState((s) => ({ ...s, [id]: { ...(s[id] ?? { comment: "" }), ...patch } }));
  const complete = lines.every((l) => {
    const d = state[l.id];
    return d?.decision && (!needsComment.has(d.decision) || d.comment.trim());
  });
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {lines.map((l) => (
        <div key={l.id} className="card" style={{ padding: 8, display: "grid", gap: 6 }}>
          <div><code>{l.id}</code> {l.text}</div>
          {l.comment && <div className="muted">previous comment: {l.comment}</div>}
          <div className="row">
            {item.line_options.map((o) => (
              <label key={o} className="row" style={{ gap: 4 }}>
                <input type="radio" name={`line-${item.id}-${l.id}`} checked={state[l.id]?.decision === o}
                  onChange={() => set(l.id, { decision: o })} />
                {o}
              </label>
            ))}
            <input style={{ flex: 1, minWidth: 140 }} placeholder="comment" value={state[l.id]?.comment ?? ""}
              onChange={(e) => set(l.id, { comment: e.target.value })} />
          </div>
        </div>
      ))}
      <div className="row">
        <button className="primary" disabled={!complete || busy} onClick={() => submit({ detail: { lines: state } })}>
          Submit {lines.length} line{lines.length === 1 ? "" : "s"}
        </button>
        {item.options.includes("defer") && <button disabled={busy} onClick={() => submit({ decision: "defer" })}>defer</button>}
        {!complete && <span className="muted">Decide every line; rejections need a comment.</span>}
      </div>
    </div>
  );
}

/** Generic multiple-choice question with a recommended default. */
function ChoiceDecision({ item, submit, busy }: { item: ReviewItem; submit: (r: Resolution) => void; busy: boolean }) {
  const choices = item.payload.choices as Choice[];
  const recommended = item.payload.recommended as string | undefined;
  const [choice, setChoice] = useState<string | undefined>(recommended);
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {typeof item.payload.text === "string" && <p>{item.payload.text}</p>}
      {choices.map((c) => (
        <label key={c.id} className="row">
          <input type="radio" name={`q-${item.id}`} checked={choice === c.id} onChange={() => setChoice(c.id)} />
          <code>{c.id}</code> {c.text} {c.id === recommended && <span className="badge">recommended</span>}
        </label>
      ))}
      <div className="row">
        <button className="primary" disabled={!choice || busy} onClick={() => submit({ decision: "pick", detail: { choice } })}>Pick</button>
        <button disabled={busy} onClick={() => submit({ decision: "defer" })}>defer</button>
      </div>
    </div>
  );
}

function Detail({ item }: { item: ReviewItem }) {
  const qc = useQueryClient();
  const [by, setBy] = useState(reviewer);
  const [reason, setReason] = useState("");
  const [shownAt] = useState(() => Date.now()); // Detail is keyed by item, so this is per item
  const resolve = useMutation({
    mutationFn: (r: Resolution) =>
      ok(api.POST("/reviews/{item_id}", {
        params: { path: { item_id: item.id } },
        body: {
          by, reason: r.reason ?? reason, decision: r.decision ?? null, detail: r.detail ?? null,
          seconds: Math.round((Date.now() - shownAt) / 100) / 10,
        },
      })),
    onSuccess: () => {
      try { localStorage.setItem("tp.reviewer", by); } catch { /* storage may be blocked */ }
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["run"] });
    },
  });
  const submit = (r: Resolution) => resolve.mutate(r);
  const busy = !by || resolve.isPending;
  const Panel = reviewPanel(item.kind);
  const Header = reviewHeader(item.kind);
  const hasLines = item.line_options.length > 0;
  const hasChoices = item.kind === "question" && Array.isArray(item.payload.choices);

  let decide;
  if (Panel) decide = <Panel item={item} submit={submit} busy={busy} />;
  else if (hasLines) decide = <LineDecisions item={item} submit={submit} busy={busy} />;
  else if (hasChoices) decide = <ChoiceDecision item={item} submit={submit} busy={busy} />;
  else decide = (
    <div className="row">
      {item.options.map((o) => (
        <button key={o} onClick={() => submit({ decision: o })} disabled={busy}>{o.replace("_", " ")}</button>
      ))}
    </div>
  );

  return (
    <div className="card">
      <h2 className="row" style={{ marginTop: 0 }}>#{item.id} {item.kind} <Status status={item.status === "open" ? "awaiting_review" : "succeeded"} /></h2>
      <p className="muted">stage execution #{item.stage_execution}</p>
      {item.status === "open" ? (
        <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
          <input placeholder="your name (required)" value={by} onChange={(e) => setBy(e.target.value)} />
          {Header && <Header item={item} />}
          {!hasLines && !hasChoices && !Panel && (
            <>
              <div className="json"><JsonTree value={item.payload} /></div>
              <textarea placeholder="reason (required for overrides)" value={reason} onChange={(e) => setReason(e.target.value)} />
            </>
          )}
          {decide}
          {resolve.error && <p className="error">{resolve.error.message}</p>}
        </div>
      ) : (
        <>
          <p>Decided <strong>{item.decision}</strong> by {item.decided_by}{item.reason ? `: ${item.reason}` : ""}</p>
          {item.detail && <div className="json"><JsonTree value={item.detail} /></div>}
          <details><summary className="muted">payload</summary><div className="json"><JsonTree value={item.payload} /></div></details>
        </>
      )}
    </div>
  );
}

export function ReviewsPage() {
  const [status, setStatus] = useState<"open" | "all">("open");
  const [selected, setSelected] = useState<number | null>(null);
  const items = useQuery({
    queryKey: ["reviews", status],
    queryFn: () => ok(api.GET("/reviews", { params: { query: { status: status === "all" ? null : status } } })),
    refetchInterval: 3000,
  });
  const current = items.data?.find((i) => i.id === selected) ?? items.data?.[0];
  return (
    <>
      <h1>Review queue</h1>
      <div className="row" style={{ marginBottom: 12 }}>
        <select value={status} onChange={(e) => setStatus(e.target.value as "open" | "all")}>
          <option value="open">open</option>
          <option value="all">all</option>
        </select>
      </div>
      {!items.data ? <Loading error={items.error} /> : items.data.length === 0 ? <p className="muted">Nothing to review.</p> : (
        <div className="grid2">
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {items.data.map((i) => (
                  <tr key={i.id} onClick={() => setSelected(i.id)} style={{ cursor: "pointer", fontWeight: current?.id === i.id ? 600 : 400 }}>
                    <td>#{i.id}</td><td>{i.kind}</td>
                    <td className="muted">{Array.isArray(i.payload.lines) ? `${i.payload.lines.length} lines` : ""}</td>
                    <td className="muted">{i.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {current && <Detail key={current.id} item={current} />}
        </div>
      )}
    </>
  );
}
