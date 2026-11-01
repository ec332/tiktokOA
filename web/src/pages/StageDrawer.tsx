import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ok, type RunDetail } from "../api/client";
import { ArtifactLink, Status } from "../components/bits";

type Source = "latest" | "run" | "upload";

export function StageDrawer({ run, stage, onClose }: { run: RunDetail; stage: string; onClose: () => void }) {
  const qc = useQueryClient();
  const execs = run.executions.filter((e) => e.stage === stage);
  const latest = execs[execs.length - 1];
  const stages = useQuery({ queryKey: ["stages"], queryFn: () => ok(api.GET("/stages")) });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => ok(api.GET("/runs")) });
  const events = useQuery({
    queryKey: ["events", run.id],
    queryFn: () => ok(api.GET("/runs/{run_id}/events/list", { params: { path: { run_id: run.id } } })),
  });
  const consumes = Object.keys(stages.data?.find((s) => s.name === stage)?.consumes ?? {});
  const myEvents = (events.data ?? []).filter((e) => execs.some((x) => x.id === e.stage_execution));

  const [source, setSource] = useState<Source>("latest");
  const [fromRun, setFromRun] = useState<number | null>(null);
  const [uploads, setUploads] = useState<Record<string, string>>({}); // type -> artifact id
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function upload(type: string, file: File) {
    setUploadError(null);
    try {
      const body = JSON.parse(await file.text());
      const art = await ok(api.POST("/artifacts", { body: { type, body, schema_version: 1 } }));
      setUploads((u) => ({ ...u, [type]: art.id }));
    } catch (e) {
      setUploadError(`${file.name}: ${(e as Error).message}`);
    }
  }

  const rerun = useMutation({
    mutationFn: () =>
      ok(
        api.POST("/runs/{run_id}/stages/{name}/rerun", {
          params: { path: { run_id: run.id, name: stage } },
          body: {
            inputs: source === "upload" ? uploads : {},
            from_run: source === "run" ? fromRun : null,
          },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", run.id] }),
  });
  const canRerun =
    !rerun.isPending && (source !== "run" || fromRun !== null) &&
    (source !== "upload" || Object.keys(uploads).length > 0);

  return (
    <aside className="drawer" aria-label={`${stage} stage`}>
      <button className="close" onClick={onClose} aria-label="Close">✕</button>
      <h1 className="row">{stage} {latest && <Status status={latest.status} />}</h1>
      {latest?.reason && <p className="error">{latest.reason}</p>}

      <div className="grid2">
        <div>
          <h2>Inputs</h2>
          {latest && Object.keys(latest.inputs).length ? (
            Object.entries(latest.inputs).map(([t, a]) => <div key={t} className="trunc"><ArtifactLink id={a} type={t} /></div>)
          ) : <p className="muted">none</p>}
        </div>
        <div>
          <h2>Outputs</h2>
          {latest && Object.keys(latest.outputs).length ? (
            Object.entries(latest.outputs).map(([t, a]) => <div key={t} className="trunc"><ArtifactLink id={a} type={t} /></div>)
          ) : <p className="muted">none</p>}
        </div>
      </div>

      <h2>Re-run this stage</h2>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label className="row"><input type="radio" checked={source === "latest"} onChange={() => setSource("latest")} /> latest artifacts in this run</label>
        <label className="row">
          <input type="radio" checked={source === "run"} onChange={() => setSource("run")} /> inputs from run
          <select value={fromRun ?? ""} onChange={(e) => { setSource("run"); setFromRun(e.target.value ? Number(e.target.value) : null); }}>
            <option value="">choose…</option>
            {runs.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
        <label className="row"><input type="radio" checked={source === "upload"} onChange={() => setSource("upload")} /> upload fixtures (JSON body per input)</label>
        {source === "upload" && consumes.map((t) => (
          <label key={t} className="row" style={{ marginLeft: 24 }}>
            <code>{t}</code>
            <input type="file" accept="application/json,.json" onChange={(e) => e.target.files?.[0] && upload(t, e.target.files[0])} />
            {uploads[t] && <ArtifactLink id={uploads[t]} />}
          </label>
        ))}
        {source === "upload" && consumes.length === 0 && <p className="muted">This stage consumes nothing.</p>}
        {uploadError && <p className="error">{uploadError}</p>}
        <div className="row">
          <button className="primary" disabled={!canRerun} onClick={() => rerun.mutate()}>Re-run {stage}</button>
          <span className="muted">Downstream stages that consumed the old output become stale.</span>
        </div>
        {rerun.error && <p className="error">{rerun.error.message}</p>}
      </div>

      <h2>Executions</h2>
      {execs.map((e) => (
        <div key={e.id} className="row mono">
          #{e.id} <Status status={e.status} /> {e.stage_version ?? ""} <span className="muted">{e.started ?? ""}</span>
        </div>
      ))}

      <h2>Events</h2>
      <div className="events mono">
        {myEvents.length === 0 && <p className="muted">none</p>}
        {myEvents.map((e) => (
          <div key={e.id}>
            <span className="muted">{e.at.slice(11, 19)}</span> #{e.stage_execution} {e.kind}{" "}
            <span className="muted">{Object.keys(e.payload).length ? JSON.stringify(e.payload) : ""}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}
