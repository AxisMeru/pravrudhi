"use client";

// Page-level version line: the exact build this view is describing. Omitted entirely when the engine has not
// reported one, rather than shown with placeholder dashes.

import { useEffect, useState } from "react";
import { versionInfo, type VersionInfo } from "@/lib/system";

export function VersionBadge() {
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

  if (!v) return null;

  return (
    <span className="font-mono text-xs text-[var(--color-text-dim)]">
      engine {v.engine} · kernel {v.kernel} · {v.commit}
    </span>
  );
}
