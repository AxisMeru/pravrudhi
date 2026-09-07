"use client";

// The compose box for a durable note. The store refuses a note that reads as a bare numeric claim about a
// result (memory.py::remember's `_NUMERIC_CLAIM_RE` guard) because that number belongs to the ledger, not to
// memory — if it's true the ledger already has it, and a copy kept here would go on lying the moment the ledger
// is repaired. When that happens this shows the engine's own refusal text verbatim, not a generic error.

import { useState } from "react";
import { MarkdownEditor } from "./MarkdownEditor";
import { IS_DEMO } from "@/lib/api";
import { RememberError, remember, type MemoryNote } from "@/lib/memory";

export function ComposeNote({ onRemembered }: { onRemembered: (note: MemoryNote) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setRefusal(null);
    setError(null);
    try {
      const note = await remember(text, "user");
      setText("");
      onRemembered(note);
    } catch (e) {
      if (e instanceof RememberError && e.status === 422) {
        setRefusal(e.message);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = !IS_DEMO && !busy && text.trim() !== "";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <MarkdownEditor text={text} onChange={setText} disabled={IS_DEMO || busy} />

      {IS_DEMO && (
        <p className="mt-2 text-sm text-[var(--color-text-dim)]">This is a recording. Remembering needs a local engine.</p>
      )}

      {refusal && (
        <div className="mt-3 rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 p-3 text-xs text-[var(--color-danger)]">
          <p className="font-mono leading-5">{refusal}</p>
          <p className="mt-2 leading-5 text-[var(--color-text-dim)]">
            Results belong to the ledger, not to memory: if this is true, the ledger already has it, and a copy
            kept here would go on lying the moment the ledger is repaired.
          </p>
        </div>
      )}

      {error && <p className="mt-3 text-xs text-[var(--color-danger)]">{error}</p>}

      <div className="mt-3">
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Remembering…" : "Remember"}
        </button>
      </div>
    </div>
  );
}
