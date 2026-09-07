"use client";

// The Search page: whether this engine has actually been searching.
//
// Two measurements, both folded from the ledger. How the candidate graph branches, which says how much of the
// space the loop has reached from how many starting points. And how often the live pool exceeded what the budget
// could run, which is the only condition under which a selection rule earns anything at all — a controller that
// ranks a set it is going to run in full has decided nothing, however good the ranking.

import { useEffect, useState } from "react";
import { GitBranch } from "lucide-react";
import { search as fetchSearch, type SearchSnapshot } from "@/lib/search";
import { Section, Empty } from "@/components/system/Section";

function useSearch() {
  const [snap, setSnap] = useState<SearchSnapshot | null | undefined>(undefined);
  useEffect(() => {
    let off = false;
    fetchSearch()
      .then((s) => !off && setSnap(s))
      .catch(() => !off && setSnap(null));
    return () => {
      off = true;
    };
  }, []);
  return snap;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] p-4">
      <div className="text-xs uppercase tracking-wide text-[var(--color-text-dim)]">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-text)]">{value}</div>
      {hint ? <div className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">{hint}</div> : null}
    </div>
  );
}

// Each night is one row: the live pool as a full-width bar, the part the budget ran filled in. The gap is what
// the controller left on the table, which is the thing being measured.
function PressureChart({ snap }: { snap: SearchSnapshot }) {
  const widest = Math.max(...snap.pressure.map((p) => p.live), 1);
  return (
    <div className="space-y-1">
      {snap.pressure.map((p) => {
        const livePct = (p.live / widest) * 100;
        const ranPct = (p.selected / widest) * 100;
        return (
          <div key={p.night} className="flex items-center gap-3 text-xs">
            <div className="w-14 shrink-0 text-right tabular-nums text-[var(--color-text-dim)]">night {p.night}</div>
            <div className="relative h-5 flex-1 rounded bg-[var(--color-surface)]">
              <div
                className="absolute inset-y-0 left-0 rounded bg-[var(--color-border)]"
                style={{ width: `${livePct}%` }}
                title={`${p.live} live in the pool`}
              />
              <div
                className={`absolute inset-y-0 left-0 rounded ${p.binding ? "bg-amber-500/70" : "bg-emerald-500/60"}`}
                style={{ width: `${ranPct}%` }}
                title={`${p.selected} actually run`}
              />
            </div>
            <div className="w-32 shrink-0 tabular-nums text-[var(--color-text-dim)]">
              {p.selected} of {p.live}
              {p.declined > 0 ? <span className="text-amber-600"> · {p.declined} declined</span> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function SearchPage() {
  const snap = useSearch();
  const recent = snap ? snap.pressure.filter((p) => p.night >= 7) : [];
  const recentBinding = recent.filter((p) => p.binding).length;

  return (
    <div>
      <div className="border-b border-[var(--color-border)] px-6 py-6">
        <h1 className="text-lg font-semibold text-[var(--color-text)]">Search</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--color-text-dim)]">
          Whether the engine has been searching, or climbing. Both numbers below are folded from the ledger and
          neither is a score: they describe the shape of what the loop has explored and how often its selection
          rule was actually asked to choose.
        </p>
      </div>

      <div className="px-6 py-6">
        {snap === undefined ? (
          <Empty text="Reading the ledger…" />
        ) : snap === null ? (
          <Empty text="No ledger reachable, so there is nothing to describe." />
        ) : (
          <div className="space-y-8">
            <Section title="Branching" icon={GitBranch}>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="candidates" value={String(snap.ancestry.nodes)} hint="proposed across every night" />
                <Stat
                  label="distinct parents"
                  value={String(snap.ancestry.distinct_parents)}
                  hint="every candidate descends from one of these"
                />
                <Stat
                  label="deepest lineage"
                  value={`${snap.ancestry.max_depth}`}
                  hint={snap.ancestry.max_depth <= 1 ? "one generation: a star, not a tree" : "generations from a root"}
                />
                <Stat
                  label="widest parent"
                  value={snap.ancestry.widest ? String(snap.ancestry.widest[1]) : "—"}
                  hint={snap.ancestry.widest ? `children of ${snap.ancestry.widest[0]}` : "no parent recorded"}
                />
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-6 text-[var(--color-text-dim)]">
                A candidate&apos;s parent is whichever artifact was standing when it was built. When nothing
                survives to become one, every candidate hangs off the same small set of ancestors and the graph is
                a star rather than a tree.
              </p>
            </Section>

            <Section title="Did the budget force a choice?" icon={GitBranch}>
              <p className="mb-4 max-w-3xl text-sm leading-6 text-[var(--color-text-dim)]">
                The full bar is the live pool on the bench that night. The filled part is what the budget ran.
                Amber means the rule had to leave something out; green means it could afford everything it had, and
                on those nights no selection rule can be distinguished from any other.
              </p>
              <PressureChart snap={snap} />
              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                <Stat
                  label="nights with a choice"
                  value={`${snap.binding_nights} of ${snap.pressure.length}`}
                  hint="the budget ran short of the pool"
                />
                <Stat label="candidates declined" value={String(snap.declined)} hint="summed over every night" />
                <Stat
                  label="recent nights with a choice"
                  value={recent.length ? `${recentBinding} of ${recent.length}` : "—"}
                  hint="from night 7 onward"
                />
              </div>
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}
