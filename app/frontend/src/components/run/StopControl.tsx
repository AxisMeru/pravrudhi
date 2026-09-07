"use client";

import { useState } from "react";
import { Square } from "lucide-react";
import { ApiError, stopRun, type RunHandle } from "@/lib/api";
import { RUNNING_STATUSES } from "@/lib/run";

// Calls the engine's own /runs/{id}/stop — the same endpoint a terminal user would hit — and shows
// exactly what it answered. Never a friendlier paraphrase: if the engine says the run already
// finished, that is what the operator needs to read, not a guess at what it meant.
export function StopControl({ runId, status, onStopped }: { runId: string; status: string; onStopped: (h: RunHandle) => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  if (!RUNNING_STATUSES.has(status)) return null;

  async function handleStop() {
    setBusy(true);
    try {
      const handle = await stopRun(runId);
      setResult(JSON.stringify(handle, null, 2));
      onStopped(handle);
    } catch (err) {
      setResult(err instanceof ApiError ? `HTTP ${err.status} from ${err.path}` : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-4">
      <button
        type="button"
        onClick={handleStop}
        disabled={busy}
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-dim)] transition-colors hover:border-[var(--color-danger)] hover:text-[var(--color-danger)] disabled:opacity-40"
      >
        <Square size={12} />
        {busy ? "Stopping…" : "Stop run"}
      </button>
      {result !== null && (
        <pre className="mt-3 max-h-40 overflow-auto rounded-md bg-[var(--color-surface-raised)] p-3 text-xs text-[var(--color-text-dim)]">
          {result}
        </pre>
      )}
    </div>
  );
}
