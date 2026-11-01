import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "/api" });

export type S = components["schemas"];
export type Run = S["Run"];
export type RunDetail = S["RunDetail"];
export type Execution = S["Execution"];
export type ArtifactDetail = S["ArtifactDetail"];
export type ArtifactSummary = S["ArtifactSummary"];
export type ReviewItem = S["ReviewItem"];
export type Event = S["Event"];
export type StageInfo = S["StageInfo"];

/** Unwrap an openapi-fetch result: throw on error so TanStack Query sees it. */
export async function ok<T>(p: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await p;
  if (error !== undefined) throw new Error(JSON.stringify(error));
  return data as T;
}
