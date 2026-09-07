// One benchmark's full standing: baseline and current value, the state word when there is nothing to compare
// (unmeasured / baseline only), and -- when the ledger carries paired per-item vectors -- the honest McNemar
// result stated as wins, losses and a p-value, never collapsed to a bare delta that hides how much evidence
// backs it.

import { CircleDashed, Minus, TrendingDown, TrendingUp } from "lucide-react";
import type { BenchmarkProgress } from "@/lib/api";
import { fixed, percent } from "@/lib/num";
import { signedPercent, withPairedStats } from "@/lib/objective";

function Verdict({ p }: { p: BenchmarkProgress }) {
  if (p.state !== "measured" || p.delta === null) return null;
  if (!p.significant) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-[var(--color-text-dim)]">
        <Minus size={15} /> not distinguishable from no change
      </span>
    );
  }
  const up = p.delta > 0;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-sm font-medium ${
        up ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"
      }`}
    >
      {up ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
      {up ? "improvement" : "regression"}
    </span>
  );
}

function Bar({ p }: { p: BenchmarkProgress }) {
  if (p.state === "unmeasured" || !p.baseline) return null;
  const base = p.baseline.value;
  const now = p.latest ? p.latest.value : base;
  const top = Math.max(base, now, 0.0001);
  const scale = (v: number) => `${Math.max(2, (v / top) * 100)}%`;
  return (
    <div className="mt-4 space-y-2">
      <div className="flex items-center gap-3 text-xs">
        <span className="w-16 shrink-0 text-[var(--color-text-dim)]">baseline</span>
        <div className="h-2 flex-1 rounded-full bg-[var(--color-surface-raised)]">
          <div className="h-2 rounded-full bg-[var(--color-text-dim)]" style={{ width: scale(base) }} />
        </div>
        <span className="w-16 shrink-0 text-right tabular-nums text-[var(--color-text)]">{percent(base)}</span>
      </div>
      {p.latest && (
        <div className="flex items-center gap-3 text-xs">
          <span className="w-16 shrink-0 text-[var(--color-text-dim)]">current</span>
          <div className="h-2 flex-1 rounded-full bg-[var(--color-surface-raised)]">
            <div
              className={`h-2 rounded-full ${
                p.significant && p.delta !== null && p.delta > 0
                  ? "bg-[var(--color-accent)]"
                  : p.significant
                    ? "bg-[var(--color-danger)]"
                    : "bg-[var(--color-text-dim)]"
              }`}
              style={{ width: scale(now) }}
            />
          </div>
          <span className="w-16 shrink-0 text-right tabular-nums text-[var(--color-text)]">{percent(now)}</span>
        </div>
      )}
    </div>
  );
}

// wins/losses/p_mcnemar are only meaningful when the row is paired -- otherwise they are the dataclass's
// unpopulated defaults and stating them would claim a comparison that was never made.
function McNemar({ p }: { p: BenchmarkProgress }) {
  const row = withPairedStats(p);
  if (!row.paired || row.wins === null || row.losses === null || row.p_mcnemar === null) return null;
  return (
    <div className="mt-3 rounded border border-[var(--color-border)] bg-[var(--color-surface-raised)] p-3">
      <p className="text-xs font-medium text-[var(--color-text)]">Paired comparison (exact McNemar)</p>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">
        Scored on the same held-out items as the baseline. The candidate won{" "}
        <span className="font-medium text-[var(--color-text)]">{row.wins}</span> item
        {row.wins === 1 ? "" : "s"} it had lost and lost{" "}
        <span className="font-medium text-[var(--color-text)]">{row.losses}</span> item
        {row.losses === 1 ? "" : "s"} it had won, against the {row.baseline?.n ?? "—"}-item shared set. p ={" "}
        <span className="font-medium tabular-nums text-[var(--color-text)]">{fixed(row.p_mcnemar, 4)}</span>.
      </p>
    </div>
  );
}

export function BenchmarkDetail({ p }: { p: BenchmarkProgress }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-sm text-[var(--color-text)]">{p.benchmark}</span>
        <Verdict p={p} />
      </div>

      {p.state === "unmeasured" && (
        <p className="mt-2 flex items-start gap-2 text-sm leading-6 text-[var(--color-text-dim)]">
          <CircleDashed size={15} className="mt-0.5 shrink-0" />
          {p.reason}
        </p>
      )}

      {p.state === "baseline_only" && (
        <p className="mt-2 text-sm leading-6 text-[var(--color-text-dim)]">{p.reason}</p>
      )}

      <Bar p={p} />

      {p.state === "measured" && p.delta !== null && (
        <div className="mt-4 border-t border-[var(--color-border)] pt-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span className="text-[var(--color-text-dim)]">change</span>
            <span className="tabular-nums text-[var(--color-text)]">
              {signedPercent(p.delta)}
              {p.delta_lo !== null && p.delta_hi !== null && (
                <span className="ml-2 text-[var(--color-text-dim)]">
                  [{signedPercent(p.delta_lo)}, {signedPercent(p.delta_hi)}]
                </span>
              )}
            </span>
          </div>
          {p.target_delta !== null && (
            <div className="mt-1 flex items-baseline justify-between text-sm">
              <span className="text-[var(--color-text-dim)]">target</span>
              <span className="tabular-nums text-[var(--color-text)]">
                +{percent(p.target_delta)}
                <span className={`ml-2 ${p.met ? "text-[var(--color-accent)]" : "text-[var(--color-text-dim)]"}`}>
                  {p.met ? "met" : "not met"}
                </span>
              </span>
            </div>
          )}
          <McNemar p={p} />
          {p.baseline && p.latest && (
            <p className="mt-3 text-xs leading-5 text-[var(--color-text-dim)]">
              Scored outside the engine on {p.baseline.n} items. Baseline model {p.baseline.model}. Admitted to
              the ledger as rows {p.baseline.seq} and {p.latest.seq}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
