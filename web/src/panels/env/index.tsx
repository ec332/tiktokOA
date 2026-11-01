import { useState } from "react";
import type { ArtifactPanel, PanelModule } from "../types";

type Probe = { name: string; ok: boolean; raw: string };
type EnvSpec = {
  variant: string;
  project: string;
  flags: string[];
  spec_hash: string;
  base_url: string;
  reproduce: string;
  probes: Probe[];
  fingerprint: Record<string, unknown>;
};

const show = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

/** env_spec: probes with raw output, fingerprint fields, copyable reproduction command (env D9). */
const EnvSpecPanel: ArtifactPanel = ({ artifact }) => {
  const spec = artifact.body as EnvSpec;
  const [copied, setCopied] = useState(false);
  const copy = () =>
    navigator.clipboard.writeText(spec.reproduce).then(() => setCopied(true), () => setCopied(false));

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="card" style={{ display: "grid", gap: 6 }}>
        <div className="row">
          <strong>{spec.variant}</strong>
          <span className="muted mono">{spec.project}</span>
          {spec.flags.map((f) => <span key={f} className="badge unsigned">{f}</span>)}
        </div>
        <div className="mono trunc" title={spec.spec_hash}>spec_hash {spec.spec_hash}</div>
        <div className="row">
          <code className="trunc" style={{ flex: 1, minWidth: 0 }}>{spec.reproduce}</code>
          <button onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        </div>
      </div>

      <h2>Probes</h2>
      <div className="table-wrap">
        <table className="table">
          <tbody>
            {spec.probes.map((p) => (
              <tr key={p.name}>
                <td style={{ color: p.ok ? "var(--ok)" : "var(--bad)", fontWeight: 600 }}>{p.ok ? "ok" : "failed"}</td>
                <td>{p.name}</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>{p.raw}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Fingerprint</h2>
      <div className="table-wrap">
        <table className="table">
          <tbody>
            {Object.entries(spec.fingerprint).map(([k, v]) => (
              <tr key={k}>
                <th>{k}</th>
                <td className="mono" style={{ wordBreak: "break-all" }}>
                  {v && typeof v === "object" && !Array.isArray(v)
                    ? Object.entries(v).map(([k2, v2]) => <div key={k2}><span className="muted">{k2}</span> {show(v2)}</div>)
                    : show(v)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export const artifactPanels: PanelModule["artifactPanels"] = { env_spec: EnvSpecPanel };
