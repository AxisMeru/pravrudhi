// Band 2: what the engine is doing right now -- its own stated appetite, the last thing its heartbeat loop
// looked at and chose, any run in progress, and how many agent processes are actually working. This is the
// band that makes the engine feel alive, so every piece is real or plainly says it has nothing to report.

import Link from "next/link";
import { Activity, Bot, Play } from "lucide-react";
import type { AppetiteResponse } from "@/lib/appetite";
import type { HeartbeatBeat } from "@/lib/heartbeat";
import type { RunningRun } from "@/lib/home";
import { fixed } from "@/lib/num";

function AppetiteLine({ appetite }: { appetite: AppetiteResponse | null }) {
  if (!appetite) {
    return <p className="text-base leading-7 text-[var(--color-text-dim)]">The engine hasn&apos;t reported what it wants yet.</p>;
  }
  return (
    <div>
      <p className="text-lg leading-7 text-[var(--color-text)]">{appetite.sentence}</p>
      {appetite.appetite.resting_reason && (
        <p className="mt-1 text-xs text-[var(--color-muted)]">{appetite.appetite.resting_reason}</p>
      )}
    </div>
  );
}

function BeatLine({ beat }: { beat: HeartbeatBeat | null }) {
  if (!beat) {
    return <p className="text-sm text-[var(--color-text-dim)]">No heartbeat recorded yet.</p>;
  }
  return (
    <p className="text-sm leading-6 text-[var(--color-text-dim)]">
      Last heartbeat <span className="text-[var(--color-text)]">{beat.at}</span> looked at {beat.looked_at.length}{" "}
      objective{beat.looked_at.length === 1 ? "" : "s"} and{" "}
      {beat.chose ? (
        <>
          chose <span className="font-mono text-[var(--color-text)]">{beat.chose.step}</span> on{" "}
          <span className="font-mono text-[var(--color-text)]">{beat.chose.objective}</span>
          {beat.result && (
            <>
              {" "}
              {/* A beat records the agent only when one was actually dispatched, so "by ." with nothing after
                  it is what an absent name looked like. */}
              — {beat.result.accepted ? "accepted" : "not accepted"}
              {beat.result.agent ? ` by ${beat.result.agent}` : ""}
              {typeof beat.result.wall_s === "number" ? ` in ${fixed(beat.result.wall_s, 0)}s` : ""}
            </>
          )}
        </>
      ) : (
        <>chose nothing{beat.reason ? ` — ${beat.reason}` : ""}</>
      )}
      .
    </p>
  );
}

function RunningLine({ running }: { running: RunningRun | null }) {
  if (!running) {
    return <p className="text-sm text-[var(--color-text-dim)]">No run in progress right now.</p>;
  }
  return (
    <Link
      href={running.href}
      className="flex items-center gap-2 text-sm text-[var(--color-text)] hover:text-[var(--color-accent)]"
    >
      <Play size={14} className="shrink-0 text-[var(--color-accent)]" />
      <span className="capitalize">{running.target}</span>
      {running.model && <span className="font-mono">{running.model}</span>}
      {running.bench && <span className="text-[var(--color-text-dim)]">on {running.bench}</span>}
      {running.budgetGpuH !== null && (
        <span className="text-[var(--color-text-dim)]">— {fixed(running.budgetGpuH, 1)} GPU-h budget</span>
      )}
    </Link>
  );
}

export function NowBand({
  appetite,
  beat,
  running,
  agentsWorking,
}: {
  appetite: AppetiteResponse | null;
  beat: HeartbeatBeat | null;
  running: RunningRun | null;
  agentsWorking: number;
}) {
  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <div className="flex items-center gap-2">
        <Activity size={16} className="text-[var(--color-text-dim)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">Right now</h2>
      </div>

      <div className="mt-3">
        <AppetiteLine appetite={appetite} />
      </div>

      <div className="mt-4 space-y-2 border-t border-[var(--color-border)] pt-4">
        <BeatLine beat={beat} />
        <RunningLine running={running} />
        <Link
          href="/swarm"
          className="flex items-center gap-2 text-sm text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
        >
          <Bot size={14} className="shrink-0" />
          {agentsWorking > 0
            ? `${agentsWorking} agent${agentsWorking === 1 ? "" : "s"} working right now`
            : "No agent process running right now"}
        </Link>
      </div>
    </section>
  );
}
