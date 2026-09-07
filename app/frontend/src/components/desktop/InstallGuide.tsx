"use client";

// Per-platform install instructions, with the real asset names for the release this engine actually reports —
// never a hardcoded version — and the commands from app/desktop/README.md as copyable text.

import { useEffect, useState } from "react";
import { PLATFORM_INSTALLS, RELEASES_URL } from "@/lib/desktop";
import { versionInfo, type VersionInfo } from "@/lib/system";
import { CopyCommand } from "./CopyCommand";

export function InstallGuide() {
  const [v, setV] = useState<VersionInfo | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
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
        Download the asset for your platform from the{" "}
        <a href={RELEASES_URL} className="text-[var(--color-accent)] hover:underline" target="_blank" rel="noreferrer">
          releases page
        </a>
        {v ? (
          <>
            {" "}
            — the current release is <span className="font-mono text-[var(--color-text)]">{v.engine}</span>.
          </>
        ) : (
          " — match the asset to whichever release you download."
        )}
      </p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {PLATFORM_INSTALLS.map((platform) => (
          <div key={platform.id} className="rounded-md border border-[var(--color-border)] p-4">
            <h3 className="text-sm font-medium text-[var(--color-text)]">{platform.label}</h3>
            <p className="mt-1 font-mono text-xs text-[var(--color-text-dim)]">
              Pravrudhi-{v ? v.engine : "<version>"}.{platform.assetSuffix}
            </p>
            <div className="mt-3 space-y-2">
              {platform.steps.map((step) => (
                <div key={step.title}>
                  <div className="text-xs font-medium text-[var(--color-text)]">{step.title}</div>
                  <p className="mt-0.5 text-xs leading-5 text-[var(--color-text-dim)]">{step.detail}</p>
                </div>
              ))}
            </div>
            <div className="mt-3 space-y-2">
              {platform.commands.map((c) => (
                <CopyCommand key={c.label} label={c.label} command={c.command} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
