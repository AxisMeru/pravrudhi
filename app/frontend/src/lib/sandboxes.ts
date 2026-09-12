// Typed fetch client for the engine's sandbox monitor: what each live agent's worktree has touched against its
// declared policy, how much of its wall-clock budget it has spent, and the persisted history of every write a
// policy has forbidden. A new file rather than additions to swarm.ts, so the two views built in parallel never
// contend for that one.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "./api";

export interface SandboxViolation {
  task_id: string;
  path: string;
  allowed_paths: string[];
  at: string;
}

export interface SandboxObservation {
  created: string[];
  modified: string[];
  deleted: string[];
  bytes_written: number;
  allowed_count: number;
  violations: SandboxViolation[];
}

export interface LiveSandbox {
  pid: number;
  kind: string;
  task_id: string;
  worktree: string;
  elapsed_s: number;
  budget_s: number;
  budget_fraction: number | null;
  allowed_paths: string[];
  observation: SandboxObservation;
}

export interface SandboxesSnapshot {
  live: LiveSandbox[];
  recent_violations: SandboxViolation[];
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// Sandbox state is never part of a recording: it describes what a live worktree looks like right now.
export async function sandboxes(): Promise<SandboxesSnapshot> {
  if (IS_DEMO) return { live: [], recent_violations: [] };
  return getJSON<SandboxesSnapshot>("/api/sandboxes");
}
