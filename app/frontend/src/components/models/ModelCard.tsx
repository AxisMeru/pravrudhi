// One promoted model: what it was derived from, the night and policy that produced it, its cost, and every
// benchmark that scored the change it carries -- paired against the baseline's own per-item vector wherever the
// ledger holds one, never a bare delta dressed up as a significant result.

import Link from "next/link";
import { ArrowRight, ArrowUpRight, GitCompare, Minus, Package, TrendingDown, TrendingUp } from "lucide-react";
import { fixed, percent } from "@/lib/num";
import { signedPercent, type ModelBenchmark, type ModelCard as ModelCardData } from "@/lib/models";

function Verdict({ significant, delta }: { significant: boolean; delta: number | null }) {
  if (delta === null) return null;
  if (!significant) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-dim)]">
        <Minus size={12} /> not distinguishable
      </span>
    );
  }
  const up = delta > 0;
  return (
    <span
      className={`inline-flex items-center gap-1 text-[11px] font-medium ${
        up ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"
      }`}
    >
      {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
      {up ? "improvement" : "regression"}
    </span>
  );
}

function BenchmarkRow({ b }: { b: ModelBenchmark }) {
  return (
    <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-[11px] text-[var(--color-text)]">{b.name}</span>
        <Verdict significant={b.significant} delta={b.delta} />
      </div>
      <div className="mt-2 flex items-center gap-2 text-sm">
        <span className="tabular-nums text-[var(--color-text-dim)]">{percent(b.before)}</span>
        <ArrowRight size={12} className="text-[var(--color-text-dim)]" />
        <span className="tabular-nums text-[var(--color-text)]">{percent(b.after)}</span>
        <span className="ml-auto tabular-nums text-xs text-[var(--color-text-dim)]">{signedPercent(b.delta)}</span>
      </div>
      <p className="mt-1.5 text-[11px] leading-4 text-[var(--color-text-dim)]">
        {b.paired
          ? `Paired on ${fixed(b.pairedN, 0)} shared items: ${fixed(b.wins, 0)} won, ${fixed(b.losses, 0)} lost against the baseline.`
          : b.before !== null && b.after !== null
            ? "No shared per-item vector was found for this benchmark; the change above is unpaired."
            : "Only one side of this benchmark has been scored so far."}
        {b.n !== null && ` Scored on ${fixed(b.n, 0)} items.`}
      </p>
    </div>
  );
}

export function ModelCard({
  model,
  selected,
  onToggle,
}: {
  model: ModelCardData;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <article className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-center gap-2">
        <Package size={16} className="text-[var(--color-text-dim)]" />
        <span className="font-mono text-sm text-[var(--color-text)]">{model.id}</span>
        <span className="ml-auto text-[11px] text-[var(--color-text-dim)]">
          {model.track} · night {model.night}
          {model.policy && ` · ${model.policy}`}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--color-text-dim)]">
        <span>derived from {model.baseModelName ?? "an unrecorded base model"}</span>
        <Link
          href="/candidates"
          title="Find this candidate in the Candidates list"
          className="inline-flex items-center gap-0.5 font-mono text-[var(--color-text)] hover:text-[var(--color-accent)]"
        >
          {model.id}
          <ArrowUpRight size={11} />
        </Link>
      </div>

      {(model.editFamily || model.recipe.length > 0) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {model.editFamily && (
            <span className="rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[11px] text-[var(--color-text-dim)]">
              {model.editFamily}
            </span>
          )}
          {model.recipe.map((f) => (
            <span
              key={f.key}
              title={`${f.key}: ${f.value}`}
              className="rounded border border-[var(--color-border)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-dim)]"
            >
              {f.key}: {f.value}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-dim)]">
        <span>cost {fixed(model.costGpuH, 2)} GPU-h</span>
        {model.artefact && (
          <span className="truncate font-mono" title={model.artefact}>
            {model.artefact}
          </span>
        )}
      </div>

      <div className="mt-3 space-y-2 border-t border-[var(--color-border)] pt-3">
        {model.benchmarks.length === 0 ? (
          <p className="text-xs text-[var(--color-text-dim)]">
            No external score has been recorded for this promotion yet. The engine&apos;s own selection is not shown
            here as if it were one.
          </p>
        ) : (
          model.benchmarks.map((b) => <BenchmarkRow key={b.name} b={b} />)
        )}
      </div>

      <label className="mt-3 flex items-center gap-2 border-t border-[var(--color-border)] pt-3 text-xs text-[var(--color-text-dim)]">
        <input type="checkbox" checked={selected} onChange={onToggle} className="accent-[var(--color-accent)]" />
        <GitCompare size={13} />
        compare
      </label>
    </article>
  );
}
