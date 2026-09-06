import type { AppetiteDecision, Drive } from "@/lib/appetite";

export function DecisionPanel({ decision, drives }: { decision: AppetiteDecision; drives: Drive[] }) {
  const selectedDrive = drives.find((d) => d.id === decision.selected) ?? null;
  const largestUnmetDrive = drives.find((d) => d.id === decision.largest_unmet) ?? null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">action</p>
          <p className="mt-1 text-sm text-[var(--color-text)]">{decision.action?.description ?? "none"}</p>
          {decision.action?.kind ? (
            <p className="mt-1 text-[11px] text-[var(--color-text-dim)]">
              {decision.action.kind}
              {decision.action.drive ? ` · ${decision.action.drive}` : ""}
            </p>
          ) : null}
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">next wake</p>
          <p className="mt-1 text-sm text-[var(--color-text)]">{decision.next_wake ?? "unscheduled"}</p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">largest unmet drive</p>
          <p className="mt-1 font-mono text-sm text-[var(--color-text)]">
            {largestUnmetDrive?.wire_name ?? decision.largest_unmet ?? "none"}
          </p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">selected drive</p>
          <p className="mt-1 font-mono text-sm text-[var(--color-text)]">
            {selectedDrive?.wire_name ?? decision.selected ?? "none"}
          </p>
        </div>
      </div>
      {decision.resting_reason && (
        <p className="mt-4 border-t border-[var(--color-border)] pt-3 text-xs text-[var(--color-text-dim)]">
          resting — {decision.resting_reason}
        </p>
      )}
      <p className="mt-3 text-[11px] text-[var(--color-text-dim)]">policy {decision.policy_version}</p>
    </div>
  );
}
