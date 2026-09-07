"use client";

// The System page: how this engine keeps itself running, for an operator who wants to see the machinery behind
// the results rather than just the results. Four sections — how it updates itself, the machines it keeps
// current, whether it is healthy, and who builds it — each fetching its own data so a fault in one never blanks
// the rest, matching the resilience objectives/detail already established for this app.

import { Bot, Download, HeartPulse, Server } from "lucide-react";
import { IS_DEMO } from "@/lib/api";
import { Section } from "@/components/system/Section";
import { UpdateChannels } from "@/components/system/UpdateChannels";
import { FleetTable } from "@/components/system/FleetTable";
import { HealthPanel, useHealthState } from "@/components/system/HealthPanel";
import { SwarmPanel } from "@/components/system/SwarmPanel";
import { CapabilitiesPanel } from "@/components/system/CapabilitiesPanel";
import { VersionBadge } from "@/components/system/VersionBadge";

export default function SystemPage() {
  const { state: health, errored: healthErrored } = useHealthState();
  const showHealth = health !== null || healthErrored;

  return (
    <div>
      <div className="border-b border-[var(--color-border)] px-6 py-6">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold text-[var(--color-text)]">System</h1>
          <VersionBadge />
        </div>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--color-text-dim)]">
          How this engine keeps itself running: how it updates, which machines it keeps current, whether it is
          healthy right now, and who — which agents, on which routes — actually builds it.
        </p>
        {IS_DEMO && (
          <p className="mt-3 text-xs text-[var(--color-text-dim)]">
            This is a recording. A live engine answers these same questions from its own state.
          </p>
        )}
      </div>

      <div className="grid gap-5 p-6">
        <Section icon={Download} title="How it updates itself" subtitle="Two channels, one mechanism.">
          <UpdateChannels />
        </Section>

        <Section
          icon={Server}
          title="The machines it keeps current"
          subtitle="Every install this engine updates unattended."
        >
          <FleetTable />
        </Section>

        {showHealth && (
          <Section icon={HeartPulse} title="Is it healthy">
            <HealthPanel state={health} errored={healthErrored} />
          </Section>
        )}

        <Section
          icon={Bot}
          title="Who builds it"
          subtitle="Agents, the routes they've earned, and what they've recently produced."
        >
          <div className="grid gap-5">
            <SwarmPanel />
            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
                What the builders have on hand
              </h3>
              <CapabilitiesPanel />
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}
