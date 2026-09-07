// Data helpers for the objective detail page: routing to it, and joining the runs and candidates that were
// scored under this objective's track -- the only association the ledger records between an objective and the
// work it produced. Kept out of lib/api.ts so this join logic does not contend with pages already built against
// it, the same reason lib/candidates.ts keeps its own fetch path instead of extending api.ts's.

import { ApiError, apiBase, IS_DEMO, runs as fetchRuns, type BenchmarkProgress, type RunHandle } from "./api";
import { candidatesSnapshot, type CandidateRow } from "./candidates";
import { percent } from "./num";

export function objectiveHref(id: string): string {
  return `/objectives/detail?id=${encodeURIComponent(id)}`;
}

// `Progress.to_dict()` (application/objectives.py) always carries these fields via `asdict`, but lib/api.ts's
// `BenchmarkProgress` predates the paired McNemar comparison and never declared them. The values are really on
// every row the engine or the demo snapshot sends; this reads them without editing api.ts.
export interface PairedProgress extends BenchmarkProgress {
  paired: boolean;
  wins: number | null;
  losses: number | null;
  p_mcnemar: number | null;
}

export function withPairedStats(p: BenchmarkProgress): PairedProgress {
  const row = p as Partial<PairedProgress>;
  return {
    ...p,
    paired: row.paired ?? false,
    wins: row.wins ?? null,
    losses: row.losses ?? null,
    p_mcnemar: row.p_mcnemar ?? null,
  };
}

// A percentage already screened by lib/num.ts's `percent`, with an explicit sign prepended -- every delta this
// page shows is a change against a baseline, so the sign is the point (mirrors lib/candidates.ts's signedDelta).
export function signedPercent(v: number | null | undefined, digits = 1): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const s = percent(v, digits);
  return v > 0 ? `+${s}` : s;
}

interface NightTrack {
  night: number;
  track: string;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

async function nightTracks(): Promise<NightTrack[]> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const rows = ((await demo()) as unknown as Record<string, unknown>)["nights"];
    return Array.isArray(rows) ? (rows as NightTrack[]) : [];
  }
  return getJSON<NightTrack[]>("/api/nights");
}

function nightOf(handle: RunHandle): number | undefined {
  const v = (handle as Record<string, unknown>).night;
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

export interface ObjectiveActivity {
  runs: RunHandle[];
  candidates: CandidateRow[];
}

// Everything scored on this objective's track: a run whose night was recorded against the track, and a
// candidate whose most recently observed night was. Neither a `Candidate` nor a `RunHandle` names an objective
// directly -- the track, joined through the night it was scored on, is the only link the ledger supports.
export async function objectiveActivity(track: string): Promise<ObjectiveActivity> {
  const [allRuns, nights, snapshot] = await Promise.all([
    fetchRuns().catch(() => [] as RunHandle[]),
    nightTracks().catch(() => [] as NightTrack[]),
    candidatesSnapshot().catch(() => ({ candidates: [], rows: [], obsPoints: [], tracks: [] })),
  ]);
  const nightsOnTrack = new Set(nights.filter((n) => n.track === track).map((n) => n.night));
  return {
    runs: allRuns.filter((r) => {
      const night = nightOf(r);
      return night !== undefined && nightsOnTrack.has(night);
    }),
    candidates: snapshot.rows.filter((row) => row.tracks.includes(track)),
  };
}
