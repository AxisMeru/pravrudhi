"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { RequestItem } from "@/lib/requests";
import { CriteriaList } from "./CriteriaList";
import { ProgressBar } from "./ProgressBar";
import { StateChip } from "./StateChip";
import { Staleness } from "./Staleness";

export function RequestRow({ item }: { item: RequestItem }) {
  const [open, setOpen] = useState(false);
  const [met, total] = item.progress;
  const firstLine = item.text.split("\n")[0];

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full flex-wrap items-center gap-3 p-3 text-left"
      >
        {open ? (
          <ChevronDown size={14} className="shrink-0 text-[var(--color-text-dim)]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[var(--color-text-dim)]" />
        )}
        <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text)]">{firstLine}</span>
        <ProgressBar met={met} total={total} state={item.state} />
        <StateChip state={item.state} />
        <Staleness days={item.staleness_days} />
      </button>
      {open && (
        <div className="space-y-4 border-t border-[var(--color-border)] p-4">
          <div>
            <h3 className="mb-1 text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">
              Asked, in the operator&apos;s own words
            </h3>
            <p className="whitespace-pre-wrap text-sm text-[var(--color-text)]">{item.text}</p>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-[var(--color-text-dim)]">
            <span>asked {item.asked_at}</span>
            {item.session && <span className="font-mono">session {item.session}</span>}
          </div>
          {item.notes && (
            <div>
              <h3 className="mb-1 text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">Notes</h3>
              <p className="whitespace-pre-wrap text-sm text-[var(--color-text-dim)]">{item.notes}</p>
            </div>
          )}
          <div>
            <h3 className="mb-2 text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">
              Criteria ({met}/{total} met)
            </h3>
            <CriteriaList criteria={item.criteria} />
          </div>
        </div>
      )}
    </div>
  );
}
