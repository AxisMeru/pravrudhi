// Data-shaping for the home page's four bands. Every function here is pure: given what the engine (or the
// recorded demo) actually returned, decide what a band should show, including the honest "nothing yet" case.
// Fetching lives in the page; this file only turns raw responses into what a band renders.

import {
  status as fetchStatus,
  nights as fetchNights,
  models as fetchModels,
  runs as fetchRuns,
  objectives as fetchObjectives,
  type StatusResponse,
  type NightSummary,
  type PromotedModel,
  type RunHandle,
  type Objective,
  type BenchmarkProgress,
} from "./api";
import { appetite as fetchAppetite, type AppetiteResponse } from "./appetite";
import { requests as fetchRequests, type RequestsResponse } from "./requests";
import { swarmLive as fetchSwarmLive, type LiveAgent } from "./swarm";
import { heartbeat as fetchHeartbeat, type HeartbeatBeat } from "./heartbeat";
import { RUNNING_STATUSES, asStr, asNum, runHref } from "./run";
import { objectiveHref, withPairedStats } from "./objective";

export interface HomeData {
  status: StatusResponse | null;
  objectives: Objective[];
  nights: NightSummary[];
  models: PromotedModel[];
  runHandles: RunHandle[];
  appetite: AppetiteResponse | null;
  requests: RequestsResponse | null;
  agentsWorking: LiveAgent[];
  heartbeats: HeartbeatBeat[];
}

function settled<T>(r: PromiseSettledResult<T>, fallback: T): T {
  return r.status === "fulfilled" ? r.value : fallback;
}

// Every source is fetched independently and a failed one falls back to its honest empty value rather than
// taking the whole page down -- a workspace that has never run a night, or an engine that is briefly
// unreachable, must still render the bands that do have data.
export async function loadHome(): Promise<HomeData> {
  const [st, objs, nts, mdls, rns, app, reqs, live, beats] = await Promise.allSettled([
    fetchStatus(),
    fetchObjectives(),
    fetchNights(),
    fetchModels(),
    fetchRuns(),
    fetchAppetite(),
    fetchRequests(),
    fetchSwarmLive(),
    fetchHeartbeat(50),
  ]);
  return {
    status: settled(st, null),
    objectives: settled(objs, { objectives: [], problems: [] }).objectives,
    nights: settled(nts, []),
    models: settled(mdls, []),
    runHandles: settled(rns, []),
    appetite: settled(app, null),
    requests: settled(reqs, null),
    agentsWorking: settled(live, []),
    heartbeats: settled(beats, []),
  };
}

// ---------------------------------------------------------------------------
// Band 1: the result. The largest measured, honestly-labelled improvement across every objective's benchmarks.

export interface BiggestResult {
  objectiveId: string;
  objectiveHref: string;
  benchmark: string;
  scorer: string | null;
  model: string;
  n: number;
  baseline: number;
  current: number;
  delta: number;
  deltaLo: number | null;
  deltaHi: number | null;
  significant: boolean;
  paired: boolean;
  wins: number | null;
  losses: number | null;
  pMcnemar: number | null;
}

// A row only counts once it has a delta against a baseline. Among those, a significant result outranks a
// merely larger one -- an unverified swing is not "the biggest result", it is noise that happens to be big.
function betterRow(a: MeasuredProgress, b: MeasuredProgress): boolean {
  if (a.significant !== b.significant) return a.significant;
  return a.delta > b.delta;
}

// A `BenchmarkProgress` row with its optional fields narrowed to what "measured" guarantees, so the object
// picked as the winner can be read back without every field re-checked against null.
type MeasuredProgress = BenchmarkProgress & {
  delta: number;
  baseline: NonNullable<BenchmarkProgress["baseline"]>;
  latest: NonNullable<BenchmarkProgress["latest"]>;
};

function isMeasured(p: BenchmarkProgress): p is MeasuredProgress {
  return p.state === "measured" && p.delta !== null && p.baseline !== null && p.latest !== null;
}

export function biggestResult(objs: Objective[]): BiggestResult | null {
  let best: { obj: Objective; p: MeasuredProgress } | null = null;
  for (const obj of objs) {
    for (const p of obj.progress) {
      if (!isMeasured(p)) continue;
      if (!best || betterRow(p, best.p)) best = { obj, p };
    }
  }
  if (!best) return null;
  const { obj, p } = best;
  const spec = obj.benchmarks.find((b) => b.id === p.benchmark);
  const paired = withPairedStats(p);
  return {
    objectiveId: obj.id,
    objectiveHref: objectiveHref(obj.id),
    benchmark: p.benchmark,
    scorer: spec?.tool ?? null,
    model: p.latest.model,
    n: p.baseline.n,
    baseline: p.baseline.value,
    current: p.latest.value,
    delta: p.delta,
    deltaLo: p.delta_lo,
    deltaHi: p.delta_hi,
    significant: p.significant,
    paired: paired.paired,
    wins: paired.wins,
    losses: paired.losses,
    pMcnemar: paired.p_mcnemar,
  };
}

// ---------------------------------------------------------------------------
// Band 2: what it is doing now.

export interface RunningRun {
  id: string;
  href: string;
  target: string;
  model: string | null;
  bench: string | null;
  budgetGpuH: number | null;
}

export function runningRun(handles: RunHandle[]): RunningRun | null {
  const running = handles.find((h) => RUNNING_STATUSES.has(asStr(h.status) ?? ""));
  if (!running) return null;
  return {
    id: running.id,
    href: runHref(running.id),
    target: asStr(running.target) ?? "model",
    model: asStr(running.model) ?? null,
    bench: asStr(running.bench) ?? null,
    budgetGpuH: asNum(running.budget_gpu_h) ?? null,
  };
}

export function latestBeat(beats: HeartbeatBeat[]): HeartbeatBeat | null {
  return beats.length ? beats[beats.length - 1] : null;
}

// ---------------------------------------------------------------------------
// Band 3: what it has done.

export interface DoneStrip {
  nightsRun: number;
  gpuHoursSpent: number;
  candidatesScored: number;
  promoted: number;
  openRequests: number;
}

export function doneStrip(data: HomeData): DoneStrip {
  return {
    nightsRun: data.nights.length,
    gpuHoursSpent: data.nights.reduce((sum, n) => sum + (n.spent_gpu_h ?? 0), 0),
    candidatesScored: data.status?.initialised ? data.status.candidates : 0,
    promoted: data.models.length,
    openRequests: data.requests?.open ?? 0,
  };
}
