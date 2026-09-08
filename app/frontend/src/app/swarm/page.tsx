"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { DispatchesTable } from "@/components/swarm/DispatchesTable";
import { DispatchPanel } from "@/components/swarm/DispatchPanel";
import { FleetTable } from "@/components/swarm/FleetTable";
import { LivePanel } from "@/components/swarm/LivePanel";
import { RoutingTable } from "@/components/swarm/RoutingTable";
import { SeatsTable } from "@/components/swarm/SeatsTable";
import { SandboxesSection } from "@/components/sandboxes/SandboxesSection";
import { swarm, type SwarmSnapshot } from "@/lib/swarm";

export default function SwarmPage() {
  // `undefined` is "still loading"; `null` is "the engine/recording has nothing to show" — kept apart from a
  // failed fetch so the empty state reads as honest rather than broken.
  const [data, setData] = useState<SwarmSnapshot | null | undefined>(undefined);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    swarm()
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
      <PageHeader
        title="Swarm"
        subtitle="Every agent the engine can dispatch, where each tier routes today, and what is running right now."
      />
      <div className="space-y-8 p-8">
        {failed && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s swarm API.</p>
        )}
        {!failed && data === undefined && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
        {!failed && data === null && (
          <p className="text-sm text-[var(--color-text-dim)]">
            This recording predates the swarm view. Nothing to show.
          </p>
        )}
        {!failed && data && (
          <>
            <LivePanel />

            <DispatchPanel />
            <SandboxesSection />

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Fleet</h2>
              <FleetTable agents={data.agents} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Routing</h2>
              <SeatsTable seats={data.roster ?? []} />
              <RoutingTable rows={data.routing} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Recent dispatches</h2>
              <DispatchesTable subagentRuns={data.subagent_runs} selfbuildRuns={data.selfbuild_runs} />
            </section>
          </>
        )}
      </div>
    </div>
  );
}
