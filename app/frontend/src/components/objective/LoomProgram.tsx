"use client";

// The plan compiled to Loom (application/loom.py's `lower`, via GET /api/objectives/{id}/loom): the actual
// program the engine would run, naming nothing the plan did not itself propose. A proposal, not a record --
// nothing here has executed.

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { objectiveLoom, type LoomResponse } from "@/lib/api";

export function LoomProgram({ id }: { id: string }) {
  const [loom, setLoom] = useState<LoomResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    objectiveLoom(id)
      .then((l) => {
        if (!cancelled) setLoom(l);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const copy = () => {
    if (!loom || !navigator.clipboard) return;
    navigator.clipboard.writeText(loom.source).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  if (error) return <p className="text-sm text-[var(--color-danger)]">{error}</p>;
  if (!loom) return <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>;

  return (
    <div>
      <p className="text-sm leading-6 text-[var(--color-text-dim)]">
        A proposed program, compiled from the plan above. Nothing below has run.
      </p>
      <div className="mt-4 flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-dim)]">source</span>
        <button
          onClick={copy}
          disabled={!loom.source}
          className="flex items-center gap-1 text-xs text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="mt-1 overflow-x-auto rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-4 font-mono text-xs leading-5 text-[var(--color-text)]">
        {loom.source || "(no Loom source for this objective yet)"}
      </pre>
      {loom.steps.length > 0 && (
        <ol className="mt-4 space-y-1.5">
          {loom.steps.map((s, i) => (
            <li key={s.id} className="flex gap-2 text-xs leading-5 text-[var(--color-text-dim)]">
              <span className="w-4 shrink-0 text-right font-mono">{i + 1}</span>
              <span className="shrink-0 font-mono text-[var(--color-text)]">{s.id}</span>
              <span>{s.text}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
