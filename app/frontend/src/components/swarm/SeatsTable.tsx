"use client";

// What each route costs, how it has actually done, and whether it can be used at all right now.
//
// The engine has answered this from the beginning — `pravrudhi routes` prints it and `/api/routes` serves it —
// and no page ever read it. The consequence is the one an operator most wants to know: on 2026-09-08 the 0.15
// seat sat out a vendor limit until 15:07 and its work went to the 2.90 seat, and nothing in the interface said
// so. A routing table that shows where work goes without showing what is unavailable is a plan, not a status.

import type { Seat } from "@/lib/swarm";

function record(seat: Seat): string {
  return seat.trials > 0 ? `${seat.successes}/${seat.trials}` : "—";
}

function returnsIn(returnsAt: string | null): string {
  if (!returnsAt) return "unavailable";
  const ms = new Date(returnsAt).getTime() - Date.now();
  if (Number.isNaN(ms)) return "unavailable";
  if (ms <= 0) return "returning";
  const mins = Math.round(ms / 60000);
  return mins >= 60 ? `back in ${Math.floor(mins / 60)}h ${mins % 60}m` : `back in ${mins}m`;
}

export function SeatsTable({ seats }: { seats: Seat[] }) {
  if (seats.length === 0) return null;
  const down = seats.filter((s) => !s.usable);
  const cheapestDown = down.length > 0 ? down.reduce((a, b) => (a.relative_cost <= b.relative_cost ? a : b)) : null;
  const cheapestUp = seats
    .filter((s) => s.usable && !s.sentinel)
    .reduce<Seat | null>((a, b) => (a === null || b.relative_cost < a.relative_cost ? b : a), null);

  return (
    <section>
      <h2 className="mb-1 text-sm font-medium text-[var(--color-text)]">Seats</h2>
      <p className="mb-3 text-xs text-[var(--color-text-dim)]">
        Every route the engine can spend on, what it costs relative to the others, and what it is doing now.
      </p>

      {cheapestDown && cheapestUp && cheapestUp.relative_cost > cheapestDown.relative_cost && (
        <p className="mb-3 rounded-md border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/10 px-3 py-2 text-xs text-[var(--color-text)]">
          <strong>{cheapestDown.id}</strong> ({cheapestDown.relative_cost.toFixed(2)}) is sitting out a usage limit,
          so work is going to <strong>{cheapestUp.id}</strong> ({cheapestUp.relative_cost.toFixed(2)}) meanwhile —{" "}
          {returnsIn(cheapestDown.returns_at)}.
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
        <table className="w-full min-w-[34rem] text-sm">
          <thead className="bg-[var(--color-surface)] text-left text-xs uppercase tracking-wide text-[var(--color-muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Route</th>
              <th className="px-3 py-2 font-medium">Agent</th>
              <th className="px-3 py-2 text-right font-medium">Cost</th>
              <th className="px-3 py-2 text-right font-medium">Record</th>
              <th className="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {seats.map((seat) => (
              <tr key={seat.id} className="border-t border-[var(--color-border)]">
                <td className="px-3 py-2 text-[var(--color-text)]">
                  {seat.id}
                  {seat.sentinel && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide text-[var(--color-muted)]">standby</span>
                  )}
                </td>
                <td className="px-3 py-2 text-[var(--color-text-dim)]">{seat.agent}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-dim)]">
                  {seat.relative_cost.toFixed(2)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-dim)]">{record(seat)}</td>
                <td className="px-3 py-2">
                  {seat.usable ? (
                    <span className="text-[var(--color-accent)]">ready</span>
                  ) : (
                    <span className="text-[var(--color-warn)]">{returnsIn(seat.returns_at)}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
