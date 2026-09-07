"use client";

// Section 4 assembled: one fetch of the swarm snapshot, handed down to the agent roster, the routing table and
// the dispatch log so all three stay consistent with each other and the page only shows one loading state for
// the whole section.

import { useEffect, useState } from "react";
import { swarm as fetchSwarm, type SwarmSnapshot } from "@/lib/system";
import { AgentRoster } from "./AgentRoster";
import { RoutingTable } from "./RoutingTable";
import { DispatchLog } from "./DispatchLog";
import { Empty } from "./Section";

export function SwarmPanel() {
  const [snap, setSnap] = useState<SwarmSnapshot | null | undefined>(undefined);
  const [errored, setErrored] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSwarm()
      .then((s) => {
        if (!cancelled) setSnap(s);
      })
      .catch(() => {
        if (!cancelled) {
          setSnap(null);
          setErrored(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (snap === undefined) return <Empty text="Loading…" />;
  if (snap === null) {
    return errored ? (
      <Empty text="Could not reach the engine to ask." />
    ) : (
      <Empty text="This recording predates the swarm view." />
    );
  }

  return (
    <div className="grid gap-5">
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">Agents</h3>
        <AgentRoster agents={snap.agents} />
      </div>
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
          Routing, by tier
        </h3>
        <RoutingTable rows={snap.routing} />
      </div>
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
          Recent dispatches
        </h3>
        <DispatchLog subagent={snap.subagent_runs} selfbuild={snap.selfbuild_runs} />
      </div>
    </div>
  );
}
