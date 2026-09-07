"use client";

import { useEffect, useState } from "react";
import { previewPlan, type ObjectiveDraft, type Plan } from "@/lib/start";

interface Props {
  draft: ObjectiveDraft;
}

const AVAILABILITY_LABEL: Record<string, string> = {
  available: "recipe available",
  uninstalled: "recipe not installed",
  no_recipe: "no recipe for this",
};

export function StepPlan({ draft }: Props) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPlan(null);
    setError(null);
    previewPlan(draft)
      .then((p) => {
        if (!cancelled) setPlan(p);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.id, draft.intent, draft.domain, draft.track, draft.targetDelta, JSON.stringify(draft.benchmarks)]);

  return (
    <div>
      <h2 className="text-sm font-medium text-[var(--color-text)]">Review the compiled plan</h2>
      <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--color-text-dim)]">
        This is the same compiler the objective page calls after creation, run now against what you have typed so
        far. Nothing below has happened yet, and a quantity the intent does not supply is named rather than guessed.
      </p>

      {error && <p className="mt-4 text-xs text-[var(--color-danger)]">{error}</p>}
      {!plan && !error && <p className="mt-4 text-xs text-[var(--color-text-dim)]">Compiling…</p>}

      {plan && (
        <ol className="mt-4 space-y-2">
          {plan.steps.map((s, i) => (
            <li key={s.id} className="flex gap-3">
              <span className="mt-0.5 w-4 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-dim)]">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-xs text-[var(--color-text)]">{s.id}</span>
                  <span className="font-mono text-[11px] text-[var(--color-text-dim)]">{s.capability}</span>
                  <span
                    className={`ml-auto text-[11px] ${
                      s.availability === "available" ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"
                    }`}
                  >
                    {AVAILABILITY_LABEL[s.availability] ?? s.availability}
                  </span>
                </div>
                {s.recipe_ids.length > 0 && (
                  <p className="mt-1 text-[11px] text-[var(--color-text-dim)]">recipe: {s.recipe_ids.join(", ")}</p>
                )}
                <p className="mt-1 text-[11px] leading-4 text-[var(--color-text-dim)]">{s.check.criterion}</p>
                {s.quantities.length > 0 && (
                  <p className="mt-1 text-[11px] text-[var(--color-text-dim)]">
                    unspecified: {s.quantities.map((q) => q.name).join(", ")}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      {plan?.assumptions && plan.assumptions.length > 0 && (
        <div className="mt-3">
          {plan.assumptions.map((a) => (
            <p key={a} className="text-[11px] leading-4 text-[var(--color-text-dim)]">
              Assumed: {a}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
