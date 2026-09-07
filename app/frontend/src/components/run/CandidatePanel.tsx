import { Star } from "lucide-react";
import type { RunEvent } from "@/lib/api";
import { percent } from "@/lib/num";
import { candidateState, type CandidateRow } from "@/lib/run";

function ScoreBar({ incumbent, score }: { incumbent: number | undefined; score: number | undefined }) {
  // Both scores land in [0, 1] on this axis (they're accuracy fractions), so a plain percentage
  // width places the incumbent tick and the candidate fill on the same scale with no rescaling.
  const incPct = typeof incumbent === "number" ? Math.min(100, Math.max(0, incumbent * 100)) : null;
  const scorePct = typeof score === "number" ? Math.min(100, Math.max(0, score * 100)) : 0;
  const positive = typeof score === "number" && typeof incumbent === "number" && score >= incumbent;
  return (
    <div className="relative mt-1.5 h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-raised)]">
      <div
        className={`h-full rounded-full ${positive ? "bg-[var(--color-accent)]" : "bg-[var(--color-danger)]"}`}
        style={{ width: `${scorePct}%` }}
      />
      {incPct !== null && (
        <div
          className="absolute top-0 h-full w-0.5 bg-[var(--color-text)]"
          style={{ left: `${incPct}%` }}
          title="incumbent"
        />
      )}
    </div>
  );
}

function CandidateLine({ row, badge }: { row: CandidateRow; badge?: string }) {
  const positive = (row.delta ?? 0) >= 0;
  return (
    <li className="border-b border-[var(--color-border)] px-4 py-2 last:border-b-0">
      <div className="flex items-center gap-2 text-sm">
        {row.promoted && <Star size={13} className="shrink-0 text-[var(--color-accent)]" />}
        <span className="truncate font-mono text-xs text-[var(--color-text-dim)]">{row.candidate}</span>
        <span className={`ml-auto font-medium ${positive ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"}`}>
          {row.delta !== undefined ? `${positive ? "+" : ""}${percent(row.delta, 1)}` : "—"}
        </span>
        {badge && <span className="text-xs uppercase tracking-wide text-[var(--color-text-dim)]">{badge}</span>}
      </div>
      <ScoreBar incumbent={row.incumbent} score={row.candidateScore} />
    </li>
  );
}

export function CandidatePanel({ events }: { events: RunEvent[] }) {
  const state = candidateState(events);

  return (
    <section className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      <header className="border-b border-[var(--color-border)] px-4 py-3">
        <h2 className="text-sm font-medium text-[var(--color-text)]">Candidates</h2>
        <p className="text-xs text-[var(--color-text-dim)]">
          incumbent {state.incumbent !== null ? percent(state.incumbent, 1) : "—"}
        </p>
      </header>
      {state.rows.length === 0 && (
        <p className="px-4 py-6 text-sm text-[var(--color-text-dim)]">No candidate has been evaluated yet.</p>
      )}
      {state.rows.length > 0 && (
        <ul className="max-h-96 overflow-y-auto">
          {state.rows.map((row, i) => (
            <CandidateLine
              key={`${row.candidate}-${i}`}
              row={row}
              badge={row === state.current ? "current" : row === state.best ? "best" : undefined}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
