// Typed fetch + join logic for the Candidates page: every candidate the ledger has ever scored, joined against
// the raw `observe` rows (for the night each trial happened) and the closed nights (for track and incumbent). A
// new file so this page's fetch logic does not contend with pages already built against lib/api.ts.
//
// A candidate's `xs` (see pravrudhi_kernel's CandidateView) is already `delta_in`: its measured delta against
// whatever was incumbent at the time of each observation, reset to empty whenever the incumbent it is compared
// against changes (a rebase). That is the one performance number the ledger actually carries per candidate —
// there is no separate untethered "score" — so every number this file derives is built from it or from the raw
// observation rows, never invented.

import { ApiError, IS_DEMO, apiBase, engineFetch, type Candidate } from "./api";
import { fixed } from "./num";

export type { Candidate };

export interface CandidateLedgerEvent {
  seq: number;
  t: string;
  epoch: number;
  night: number;
  kind: string;
  actor: string;
  candidate_id: string | null;
  surface: string | null;
  payload: Record<string, unknown>;
}

export interface CandidateDetail {
  id: string;
  badge: Candidate["badge"];
  view: Omit<Candidate, "id" | "badge">;
  events: CandidateLedgerEvent[];
}

interface ObservationRow {
  seq: number;
  night: number;
  candidate_id: string | null;
  delta: number | null;
}

interface NightTrack {
  night: number;
  track: string;
  incumbent: string | null;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// The demo snapshot predates this page; a recording only carries a `candidates` array once re-exported with
// one, and the raw JSON is read directly (rather than through the typed DemoBundle in lib/demo.ts, which this
// file must not edit) so an older snapshot without the key falls back to an honest empty state instead of
// throwing.
async function demoBundle(): Promise<Record<string, unknown>> {
  const { demo } = await import("./demo");
  return (await demo()) as unknown as Record<string, unknown>;
}

export async function candidatesList(): Promise<Candidate[]> {
  if (IS_DEMO) {
    const rows = (await demoBundle())["candidates"];
    return Array.isArray(rows) ? (rows as Candidate[]) : [];
  }
  return getJSON<Candidate[]>("/api/candidates");
}

export async function candidateDetail(id: string): Promise<CandidateDetail | null> {
  if (IS_DEMO) return null; // no per-candidate ledger events ship in a recorded snapshot
  try {
    return await getJSON<CandidateDetail>(`/api/candidates/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

// Every `observe` row ever written, so each candidate's trials can be placed on a night axis. The server's
// default limit (200) would silently truncate a ledger with more history than that, so a high explicit limit
// is passed instead.
async function observations(): Promise<ObservationRow[]> {
  if (IS_DEMO) return [];
  const events = await getJSON<CandidateLedgerEvent[]>("/api/observations?limit=100000");
  return events.map((e) => {
    const observed = e.payload?.["observed"] as Record<string, unknown> | undefined;
    const delta = observed && typeof observed["delta_in"] === "number" ? (observed["delta_in"] as number) : null;
    return { seq: e.seq, night: e.night, candidate_id: e.candidate_id, delta };
  });
}

async function nightTracks(): Promise<NightTrack[]> {
  if (IS_DEMO) {
    const rows = ((await demoBundle())["nights"] as { night: number; track: string }[] | undefined) ?? [];
    // The exported demo snapshot's night rows do not carry an incumbent id (see public/demo.json); null is the
    // same "absent" value the live API itself uses for a night with none, so this stays honest rather than
    // fabricating one.
    return rows.map((r) => ({ night: r.night, track: r.track, incumbent: null }));
  }
  const rows = await getJSON<{ night: number; track: string; incumbent: string | null }[]>("/api/nights");
  return rows.map((r) => ({ night: r.night, track: r.track, incumbent: r.incumbent }));
}

// A number already screened by lib/num.ts's `fixed`, with an explicit sign prepended. Every number this page
// plots is a delta against an incumbent, so the sign is the point.
export function signedDelta(v: number | null | undefined, digits = 3): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const s = fixed(v, digits);
  return v > 0 ? `+${s}` : s;
}

export interface CandidateRow {
  candidate: Candidate;
  night: number | null; // the night of this candidate's most recent observation; null if never observed
  tracks: string[]; // the track(s) recorded for that night; empty if unknown
  score: number | null; // xs[last]: the most recent measured Δ against the incumbent
  pairedDelta: number | null; // mean(xs): the aggregate paired Δ against the incumbent across current xs
  wasIncumbent: number[]; // nights this candidate id was recorded as the incumbent
}

export interface ObservationPoint {
  candidateId: string;
  night: number;
  seq: number;
  delta: number;
  badge: Candidate["badge"];
}

export interface CandidatesSnapshot {
  candidates: Candidate[];
  rows: CandidateRow[];
  obsPoints: ObservationPoint[];
  tracks: string[];
}

export async function candidatesSnapshot(): Promise<CandidatesSnapshot> {
  const [candidates, obs, nights] = await Promise.all([candidatesList(), observations(), nightTracks()]);

  const nightToTracks = new Map<number, Set<string>>();
  const incumbentToNights = new Map<string, number[]>();
  for (const n of nights) {
    const set = nightToTracks.get(n.night) ?? new Set<string>();
    set.add(n.track);
    nightToTracks.set(n.night, set);
    if (n.incumbent) {
      const arr = incumbentToNights.get(n.incumbent) ?? [];
      arr.push(n.night);
      incumbentToNights.set(n.incumbent, arr);
    }
  }

  const obsByCandidate = new Map<string, ObservationRow[]>();
  const obsPoints: ObservationPoint[] = [];
  const badgeById = new Map(candidates.map((c) => [c.id, c.badge] as const));
  for (const o of obs) {
    if (!o.candidate_id || o.delta === null) continue;
    const arr = obsByCandidate.get(o.candidate_id) ?? [];
    arr.push(o);
    obsByCandidate.set(o.candidate_id, arr);
    obsPoints.push({
      candidateId: o.candidate_id,
      night: o.night,
      seq: o.seq,
      delta: o.delta,
      badge: badgeById.get(o.candidate_id) ?? "grey",
    });
  }
  for (const arr of obsByCandidate.values()) arr.sort((a, b) => a.seq - b.seq);

  const rows: CandidateRow[] = candidates.map((c) => {
    const cObs = obsByCandidate.get(c.id) ?? [];
    const lastNight = cObs.length > 0 ? cObs[cObs.length - 1].night : null;
    const tracks = lastNight !== null ? [...(nightToTracks.get(lastNight) ?? [])].sort() : [];
    const xs = c.xs ?? [];
    const score = xs.length > 0 ? xs[xs.length - 1] : null;
    const pairedDelta = xs.length > 0 ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
    return { candidate: c, night: lastNight, tracks, score, pairedDelta, wasIncumbent: incumbentToNights.get(c.id) ?? [] };
  });

  const tracks = [...new Set(nights.map((n) => n.track))].sort();

  return { candidates, rows, obsPoints, tracks };
}
