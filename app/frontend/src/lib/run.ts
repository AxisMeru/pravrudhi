// Shared logic for the run detail page: turning a run's raw event stream into what a person
// watching actually needs — elapsed time, GPU-hours spent against budget, and the state of each
// candidate as it is scored. Kept out of the page and components so both stay about rendering.

import type { RunEvent, RunHandle } from "./api";

export function asStr(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}
export function asNum(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}
export function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

export const RUNNING_STATUSES = new Set(["running", "stopping"]);

export function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "var(--color-accent)";
    case "stopping":
      return "var(--color-warn)";
    case "failed":
      return "var(--color-danger)";
    default:
      return "var(--color-text-dim)";
  }
}

export function trackLabel(target: string | undefined): string {
  if (target === "harness") return "Harness";
  if (target === "model") return "LoRA";
  return target ?? "unknown";
}

// mm:ss under an hour, "XhYYm" past it. "—" for a handle whose start time never arrived, which
// happens for a run record from an engine build too old to have recorded one.
export function elapsedLabel(startedAt: number | undefined, endedAt: number | undefined): string {
  if (startedAt === undefined) return "—";
  const end = endedAt ?? Date.now() / 1000;
  const totalSeconds = Math.max(0, Math.floor(end - startedAt));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

export interface GpuBudget {
  budget: number | null;
  spent: number | null;
  remaining: number | null;
  fraction: number | null; // 0..1; null when there is nothing to measure spend against
}

// The engine's own log only ever reports what is left after a round, never what has been spent —
// so spend is derived here once, from the budget the run was started with and the most recent
// `round` event, rather than re-derived by every consumer.
export function gpuBudget(events: RunEvent[], budgetGpuH: number | undefined): GpuBudget {
  const budget = typeof budgetGpuH === "number" && Number.isFinite(budgetGpuH) ? budgetGpuH : null;
  let remaining: number | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type === "round" && typeof e.remaining_gpu_h === "number") {
      remaining = e.remaining_gpu_h;
      break;
    }
  }
  if (budget === null || remaining === null) return { budget, spent: null, remaining, fraction: null };
  const spent = Math.max(0, budget - remaining);
  const fraction = budget > 0 ? Math.min(1, spent / budget) : null;
  return { budget, spent, remaining, fraction };
}

export interface CandidateRow {
  candidate: string;
  incumbent?: number;
  candidateScore?: number;
  delta?: number;
  decision?: string;
  n?: number;
  t?: number;
  promoted: boolean;
}

export interface CandidateState {
  incumbent: number | null;
  current: CandidateRow | null; // most recently scored candidate
  best: CandidateRow | null; // best delta seen this run so far
  rows: CandidateRow[]; // most recent first
}

// Folds the event stream into what the candidate panel needs, so the panel itself never re-scans
// the whole history on every tick of a long-running night.
export function candidateState(events: RunEvent[]): CandidateState {
  const byCandidate = new Map<string, CandidateRow>();
  const order: CandidateRow[] = [];
  let incumbent: number | null = null;
  for (const e of events) {
    if (e.type === "paired" && e.candidate) {
      incumbent = e.incumbent ?? incumbent;
      const row: CandidateRow = {
        candidate: e.candidate,
        incumbent: e.incumbent,
        candidateScore: e.candidate_score,
        delta: e.delta,
        decision: e.decision,
        n: e.n,
        t: e.t,
        promoted: false,
      };
      byCandidate.set(e.candidate, row);
      order.push(row);
    }
    if (e.type === "promoted" && e.candidate) {
      const row = byCandidate.get(e.candidate);
      if (row) row.promoted = true;
    }
  }
  const rows = [...order].reverse();
  const best = rows.reduce<CandidateRow | null>(
    (b, r) => (r.delta !== undefined && (b === null || r.delta > (b.delta ?? -Infinity)) ? r : b),
    null,
  );
  return { incumbent, current: rows[0] ?? null, best, rows };
}

// The run's status text, if the caller only has a loosely-typed RunHandle to work from.
export function runStatus(handle: RunHandle): string {
  return asStr(handle.status) ?? "unknown";
}

// A run id is a runtime UUID, so a static export cannot pre-render `/runs/<id>` directly (see
// runs/[id]/page.tsx). Every link to a run's detail page goes through this one function so that
// the actual routing scheme — a fixed path plus a `run` query parameter — lives in exactly one place.
export function runHref(id: string): string {
  return `/runs/view?run=${encodeURIComponent(id)}`;
}
