"use client";

// r-e84f8a50: the web door could not originate an ask; only the operator's local hook
// (`pravrudhi requests-capture`) could write the requests store. This posts through the same store, in the
// caller's own words, verbatim - nothing here paraphrases or drafts criteria; that is `triage`'s job, later.

import { useState } from "react";
import { IS_DEMO } from "@/lib/api";
import { captureRequest, type RequestItem } from "@/lib/requests";

export function AskBox({ onAsked }: { onAsked: (request: RequestItem) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const request = await captureRequest(text);
      setText("");
      onAsked(request);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = !IS_DEMO && !busy && text.trim() !== "";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={IS_DEMO || busy}
        placeholder="Ask for something. Your own words - nothing is paraphrased."
        rows={3}
        className="w-full resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-dim)] disabled:opacity-50"
      />

      {IS_DEMO && (
        <p className="mt-2 text-sm text-[var(--color-text-dim)]">This is a recording. Asking needs a local engine.</p>
      )}

      {error && <p className="mt-3 text-xs text-[var(--color-danger)]">{error}</p>}

      <div className="mt-3">
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </div>
    </div>
  );
}
