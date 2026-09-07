import { Sparkles, Wrench } from "lucide-react";
import type { RunEvent, RunHandle } from "@/lib/api";
import { fixed } from "@/lib/num";
import { asNum, asRecord, asStr, elapsedLabel, gpuBudget, runStatus, statusColor, trackLabel } from "@/lib/run";

export function RunHeader({ handle, events }: { handle: RunHandle; events: RunEvent[] }) {
  const target = asStr(handle.target);
  const night = asNum(handle.night);
  const status = runStatus(handle);
  const request = asRecord(handle.request);
  const policy = asStr(request.policy);
  const budgetReq = asNum(request.budget_gpu_h);
  const budget = gpuBudget(events, budgetReq);
  const startedAt = asNum(handle.started_at);
  const finishedAt = asNum(handle.finished_at);
  const Icon = target === "harness" ? Wrench : Sparkles;

  return (
    <header className="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <Icon size={18} className="shrink-0 text-[var(--color-text-dim)]" />
        <h1 className="text-lg font-semibold text-[var(--color-text)]">
          {trackLabel(target)} {night !== undefined && `· Night ${night}`}
        </h1>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-2.5 py-1 text-xs text-[var(--color-text-dim)]">
          <span className="h-2 w-2 rounded-full" style={{ background: statusColor(status) }} aria-hidden />
          {status}
        </span>
        {policy && (
          <span className="text-xs text-[var(--color-text-dim)]">policy: {policy}</span>
        )}
        <span className="ml-auto text-sm text-[var(--color-text-dim)]">
          elapsed {elapsedLabel(startedAt, finishedAt)}
        </span>
      </div>
      <div className="mt-4">
        <div className="flex items-center justify-between text-xs text-[var(--color-text-dim)]">
          <span>GPU-hours spent</span>
          <span>
            {budget.spent !== null ? fixed(budget.spent, 1) : "—"}
            {budget.budget !== null && ` / ${fixed(budget.budget, 1)} GPU-h budget`}
          </span>
        </div>
        <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-raised)]">
          <div
            className="h-full rounded-full bg-[var(--color-accent)] transition-[width]"
            style={{ width: `${budget.fraction !== null ? Math.round(budget.fraction * 100) : 0}%` }}
          />
        </div>
        {budget.budget === null && (
          <p className="mt-1 text-xs text-[var(--color-text-dim)]">no budget cap was set for this run</p>
        )}
      </div>
    </header>
  );
}
