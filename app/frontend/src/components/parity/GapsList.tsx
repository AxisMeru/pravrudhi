// The open gaps, surfaced above the matrix rather than left to be spotted by scanning it — these are what the
// work actually is. `gaps` holds row ids; each is resolved against `rows` for its capability name and why it
// matters, but a gap id with no matching row still renders (by its bare id) rather than silently vanishing —
// that mismatch is itself something worth seeing, not something to hide.

import type { ParityRow } from "@/lib/parity";
import { Empty } from "@/components/system/Section";

export function GapsList({ gaps, rows }: { gaps: string[]; rows: ParityRow[] }) {
  if (gaps.length === 0) return <Empty text="No open gaps — every capability tracked here is met." />;

  const byId = new Map(rows.map((r) => [r.id, r]));

  return (
    <ul className="grid gap-2">
      {gaps.map((id) => {
        const row = byId.get(id);
        return (
          <li key={id} className="rounded-md border border-[var(--color-border)] p-3 text-xs">
            <div className="font-medium text-[var(--color-text)]">{row?.capability ?? id}</div>
            {row?.why_it_matters && (
              <p className="mt-1 leading-5 text-[var(--color-text-dim)]">{row.why_it_matters}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
