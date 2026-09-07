// One cell's verdict, shared by every column of the matrix. `unknown` is deliberately styled the same as `none`
// in emphasis (dim, not red) but labelled "not checked" — the whole point of carrying it separately from `none`
// is that a reader must never mistake "we never looked" for "the rival lacks this".

import { STATUS_LABEL, type ParityStatus } from "@/lib/parity";

const TONE: Record<ParityStatus, string> = {
  have: "border-[var(--color-accent)] text-[var(--color-accent)]",
  partial: "border-[var(--color-border)] text-[var(--color-text)]",
  none: "border-[var(--color-border)] text-[var(--color-text-dim)]",
  unknown: "border-[var(--color-border)] text-[var(--color-text-dim)] italic",
};

export function StatusPill({ status }: { status: ParityStatus }) {
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[10px] whitespace-nowrap ${TONE[status]}`}
      title={status === "unknown" ? "Never checked against this rival" : undefined}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
