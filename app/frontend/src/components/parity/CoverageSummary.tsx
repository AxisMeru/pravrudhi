// The headline number, stated as met over total rather than a bare percentage — "9 of 14" says both how much is
// done and how much the total is, which a percentage alone throws away.

import { fixed } from "@/lib/num";
import type { ParityCoverage } from "@/lib/parity";

export function CoverageSummary({ coverage }: { coverage: ParityCoverage }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] p-4">
      <dt className="text-xs text-[var(--color-text-dim)]">Capabilities met</dt>
      <dd className="mt-1 text-lg text-[var(--color-text)]">
        {fixed(coverage.met, 0)} of {fixed(coverage.total, 0)}
      </dd>
    </div>
  );
}
