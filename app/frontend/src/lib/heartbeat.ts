// Typed fetch client for the engine's heartbeat log: what the loop looked at each beat, what it chose to
// dispatch (if anything), why, and what came back. A new file rather than additions to api.ts, so pages built
// in parallel never contend for that one.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "./api";

export interface HeartbeatChoice {
  objective: string;
  step: string;
}

// A beat's result is not one shape. A dispatch records an agent, a wall time and the files it touched; a survey
// beat records what it looked at and found nothing to do, and carries none of those fields. Declaring them all
// as present let the timeline read `files.length` on a survey beat and crash the page, so every field a caller
// must check for itself is optional here rather than promised.
export interface HeartbeatResult {
  accepted?: boolean;
  agent?: string;
  wall_s?: number | null;
  files?: string[];
  reasons?: string[];
  kind?: string;
  sources?: string[];
}

export interface HeartbeatBeat {
  at: string;
  looked_at: string[];
  chose: HeartbeatChoice | null;
  reason: string;
  result: HeartbeatResult | null;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// The heartbeat log may not exist yet on an older engine build, and the endpoint itself is being added
// separately — either shows up as a 404 or a network error, and both mean the same thing to this page: no
// beats to show yet. So the empty state is the only failure mode a caller ever sees; nothing here throws.
export async function heartbeat(n = 100): Promise<HeartbeatBeat[]> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { heartbeat?: HeartbeatBeat[] };
    return bundle.heartbeat ?? [];
  }
  try {
    const { beats } = await getJSON<{ beats: HeartbeatBeat[] }>(`/api/heartbeat?n=${n}`);
    return beats;
  } catch {
    return [];
  }
}
