// One machine, stated plainly: what it can do — train, serve open models, run containers — and for anything it
// cannot, the specific measured reason. Reachability and the install running on it (when this is the machine
// the engine itself is on) are shown before any capability, since neither one is safe to assume.

import { CheckCircle2, DownloadCloud, XCircle } from "lucide-react";
import type { HostRow, InstallStatus } from "@/lib/machines";
import { capabilityFacts } from "@/lib/machines";
import { fixed } from "@/lib/num";

function ChipRow({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-xs text-[var(--color-text-dim)]">{empty}</p>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <span
          key={item}
          className="rounded-full border border-[var(--color-border)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-text)]"
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function InstallSection({ install, installError }: { install: InstallStatus | null; installError: boolean }) {
  if (installError) {
    return <p className="text-xs text-[var(--color-text-dim)]">Install status is not reported on this build.</p>;
  }
  if (install === null) {
    return <p className="text-xs text-[var(--color-text-dim)]">Checking install status…</p>;
  }
  return (
    <dl className="space-y-1.5 text-xs text-[var(--color-text-dim)]">
      <div className="flex justify-between">
        <dt>Channel</dt>
        <dd className="text-[var(--color-text)]">{install.channel}</dd>
      </div>
      <div className="flex justify-between">
        <dt>Version</dt>
        <dd className="font-mono text-[var(--color-text)]">
          {install.version}
          {install.git_describe ? ` (${install.git_describe})` : ""}
        </dd>
      </div>
      <div className="flex justify-between">
        <dt>Last checked for an update</dt>
        <dd className="text-[var(--color-text)]">{install.last_checked ?? "never"}</dd>
      </div>
      <div className="flex items-center justify-between">
        <dt>Update available</dt>
        <dd
          className={
            install.update_available ? "flex items-center gap-1 text-[var(--color-warn)]" : "text-[var(--color-text)]"
          }
        >
          {install.update_available && <DownloadCloud size={12} />}
          {install.update_available ? install.latest_tag ?? "yes" : "no"}
        </dd>
      </div>
    </dl>
  );
}

export function MachineCard({
  row,
  isLocal,
  install,
  installError,
}: {
  row: HostRow;
  isLocal: boolean;
  install: InstallStatus | null;
  installError: boolean;
}) {
  const { host, capabilities: cap } = row;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-sm font-medium text-[var(--color-text)]">{host.name}</span>
          <span className="ml-2 text-[11px] text-[var(--color-text-dim)]">{host.transport}</span>
        </div>
        {cap.reachable ? (
          <CheckCircle2 size={16} className="text-[var(--color-accent)]" />
        ) : (
          <XCircle size={16} className="text-[var(--color-danger)]" />
        )}
      </div>

      {!cap.reachable ? (
        <p className="mt-2 text-xs text-[var(--color-danger)]">{cap.error || "unreachable"}</p>
      ) : (
        <div className="mt-3 space-y-4">
          <dl className="space-y-1.5 text-xs text-[var(--color-text-dim)]">
            <div className="flex justify-between">
              <dt>OS / arch</dt>
              <dd className="text-[var(--color-text)]">
                {cap.os || "?"} / {cap.arch || "?"}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt>Accelerator</dt>
              <dd className="text-[var(--color-text)]">
                {cap.accelerator || "none"}
                {cap.gpu_name ? ` — ${cap.gpu_name}` : ""}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt>Usable memory</dt>
              <dd className="text-[var(--color-text)]">{fixed(cap.usable_model_gb)} GB</dd>
            </div>
          </dl>

          <div>
            <p className="mb-1.5 text-xs font-medium text-[var(--color-text-dim)]">Can do</p>
            <ul className="space-y-1.5">
              {capabilityFacts(cap).map((fact) => (
                <li key={fact.label} className="flex items-start gap-2 text-xs">
                  {fact.can ? (
                    <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-[var(--color-accent)]" />
                  ) : (
                    <XCircle size={13} className="mt-0.5 shrink-0 text-[var(--color-text-dim)]" />
                  )}
                  <span>
                    <span className="text-[var(--color-text)]">{fact.label}</span>
                    <span className="text-[var(--color-text-dim)]"> — {fact.reason}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="mb-1.5 text-xs font-medium text-[var(--color-text-dim)]">Coding agents detected</p>
            <ChipRow items={cap.agents} empty="none detected on this host" />
          </div>

          <div>
            <p className="mb-1.5 text-xs font-medium text-[var(--color-text-dim)]">Local models found</p>
            <ChipRow items={cap.local_models} empty="none found" />
          </div>

          <div className="border-t border-[var(--color-border)] pt-3">
            <p className="mb-1.5 text-xs font-medium text-[var(--color-text-dim)]">Install</p>
            {isLocal ? (
              <InstallSection install={install} installError={installError} />
            ) : (
              <p className="text-xs text-[var(--color-text-dim)]">
                not tracked — this host is enrolled for placement only; the engine does not run on it directly.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
