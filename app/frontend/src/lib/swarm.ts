// Typed fetch client for the engine's swarm views: which agents exist, where each tier routes today, what has
// been dispatched, and what agent process is running right now. A new file rather than additions to api.ts, so
// pages built in parallel never contend for that one.

import { ApiError, apiBase, IS_DEMO, localToken } from "./api";

export interface SwarmAgent {
  name: string;
  available: boolean;
  reason: string;
}

export interface RoutingRecord {
  route_id: string;
  tier: string;
  trials: number;
  successes: number;
  rate: number;
  lo: number;
  hi: number;
  mean_wall_s: number;
  relative_cost: number;
}

export interface RoutingRow {
  tier: string;
  route: string | null;
  agent: string | null;
  model: string | null;
  relative_cost: number | null;
  reason: string | null;
  records: RoutingRecord[];
  error: string | null;
}

export interface SubagentRun {
  objective: string;
  step: string;
  task_id: string;
  route: string;
  accepted: boolean;
  wall_s: number;
  files: string[];
  reasons: string[];
  at: string;
}

export interface SelfBuildRun {
  task_id: string;
  route: string;
  accepted: boolean;
  wall_s: number;
  files: string[];
  reasons: string[];
  at: string;
}

/** One route as it stands right now: what it costs, how it has done, and whether it can be used at all. */
export interface Seat {
  id: string;
  agent: string;
  model: string;
  relative_cost: number;
  tiers: string[];
  sentinel: boolean;
  trials: number;
  successes: number;
  usable: boolean;
  returns_at: string | null;
  note: string;
}

export interface SwarmSnapshot {
  agents: SwarmAgent[];
  roster?: Seat[];
  routing: RoutingRow[];
  subagent_runs: SubagentRun[];
  selfbuild_runs: SelfBuildRun[];
}

export interface LiveAgent {
  pid: number;
  elapsed_s: number;
  kind: string;
  worktree: string | null;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// `null` means the recorded snapshot predates the swarm view, not that the engine has no swarm to show.
export async function swarm(): Promise<SwarmSnapshot | null> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { swarm?: SwarmSnapshot };
    return bundle.swarm ?? null;
  }
  return getJSON<SwarmSnapshot>("/api/swarm");
}

// Live process state is never part of a recording: it describes what is running on this machine right now.
export async function swarmLive(): Promise<LiveAgent[]> {
  if (IS_DEMO) return [];
  return getJSON<LiveAgent[]>("/api/swarm/live");
}

// The dispatch board: an ad hoc brief the operator hands the swarm directly, rather than one compiled from an
// objective's plan. A job is queued, then run through the same swarm machinery, so its states mirror what
// `application/dispatchboard.py` actually persists.

export type JobState = "queued" | "running" | "accepted" | "rejected" | "cancelled";

export interface Job {
  id: string;
  title: string;
  brief: string;
  allowed_paths: string[];
  validate: string;
  tier: string;
  policy: string;
  agent: string | null;
  state: JobState;
  created: string;
  started: string | null;
  ended: string | null;
  route: string | null;
  accepted: boolean | null;
  reasons: string[];
  files: string[];
  wall_s: number;
}

export interface JobInput {
  title: string;
  brief: string;
  allowed_paths: string[];
  validate: string;
  tier: string;
  policy: string;
  agent?: string | null;
}

// State-changing, like every other POST the engine answers: the local token is required (see api.ts's
// localToken and the engine's LocalGuard), so this is not reused from api.ts only to keep the two clients
// independent, the same reason swarm.ts has its own getJSON.
async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const token = await localToken();
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export async function jobs(): Promise<Job[]> {
  if (IS_DEMO) return [];
  return getJSON<Job[]>("/api/jobs");
}

export async function submitJob(input: JobInput): Promise<Job> {
  if (IS_DEMO) throw new ApiError(501, "/api/jobs");
  return postJSON<Job>("/api/jobs", input);
}

export async function cancelJob(id: string): Promise<Job> {
  if (IS_DEMO) throw new ApiError(501, `/api/jobs/${id}/cancel`);
  return postJSON<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`, {});
}
