// Band 3: a compact strip of what the engine has done so far, each figure a link to the page that explains it.

import Link from "next/link";
import type { DoneStrip } from "@/lib/home";
import { fixed } from "@/lib/num";

function Tile({ href, value, label }: { href: string; value: string; label: string }) {
  return (
    <Link
      href={href}
      className="flex-1 px-4 py-3 text-center transition-colors hover:bg-[var(--color-surface-raised)]"
    >
      <div className="text-xl font-semibold tabular-nums text-[var(--color-text)]">{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-[var(--color-muted)]">{label}</div>
    </Link>
  );
}

export function DoneBand({ strip }: { strip: DoneStrip }) {
  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="border-b border-[var(--color-border)] px-5 py-3">
        <h2 className="text-sm font-medium text-[var(--color-text)]">What it has done</h2>
      </div>
      <div className="flex flex-wrap divide-x divide-[var(--color-border)]">
        <Tile href="/runs" value={String(strip.nightsRun)} label="nights run" />
        <Tile href="/runs" value={fixed(strip.gpuHoursSpent, 1)} label="GPU-hours spent" />
        <Tile href="/candidates" value={String(strip.candidatesScored)} label="candidates scored" />
        <Tile href="/models" value={String(strip.promoted)} label="promoted" />
        <Tile href="/requests" value={String(strip.openRequests)} label="open decisions" />
      </div>
    </section>
  );
}
