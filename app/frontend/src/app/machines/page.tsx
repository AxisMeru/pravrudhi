"use client";

// The machines page: what this engine can actually do about the next piece of work, right now.
//
// Four independent facts, each rendered as soon as it arrives and never blocking the others: the engine's own
// survival state (the single most useful thing this page can say when something is wrong), which coding agents
// are routable and which are cooling down after a usage limit, this install's channel/version/update standing,
// and every enrolled machine's measured capabilities with the specific reason behind anything it cannot do. A
// workspace that has never run a night still renders all four as real, empty-but-honest states — never a
// spinner that never ends, and never a fabricated number where a measurement is missing.

import { useEffect, useState } from "react";
import { Activity, Server, Users } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EngineHealthCard } from "@/components/machines/EngineHealthCard";
import { AgentsPanel } from "@/components/machines/AgentsPanel";
import { MachineCard } from "@/components/machines/MachineCard";
import {
  agentCooldowns,
  agents as fetchAgents,
  hosts as fetchHosts,
  installStatus,
  svasthya,
  type AgentCooldown,
  type AgentStatus,
  type HostRow,
  type InstallStatus,
  type SvasthyaHealth,
} from "@/lib/machines";

function SectionHeading({ icon: Icon, title }: { icon: typeof Activity; title: string }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <Icon size={15} className="text-[var(--color-text-dim)]" />
      <h2 className="text-sm font-medium text-[var(--color-text)]">{title}</h2>
    </div>
  );
}

export default function MachinesPage() {
  const [rows, setRows] = useState<HostRow[] | null>(null);
  const [hostsUnsupported, setHostsUnsupported] = useState(false);

  const [health, setHealth] = useState<SvasthyaHealth | null>(null);
  const [healthError, setHealthError] = useState(false);

  const [agents, setAgents] = useState<AgentStatus[] | null>(null);
  const [agentsError, setAgentsError] = useState(false);
  const [cooldowns, setCooldowns] = useState<AgentCooldown[]>([]);

  const [install, setInstall] = useState<InstallStatus | null>(null);
  const [installError, setInstallError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchHosts()
      .then((data) => {
        if (!cancelled) setRows(data.hosts);
      })
      .catch(() => {
        if (!cancelled) setHostsUnsupported(true);
      });
    svasthya()
      .then((h) => {
        if (!cancelled) setHealth(h);
      })
      .catch(() => {
        if (!cancelled) setHealthError(true);
      });
    fetchAgents()
      .then((a) => {
        if (!cancelled) setAgents(a);
      })
      .catch(() => {
        if (!cancelled) setAgentsError(true);
      });
    agentCooldowns()
      .then((c) => {
        if (!cancelled) setCooldowns(c);
      })
      .catch(() => {
        /* an engine too old to report cooldowns still shows agent availability; leave the list empty */
      });
    installStatus()
      .then((s) => {
        if (!cancelled) setInstall(s);
      })
      .catch(() => {
        if (!cancelled) setInstallError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <PageHeader title="Machines" subtitle="This machine, and any others enrolled to run work." />
      <div className="space-y-6 p-8">
        <section>
          <SectionHeading icon={Activity} title="Engine health" />
          <EngineHealthCard health={health} error={healthError} />
        </section>

        <section>
          <SectionHeading icon={Users} title="Agents" />
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <AgentsPanel agents={agents} agentsError={agentsError} cooldowns={cooldowns} />
          </div>
        </section>

        <section>
          <SectionHeading icon={Server} title="Fleet" />
          {hostsUnsupported && (
            <p className="text-sm text-[var(--color-text-dim)]">engine does not report machines yet.</p>
          )}
          {!hostsUnsupported && rows === null && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
          {!hostsUnsupported && rows !== null && rows.length === 0 && (
            <p className="text-sm text-[var(--color-text-dim)]">No machine is enrolled.</p>
          )}
          {!hostsUnsupported && rows !== null && rows.length > 0 && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {rows.map((row) => (
                <MachineCard
                  key={row.host.name}
                  row={row}
                  isLocal={row.host.name === "local"}
                  install={install}
                  installError={installError}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
