"use client";

// Section 1: how the engine updates itself. The two channels it can run on are a fixed pair — what varies is
// which one *this* install follows, and with what cadence — so both cards render unconditionally and only the
// one matching the fetched config gets live numbers; the other states plainly that this install carries no data
// for it, rather than repeating the same figures under a different label.

import { useEffect, useState } from "react";
import { fixed } from "@/lib/num";
import { updateConfig, type Channel, type UpdateConfig } from "@/lib/system";
import { Empty } from "./Section";

const CHANNEL_INFO: Record<Channel, { label: string; blurb: string }> = {
  dev: {
    label: "Developer channel",
    blurb: "Follows this git branch directly — whatever the checkout is on, including commits still in flight.",
  },
  release: {
    label: "Release channel",
    blurb: "Follows tagged releases only — pinned to whatever version was last published, never a mid-branch commit.",
  },
};

const SAFEGUARDS = [
  "A run already in progress blocks a second one — no two updates apply at once.",
  "The downloaded release is checksummed before anything is unpacked from it.",
  "It is unpacked into a fresh directory, never over the running install.",
  "The new install answers a self-check before it is trusted with traffic.",
  "The switch to it is atomic — a symlink flip, not a file-by-file copy.",
  "The version it replaces is kept, not deleted, so a rollback has something to return to.",
  "A kill switch can disable auto-apply on any install without touching its code.",
];

function ChannelCard({ channel, cfg }: { channel: Channel; cfg: UpdateConfig | null }) {
  const info = CHANNEL_INFO[channel];
  const isThisInstall = cfg?.channel === channel;
  return (
    <div className="rounded-md border border-[var(--color-border)] p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-[var(--color-text)]">{info.label}</h3>
        {isThisInstall && (
          <span className="rounded-full border border-[var(--color-accent)] px-2 py-0.5 text-[10px] text-[var(--color-accent)]">
            this install
          </span>
        )}
      </div>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">{info.blurb}</p>
      {isThisInstall && cfg ? (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <dt className="text-[var(--color-text-dim)]">Checks every</dt>
          <dd className="text-[var(--color-text)]">{fixed(cfg.check_interval_min, 0)} min</dd>
          <dt className="text-[var(--color-text-dim)]">Applies automatically</dt>
          <dd className="text-[var(--color-text)]">{cfg.auto_apply ? "yes" : "no — waits to be told"}</dd>
          <dt className="text-[var(--color-text-dim)]">Keeps previous versions</dt>
          <dd className="text-[var(--color-text)]">{fixed(cfg.keep_previous, 0)}</dd>
          <dt className="text-[var(--color-text-dim)]">Running version</dt>
          <dd className="text-[var(--color-text)]">{cfg.current.version}</dd>
          <dt className="text-[var(--color-text-dim)]">Kernel</dt>
          <dd className="text-[var(--color-text)]">{cfg.current.kernel_version}</dd>
          {cfg.current.git_describe && (
            <>
              <dt className="text-[var(--color-text-dim)]">Git describe</dt>
              <dd className="font-mono text-[var(--color-text)]">{cfg.current.git_describe}</dd>
            </>
          )}
        </dl>
      ) : (
        <p className="mt-3 text-xs text-[var(--color-text-dim)]">
          This install does not run this channel — no cadence or version to report for it here.
        </p>
      )}
    </div>
  );
}

export function UpdateChannels() {
  const [cfg, setCfg] = useState<UpdateConfig | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    updateConfig()
      .then((c) => {
        if (!cancelled) setCfg(c);
      })
      .catch(() => {
        if (!cancelled) setCfg(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (cfg === undefined) return <Empty text="Loading…" />;

  return (
    <div>
      {cfg === null && (
        <p className="mb-3 text-xs text-[var(--color-text-dim)]">
          This install has not reported its update configuration.
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        <ChannelCard channel="dev" cfg={cfg} />
        <ChannelCard channel="release" cfg={cfg} />
      </div>
      <h3 className="mt-5 text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
        What stands between a new release and being switched to
      </h3>
      <ul className="mt-2 grid gap-1.5 text-xs leading-5 text-[var(--color-text)] sm:grid-cols-2">
        {SAFEGUARDS.map((s) => (
          <li key={s} className="flex gap-2">
            <span className="text-[var(--color-text-dim)]">•</span>
            <span>{s}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
