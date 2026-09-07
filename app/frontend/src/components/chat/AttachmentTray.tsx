"use client";

// What a drop produced, above the input and before the send: each attached file by name and size with a way to
// take it back out, and each refusal with the reason it was refused. A refusal that is not shown is a file that
// vanished, so both are rendered here and neither is collapsible.

import { Ban, FileText, X } from "lucide-react";
import { formatBytes, type Attachment, type AttachmentRefusal } from "@/lib/attachments";

export function AttachmentTray({
  attachments,
  refusals,
  reading,
  onRemove,
  onDismissRefusal,
}: {
  attachments: Attachment[];
  refusals: AttachmentRefusal[];
  reading: boolean;
  onRemove: (id: string) => void;
  onDismissRefusal: (id: string) => void;
}) {
  if (!reading && attachments.length === 0 && refusals.length === 0) return null;

  return (
    <div className="mb-2 space-y-2">
      {attachments.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {attachments.map((attachment) => (
            <li
              key={attachment.id}
              className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-1.5 pl-2.5 pr-1.5"
            >
              <FileText size={13} className="shrink-0 text-[var(--color-text-dim)]" />
              <span className="max-w-[16rem] truncate text-xs text-[var(--color-text)]" title={attachment.name}>
                {attachment.name}
              </span>
              <span className="shrink-0 font-mono text-[11px] text-[var(--color-text-dim)]">
                {formatBytes(attachment.size)}
                {attachment.lines > 0 &&
                  ` · ${attachment.lines.toLocaleString("en-US")} ${attachment.lines === 1 ? "line" : "lines"}`}
              </span>
              <button
                onClick={() => onRemove(attachment.id)}
                aria-label={`Remove ${attachment.name}`}
                title="Remove before sending"
                className="shrink-0 rounded p-1 text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {reading && <p className="text-xs text-[var(--color-text-dim)]">Reading the dropped file…</p>}

      {refusals.length > 0 && (
        // Announced rather than only painted: a dropped file that did not attach has to be heard as well as seen.
        <div aria-live="polite" className="space-y-2">
          {refusals.map((refusal) => (
            <div
              key={refusal.id}
              className="rounded-md border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 p-2.5"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="flex items-center gap-1.5 text-xs text-[var(--color-danger)]">
                  <Ban size={12} className="shrink-0" />
                  <span className="font-mono">{refusal.file.name}</span> was not attached
                </p>
                <button
                  onClick={() => onDismissRefusal(refusal.id)}
                  className="shrink-0 text-[11px] text-[var(--color-text-dim)] underline-offset-2 hover:text-[var(--color-text)] hover:underline"
                >
                  Dismiss
                </button>
              </div>
              <p className="mt-1 text-[11px] leading-4 text-[var(--color-text-dim)]">{refusal.why}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
