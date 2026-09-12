"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, PlayCircle } from "lucide-react";
import { apiBase, IS_DEMO, health } from "@/lib/api";

/**
 * What the top of the page says about where its data comes from.
 *
 * On the public site the answer is fixed and honest: this is a recording of real runs, because a browser will not
 * let a public page reach an engine on the visitor's machine. That is stated once, calmly, with the way to get a
 * live one — not as an error, because nothing has gone wrong.
 */
export function ConnectionBanner() {
  const [reachable, setReachable] = useState(true);

  // IS_DEMO is NEXT_PUBLIC_DEMO, inlined at build time into both the server-rendered HTML and the client
  // bundle identically, so it needs neither a placeholder render nor an effect to read safely.
  useEffect(() => {
    if (IS_DEMO) return;
    let cancelled = false;

    async function check() {
      try {
        await health();
        if (!cancelled) setReachable(true);
      } catch {
        if (!cancelled) setReachable(false);
      }
    }

    check();
    const id = setInterval(check, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (IS_DEMO) {
    return (
      <div className="flex flex-wrap items-center gap-2 border-b border-emerald-500/30 bg-emerald-500/10 px-5 py-2 text-sm text-emerald-300">
        <PlayCircle size={14} />
        <span>
          Recorded from Pravrudhi Studio&apos;s own runs, on one RTX&nbsp;5090. To improve your own model,{" "}
          <a className="underline underline-offset-2 hover:text-emerald-200" href="/install">
            install the engine
          </a>{" "}
          and open it locally.
        </span>
      </div>
    );
  }

  if (reachable) return null;

  return (
    <div className="flex items-center gap-2 border-b border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-5 py-2 text-sm text-[var(--color-danger)]">
      <AlertTriangle size={14} />
      <span>
        No engine reachable at {apiBase() || window.location.origin}. Start one with <code>pravrudhi app</code>.
      </span>
    </div>
  );
}
