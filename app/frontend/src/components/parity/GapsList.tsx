// The open gaps, surfaced above the matrix rather than left to be spotted by scanning it — these are what the
// work actually is.
//
// `gaps` carries whole rows, not ids. It was typed as ids here and resolved against `rows`, which meant every
// gap object arriving from the engine was handed to React as a child and crashed the page outright: the parity
// page, whose whole job is to say honestly what this product can do, showed "This page couldn't load" wherever
// there were gaps to show. The renderer now takes what the engine actually sends, and a row missing its
// capability name still renders by its id rather than vanishing, because that mismatch is worth seeing too.

import type { ParityRow } from "@/lib/parity";
import { Empty } from "@/components/system/Section";

const BADGE: Record<string, string> = {
  none: "text-red-600",
  partial: "text-amber-600",
  unknown: "text-[var(--color-text-dim)]",
};

export function GapsList({ gaps }: { gaps: ParityRow[] }) {
  if (gaps.length === 0) return <Empty text="No open gaps — every capability tracked here is met." />;

  return (
    <ul className="grid gap-2">
      {gaps.map((row) => (
        <li key={row.id} className="rounded-md border border-[var(--color-border)] p-3 text-xs">
          <div className="flex items-baseline gap-2">
            <span className="font-medium text-[var(--color-text)]">{row.capability || row.id}</span>
            <span className={`text-[11px] uppercase tracking-wide ${BADGE[row.ours] ?? ""}`}>{row.ours}</span>
          </div>
          {row.why_it_matters && (
            <p className="mt-1 leading-5 text-[var(--color-text-dim)]">{row.why_it_matters}</p>
          )}
        </li>
      ))}
    </ul>
  );
}
