// What the agents have been doing, newest first.
//
// Adapted from OpenClaw, whose dashboard shows each agent's messages as competitive rounds run. This engine
// dispatched agents and recorded only usage limits and fallbacks, so what the agents actually did — accepted,
// rejected, why, how long — was readable nowhere. The record now exists; this reads it.

import { ApiError, apiBase, IS_DEMO } from "./api";

export interface TraceEntry {
  at: string;
  kind: string;
  summary: string;
  detail: string;
  agent: string;
  objective: string;
}

export async function agentTrace(limit = 100): Promise<TraceEntry[]> {
  if (IS_DEMO) return [];
  const path = `/api/agent-trace?limit=${limit}`;
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return ((await res.json()) as { entries: TraceEntry[] }).entries;
}

// Each kind gets a colour so a wave reads at a glance: what was taken, what was refused, and where a route
// gave out. OpenClaw colours by agent role; this engine's agents differ by outcome rather than by role, so
// that is what is coloured.
export function toneFor(kind: string): string {
  return (
    {
      accepted: "text-[var(--color-accent)]",
      rejected: "text-[var(--color-danger)]",
      limited: "text-[var(--color-warn)]",
      fallback: "text-[var(--color-text-dim)]",
    }[kind] ?? "text-[var(--color-text-dim)]"
  );
}
