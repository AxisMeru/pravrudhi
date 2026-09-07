"use client";

// Part of section 4: what the builders have on hand — connectors, MCP servers and model servers alongside the
// agents already listed above, plus the policy and recipe inventory routing draws from.

import { useEffect, useState } from "react";
import { fixed } from "@/lib/num";
import { capabilities as fetchCapabilities, type Capabilities } from "@/lib/system";
import { Empty } from "./Section";

function ToolChip({ id, kind, available }: { id: string; kind: string; available: boolean }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] ${
        available
          ? "border-[var(--color-border)] text-[var(--color-text)]"
          : "border-[var(--color-border)] text-[var(--color-text-dim)] line-through"
      }`}
      title={`${kind}${available ? "" : " — unavailable"}`}
    >
      {id}
    </span>
  );
}

export function CapabilitiesPanel() {
  const [caps, setCaps] = useState<Capabilities | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    fetchCapabilities()
      .then((c) => {
        if (!cancelled) setCaps(c);
      })
      .catch(() => {
        if (!cancelled) setCaps(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (caps === undefined) return <Empty text="Loading…" />;
  if (caps === null) return <Empty text="This engine has not reported its tool inventory." />;

  return (
    <div>
      <dl className="grid grid-cols-3 gap-3 text-xs">
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <dt className="text-[var(--color-text-dim)]">Policies</dt>
          <dd className="mt-1 text-lg text-[var(--color-text)]">{fixed(caps.policies.length, 0)}</dd>
        </div>
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <dt className="text-[var(--color-text-dim)]">Recipes</dt>
          <dd className="mt-1 text-lg text-[var(--color-text)]">{fixed(caps.recipes, 0)}</dd>
        </div>
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <dt className="text-[var(--color-text-dim)]">Pages published</dt>
          <dd className="mt-1 text-lg text-[var(--color-text)]">{fixed(caps.pages.length, 0)}</dd>
        </div>
      </dl>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {caps.tools.map((t) => (
          <ToolChip key={t.id} id={t.id} kind={t.kind} available={t.available} />
        ))}
      </div>
    </div>
  );
}
