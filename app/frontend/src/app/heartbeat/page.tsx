"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { HeartbeatSummary } from "@/components/heartbeat/HeartbeatSummary";
import { HeartbeatTimeline } from "@/components/heartbeat/HeartbeatTimeline";
import { ObjectiveTouchCards } from "@/components/heartbeat/ObjectiveTouchCards";
import { heartbeat, type HeartbeatBeat } from "@/lib/heartbeat";

export default function HeartbeatPage() {
  // `undefined` is "still loading"; an empty array is "the engine answered, nothing recorded yet" — kept apart
  // from a failed fetch (heartbeat() rejects on those) so the empty state reads as honest rather than broken.
  const [beats, setBeats] = useState<HeartbeatBeat[] | undefined>(undefined);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    heartbeat(100)
      .then((rows) => {
        if (!cancelled) setBeats(rows);
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
      <PageHeader
        title="Heartbeat"
        subtitle="Every beat the engine has taken: what it looked at, what it chose, why, and what happened."
      />
      <div className="space-y-8 p-8">
        {failed && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s heartbeat log.</p>
        )}
        {!failed && beats === undefined && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
        {!failed && beats !== undefined && beats.length === 0 && (
          <p className="text-sm text-[var(--color-text-dim)]">No heartbeats recorded yet.</p>
        )}
        {!failed && beats !== undefined && beats.length > 0 && (
          <>
            <HeartbeatSummary beats={beats} />

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Objectives</h2>
              <ObjectiveTouchCards beats={beats} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Timeline</h2>
              <HeartbeatTimeline beats={beats} />
            </section>
          </>
        )}
      </div>
    </div>
  );
}
