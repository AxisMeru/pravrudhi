"use client";

// One chronological read across a whole wave: which agent took which task, what it touched, what was refused
// and why, and where a route gave out and the work moved. The per-job records cannot give this — they are one
// job each, and the thing worth seeing is the sequence.

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { agentTrace, toneFor, type TraceEntry } from "@/lib/trace";

export default function TracePage() {
  const [entries, setEntries] = useState<TraceEntry[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let off = false;
    const load = () =>
      agentTrace(200)
        .then((rows) => !off && setEntries(rows))
        .catch(() => !off && setFailed(true));
    load();
    // The trace is only interesting while work is running, so it refreshes rather than needing a reload.
    const timer = setInterval(load, 10_000);
    return () => {
      off = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div>
      <PageHeader
        title="Agent trace"
        subtitle="What the agents did, in the order they did it."
      />
      <div className="space-y-4 p-8">
        {failed && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s agent trace.</p>
        )}
        {!failed && entries === null && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
        {!failed && entries !== null && entries.length === 0 && (
          <p className="text-sm text-[var(--color-text-dim)]">
            No agent activity recorded yet. Entries appear as waves run.
          </p>
        )}
        {!failed && entries !== null && entries.length > 0 && (
          <ol className="space-y-2">
            {entries.map((e, i) => (
              <li
                key={`${e.at}-${i}`}
                className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className={`font-mono text-[11px] uppercase ${toneFor(e.kind)}`}>{e.kind}</span>
                  <span className="text-sm text-[var(--color-text)]">{e.summary}</span>
                  <span className="ml-auto font-mono text-[11px] text-[var(--color-text-dim)]">{e.at}</span>
                </div>
                {e.detail && (
                  <p className="mt-1 break-words font-mono text-[11px] leading-5 text-[var(--color-text-dim)]">
                    {e.detail}
                  </p>
                )}
                {e.agent && (
                  <p className="mt-1 font-mono text-[11px] text-[var(--color-text-dim)]">{e.agent}</p>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
