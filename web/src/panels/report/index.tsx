import type { ArtifactPanel, PanelModule } from "../types";

/** The same static report.html, so there is one view to maintain (design D9). */
const ReportPanel: ArtifactPanel = ({ artifact }) => {
  const r = artifact.body as { html: string; path: string };
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <span className="muted mono">{r.path}</span>
      <iframe title="report" srcDoc={r.html} sandbox="" style={{ width: "100%", height: "80vh", border: "1px solid var(--line, #ddd)" }} />
    </div>
  );
};

export const artifactPanels: PanelModule["artifactPanels"] = { report: ReportPanel };
