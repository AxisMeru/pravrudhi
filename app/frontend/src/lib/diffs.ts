// Typed fetch client for the engine's diff viewer: what a dispatched task's worktree actually changed, against
// the commit its branch forked from. A new file rather than additions to api.ts, so pages built in parallel
// never contend for that one -- the same reasoning swarm.ts already followed for its own views.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "./api";

export type DiffLineKind = "context" | "add" | "del";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

export interface FileDiff {
  path: string;
  added: number;
  removed: number;
  hunks: DiffHunk[];
  binary: boolean;
  too_large: boolean;
}

export interface WorktreeDiff {
  files: FileDiff[];
  base: string;
  head: string;
  truncated: boolean;
  reason: string;
}

export interface TaskSummary {
  task_id: string;
  files: number;
  added: number;
  removed: number;
  truncated: boolean;
}

// A recorded snapshot may carry full per-task diffs under `diffs`, added to demo.json after this viewer shipped
// -- so an older recording simply has none, and the page shows an honest empty state rather than throwing.
interface DemoDiffRecord extends WorktreeDiff {
  task_id: string;
}

async function demoDiffRecords(): Promise<DemoDiffRecord[]> {
  const { demo } = await import("./demo");
  const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { diffs?: DemoDiffRecord[] };
  return bundle.diffs ?? [];
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export async function recentDiffs(): Promise<TaskSummary[]> {
  if (IS_DEMO) {
    const records = await demoDiffRecords();
    return records.map((r) => ({
      task_id: r.task_id,
      files: r.files.length,
      added: r.files.reduce((n, f) => n + f.added, 0),
      removed: r.files.reduce((n, f) => n + f.removed, 0),
      truncated: r.truncated,
    }));
  }
  return getJSON<TaskSummary[]>("/api/diffs");
}

export async function diffFor(taskId: string): Promise<WorktreeDiff> {
  if (IS_DEMO) {
    const records = await demoDiffRecords();
    const found = records.find((r) => r.task_id === taskId);
    return found ?? { files: [], base: "", head: "", truncated: false, reason: "not part of this recording" };
  }
  return getJSON<WorktreeDiff>(`/api/diffs/${encodeURIComponent(taskId)}`);
}
