// Typed fetch client for the engine's inbox: the review packs waiting for a human sign-off, and the one endpoint
// that records a decision. A new file rather than additions to api.ts, so pages built in parallel never contend
// for that one.
//
// Signing is a human act (see the /inbox/sign handler in server.py): the engine refuses it for an agent identity,
// and — like every other state change — refuses it without the local token (see localguard.py). This module
// reuses `localToken` from api.ts, the same source the settings page's state-changing calls draw from, rather
// than duplicating how that token is fetched or cached.

import { ApiError, IS_DEMO, apiBase, engineFetch, localToken } from "./api";

export type Badge = "grey" | "amber" | "green" | "red";

export interface InboxItem {
  pack: string;
  candidate: string;
  badge: Badge | null;
  night: string;
  signed: boolean;
}

// The paired observations behind a candidate's badge, straight from its replayed ledger view.
export interface CandidateEvidence {
  id: string;
  badge: Badge;
  xs: number[];
  n_obs: number;
  cost_gpu_h: number;
}

export type SignDecision = "approve" | "reject" | "defer";

export interface SignResult {
  seq: number;
  this_hash: string;
  decision: string;
  by: string;
}

// Carries the engine's own refusal text (a 400/403/404 body's `detail`) so the caller can show it verbatim
// instead of a generic failure message.
export class SignError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "SignError";
  }
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// `[]` in demo mode means the recorded snapshot carries no inbox section, not that nothing was ever pending.
export async function inbox(): Promise<InboxItem[]> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { inbox?: InboxItem[] };
    return bundle.inbox ?? [];
  }
  return getJSON<InboxItem[]>("/api/inbox");
}

interface CandidateDetail {
  id: string;
  badge: Badge;
  view: { xs: number[]; n_obs: number; cost_gpu_h: number };
}

// Not available against a recording: a demo has no live ledger to replay a single candidate from.
export async function candidateEvidence(id: string): Promise<CandidateEvidence> {
  if (IS_DEMO) throw new ApiError(501, `/api/candidates/${id}`);
  const detail = await getJSON<CandidateDetail>(`/api/candidates/${encodeURIComponent(id)}`);
  return { id: detail.id, badge: detail.badge, xs: detail.view.xs, n_obs: detail.view.n_obs, cost_gpu_h: detail.view.cost_gpu_h };
}

export async function signInboxItem(
  pack: string,
  decision: SignDecision,
  operator: string,
  note: string,
): Promise<SignResult> {
  if (IS_DEMO) throw new SignError(501, "this is a recorded run: signing needs a local engine");
  const token = await localToken();
  const res = await engineFetch(`${apiBase()}/api/inbox/sign`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
      "x-pravrudhi-operator": operator,
    },
    body: JSON.stringify({ pack, decision, note }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string";
    throw new SignError(res.status, detail ? (body as { detail: string }).detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as SignResult;
}
