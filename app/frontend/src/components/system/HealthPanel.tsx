"use client";

// Section 3: is it healthy. Every check named, and for any that failed, its own detail — never folded into a
// generic "unhealthy". `null` means this build has no /api/health-state at all, which the caller (page.tsx)
// treats as reason to omit the section outright, honestly, rather than render an empty shell.

import { useEffect, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { healthState, type HealthState } from "@/lib/system";
import { Empty } from "./Section";

export function useHealthState() {
  const [state, setState] = useState<HealthState | null | undefined>(undefined);
  const [errored, setErrored] = useState(false);

  useEffect(() => {
    let cancelled = false;
    healthState()
      .then((h) => {
        if (!cancelled) setState(h);
      })
      .catch(() => {
        if (!cancelled) {
          setState(null);
          setErrored(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { state, errored };
}

export function HealthPanel({ state, errored }: { state: HealthState | null | undefined; errored: boolean }) {
  if (state === undefined) return <Empty text="Loading…" />;
  if (state === null) {
    return errored ? (
      <Empty text="Could not reach the engine to ask." />
    ) : (
      <Empty text="This build does not report a health state." />
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-[var(--color-text)]">{state.state}</span>
      </div>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {state.checks.map((c) => (
          <li
            key={c.name}
            className="flex items-start gap-2 rounded-md border border-[var(--color-border)] p-3 text-xs"
          >
            {c.ok ? (
              <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[var(--color-accent)]" />
            ) : (
              <XCircle size={14} className="mt-0.5 shrink-0 text-red-500" />
            )}
            <div>
              <div className="font-medium text-[var(--color-text)]">{c.name}</div>
              {!c.ok && <div className="mt-0.5 text-[var(--color-text-dim)]">{c.detail}</div>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
