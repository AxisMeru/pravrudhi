"use client";

import { useEffect, useState } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { fetchBenchmarks, type BenchmarkCatalogEntry, type BenchmarkSpec } from "@/lib/start";
import { percent } from "@/lib/num";

const FIELD =
  "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]";

interface Props {
  track: string;
  benchmarks: BenchmarkSpec[];
  targetDelta: string;
  onChangeTrack: (v: string) => void;
  onChangeBenchmarks: (v: BenchmarkSpec[]) => void;
  onChangeTargetDelta: (v: string) => void;
}

function toggled(entry: BenchmarkCatalogEntry, benchmarks: BenchmarkSpec[]): BenchmarkSpec[] {
  const on = benchmarks.some((b) => b.metric === entry.metric);
  if (on) return benchmarks.filter((b) => b.metric !== entry.metric);
  return [...benchmarks, { id: entry.id, tool: entry.tool, metric: entry.metric, direction: "up" }];
}

function withDirection(benchmarks: BenchmarkSpec[], metric: string, direction: string): BenchmarkSpec[] {
  return benchmarks.map((b) => (b.metric === metric ? { ...b, direction } : b));
}

export function StepSuccess({
  track,
  benchmarks,
  targetDelta,
  onChangeTrack,
  onChangeBenchmarks,
  onChangeTargetDelta,
}: Props) {
  const [catalog, setCatalog] = useState<BenchmarkCatalogEntry[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetchBenchmarks()
      .then(setCatalog)
      .catch(() => setFailed(true));
  }, []);

  return (
    <div>
      <h2 className="text-sm font-medium text-[var(--color-text)]">Choose what success means</h2>
      <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--color-text-dim)]">
        Pick from benchmarks this engine has already measured. An objective needs at least one: without a
        benchmark there is nothing that could tell you whether the intent was met.
      </p>

      <label className="mt-4 grid max-w-xs gap-1.5">
        <span className="text-xs text-[var(--color-text-dim)]">Track</span>
        <input
          className={FIELD}
          placeholder="nyaya"
          value={track}
          onChange={(e) => onChangeTrack(e.target.value)}
        />
        <span className="text-[11px] leading-4 text-[var(--color-text-dim)]">
          The bucket this objective&apos;s evidence accumulates under in the ledger.
        </span>
      </label>

      <div className="mt-5">
        {failed && (
          <p className="text-xs text-[var(--color-text-dim)]">Could not reach the engine&apos;s benchmark catalogue.</p>
        )}
        {!failed && catalog === null && <p className="text-xs text-[var(--color-text-dim)]">Loading…</p>}
        {!failed && catalog !== null && catalog.length === 0 && (
          <p className="text-xs leading-5 text-[var(--color-text-dim)]">
            This workspace has never scored anything yet, so there is nothing measurable to pick. Run a night, or
            admit an external result, before an objective can be created here.
          </p>
        )}
        {!failed && catalog !== null && catalog.length > 0 && (
          <div className="grid gap-2">
            {catalog.map((entry) => {
              const spec = benchmarks.find((b) => b.metric === entry.metric);
              return (
                <div
                  key={entry.metric}
                  className={`flex flex-wrap items-center gap-3 rounded-md border px-3 py-2.5 ${
                    spec
                      ? "border-[var(--color-accent)] bg-[var(--color-surface)]"
                      : "border-[var(--color-border)] bg-[var(--color-bg)]"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={Boolean(spec)}
                    onChange={() => onChangeBenchmarks(toggled(entry, benchmarks))}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-mono text-xs text-[var(--color-text)]">{entry.metric}</span>
                      <span className="text-[11px] text-[var(--color-text-dim)]">{entry.tool}</span>
                      <span className="text-[11px] text-[var(--color-text-dim)]">track {entry.track}</span>
                    </div>
                    <span className="text-[11px] text-[var(--color-text-dim)]">
                      last measured: {entry.value === null ? "—" : percent(entry.value)}
                      {entry.n !== null && ` (n=${entry.n})`}
                    </span>
                  </div>
                  {spec && (
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => onChangeBenchmarks(withDirection(benchmarks, entry.metric, "up"))}
                        title="better means higher"
                        className={`rounded p-1 ${
                          spec.direction === "up"
                            ? "bg-[var(--color-accent)] text-[var(--color-bg)]"
                            : "text-[var(--color-text-dim)]"
                        }`}
                      >
                        <TrendingUp size={14} />
                      </button>
                      <button
                        onClick={() => onChangeBenchmarks(withDirection(benchmarks, entry.metric, "down"))}
                        title="better means lower"
                        className={`rounded p-1 ${
                          spec.direction === "down"
                            ? "bg-[var(--color-accent)] text-[var(--color-bg)]"
                            : "text-[var(--color-text-dim)]"
                        }`}
                      >
                        <TrendingDown size={14} />
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <label className="mt-5 grid max-w-xs gap-1.5">
        <span className="text-xs text-[var(--color-text-dim)]">Target improvement (optional)</span>
        <input
          className={FIELD}
          type="number"
          step="0.01"
          placeholder="0.03"
          value={targetDelta}
          onChange={(e) => onChangeTargetDelta(e.target.value)}
        />
        <span className="text-[11px] leading-4 text-[var(--color-text-dim)]">
          In the benchmark&apos;s own units. Leave blank for &quot;better is better&quot; -- most objectives should.
        </span>
      </label>
    </div>
  );
}
