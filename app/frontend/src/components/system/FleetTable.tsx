"use client";

// Section 2: the machines the engine keeps current — every enrolled install, unattended, each reporting its own
// channel, version, kept history, and last check-in.

import { useEffect, useState } from "react";
import { fleet as fetchFleet, type FleetHost } from "@/lib/system";
import { Empty } from "./Section";

function StatusDot({ healthy }: { healthy: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${healthy ? "bg-[var(--color-accent)]" : "bg-red-500"}`}
      aria-hidden
    />
  );
}

function HostCard({ host }: { host: FleetHost }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusDot healthy={host.healthy} />
        <span className="font-mono text-xs text-[var(--color-text)]">{host.root}</span>
        <span className="rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[10px] text-[var(--color-text-dim)]">
          {host.channel}
        </span>
        <span className="rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[10px] text-[var(--color-text-dim)]">
          {host.auto_apply ? "auto-applies" : "manual apply"}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        <dt className="text-[var(--color-text-dim)]">Running</dt>
        <dd className="text-[var(--color-text)]">{host.current_version}</dd>
        <dt className="text-[var(--color-text-dim)]">Kept versions</dt>
        <dd className="text-[var(--color-text)]">
          {host.available_versions.length > 0 ? host.available_versions.join(", ") : "none besides the running one"}
        </dd>
        <dt className="text-[var(--color-text-dim)]">Last checked</dt>
        <dd className="text-[var(--color-text)]">{whenChecked(host.last_check)}</dd>
        <dt className="text-[var(--color-text-dim)]">Last result</dt>
        <dd className="text-[var(--color-text)]">{host.last_result ?? "—"}</dd>
      </dl>
    </div>
  );
}

// The engine records the last check as seconds since the epoch. Printed raw it read as a eleven-digit number,
// which tells a person nothing about whether the machine is current.
function whenChecked(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "never";
  const seconds = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(seconds)) return String(value);
  const when = new Date(seconds * 1000);
  if (Number.isNaN(when.getTime())) return String(value);
  const minutes = Math.max(0, Math.round((Date.now() - when.getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

export function FleetTable() {
  const [hosts, setHosts] = useState<FleetHost[] | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    fetchFleet()
      .then((h) => {
        if (!cancelled) setHosts(h);
      })
      .catch(() => {
        if (!cancelled) setHosts(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (hosts === undefined) return <Empty text="Loading…" />;
  if (hosts === null) return <Empty text="Could not reach the fleet — the engine may not be running." />;
  if (hosts.length === 0) return <Empty text="No machines are enrolled in the update fleet yet." />;

  return (
    <div className="grid gap-3 md:grid-cols-2">
      {hosts.map((h) => (
        <HostCard key={h.root} host={h} />
      ))}
    </div>
  );
}
