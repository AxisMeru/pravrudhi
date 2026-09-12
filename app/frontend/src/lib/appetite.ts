// Typed fetch client for the engine's appetite view: what it wants right now, how far each drive is from its
// target, and why. A new file rather than additions to api.ts, so pages built in parallel never contend for
// that one — the same reason swarm.ts has its own client.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "./api";

// A drive with `unknown: true` carries no measurement at all — `value`, `target` and `deficit` are null and must
// render as "not measurable yet", never as a zero or a bare dash. `sources` names what a known number was
// measured from, so it can be traced back to the ledger.
export interface Drive {
  id: string;
  wire_name: string;
  value: number | null;
  target: number | null;
  deficit: number | null;
  weight: number;
  eligible: boolean;
  blocked_reason: string | null;
  sources: string[];
  unknown: boolean;
}

export interface AppetiteDecision {
  as_of: string;
  policy_version: string;
  drives: string[];
  largest_unmet: string | null;
  selected: string | null;
  // The engine's chosen action is a record, not a sentence: {kind, drive, description}. Rendering it directly
  // threw React error 31 ("objects are not valid as a React child") and the page showed a crash screen.
  action: { kind?: string; drive?: string; description?: string } | null;
  next_wake: string | null;
  resting_reason: string | null;
}

export interface AppetiteResponse {
  drives: Drive[];
  appetite: AppetiteDecision;
  sentence: string;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// `null` means the recorded snapshot predates the appetite view, not that the engine has nothing to want.
export async function appetite(): Promise<AppetiteResponse | null> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { appetite?: AppetiteResponse };
    return bundle.appetite ?? null;
  }
  return getJSON<AppetiteResponse>("/api/appetite");
}
