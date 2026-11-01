/** Stage-specific views, keyed by artifact type and review kind (plan 2.2). */
import type { ArtifactPanel, PanelModule, ReviewPanel } from "./types";

const modules = import.meta.glob<PanelModule>("./*/index.tsx", { eager: true });

function collect<T>(pick: (m: PanelModule) => Record<string, T> | undefined): Record<string, T> {
  const out: Record<string, T> = {};
  for (const [path, mod] of Object.entries(modules)) {
    for (const [key, panel] of Object.entries(pick(mod) ?? {})) {
      if (key in out) throw new Error(`two panels for "${key}" (second in ${path})`);
      out[key] = panel;
    }
  }
  return out;
}

const artifactPanels = collect((m) => m.artifactPanels);
const reviewPanels = collect((m) => m.reviewPanels);

export const artifactPanel = (type: string): ArtifactPanel | undefined => artifactPanels[type];
export const reviewPanel = (kind: string): ReviewPanel | undefined => reviewPanels[kind];
