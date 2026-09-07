"use client";

// How the shell stays current: it checks for engine updates through the same GET /api/update path and the
// same safeguarded pravrudhi update --apply --channel release --json apply as the System page describes — it
// does not have its own update mechanism. The version/channel numbers here come from the same engine the
// System page reads, never restated independently.

import { useEffect, useState } from "react";
import Link from "next/link";
import { updateConfig, versionInfo, type UpdateConfig, type VersionInfo } from "@/lib/system";
import { Empty } from "@/components/system/Section";

export function StaysCurrent() {
  const [cfg, setCfg] = useState<UpdateConfig | null | undefined>(undefined);
  const [v, setV] = useState<VersionInfo | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    updateConfig()
      .then((c) => {
        if (!cancelled) setCfg(c);
      })
      .catch(() => {
        if (!cancelled) setCfg(null);
      });
    versionInfo()
      .then((info) => {
        if (!cancelled) setV(info);
      })
      .catch(() => {
        if (!cancelled) setV(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <p className="text-xs leading-5 text-[var(--color-text-dim)]">
        The shell asks the engine it started whether a newer release exists (
        <code className="text-[var(--color-text)]">GET /api/update</code>), and, if you choose to apply one, hands
        the engine the same safeguarded command the System page describes (
        <code className="text-[var(--color-text)]">pravrudhi update --apply --channel release --json</code>). The
        engine owns every safeguard around that — checksum, atomic switch, kept rollback — the shell only shows the
        engine&apos;s own reported reason once it finishes.
      </p>
      {cfg === undefined || v === undefined ? (
        <div className="mt-3">
          <Empty text="Loading…" />
        </div>
      ) : (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:max-w-sm">
          <dt className="text-[var(--color-text-dim)]">This install&apos;s channel</dt>
          <dd className="text-[var(--color-text)]">{cfg ? cfg.channel : "unreported"}</dd>
          <dt className="text-[var(--color-text-dim)]">Release the shell drives</dt>
          <dd className="text-[var(--color-text)]">{v ? v.engine : "unreported"}</dd>
        </dl>
      )}
      <p className="mt-4 text-xs text-[var(--color-text-dim)]">
        See every machine this engine keeps current, and what stands between a new release and being switched to, on the{" "}
        <Link href="/system" className="text-[var(--color-accent)] hover:underline">
          System
        </Link>{" "}
        page.
      </p>
    </div>
  );
}
