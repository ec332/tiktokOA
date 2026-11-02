import { api, ok } from "../api/client";

export type Resolution = { decision?: string | null; detail?: Record<string, unknown> | null; reason?: string };

/** Resolve one review item as "you" (the happy path has one reviewer). */
export function resolveItem(id: number, r: Resolution, seconds?: number) {
  return ok(api.POST("/reviews/{item_id}", {
    params: { path: { item_id: id } },
    body: { by: "you", reason: r.reason ?? "", decision: r.decision ?? null, detail: r.detail ?? null, seconds: seconds ?? null },
  }));
}
