import { fixed } from "@/lib/num";

// `met` counts criteria that carry evidence. That is not the same as verified: verification re-runs every
// reference and puts the whole request to an adversarial reviewer, which has refused claims that looked complete
// here. An adversarial reviewer pointed out that the published dashboard showed 6 of 6 met on a request the gate
// had not passed, which reads as done. So a full bar that has not been verified says so.
export function ProgressBar({ met, total, state }: { met: number; total: number; state?: string }) {
  const pct = total > 0 ? Math.round((met / total) * 100) : 0;
  const complete = total > 0 && met === total;
  const verified = (state ?? "").toLowerCase() === "verified";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-[var(--color-surface-raised)]">
        <div
          className="h-full rounded-full bg-[var(--color-accent)]"
          style={{ width: `${total > 0 ? pct : 0}%` }}
        />
      </div>
      <span className="whitespace-nowrap font-mono text-[11px] text-[var(--color-text-dim)]">
        {fixed(met, 0)}/{fixed(total, 0)}
        {complete && !verified ? (
          <span className="ml-2 text-[11px] text-[var(--color-text-dim)]">
            evidence attached, not yet verified
          </span>
        ) : null}
      </span>
    </div>
  );
}
