import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api, ok, type Execution } from "../api/client";
import { ArtifactLink, Loading, Status } from "../components/bits";
import { StageDrawer } from "./StageDrawer";

const PIPELINE = ["spec", "cases", "generate", "gate", "env", "execute", "triage", "report", "retest"];

function duration(e: Execution) {
  if (!e.started || !e.finished) return "";
  const ms = Date.parse(e.finished) - Date.parse(e.started);
  return `${(ms / 1000).toFixed(0)}s`;
}

export function RunPage() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const id = Number(runId);
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
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
    };
    return () => es.close();
  }, [id, qc]);

  if (!run.data) return <Loading error={run.error} />;
  const latest = new Map<string, Execution>();
  for (const e of run.data.executions) latest.set(e.stage, e);

  return (
    <>
      <h1 className="row">
        {run.data.name} <Status status={run.data.status} />
        <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>
          {run.data.mode}{run.data.stub ? " · stub" : ""}{run.data.unattended ? " · unattended" : ""}
        </span>
      </h1>
      {run.data.status === "awaiting_review" && (
        <p className="card">
          Waiting on a human decision. <Link to="/reviews">Open the review queue →</Link>
        </p>
      )}
      <div className="pipeline">
        {PIPELINE.map((name, i) => {
          const e = latest.get(name);
          return (
            <button
              key={name}
              className={`card stage ${selected === name ? "selected" : ""}`}
              onClick={() => setSelected(name)}
            >
              <span className="muted">{i + 1}</span>
              <span className="name">{name}</span>
              <Status status={e?.status ?? "pending"} />
              <span className="muted mono">{e?.stage_version ?? "—"} {e && duration(e)}</span>
              {e && Object.entries(e.outputs).map(([t, a]) => (
                <span key={t} className="trunc" onClick={(ev) => ev.stopPropagation()}>
                  <ArtifactLink id={a} type={t} />
                </span>
              ))}
            </button>
          );
        })}
      </div>
      {selected && (
        <StageDrawer run={run.data} stage={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
