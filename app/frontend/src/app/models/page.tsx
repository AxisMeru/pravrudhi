"use client";

// Rebuilt from the promotions-only list this page used to show: each promoted model is a candidate that
// survived the loop's gate, and the engine already knows far more about it than a bare before/after -- what
// base model and candidate it came from, the night and policy that produced it, its cost in GPU-hours, its
// recipe or edit family, and where its artefact lives. lib/models.ts joins /api/models against /api/candidates
// and /api/external so this page can show all of it without inventing anything the ledger does not contain.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";
import { ModelCard } from "@/components/models/ModelCard";
import { ComparisonTable } from "@/components/models/ComparisonTable";
import { modelCards, type ModelCard as ModelCardData } from "@/lib/models";

export default function ModelsPage() {
  const [rows, setRows] = useState<ModelCardData[] | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    modelCards()
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        if (!cancelled) setUnsupported(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectedModels = useMemo(() => (rows ?? []).filter((m) => selected.has(m.id)), [rows, selected]);

  return (
    <div>
      <PageHeader
        title="Models"
        subtitle="What the loop promoted, what it was derived from, and what an external scorer measured before and after."
      />
      <div className="space-y-6 p-8">
        {unsupported && (
          <p className="text-sm text-[var(--color-text-dim)]">This engine build does not report promotions yet.</p>
        )}
        {!unsupported && rows === null && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
        {!unsupported && rows !== null && rows.length === 0 && (
          <p className="max-w-2xl text-sm leading-6 text-[var(--color-text-dim)]">
            Nothing has been promoted yet. A promotion happens when a change survives the loop&apos;s own gate and is
            then scored by a benchmark outside the engine.{" "}
            <Link href="/start" className="text-[var(--color-accent)] hover:underline">
              Start a night
            </Link>{" "}
            to produce one.
          </p>
        )}
        {!unsupported && rows !== null && rows.length > 0 && (
          <>
            {selectedModels.length >= 2 && <ComparisonTable models={selectedModels} />}
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {rows.map((m) => (
                <ModelCard key={m.id} model={m} selected={selected.has(m.id)} onToggle={() => toggle(m.id)} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
