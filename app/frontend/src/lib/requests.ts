// Typed fetch client for the engine's request log: every ask the operator has made and how far the engine has
// got on each. A new file rather than additions to api.ts, so pages built in parallel never contend for that one.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "./api";

export type CriterionSource = "operator" | "engine";

export interface RequestEvidence {
  kind: string;
  ref: string;
  note: string;
}

export interface RequestCriterion {
  text: string;
  source: CriterionSource;
  met: boolean;
  evidence: RequestEvidence[];
}

export interface RequestItem {
  id: string;
  asked_at: string;
  text: string;
  state: string;
  session: string;
  notes: string;
  staleness_days: number | null;
  progress: [number, number];
  criteria: RequestCriterion[];
}

export interface RequestsResponse {
  total: number;
  open: number;
  by_state: Record<string, number>;
  oldest_open_days: number | null;
  requests: RequestItem[];
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// `null` means the recording carries no requests section, not that the operator never asked anything.
export async function requests(): Promise<RequestsResponse | null> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { requests?: RequestsResponse };
    return bundle.requests ?? null;
  }
  return getJSON<RequestsResponse>("/api/requests");
}
