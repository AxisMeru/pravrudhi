// Which coding agents this engine can dispatch to right now, and which are sitting out a vendor usage limit —
// a route that is installed but cooling reads exactly like a route that is simply unavailable unless the two
// are shown apart, with when the cooling one returns.

import { Clock, XCircle } from "lucide-react";
import type { AgentCooldown, AgentStatus } from "@/lib/machines";

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${ok ? "bg-[var(--color-accent)]" : "bg-[var(--color-text-dim)]"}`}
    />
  );
}

export function AgentsPanel({
  agents,
  agentsError,
  cooldowns,
}: {
  agents: AgentStatus[] | null;
  agentsError: boolean;
  cooldowns: AgentCooldown[];
}) {
  if (agentsError) {
    return <p className="text-sm text-[var(--color-text-dim)]">engine does not report agents yet.</p>;
  }
  if (agents === null) {
    return <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>;
  }
  if (agents.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">No coding agent is configured on this engine.</p>;
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <ul className="space-y-2">
        {agents.map((a) => (
          <li key={a.name} className="flex items-start gap-2 text-xs">
            <Dot ok={a.available} />
            <span>
              <span className="font-mono text-[var(--color-text)]">{a.name}</span>
              <span className="text-[var(--color-text-dim)]"> — {a.reason}</span>
            </span>
          </li>
        ))}
      </ul>
      <div>
        <p className="mb-2 text-xs font-medium text-[var(--color-text-dim)]">Cooling down</p>
        {cooldowns.length === 0 ? (
          <p className="flex items-start gap-2 text-xs text-[var(--color-text-dim)]">
            <Clock size={13} className="mt-0.5 shrink-0" />
            No agent is currently cooling down after a usage limit.
          </p>
        ) : (
          <ul className="space-y-2">
            {cooldowns.map((c) => (
              <li key={c.agent} className="flex items-start gap-2 text-xs">
                <XCircle size={13} className="mt-0.5 shrink-0 text-[var(--color-warn)]" />
                <span>
                  <span className="font-mono text-[var(--color-text)]">{c.agent}</span>
                  <span className="text-[var(--color-text-dim)]"> — returns at {c.until}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
