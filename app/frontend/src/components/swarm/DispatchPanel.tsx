"use client";

// The swarm page reported and could not act: it rendered routing rows with no control that dispatched anything.
// This panel is the control. It queues an ad hoc brief through /api/jobs and shows the board's job list as it
// runs, polling like LivePanel does because a job's state changes on the engine's own background thread, not on
// anything this page does.

import { useEffect, useState } from "react";
import { secs } from "@/lib/num";
import { IS_DEMO } from "@/lib/api";
import { cancelJob, jobs as fetchJobs, submitJob, type Job } from "@/lib/swarm";

const TIERS = ["mechanical", "standard", "design", "critical"] as const;
const POLICIES = ["proposal", "selfbuild", "review"] as const;
const POLL_MS = 4000;

const field =
  "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] disabled:cursor-not-allowed disabled:opacity-50";

function elapsedSeconds(job: Job): number | null {
  const start = job.started ?? job.created;
  const end = job.ended ?? new Date().toISOString();
  const ms = Date.parse(end) - Date.parse(start);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

function stateColor(state: Job["state"]): string {
  if (state === "accepted") return "text-[var(--color-accent)]";
  if (state === "rejected") return "text-[var(--color-danger)]";
  if (state === "cancelled") return "text-[var(--color-text-dim)]";
  return "text-[var(--color-text)]";
}

function JobRow({ job, onCancel }: { job: Job; onCancel: (id: string) => void }) {
  const elapsed = elapsedSeconds(job);
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${stateColor(job.state)}`}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: "currentColor" }} />
          {job.state}
        </span>
        <span className="text-[var(--color-text)]">{job.title}</span>
        <span className="ml-auto font-mono text-[11px] text-[var(--color-text-dim)]">
          {job.route ?? job.agent ?? job.tier}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[var(--color-text-dim)]">
        <span>{elapsed === null ? "—" : secs(elapsed, 0)} elapsed</span>
        <span>tier {job.tier}</span>
        <span>policy {job.policy}</span>
        {job.files.length > 0 && <span className="truncate font-mono">{job.files.join(", ")}</span>}
        {job.state === "queued" && (
          <button
            onClick={() => onCancel(job.id)}
            className="text-[var(--color-danger)] transition-colors hover:opacity-80"
          >
            cancel
          </button>
        )}
      </div>
      {job.reasons.length > 0 && (
        <ul className="mt-1 list-inside list-disc text-[11px] text-[var(--color-text-dim)]">
          {job.reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DispatchPanel() {
  const [rows, setRows] = useState<Job[]>([]);
  const [failed, setFailed] = useState(false);

  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [allowedPaths, setAllowedPaths] = useState("");
  const [validateCmd, setValidateCmd] = useState("uv run pytest -q");
  const [tier, setTier] = useState<(typeof TIERS)[number]>("standard");
  const [policy, setPolicy] = useState<(typeof POLICIES)[number]>("proposal");
  const [agent, setAgent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    if (IS_DEMO) return;
    fetchJobs()
      .then((next) => {
        setRows(next);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  };

  useEffect(() => {
    if (IS_DEMO) return;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const paths = allowedPaths
    .split(/[\n,]/)
    .map((p) => p.trim())
    .filter(Boolean);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await submitJob({
        title,
        brief,
        allowed_paths: paths,
        validate: validateCmd,
        tier,
        policy,
        agent: agent.trim() || null,
      });
      setTitle("");
      setBrief("");
      setAllowedPaths("");
      setAgent("");
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (id: string) => {
    try {
      await cancelJob(id);
    } finally {
      refresh();
    }
  };

  const canSubmit = !IS_DEMO && !busy && title.trim() !== "" && brief.trim() !== "" && paths.length > 0;

  return (
    <section>
      <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Dispatch</h2>
      <p className="mb-3 text-[11px] leading-4 text-[var(--color-text-dim)]">
        Hand the swarm a brief directly. It runs under the named sandbox policy, in its own worktree, and is
        accepted only if the validate command passes.
      </p>

      {IS_DEMO && (
        <p className="mb-3 text-sm text-[var(--color-text-dim)]">
          This is a recording. Dispatch needs a local engine.
        </p>
      )}

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="grid gap-3">
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Title</span>
            <input
              className={field}
              disabled={IS_DEMO}
              placeholder="tidy the routing table's error messages"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>

          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Brief</span>
            <textarea
              className={`${field} min-h-24 resize-y leading-6`}
              disabled={IS_DEMO}
              placeholder="What the agent should do, and what it should read to do it."
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
            />
          </label>

          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Allowed paths (one per line, or comma-separated)</span>
            <textarea
              className={`${field} min-h-16 resize-y font-mono leading-6`}
              disabled={IS_DEMO}
              placeholder="proposals/my-task/*"
              value={allowedPaths}
              onChange={(e) => setAllowedPaths(e.target.value)}
            />
          </label>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="grid gap-1.5">
              <span className="text-xs text-[var(--color-text-dim)]">Validate command</span>
              <input
                className={`${field} font-mono`}
                disabled={IS_DEMO}
                value={validateCmd}
                onChange={(e) => setValidateCmd(e.target.value)}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-xs text-[var(--color-text-dim)]">Tier</span>
              <select
                className={field}
                disabled={IS_DEMO}
                value={tier}
                onChange={(e) => setTier(e.target.value as (typeof TIERS)[number])}
              >
                {TIERS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1.5">
              <span className="text-xs text-[var(--color-text-dim)]">Sandbox policy</span>
              <select
                className={field}
                disabled={IS_DEMO}
                value={policy}
                onChange={(e) => setPolicy(e.target.value as (typeof POLICIES)[number])}
              >
                {POLICIES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1.5">
              <span className="text-xs text-[var(--color-text-dim)]">Pin an agent (optional)</span>
              <input
                className={field}
                disabled={IS_DEMO}
                placeholder="leave blank to let the router choose"
                value={agent}
                onChange={(e) => setAgent(e.target.value)}
              />
            </label>
          </div>
        </div>

        {error && <p className="mt-3 text-xs text-[var(--color-danger)]">{error}</p>}

        <div className="mt-4">
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Queuing…" : "Dispatch"}
          </button>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {!IS_DEMO && failed && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s job board.</p>
        )}
        {!IS_DEMO && !failed && rows.length === 0 && (
          <p className="text-sm text-[var(--color-text-dim)]">No jobs queued or dispatched yet.</p>
        )}
        {rows.map((job) => (
          <JobRow key={job.id} job={job} onCancel={cancel} />
        ))}
      </div>
    </section>
  );
}
