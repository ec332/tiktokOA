import type { ComponentType } from "react";
import type { ArtifactDetail, ReviewItem } from "../api/client";

export type Resolution = { decision?: string | null; detail?: Record<string, unknown>; reason?: string };

/** Replaces the generic JSON view for one artifact type. Raw JSON stays available below it. */
export type ArtifactPanel = ComponentType<{ artifact: ArtifactDetail }>;

/** Replaces the generic decision UI for one review kind. */
export type ReviewPanel = ComponentType<{ item: ReviewItem; submit: (r: Resolution) => void; busy: boolean }>;

/**
 * What a stage's web/src/panels/<stage>/index.tsx exports. Export, don't register:
 * the registry collects these, so stage folders never edit skeleton files.
 */
export type PanelModule = {
  artifactPanels?: Record<string, ArtifactPanel>;
  reviewPanels?: Record<string, ReviewPanel>;
};
