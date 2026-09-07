// Band 1: the largest measured improvement this engine has produced, stated the same way the objective detail
// page states a benchmark's standing -- baseline, current, the change, and its honest significance.

import Link from "next/link";
import { ArrowRight, Minus, TrendingDown, TrendingUp } from "lucide-react";
import type { BiggestResult } from "@/lib/home";
import { fixed, percent } from "@/lib/num";
import { signedPercent } from "@/lib/objective";

function Verdict({ result }: { result: BiggestResult }) {
  if (!result.significant) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-[var(--color-text-dim)]">
        <Minus size={15} /> not distinguishable from no change
      </span>
    );
  }
  const up = result.delta > 0;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${
        up ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"
      }`}
    >
      {up ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
      {signedPercent(result.delta)}
      {result.deltaLo !== null && result.deltaHi !== null && (
        <span className="font-normal opacity-80">
          [{signedPercent(result.deltaLo)}, {signedPercent(result.deltaHi)}]
        </span>
      )}
    </span>
  );
}

function McNemar({ result }: { result: BiggestResult }) {
  if (!result.paired || result.wins === null || result.losses === null || result.pMcnemar === null) return null;
  return (
    <p className="mt-3 text-xs leading-5 text-[var(--color-muted)]">
      Paired comparison (exact McNemar) on the same {result.n} held-out items: won {result.wins} item
      {result.wins === 1 ? "" : "s"} it had lost, lost {result.losses} item{result.losses === 1 ? "" : "s"} it had
      won. p = {fixed(result.pMcnemar, 4)}.
    </p>
  );
}

export function ResultBand({ result }: { result: BiggestResult | null }) {
  if (!result) {
    return (
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">The result</p>
        <p className="mt-2 text-sm leading-6 text-[var(--color-text-dim)]">
          Nothing has been measured against a baseline yet. Once a candidate is scored against the current best
          on a held-out set, the result will show here.
        </p>
      </section>
    );
  }

  return (
    <section
      className={`rounded-lg border p-5 ${
        result.significant && result.delta > 0
          ? "border-emerald-500/30 bg-gradient-to-br from-emerald-500/10 to-transparent"
          : "border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          {result.model.split("/").pop()} on {result.benchmark}
          {result.scorer ? `, scored by ${result.scorer}` : ""}
        </p>
        <Link
          href={result.objectiveHref}
          className="text-xs text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
        >
          {result.objectiveId} →
        </Link>
      </div>

      <div className="mt-3 flex flex-wrap items-baseline gap-3">
        <span className="text-3xl font-semibold tabular-nums text-[var(--color-muted)]">{percent(result.baseline, 1)}</span>
        <ArrowRight size={22} className="text-[var(--color-muted)]" />
        <span
          className={`text-5xl font-bold tabular-nums ${
            result.significant ? (result.delta > 0 ? "text-emerald-400" : "text-red-400") : "text-[var(--color-text)]"
          }`}
        >
          {percent(result.current, 1)}
        </span>
        <Verdict result={result} />
      </div>

      <McNemar result={result} />
      <p className="mt-3 text-xs leading-5 text-[var(--color-muted)]">
        Scored outside the engine on {result.n} items, baseline and current from the same objective&apos;s ledger
        history.
      </p>
    </section>
  );
}
