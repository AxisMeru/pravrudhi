// The engine's own survival state (`application.svasthya`), with every failing check named and its detail —
// the single most useful thing this page can tell an operator, since a degraded or halted engine explains any
// other card on this page that looks wrong.

import { AlertTriangle, CheckCircle2, PauseCircle, ShieldAlert, XCircle } from "lucide-react";
import type { SvasthyaHealth, SvasthyaState } from "@/lib/machines";

const STATE_COPY: Record<SvasthyaState, { label: string; className: string; Icon: typeof CheckCircle2 }> = {
  ready: { label: "Ready", className: "text-[var(--color-accent)]", Icon: CheckCircle2 },
  degraded: { label: "Degraded", className: "text-[var(--color-warn)]", Icon: AlertTriangle },
  recovering: { label: "Recovering", className: "text-[var(--color-warn)]", Icon: AlertTriangle },
  integrity_halt: { label: "Integrity halt", className: "text-[var(--color-danger)]", Icon: ShieldAlert },
  paused: { label: "Paused by operator", className: "text-[var(--color-text-dim)]", Icon: PauseCircle },
};

function Check({ check }: { check: { name: string; ok: boolean; detail: string; integrity: boolean } }) {
  return (
    <li className="flex items-start gap-2 py-1.5 text-xs">
      {check.ok ? (
        <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[var(--color-accent)]" />
      ) : (
        <XCircle
          size={14}
          className={`mt-0.5 shrink-0 ${check.integrity ? "text-[var(--color-danger)]" : "text-[var(--color-warn)]"}`}
        />
      )}
      <span>
        <span className="font-mono text-[var(--color-text)]">{check.name}</span>
        <span className="text-[var(--color-text-dim)]"> — {check.detail}</span>
      </span>
    </li>
  );
}

export function EngineHealthCard({ health, error }: { health: SvasthyaHealth | null; error: boolean }) {
  if (error) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <p className="text-sm text-[var(--color-text-dim)]">
          Engine health does not report yet on this build — no /api/svasthya route to ask.
        </p>
      </div>
    );
  }
  if (health === null) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <p className="text-sm text-[var(--color-text-dim)]">Checking engine health…</p>
      </div>
    );
  }
  const copy = STATE_COPY[health.state];
  const failing = health.checks.filter((c) => !c.ok);
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-center justify-between">
        <div className={`flex items-center gap-2 text-sm font-medium ${copy.className}`}>
          <copy.Icon size={16} />
          Engine health: {copy.label}
        </div>
        <span className="text-[11px] text-[var(--color-text-dim)]">as of {health.as_of || "—"}</span>
      </div>
      {health.state === "paused" ? (
        <p className="mt-2 text-xs leading-5 text-[var(--color-text-dim)]">{health.reason}</p>
      ) : failing.length === 0 ? (
        <p className="mt-2 text-xs text-[var(--color-text-dim)]">All survival checks pass.</p>
      ) : (
        <ul className="mt-2 divide-y divide-[var(--color-border)]">
          {failing.map((c) => (
            <Check key={c.name} check={c} />
          ))}
        </ul>
      )}
    </div>
  );
}
