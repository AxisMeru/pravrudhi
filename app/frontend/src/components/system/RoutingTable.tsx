// Part of section 4: which model each tier routes to today, and why — plus, per tier, every route it measured
// to get there. A route costing under 1x is a local or free model: it only ever takes over when the paid routes
// above it are rate-limited or excluded, so it is marked "standby" rather than left to look like a live
// contender at the same weight class.

import { fixed, percent } from "@/lib/num";
import type { RoutingRecord, RoutingRow } from "@/lib/swarm";

const STANDBY_MAX_COST = 1;

function RecordRow({ r }: { r: RoutingRecord }) {
  const standby = r.relative_cost < STANDBY_MAX_COST;
  return (
    <tr className="border-t border-[var(--color-border)]">
      <td className="py-1.5 pr-3 font-mono text-[var(--color-text)]">{r.route_id}</td>
      <td className="py-1.5 pr-3 text-[var(--color-text-dim)]">
        {standby ? (
          <span className="rounded-full border border-[var(--color-border)] px-1.5 py-0.5 text-[10px]">
            standby
          </span>
        ) : (
          "paid"
        )}
      </td>
      <td className="py-1.5 pr-3 text-[var(--color-text)]">
        {r.successes}/{r.trials}
      </td>
      <td className="py-1.5 pr-3 text-[var(--color-text)]">{percent(r.rate)}</td>
      <td className="py-1.5 text-[var(--color-text)]">{fixed(r.relative_cost, 1)}x</td>
    </tr>
  );
}

function TierCard({ row }: { row: RoutingRow }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded-full border border-[var(--color-accent)] px-2 py-0.5 text-[10px] text-[var(--color-accent)]">
          {row.tier}
        </span>
        {row.route ? (
          <span className="font-mono text-sm text-[var(--color-text)]">
            {row.agent ?? "?"} / {row.model ?? row.route}
          </span>
        ) : (
          <span className="text-sm text-[var(--color-text-dim)]">no route chosen yet</span>
        )}
      </div>
      {row.reason && <p className="mt-1.5 text-xs leading-5 text-[var(--color-text-dim)]">{row.reason}</p>}
      {row.error && <p className="mt-1.5 text-xs leading-5 text-red-500">{row.error}</p>}
      {row.records.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[420px] text-left text-xs">
            <thead>
              <tr className="text-[var(--color-text-dim)]">
                <th className="pb-1 pr-3 font-normal">route</th>
                <th className="pb-1 pr-3 font-normal">kind</th>
                <th className="pb-1 pr-3 font-normal">wins</th>
                <th className="pb-1 pr-3 font-normal">rate</th>
                <th className="pb-1 font-normal">cost</th>
              </tr>
            </thead>
            <tbody>
              {row.records.map((r) => (
                <RecordRow key={r.route_id} r={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function RoutingTable({ rows }: { rows: RoutingRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">No tier has routed a task yet.</p>;
  }
  return (
    <div>
      <p className="mb-3 text-xs text-[var(--color-text-dim)]">
        Routes under 1x cost run locally or for free — marked <span className="italic">standby</span> because they
        only take over from a paid route when it is rate-limited, excluded, or simply not yet trusted at this tier.
      </p>
      <div className="grid gap-3 xl:grid-cols-2">
        {rows.map((row) => (
          <TierCard key={row.tier} row={row} />
        ))}
      </div>
    </div>
  );
}
