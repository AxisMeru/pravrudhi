// Typed fetch client for the engine's request log: every ask the operator has made and how far the engine has
// got on each. A new file rather than additions to api.ts, so pages built in parallel never contend for that one.

import { ApiError, IS_DEMO, apiBase, engineFetch, localToken } from "./api";

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

// r-e84f8a50: only the operator's local hook (`pravrudhi requests-capture`) could write the requests store; the
// web door had no way to originate an ask at all. State-changing, like every other POST the engine answers: the
// local token is required (see api.ts's localToken and the engine's LocalGuard). `session` is not a parameter
// here — the route derives it from the caller's own identity, never from anything the client sends.
export async function captureRequest(text: string): Promise<RequestItem> {
  if (IS_DEMO) throw new ApiError(501, "/api/requests");
  const token = await localToken();
  const res = await engineFetch(`${apiBase()}/api/requests`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new ApiError(res.status, "/api/requests");
  return (await res.json()) as RequestItem;
}
