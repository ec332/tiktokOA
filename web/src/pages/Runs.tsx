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
  const [stub, setStub] = useState(true);
  const [kind, setKind] = useState<"none" | "story" | "brief">("none");
  const [text, setText] = useState("");
  const requirements = kind === "none" ? null : { kind, text };
  const start = useMutation({
    mutationFn: () => ok(api.POST("/runs", { body: { mode, stub, unattended, requirements } })),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      nav({ to: "/runs/$runId", params: { runId: String(run.id) } });
    },
  });

  return (
    <>
      <h1>Runs</h1>
      <div className="card" style={{ marginBottom: 16, display: "grid", gap: 8 }}>
      <div className="row">
        <strong>New run</strong>
        <label className="row">
          <input type="checkbox" checked={stub} onChange={(e) => setStub(e.target.checked)} />
          stubs
        </label>
        <label className="row">
          requirements
          <select value={kind} onChange={(e) => setKind(e.target.value as "none" | "story" | "brief")}>
            <option value="none">none</option>
            <option value="story">full user story</option>
            <option value="brief">vague brief</option>
          </select>
        </label>
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
        <button
          className="primary"
          onClick={() => start.mutate()}
          disabled={start.isPending || (kind !== "none" && !text.trim())}
        >
          Start
        </button>
        {start.error && <span className="error">{start.error.message}</span>}
      </div>
      {kind !== "none" && (
        <textarea
          rows={kind === "story" ? 10 : 2}
          placeholder={kind === "story" ? "Paste the full user story (markdown)" : "e.g. test item listing and pagination"}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      )}
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
