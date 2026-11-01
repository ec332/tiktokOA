import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useState } from "react";
import { api, ok, type ArtifactSummary } from "../api/client";
import { ArtifactLink, Labels, Loading } from "../components/bits";
import { JsonTree } from "../components/JsonTree";
import { artifactPanel } from "../panels/registry";

export function ArtifactsPage() {
  const [type, setType] = useState("");
  const list = useQuery({
    queryKey: ["artifacts", type],
    queryFn: () => ok(api.GET("/artifacts", { params: { query: { type: type || null } } })),
  });
  return (
    <>
      <h1>Artifacts</h1>
      <div className="row" style={{ marginBottom: 12 }}>
        <input placeholder="filter by type, e.g. cases" value={type} onChange={(e) => setType(e.target.value.trim())} />
      </div>
      {!list.data ? <Loading error={list.error} /> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Artifact</th><th>Stage</th><th>Labels</th><th>Created</th></tr></thead>
            <tbody>
              {list.data.map((a) => (
                <tr key={a.id}>
                  <td><ArtifactLink id={a.id} type={a.type} /></td>
                  <td className="muted">{a.stage ?? "upload"} {a.stage_version}</td>
                  <td><Labels labels={a.labels} /></td>
                  <td className="muted">{a.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Node({ a }: { a: { id: string; type: string; labels: string[] } }) {
  return (
    <div className="card" style={{ padding: 8 }}>
      <div className="trunc"><ArtifactLink id={a.id} type={a.type} /></div>
      <Labels labels={a.labels} />
    </div>
  );
}

export function ArtifactPage() {
  const { id } = useParams({ from: "/artifacts/$id" });
  const art = useQuery({
    queryKey: ["artifact", id],
    queryFn: () => ok(api.GET("/artifacts/{art_id}", { params: { path: { art_id: id } } })),
  });
  const all = useQuery({ queryKey: ["artifacts", ""], queryFn: () => ok(api.GET("/artifacts", { params: { query: {} } })) });
  if (!art.data) return <Loading error={art.error} />;
  const a = art.data;
  const byId = new Map<string, ArtifactSummary>((all.data ?? []).map((x) => [x.id, x]));
  const { body, children, ...manifest } = a;
  const Panel = artifactPanel(a.type);

  return (
    <>
      <h1 className="row">{a.type} <Labels labels={a.labels} /></h1>
      <p className="mono muted" style={{ wordBreak: "break-all" }}>{a.id}</p>

      <h2>Lineage</h2>
      <div className="grid2" style={{ gridTemplateColumns: "1fr auto 1fr", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 6 }}>
          <span className="muted">parents</span>
          {a.parents.length === 0 && <span className="muted">none (root)</span>}
          {a.parents.map((p) => <Node key={p} a={byId.get(p) ?? { id: p, type: "", labels: [] }} />)}
        </div>
        <div style={{ alignSelf: "center" }} className="muted">→ this →</div>
        <div style={{ display: "grid", gap: 6 }}>
          <span className="muted">children</span>
          {children.length === 0 && <span className="muted">none yet</span>}
          {children.map((c) => <Node key={c.id} a={c} />)}
        </div>
      </div>

      {Panel && <Panel artifact={a} />}

      <div className="grid2">
        <div>
          <h2>{Panel ? "Raw body" : "Body"}</h2>
          <div className="card json">
            {Panel ? <details><summary>JSON</summary><JsonTree value={body} /></details> : <JsonTree value={body} />}
          </div>
        </div>
        <div>
          <h2>Manifest</h2>
          <div className="card json"><JsonTree value={manifest} /></div>
        </div>
      </div>
    </>
  );
}
