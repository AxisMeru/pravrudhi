"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, Rocket } from "lucide-react";
import { IS_DEMO, dispatchSubagents, postObjective, type Objective, type ObjectiveDraft } from "@/lib/start";

interface Props {
  draft: ObjectiveDraft;
}

export function StepCreate({ draft }: Props) {
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Objective | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [started, setStarted] = useState<number | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const objective = await postObjective({
        id: draft.id,
        intent: draft.intent,
        track: draft.track,
        domain: draft.domain,
        benchmarks: draft.benchmarks,
        recipes: [],
        target_delta: draft.targetDelta,
        notes: "",
      });
      setCreated(objective);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const dispatch = async () => {
    if (!created) return;
    setDispatching(true);
    setError(null);
    try {
      const result = await dispatchSubagents(created.id);
      setStarted(result.runs.length);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDispatching(false);
    }
  };

  return (
    <div>
      <h2 className="text-sm font-medium text-[var(--color-text)]">Create and go</h2>
      <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--color-text-dim)]">
        This records the objective exactly as reviewed. Nothing before this button has written anything.
      </p>

      {!created && (
        <div className="mt-4 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <p className="text-sm text-[var(--color-text)]">{draft.intent || "(no intent stated)"}</p>
          <p className="mt-2 font-mono text-[11px] text-[var(--color-text-dim)]">
            {draft.id || "(no name)"} · track {draft.track || "(none)"} ·{" "}
            {draft.benchmarks.length} benchmark{draft.benchmarks.length === 1 ? "" : "s"}
          </p>
        </div>
      )}

      {error && <p className="mt-3 text-xs text-[var(--color-danger)]">{error}</p>}

      {!created && (
        <button
          onClick={create}
          disabled={IS_DEMO || busy || !draft.id || !draft.intent || !draft.track || draft.benchmarks.length === 0}
          title={IS_DEMO ? "This is a recording. Run the engine on your own machine to create an objective." : undefined}
          className="mt-4 rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Creating…" : "Create objective"}
        </button>
      )}

      {created && (
        <div className="mt-4 rounded-md border border-[var(--color-accent)] bg-[var(--color-surface)] p-4">
          <div className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            <CheckCircle2 size={16} className="text-[var(--color-accent)]" />
            {created.id} is recorded.
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Link
              href="/objectives"
              className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-raised)]"
            >
              View it on the Objectives page
            </Link>
            <button
              onClick={dispatch}
              disabled={dispatching || started !== null}
              className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-raised)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Rocket size={14} />
              {dispatching ? "Dispatching…" : started !== null ? `Dispatched ${started}` : "Dispatch its first step"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
