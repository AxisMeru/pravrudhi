// What the agents have been doing, newest first.
//
// Adapted from OpenClaw, whose dashboard shows each agent's messages as competitive rounds run. This engine
// dispatched agents and recorded only usage limits and fallbacks, so what the agents actually did — accepted,
// rejected, why, how long — was readable nowhere. The record now exists; this reads it.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "./api";

export interface TraceEntry {
  at: string;
  kind: string;
  summary: string;
  detail: string;
  agent: string;
  objective: string;
}

export async function agentTrace(limit = 100): Promise<TraceEntry[]> {
  // The published site runs in demo mode, where returning [] rendered "No agent activity recorded yet" on the one
  // page that shows what the agents did — while the engine had a full trace on disk. The snapshot carries it now,
  // so demo reads the record rather than claiming there isn't one.
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { agent_trace?: TraceEntry[] };
    return (bundle.agent_trace ?? []).slice(-limit).reverse();
  }
  const path = `/api/agent-trace?limit=${limit}`;
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
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
