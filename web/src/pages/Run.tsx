import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import { api, ok } from "../api/client";
import { Loading, Status } from "../components/bits";
import { when } from "../flow/time";
import { Flow } from "./Flow";

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
      <Flow run={run.data} />
    </>
  );
}
