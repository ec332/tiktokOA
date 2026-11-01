import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { api, ok } from "../api/client";
import { Loading, Status } from "../components/bits";

export function RunsPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => ok(api.GET("/runs")), refetchInterval: 3000 });
  const [mode, setMode] = useState("live");
  const [unattended, setUnattended] = useState(false);
  const start = useMutation({
    mutationFn: () => ok(api.POST("/runs", { body: { mode, stub: true, unattended } })),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      nav({ to: "/runs/$runId", params: { runId: String(run.id) } });
    },
  });

  return (
    <>
      <h1>Runs</h1>
      <div className="card row" style={{ marginBottom: 16 }}>
        <strong>New stub run</strong>
        <label className="row">
          mode
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option>live</option>
            <option>record</option>
            <option>replay</option>
          </select>
        </label>
        <label className="row">
          <input type="checkbox" checked={unattended} onChange={(e) => setUnattended(e.target.checked)} />
          unattended
        </label>
        <button className="primary" onClick={() => start.mutate()} disabled={start.isPending}>
          Start
        </button>
        {start.error && <span className="error">{start.error.message}</span>}
      </div>
      {!runs.data ? (
        <Loading error={runs.error} />
      ) : runs.data.length === 0 ? (
        <p className="muted">No runs yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Run</th><th>Status</th><th>Mode</th><th>Started</th></tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id}>
                  <td><Link to="/runs/$runId" params={{ runId: String(r.id) }}>{r.name}</Link></td>
                  <td><Status status={r.status} /></td>
                  <td className="muted">{r.mode}{r.stub ? " · stub" : ""}{r.unattended ? " · unattended" : ""}</td>
                  <td className="muted">{r.started}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
