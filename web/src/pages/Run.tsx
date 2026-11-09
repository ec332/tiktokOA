import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import { api, ok, type S } from "../api/client";
import { Labels, Loading, Status } from "../components/bits";
import { when } from "../flow/time";
import { Flow } from "./Flow";

const frac = (c: S["Count"]) => (c.d ? `${c.n}/${c.d}` : `${c.n}`);

/** The claim-first header (cohesion/001 D5): target · reach · isolation · ceiling · what ran,
 *  and what the results rest on. The report's header comes from the same function. */
function ClaimLine({ id }: { id: number }) {
  const c = useQuery({
    queryKey: ["claim", id],
    queryFn: () => ok(api.GET("/runs/{run_id}/claim", { params: { path: { run_id: id } } })),
  }).data;
  if (!c) return null;
  const ran = c.ran
    ? `ran ${c.ran.n} of ${c.ran.d}` + (c.blocked.length ? `; blocked ▸ ${c.blocked.map((b) => `${b.why} ${frac(b.cases)}`).join("; ")}` : "")
    : "not run yet";
  const level = c.isolation == null ? null : `isolation ${c.isolation} (${c.isolation_words})`;
  return (
    <p className="row" style={{ fontSize: 13, gap: 6, flexWrap: "wrap" }} aria-label="What this run claims">
      <strong>{[c.target ?? "no target", c.reach, level, c.ceiling, ran].filter(Boolean).join(" · ")}</strong>
      {Object.keys(c.basis).length > 0 && <span className="muted">rests on</span>}
      {Object.entries(c.basis).map(([b, n]) => (
        <span key={b} className="row" style={{ gap: 2, display: "inline-flex" }}><Labels labels={[b]} /> {frac(n)}</span>
      ))}
    </p>
  );
}

export function RunPage() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const id = Number(runId);
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => ok(api.GET("/runs/{run_id}", { params: { path: { run_id: id } } })),
  });

  // Live updates: every event refreshes the run; EventSource reconnects when the stream ends.
  useEffect(() => {
    const es = new EventSource(`/api/runs/${id}/events`);
    es.onmessage = () => {
      qc.invalidateQueries({ queryKey: ["run", id] });
      qc.invalidateQueries({ queryKey: ["events", id] });
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["claim", id] });
    };
    return () => es.close();
  }, [id, qc]);

  if (!run.data) return <Loading error={run.error} />;
  return (
    <>
      <h1 className="row">
        {run.data.name} <Status status={run.data.status} />
        <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>started {when(run.data.started)}</span>
      </h1>
      <ClaimLine id={id} />
      <Flow run={run.data} />
    </>
  );
}
