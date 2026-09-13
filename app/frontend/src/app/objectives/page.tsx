"use client";

// The surface the whole product is for: state what you want, and see whether it is happening.
//
// Three states are kept visibly distinct because they mean different things. An objective with no baseline has not
// been measured; an objective with a baseline and nothing to compare against has been measured once; only the third
// carries a difference. Rendering the first two as a zero would tell the user the loop had failed when in fact it
// has not yet run.

import { Fragment, useCallback, useEffect, useState } from "react";
import {
  Target,
  Plus,
  CircleDashed,
  Minus,
  TrendingUp,
  TrendingDown,
  Package,
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
} from "lucide-react";
import {
  objectives as fetchObjectives,
  objectivePlan,
  objectiveLoom,
  objectiveSubagents,
  dispatchSubagents,
  recipeLibrary,
  type Plan,
  type LoomResponse,
  type SubagentsResponse,
  type SubagentRunRow,
  type BenchmarkProgress,
  type Objective,
  type Recipe,
  IS_DEMO,
  postObjective,
} from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

// SubagentRunRow.wall has gone stale against the engine's own field name; wall_s is read first and wall kept
// only as a fallback for an older engine build that never sent it.
function formatWall(wallS: number | null | undefined, legacyWall: number | null | undefined): string {
  return `${Math.round(wallS ?? legacyWall ?? 0)}s`;
}

function formatAgentModel(agent: string, model: string | null | undefined): string {
  return model ? `${agent}/${model}` : agent;
}

function truncateReason(reason: string): string {
  return reason.length > 120 ? reason.slice(0, 120) : reason;
}

function Verdict({ p }: { p: BenchmarkProgress }) {
  if (p.state !== "measured" || p.delta === null) return null;
  if (!p.significant) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[var(--color-text-dim)]">
        <Minus size={14} /> not distinguishable from no change
      </span>
    );
  }
  const up = p.delta > 0;
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${up ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"}`}
    >
      {up ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
      {up ? "improvement" : "regression"}
    </span>
  );
}

function Bar({ p }: { p: BenchmarkProgress }) {
  if (p.state === "unmeasured" || !p.baseline) return null;
  const base = p.baseline.value;
  const now = p.latest ? p.latest.value : base;
  const top = Math.max(base, now, 0.0001);
  const scale = (v: number) => `${Math.max(2, (v / top) * 100)}%`;
  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-3 text-xs">
        <span className="w-16 shrink-0 text-[var(--color-text-dim)]">baseline</span>
        <div className="h-2 flex-1 rounded-full bg-[var(--color-surface-raised)]">
          <div className="h-2 rounded-full bg-[var(--color-text-dim)]" style={{ width: scale(base) }} />
        </div>
        <span className="w-16 shrink-0 text-right tabular-nums text-[var(--color-text)]">{pct(base)}</span>
      </div>
      {p.latest && (
        <div className="flex items-center gap-3 text-xs">
          <span className="w-16 shrink-0 text-[var(--color-text-dim)]">current</span>
          <div className="h-2 flex-1 rounded-full bg-[var(--color-surface-raised)]">
            <div
              className={`h-2 rounded-full ${
                p.significant && p.delta !== null && p.delta > 0
                  ? "bg-[var(--color-accent)]"
                  : p.significant
                    ? "bg-[var(--color-danger)]"
                    : "bg-[var(--color-text-dim)]"
              }`}
              style={{ width: scale(now) }}
            />
          </div>
          <span className="w-16 shrink-0 text-right tabular-nums text-[var(--color-text)]">{pct(now)}</span>
        </div>
      )}
    </div>
  );
}

function BenchmarkCard({ p }: { p: BenchmarkProgress }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-xs text-[var(--color-text)]">{p.benchmark}</span>
        <Verdict p={p} />
      </div>

      {p.state === "unmeasured" && (
        <p className="mt-2 flex items-start gap-2 text-xs leading-5 text-[var(--color-text-dim)]">
          <CircleDashed size={14} className="mt-0.5 shrink-0" />
          {p.reason}
        </p>
      )}

      <Bar p={p} />

      {p.state === "baseline_only" && (
        <p className="mt-2 text-xs text-[var(--color-text-dim)]">{p.reason}</p>
      )}

      {p.state === "measured" && p.delta !== null && (
        <div className="mt-3 border-t border-[var(--color-border)] pt-3">
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-[var(--color-text-dim)]">change</span>
            <span className="tabular-nums text-[var(--color-text)]">
              {p.delta >= 0 ? "+" : ""}
              {pct(p.delta)}
              {p.delta_lo !== null && p.delta_hi !== null && (
                <span className="ml-2 text-[var(--color-text-dim)]">
                  [{p.delta_lo >= 0 ? "+" : ""}
                  {pct(p.delta_lo)}, {p.delta_hi >= 0 ? "+" : ""}
                  {pct(p.delta_hi)}]
                </span>
              )}
            </span>
          </div>
          {p.target_delta !== null && (
            <div className="mt-1 flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-text-dim)]">target</span>
              <span className="tabular-nums text-[var(--color-text)]">
                +{pct(p.target_delta)}
                <span className={`ml-2 ${p.met ? "text-[var(--color-accent)]" : "text-[var(--color-text-dim)]"}`}>
                  {p.met ? "met" : "not met"}
                </span>
              </span>
            </div>
          )}
          {p.baseline && p.latest && (
            <p className="mt-2 text-[11px] leading-4 text-[var(--color-text-dim)]">
              Scored outside the engine on {p.baseline.n} items. Baseline model {p.baseline.model}. Admitted to the
              ledger as rows {p.baseline.seq} and {p.latest.seq}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}


function PlanView({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || plan) return;
    objectivePlan(id)
      .then(setPlan)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [open, plan, id]);

  return (
    <div className="mt-4 border-t border-[var(--color-border)] pt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        How this intent becomes work
      </button>

      {open && (
        <div className="mt-3">
          <p className="mb-3 text-[11px] leading-4 text-[var(--color-text-dim)]">
            A proposed decomposition, not a record. Nothing below has run, and a quantity the objective does not
            supply is named rather than guessed.
          </p>
          {error && <p className="text-xs text-[var(--color-danger)]">{error}</p>}
          {!plan && !error && <p className="text-xs text-[var(--color-text-dim)]">Loading…</p>}
          {plan && (
            <ol className="space-y-2">
              {plan.steps.map((s, i) => (
                <li key={s.id} className="flex gap-3">
                  <span className="mt-0.5 w-4 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-dim)]">
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="text-xs text-[var(--color-text)]">{s.id}</span>
                      <span className="font-mono text-[11px] text-[var(--color-text-dim)]">{s.capability}</span>
                      <span
                        className={`ml-auto text-[11px] ${
                          s.availability === "available"
                            ? "text-[var(--color-accent)]"
                            : "text-[var(--color-danger)]"
                        }`}
                      >
                        {s.availability === "available"
                          ? "recipe available"
                          : s.availability === "uninstalled"
                            ? "recipe not installed"
                            : "no recipe for this"}
                      </span>
                    </div>
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
      )}
    </div>
  );
}

function LoomView({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [loom, setLoom] = useState<LoomResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open || loom) return;
    objectiveLoom(id)
      .then(setLoom)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [open, loom, id]);

  const copy = () => {
    if (!loom || !navigator.clipboard) return;
    navigator.clipboard.writeText(loom.source).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="mt-4 border-t border-[var(--color-border)] pt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        The plan as Loom
      </button>

      {open && (
        <div className="mt-3">
          <p className="mb-3 text-[11px] leading-4 text-[var(--color-text-dim)]">
            A proposed program, compiled from the plan above. Nothing below has run.
          </p>
          {error && <p className="text-xs text-[var(--color-danger)]">{error}</p>}
          {!loom && !error && <p className="text-xs text-[var(--color-text-dim)]">Loading…</p>}
          {loom && (
            <>
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-[var(--color-text-dim)]">source</span>
                <button
                  onClick={copy}
                  disabled={!loom.source}
                  className="flex items-center gap-1 text-[11px] text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {copied ? <Check size={12} /> : <Copy size={12} />}
                  {copied ? "copied" : "copy"}
                </button>
              </div>
              <pre className="mt-1 overflow-x-auto rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-3 font-mono text-[11px] leading-5 text-[var(--color-text)]">
                {loom.source || "(no Loom source for this objective yet)"}
              </pre>
              {loom.steps.length > 0 && (
                <ol className="mt-3 space-y-1.5">
                  {loom.steps.map((s, i) => (
                    <li key={s.id} className="flex gap-2 text-[11px] leading-4 text-[var(--color-text-dim)]">
                      <span className="w-4 shrink-0 text-right font-mono">{i + 1}</span>
                      <span className="shrink-0 font-mono text-[var(--color-text)]">{s.id}</span>
                      <span>{s.text}</span>
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// The engine's own field names for a dispatched run, layered onto the client's SubagentRunRow because api.ts is
// not this task's to change: an older engine that never sends them still satisfies this type via the optionals.
type EngineRunRow = SubagentRunRow & {
  agent?: string | null;
  model?: string | null;
  wall_s?: number | null;
  reason?: string | null;
};

interface DispatchPoll {
  started: number;
  target: number;
  deadline: number;
}

const POLL_INTERVAL_MS = 5000;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

function SubagentsView({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<SubagentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [poll, setPoll] = useState<DispatchPoll | null>(null);

  useEffect(() => {
    if (!open || data) return;
    objectiveSubagents(id)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [open, data, id]);

  // Dispatch posts and forgets: the engine keeps running after the response returns, so the runs table only
  // catches up by asking again. Polling stops as soon as the run count reaches what was started, or after 15
  // minutes, whichever comes first — this instance of `poll` never changes underneath the effect, only the
  // recursive timeout it owns does, so the loop is self-contained.
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
      const baseline = data?.runs.length ?? 0;
      const result = await dispatchSubagents(id);
      const target = baseline + result.started;
      if (result.started > 0) {
        setPoll({ started: result.started, target, deadline: Date.now() + POLL_TIMEOUT_MS });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDispatching(false);
    }
  };

  return (
    <div className="mt-4 border-t border-[var(--color-border)] pt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        Subagent routing
      </button>

      {open && (
        <div className="mt-3">
          <p className="mb-3 text-[11px] leading-4 text-[var(--color-text-dim)]">
            A proposed routing of steps to subagents. Nothing below has run until Dispatch is used, and dispatch is
            recorded as a run like any other.
          </p>
          {error && <p className="text-xs text-[var(--color-danger)]">{error}</p>}
          {!data && !error && <p className="text-xs text-[var(--color-text-dim)]">Loading…</p>}
          {data && (
            <>
              {data.preview.length === 0 && (
                <p className="text-xs text-[var(--color-text-dim)]">No subagent routing proposed for this objective.</p>
              )}
              {data.preview.length > 0 && (
                <div className="overflow-x-auto">
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
                    </tbody>
                  </table>
                </div>
              )}

              <div className="mt-3">
                <button
                  onClick={dispatch}
                  disabled={IS_DEMO || dispatching || poll !== null || data.preview.length === 0}
                  title={
                    IS_DEMO
                      ? "This is a recording. Run the engine on your own machine to dispatch subagents."
                      : undefined
                  }
                  className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-raised)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {dispatching || poll ? "Dispatching…" : "Dispatch"}
                </button>
                {poll && (
                  <p className="mt-2 text-[11px] text-[var(--color-text-dim)]">dispatching {poll.started}…</p>
                )}
              </div>

              {data.runs.length > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-left text-[11px]">
                    <thead>
                      <tr className="text-[var(--color-text-dim)]">
                        <th className="pb-1 pr-3 font-normal">step</th>
                        <th className="pb-1 pr-3 font-normal">agent / model</th>
                        <th className="pb-1 pr-3 font-normal">accepted</th>
                        <th className="pb-1 font-normal">wall</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.runs.map((raw, i) => {
                        const r = raw as EngineRunRow;
                        const reason = !r.accepted && r.reason ? truncateReason(r.reason) : null;
                        return (
                          <Fragment key={`${r.step}-${i}`}>
                            <tr className="border-t border-[var(--color-border)]" title={reason ?? undefined}>
                              <td className="py-1 pr-3 font-mono text-[var(--color-text)]">{r.step}</td>
                              <td className="py-1 pr-3 text-[var(--color-text-dim)]">
                                {formatAgentModel(r.agent ?? r.route, r.model)}
                              </td>
                              <td className="py-1 pr-3 text-[var(--color-text-dim)]">{r.accepted ? "yes" : "no"}</td>
                              <td className="py-1 tabular-nums text-[var(--color-text-dim)]">
                                {formatWall(r.wall_s, r.wall)}
                              </td>
                            </tr>
                            {reason && (
                              <tr className="border-t border-[var(--color-border)]">
                                <td colSpan={4} className="py-1 text-[11px] text-[var(--color-text-dim)]">
                                  {reason}
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ObjectiveCard({ o }: { o: Objective }) {
  return (
    <article className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Target size={16} className="text-[var(--color-text-dim)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">{o.id}</h2>
        {o.domain && (
          <span className="rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[11px] text-[var(--color-text-dim)]">
            {o.domain}
          </span>
        )}
        <span className="ml-auto font-mono text-[11px] text-[var(--color-text-dim)]">track {o.track}</span>
      </div>

      <p className="mt-3 text-sm leading-6 text-[var(--color-text)]">{o.intent}</p>

      <div className="mt-4 grid gap-3">
        {o.progress.map((p) => (
          <BenchmarkCard key={p.benchmark} p={p} />
        ))}
      </div>

      {o.notes && (
        <p className="mt-4 border-t border-[var(--color-border)] pt-3 text-xs leading-5 text-[var(--color-text-dim)]">
          {o.notes}
        </p>
      )}

      <PlanView id={o.id} />
      <LoomView id={o.id} />
      <SubagentsView id={o.id} />

      {o.recipes.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-[var(--color-text-dim)]">recipes</span>
          {o.recipes.map((r) => (
            <span
              key={r}
              className="rounded border border-[var(--color-border)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-text-dim)]"
            >
              {r}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

function NewObjective({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [intent, setIntent] = useState("");
  const [track, setTrack] = useState("");
  const [metric, setMetric] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        disabled={IS_DEMO}
        className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5 text-sm text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-raised)] disabled:cursor-not-allowed disabled:opacity-50"
        title={IS_DEMO ? "This is a recording. Run the engine on your own machine to state an objective." : undefined}
      >
        <Plus size={16} />
        State an objective
      </button>
    );
  }

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await postObjective({
        id,
        intent,
        track,
        domain,
        benchmarks: [{ id: "", tool: "lm-eval", metric, direction: "up" }],
        recipes: [],
        target_delta: null,
        notes: "",
      });
      setOpen(false);
      setId("");
      setIntent("");
      setTrack("");
      setMetric("");
      setDomain("");
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const field =
    "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <h2 className="text-sm font-medium text-[var(--color-text)]">What do you want the loop to achieve?</h2>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">
        Write it in your own words. It is recorded verbatim and never interpreted. An objective needs a benchmark:
        without one there is nothing that could tell you whether it worked.
      </p>

      <div className="mt-4 grid gap-3">
        <label className="grid gap-1.5">
          <span className="text-xs text-[var(--color-text-dim)]">Intent</span>
          <textarea
            className={`${field} min-h-24 resize-y leading-6`}
            placeholder="A legal-reasoning assistant that answers a question of law with the statute it relied on, and says it does not know rather than inventing a citation."
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Short name</span>
            <input className={field} placeholder="legal-mvp" value={id} onChange={(e) => setId(e.target.value)} />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Domain</span>
            <input className={field} placeholder="legal" value={domain} onChange={(e) => setDomain(e.target.value)} />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Track</span>
            <input className={field} placeholder="nyaya" value={track} onChange={(e) => setTrack(e.target.value)} />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Benchmark metric</span>
            <input
              className={field}
              placeholder="mmlu_professional_law acc,none"
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
            />
          </label>
        </div>
      </div>

      {error && <p className="mt-3 text-xs text-[var(--color-danger)]">{error}</p>}

      <div className="mt-4 flex gap-2">
        <button
          onClick={submit}
          disabled={busy || !id || !intent || !track || !metric}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Saving…" : "Record it"}
        </button>
        <button
          onClick={() => setOpen(false)}
          className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-dim)]"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function RecipeShelf({ rows }: { rows: Recipe[] }) {
  const byCapability = rows.reduce<Record<string, Recipe[]>>((acc, r) => {
    (acc[r.capability] ||= []).push(r);
    return acc;
  }, {});
  return (
    <section className="mt-10">
      <div className="flex items-center gap-2">
        <Package size={16} className="text-[var(--color-text-dim)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">Recipes an objective can draw on</h2>
      </div>
      <p className="mt-1 max-w-3xl text-xs leading-5 text-[var(--color-text-dim)]">
        Published training and evaluation recipes. Listing one here does not claim it has been run: a recipe becomes
        evidence only when a run executes it and the result is recorded.
      </p>
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(byCapability).map(([capability, list]) => (
          <div key={capability} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h3 className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">{capability}</h3>
            <ul className="mt-3 space-y-2.5">
              {list.map((r) => (
                <li key={r.id}>
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm text-[var(--color-text)]">{r.title}</span>
                    <span
                      className={`text-[11px] ${r.available ? "text-[var(--color-accent)]" : "text-[var(--color-text-dim)]"}`}
                    >
                      {r.available ? "available" : "not installed"}
                    </span>
                  </div>
                  <p className="text-[11px] leading-4 text-[var(--color-text-dim)]">{r.summary}</p>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function ObjectivesPage() {
  const [rows, setRows] = useState<Objective[] | null>(null);
  const [broken, setBroken] = useState<{ file: string; reason: string }[]>([]);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [unsupported, setUnsupported] = useState(false);

  const load = useCallback(() => {
    fetchObjectives()
      .then((d) => {
        setRows(d.objectives);
        setBroken(d.problems);
      })
      .catch(() => setUnsupported(true));
    recipeLibrary()
      .then(setRecipes)
      .catch(() => setRecipes([]));
  }, []);

  useEffect(load, [load]);

  return (
    <div>
      <PageHeader
        title="Objectives"
        subtitle="What you want, and whether it is happening. Every number here comes from a benchmark scored outside the engine."
      />
      <div className="p-8">
        {unsupported && (
          <p className="text-sm text-[var(--color-text-dim)]">This engine build does not report objectives yet.</p>
        )}

        {!unsupported && (
          <>
            <NewObjective onCreated={load} />

            {broken.length > 0 && (
              <div className="mt-6 rounded-md border border-[var(--color-danger)] bg-[var(--color-surface)] p-4">
                {broken.map((b) => (
                  <p key={b.file} className="text-xs text-[var(--color-danger)]">
                    {b.file} will not load: {b.reason}
                  </p>
                ))}
              </div>
            )}

            {rows === null && <p className="mt-6 text-sm text-[var(--color-text-dim)]">Loading…</p>}

            {rows !== null && rows.length === 0 && (
              <p className="mt-6 max-w-2xl text-sm leading-6 text-[var(--color-text-dim)]">
                No objectives yet. An objective is what you want the loop to achieve, plus the benchmark that would
                tell you whether it did.
              </p>
            )}

            {rows !== null && rows.length > 0 && (
              <div className="mt-6 grid gap-5 xl:grid-cols-2">
                {rows.map((o) => (
                  <ObjectiveCard key={o.id} o={o} />
                ))}
              </div>
            )}

            {recipes.length > 0 && <RecipeShelf rows={recipes} />}
          </>
        )}
      </div>
    </div>
  );
}
