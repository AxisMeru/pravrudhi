"use client";

// The Parity page: how this engine's capabilities stack up against rival agents, capability by capability — the
// open gaps first, since those are the work, then the full matrix with our own claims backed by evidence.

import { useEffect, useState } from "react";
import { GitCompare } from "lucide-react";
import { IS_DEMO } from "@/lib/api";
import { parity as fetchParity, type ParitySnapshot } from "@/lib/parity";
import { Section, Empty } from "@/components/system/Section";
import { CoverageSummary } from "@/components/parity/CoverageSummary";
import { GapsList } from "@/components/parity/GapsList";
import { ParityMatrix } from "@/components/parity/ParityMatrix";

function useParity() {
  const [snapshot, setSnapshot] = useState<ParitySnapshot | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    fetchParity()
      .then((p) => {
        if (!cancelled) setSnapshot(p);
      })
      .catch(() => {
        if (!cancelled) setSnapshot(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return snapshot;
}

export default function ParityPage() {
  const snapshot = useParity();

  return (
    <div>
      <div className="border-b border-[var(--color-border)] px-6 py-6">
        <h1 className="text-lg font-semibold text-[var(--color-text)]">Parity</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--color-text-dim)]">
          Where this engine stands against Orca, Claude Desktop, Codex and OpenClaw, capability by capability — our
          own claims carry their evidence, and a rival marked &quot;not checked&quot; means exactly that, not that
          it lacks the capability.
        </p>
        {IS_DEMO && (
          <p className="mt-3 text-xs text-[var(--color-text-dim)]">
            This is a recording. A live engine answers these same questions from its own state.
          </p>
        )}
      </div>

      <div className="grid gap-5 p-6">
        {snapshot === undefined && (
          <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <Empty text="Loading…" />
          </section>
        )}

        {snapshot === null && (
          <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <Empty text="Could not reach the engine for a parity matrix — the route may not exist on this build yet." />
          </section>
        )}

        {snapshot && (
          <>
            <CoverageSummary coverage={snapshot.coverage} />

            <Section icon={GitCompare} title="Open gaps" subtitle="What is not yet met — the work, in order.">
              <GapsList gaps={snapshot.gaps} rows={snapshot.rows} />
            </Section>

            <Section icon={GitCompare} title="The matrix" subtitle="Every tracked capability, product by product.">
              <ParityMatrix rows={snapshot.rows} />
            </Section>
          </>
        )}
      </div>
    </div>
  );
}
