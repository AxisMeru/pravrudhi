"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { CandidateDetail, CandidateRow } from "@/lib/candidates";
import { candidateDetail, signedDelta } from "@/lib/candidates";
import { fixed } from "@/lib/num";

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border)] py-1.5 text-[12px] last:border-0">
      <span className="text-[var(--color-text-dim)]">{label}</span>
      <span className="text-right font-mono text-[var(--color-text)]">{value}</span>
    </div>
  );
}

// A candidate's full replayed record, plus its lineage: the propose event and every other ledger row the
// engine wrote for it (see pravrudhi_kernel's CandidateView and server.py's GET /api/candidates/{cid}). A
// recorded demo snapshot carries no per-candidate ledger events, so that state is shown plainly rather than
// silently rendering an empty list.
export function CandidateDetailPanel({ row, onClose }: { row: CandidateRow; onClose: () => void }) {
  // Tagged with the id it was fetched for, so a still-loading fetch from a candidate the reader has already
  // moved on from never gets rendered as if it belonged to the current one — the same effect the removed
  // `setDetail(undefined)` reset achieved, but derived at render time instead of an extra synchronous commit.
  const [fetched, setFetched] = useState<{ id: string; detail: CandidateDetail | null } | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    candidateDetail(row.candidate.id)
      .then((d) => {
        if (!cancelled) setFetched({ id: row.candidate.id, detail: d });
      })
      .catch(() => {
        if (!cancelled) setFetched({ id: row.candidate.id, detail: null });
      });
    return () => {
      cancelled = true;
    };
  }, [row.candidate.id]);

  const detail = fetched?.id === row.candidate.id ? fetched.detail : undefined;
  const c = row.candidate;

  return (
    <div className="h-fit rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-mono text-sm text-[var(--color-text)]">{c.id}</p>
          <p className="text-[11px] text-[var(--color-text-dim)]">badge {c.badge}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-[11px] text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
        >
          close ✕
        </button>
      </div>

      <div className="mt-3">
        <Field label="surface" value={c.surface ?? "—"} />
        <Field label="proposed at seq" value={c.proposed_seq} />
        <Field label="edit family" value={c.edit_family ?? "—"} />
        <Field label="observations (n_obs)" value={c.n_obs} />
        <Field label="rebased" value={c.rebased} />
        <Field label="last score (Δ)" value={signedDelta(row.score)} />
        <Field label="paired Δ vs incumbent (mean)" value={c.n_obs > 0 ? signedDelta(row.pairedDelta) : "—"} />
        <Field label="cost (GPU-h)" value={fixed(c.cost_gpu_h, 2)} />
        <Field label="last boundary" value={c.last_boundary ?? "—"} />
        <Field label="promoted" value={c.promoted ? "yes" : "no"} />
        <Field label="pruned" value={c.pruned ?? "—"} />
        <Field label="audit high" value={c.audit_high ? "yes" : "no"} />
        <Field label="skipped" value={c.skipped ? "yes" : "no"} />
        <Field label="incumbent hash" value={c.incumbent_hash ?? "—"} />
        <Field
          label="was incumbent"
          value={row.wasIncumbent.length > 0 ? row.wasIncumbent.map((n) => `N${n}`).join(", ") : "—"}
        />
      </div>

      {c.bucket && (
        <div className="mt-3">
          <p className="mb-1 text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">Bucket</p>
          {Object.entries(c.bucket).map(([k, v]) => (
            <Field key={k} label={k} value={v} />
          ))}
        </div>
      )}

      <div className="mt-3">
        <p className="mb-1 text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">Lineage</p>
        {detail === undefined && <p className="text-[11px] text-[var(--color-text-dim)]">Loading…</p>}
        {detail === null && (
          <p className="text-[11px] text-[var(--color-text-dim)]">
            No ledger events available for this candidate — a recorded snapshot does not carry them.
          </p>
        )}
        {detail && (
          <div className="space-y-1.5">
            {detail.events.length === 0 && (
              <p className="text-[11px] text-[var(--color-text-dim)]">No ledger events found for this id.</p>
            )}
            {detail.events.map((e) => (
              <div key={e.seq} className="rounded-md border border-[var(--color-border)] p-2 text-[11px]">
                <div className="flex flex-wrap items-center gap-2 text-[var(--color-text-dim)]">
                  <span className="font-mono text-[var(--color-text)]">{e.kind}</span>
                  <span>night {e.night}</span>
                  <span>seq {e.seq}</span>
                  <span className="ml-auto">{e.actor}</span>
                </div>
                {Object.keys(e.payload ?? {}).length > 0 && (
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-[10px] text-[var(--color-text-dim)]">
                    {JSON.stringify(e.payload, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
