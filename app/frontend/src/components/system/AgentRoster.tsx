// Part of section 4: every agent the swarm knows about, and whether it can run on this engine right now — with
// the measured reason when it cannot, never a bare "no".

import { CheckCircle2, XCircle } from "lucide-react";
import type { SwarmAgent } from "@/lib/swarm";

export function AgentRoster({ agents }: { agents: SwarmAgent[] }) {
  if (agents.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">No agents are registered with this engine.</p>;
  }
  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {agents.map((a) => (
        <li
          key={a.name}
          className="flex items-start gap-2 rounded-md border border-[var(--color-border)] p-3 text-xs"
        >
          {a.available ? (
            <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[var(--color-accent)]" />
          ) : (
            <XCircle size={14} className="mt-0.5 shrink-0 text-red-500" />
          )}
          <div>
            <div className="font-mono font-medium text-[var(--color-text)]">{a.name}</div>
            <div className="mt-0.5 text-[var(--color-text-dim)]">{a.reason}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}
