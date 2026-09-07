// Selected models' own measured figures, side by side. This is models against each other, not a model against
// its own baseline -- so every cell is that model's "after" score, and a row gets an explicit caveat whenever
// this engine cannot confirm every model in it was scored on the same held-out items: a comparison across
// different item sets is not a comparison.

import { Fragment } from "react";
import { fixed, percent } from "@/lib/num";
import type { ModelCard } from "@/lib/models";

export function ComparisonTable({ models }: { models: ModelCard[] }) {
  const names = Array.from(new Set(models.flatMap((m) => m.benchmarks.map((b) => b.name)))).sort();

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <h2 className="text-sm font-medium text-[var(--color-text)]">Comparing {models.length} models</h2>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">
        Each cell is that model&apos;s own measured score, not a delta against its baseline.
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-[var(--color-text-dim)]">
              <th className="pb-2 pr-3 font-normal">benchmark</th>
              {models.map((m) => (
                <th key={m.id} className="pb-2 pr-3 font-mono font-normal text-[var(--color-text)]">
                  {m.id}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const cells = models.map((m) => ({
                model: m,
                b: m.benchmarks.find((row) => row.name === name) ?? null,
                items: m.itemsByBenchmark[name] ?? null,
              }));
              const withValue = cells.filter((c) => c.b && c.b.after !== null);
              const knownItemSets = withValue.filter((c) => c.items !== null).map((c) => JSON.stringify(c.items));
              const sameItems = withValue.length > 1 && knownItemSets.length === withValue.length && new Set(knownItemSets).size === 1;
              const needsCaveat = withValue.length > 1 && !sameItems;
              return (
                <Fragment key={name}>
                  <tr className="border-t border-[var(--color-border)]">
                    <td className="py-2 pr-3 font-mono text-[11px] text-[var(--color-text)]">{name}</td>
                    {cells.map((c) => (
                      <td key={c.model.id} className="py-2 pr-3 tabular-nums text-[var(--color-text)]">
                        {c.b && c.b.after !== null ? (
                          <>
                            {percent(c.b.after)}
                            <span className="ml-1 text-[10px] text-[var(--color-text-dim)]">n={fixed(c.b.n, 0)}</span>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    ))}
                  </tr>
                  {needsCaveat && (
                    <tr>
                      <td colSpan={models.length + 1} className="pb-2 text-[11px] leading-4 text-[var(--color-text-dim)]">
                        Not confirmed to have been scored on the same items across these models — this row is not a
                        like-for-like comparison, read it as directional only.
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
