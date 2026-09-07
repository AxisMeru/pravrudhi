"use client";

// The compiled plan (application/intent.py's compile_intent, via GET /api/objectives/{id}/plan): each step's
// capability, the recipe(s) it could draw on and whether one is installed, and -- once Dispatch has been used
// -- the swarm's verdict on that step, read off the same run history the routing endpoint returns. Dispatch
// sends the whole plan in one request (there is no per-step dispatch endpoint), so one control fans every step
// out at once; each step's own row still shows only its own outcome.

import { useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, CircleDashed, XCircle } from "lucide-react";
import {
  dispatchSubagents,
  IS_DEMO,
  objectivePlan,
  objectiveSubagents,
  type Plan,
  type PlanStep,
  type SubagentRunRow,
  type SubagentsResponse,
} from "@/lib/api";
import { secs } from "@/lib/num";

// The engine's own field names for a dispatched run, layered onto the client's stale SubagentRunRow (which
// this task must not edit): an older engine that never sends them still satisfies this type via the optionals.
type EngineRunRow = SubagentRunRow & {
  agent?: string | null;
  model?: string | null;
  wall_s?: number | null;
  reason?: string | null;
};

function truncateReason(reason: string): string {
  return reason.length > 160 ? `${reason.slice(0, 160)}…` : reason;
}

function formatAgentModel(agent: string, model: string | null | undefined): string {
  return model ? `${agent}/${model}` : agent;
}

function availabilityLabel(a: PlanStep["availability"]): string {
  if (a === "available") return "recipe available";
  if (a === "uninstalled") return "recipe not installed";
  return "no recipe for this";
}

interface DispatchPoll {
  started: number;
  target: number;
  deadline: number;
}

const POLL_INTERVAL_MS = 5000;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

function StepVerdict({ run }: { run: EngineRunRow | undefined }) {
  if (!run) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-text-dim)]">
        <CircleDashed size={12} /> not yet dispatched
      </span>
    );
  }
  const reason = !run.accepted && run.reason ? truncateReason(run.reason) : null;
  return (
    <div className="text-right">
      <span
        className={`inline-flex items-center gap-1.5 text-[11px] ${
          run.accepted ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"
        }`}
        title={reason ?? undefined}
      >
        {run.accepted ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
        {formatAgentModel(run.agent ?? run.route, run.model)} · {secs(run.wall_s ?? run.wall, 0)}
      </span>
      {reason && <p className="mt-0.5 max-w-xs text-[11px] leading-4 text-[var(--color-text-dim)]">{reason}</p>}
    </div>
  );
}

function StepRow({ step, index, run }: { step: PlanStep; index: number; run: EngineRunRow | undefined }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 w-4 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-dim)]">
        {index + 1}
      </span>
      <div className="min-w-0 flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-sm text-[var(--color-text)]">{step.id}</span>
              <span className="font-mono text-[11px] text-[var(--color-text-dim)]">{step.capability}</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">{step.check.criterion}</p>
          </div>
          <StepVerdict run={run} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <span
            className={step.availability === "available" ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"}
          >
            {availabilityLabel(step.availability)}
          </span>
          {step.recipe_ids.length > 0 && (
            <span className="text-[var(--color-text-dim)]">
              recipes: {step.recipe_ids.join(", ")}
              {step.available_recipe_ids.length > 0 && step.available_recipe_ids.length < step.recipe_ids.length && (
                <> (installed: {step.available_recipe_ids.join(", ")})</>
              )}
            </span>
          )}
        </div>
        {step.quantities.length > 0 && (
          <p className="mt-1 text-[11px] text-[var(--color-text-dim)]">
            unspecified: {step.quantities.filter((q) => q.value === null).map((q) => q.name).join(", ") || "none"}
          </p>
        )}
      </div>
    </li>
  );
}

export function PlanSteps({ id }: { id: string }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [data, setData] = useState<SubagentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [poll, setPoll] = useState<DispatchPoll | null>(null);
  const [showRouting, setShowRouting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([objectivePlan(id), objectiveSubagents(id)])
      .then(([p, s]) => {
        if (cancelled) return;
        setPlan(p);
        setData(s);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Dispatch posts and forgets: the swarm keeps running after the response returns, so the step rows only
  // catch up by asking again. Polling stops once the run count reaches what was started, or after 15 minutes.
  useEffect(() => {
    if (!poll) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = () => {
      if (cancelled) return;
      objectiveSubagents(id)
        .then((next) => {
          if (cancelled) return;
          setData(next);
          if (next.runs.length >= poll.target || Date.now() >= poll.deadline) {
            setPoll(null);
            return;
          }
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        })
        .catch((e) => {
          if (cancelled) return;
          setError(e instanceof Error ? e.message : String(e));
          setPoll(null);
        });
    };

    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [poll, id]);

  const dispatch = async () => {
    setDispatching(true);
    setError(null);
    try {
      const started = data?.preview.length ?? 0;
      const baseline = data?.runs.length ?? 0;
      const result = await dispatchSubagents(id);
      setData(result);
      const target = baseline + started;
      if (started > 0 && result.runs.length < target) {
        setPoll({ started, target, deadline: Date.now() + POLL_TIMEOUT_MS });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDispatching(false);
    }
  };

  if (error) return <p className="text-sm text-[var(--color-danger)]">{error}</p>;
  if (!plan) return <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>;

  // The step a run belongs to is its own field; when several runs share a step (repeated dispatches), the
  // most recently returned one is what "verdict" means here, matching what a fresh dispatch would show next.
  const runByStep = new Map<string, EngineRunRow>();
  for (const raw of data?.runs ?? []) {
    const r = raw as EngineRunRow;
    runByStep.set(r.step, r);
  }

  return (
    <div>
      <p className="text-sm leading-6 text-[var(--color-text-dim)]">
        A proposed decomposition, not a record, compiled from the intent above. A quantity the objective does
        not supply is named rather than guessed.
      </p>

      <ol className="mt-4 space-y-2">
        {plan.steps.map((step, i) => (
          <StepRow key={step.id} step={step} index={i} run={runByStep.get(step.id)} />
        ))}
      </ol>

      {plan.assumptions && plan.assumptions.length > 0 && (
        <div className="mt-3">
          {plan.assumptions.map((a) => (
            <p key={a} className="text-[11px] leading-4 text-[var(--color-text-dim)]">
              Assumed: {a}
            </p>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--color-border)] pt-4">
        <button
          onClick={dispatch}
          disabled={IS_DEMO || dispatching || poll !== null || !data || data.preview.length === 0}
          title={IS_DEMO ? "This is a recording. Run the engine on your own machine to dispatch subagents." : undefined}
          className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-raised)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {dispatching || poll ? "Dispatching…" : "Dispatch this plan"}
        </button>
        {poll && <span className="text-[11px] text-[var(--color-text-dim)]">dispatching {poll.started}…</span>}
        {IS_DEMO && (
          <span className="text-[11px] text-[var(--color-text-dim)]">
            demo mode: dispatch is disabled because this is a recording
          </span>
        )}
        <button
          onClick={() => setShowRouting((v) => !v)}
          className="ml-auto flex items-center gap-1 text-[11px] text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
        >
          {showRouting ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          routing detail
        </button>
      </div>

      {showRouting && data && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="text-[var(--color-text-dim)]">
                <th className="pb-1 pr-3 font-normal">step</th>
                <th className="pb-1 pr-3 font-normal">tier</th>
                <th className="pb-1 pr-3 font-normal">agent / model</th>
                <th className="pb-1 font-normal">allowed path</th>
              </tr>
            </thead>
            <tbody>
              {data.preview.map((r, i) => (
                <tr key={`${r.step}-${i}`} className="border-t border-[var(--color-border)]">
                  <td className="py-1 pr-3 font-mono text-[var(--color-text)]">{r.step}</td>
                  <td className="py-1 pr-3 text-[var(--color-text-dim)]">{r.tier}</td>
                  <td className="py-1 pr-3 text-[var(--color-text-dim)]">{r.agent}</td>
                  <td className="py-1 font-mono text-[var(--color-text-dim)]">{r.allowed_path}</td>
                </tr>
              ))}
              {data.runs.map((raw, i) => {
                const r = raw as EngineRunRow;
                const reason = !r.accepted && r.reason ? truncateReason(r.reason) : null;
                return (
                  <tr key={`run-${r.step}-${i}`} className="border-t border-[var(--color-border)]" title={reason ?? undefined}>
                    <td className="py-1 pr-3 font-mono text-[var(--color-text)]">{r.step}</td>
                    <td className="py-1 pr-3 text-[var(--color-text-dim)]" colSpan={2}>
                      run: {formatAgentModel(r.agent ?? r.route, r.model)} · {r.accepted ? "accepted" : "rejected"}
                    </td>
                    <td className="py-1 tabular-nums text-[var(--color-text-dim)]">{secs(r.wall_s ?? r.wall, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
