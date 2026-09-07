"use client";

// A single copyable shell command — the same real interaction the shell's own first-run screen offers per
// failed doctor check (a copyable recovery command), just for the install steps a visitor runs before the
// shell exists to run doctor at all.

import { useState } from "react";
import { Check, Copy } from "lucide-react";

export function CopyCommand({ label, command }: { label: string; command: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the command is still selectable text below */
    }
  }

  return (
    <div className="overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-3 py-1.5">
        <span className="text-xs text-[var(--color-text-dim)]">{label}</span>
        <button
          type="button"
          onClick={copy}
          aria-label={`Copy: ${label}`}
          className={
            "flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs text-[var(--color-text-dim)] " +
            "transition-colors hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)] " +
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
          }
        >
          {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre
        tabIndex={0}
        className={
          "overflow-x-auto p-3 text-xs leading-6 text-[var(--color-text)] " +
          "focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
        }
      >
        <code>{command}</code>
      </pre>
    </div>
  );
}
