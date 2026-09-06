"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AppetiteSentence } from "@/components/appetite/AppetiteSentence";
import { DriveList } from "@/components/appetite/DriveList";
import { DecisionPanel } from "@/components/appetite/DecisionPanel";
import { appetite, type AppetiteResponse } from "@/lib/appetite";

export default function AppetitePage() {
  // `undefined` is "still loading"; `null` is "the engine/recording has nothing to show" — kept apart from a
  // failed fetch so the empty state reads as honest rather than broken.
  const [data, setData] = useState<AppetiteResponse | null | undefined>(undefined);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    appetite()
      .then((snapshot) => {
        if (!cancelled) setData(snapshot);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <PageHeader title="Appetite" subtitle="What the engine wants right now, and why — traced back to the numbers behind it." />
      <div className="space-y-8 p-8">
        {failed && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s appetite API.</p>
        )}
        {!failed && data === undefined && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
        {!failed && data === null && (
          <p className="text-sm text-[var(--color-text-dim)]">
            This recording predates the appetite view. Nothing to show.
          </p>
        )}
        {!failed && data && (
          <>
            <AppetiteSentence sentence={data.sentence} asOf={data.appetite.as_of} />

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Drives</h2>
              <DriveList drives={data.drives} selected={data.appetite.selected} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Decision</h2>
              <DecisionPanel decision={data.appetite} drives={data.drives} />
            </section>
          </>
        )}
      </div>
    </div>
  );
}
