"use client";

import { lineAddresses } from "@/lib/annotations";
import { LineNotes } from "./Annotations";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { fixed } from "@/lib/num";
import type { DiffHunk, FileDiff } from "@/lib/diffs";

// Syntax-neutral: a line's colour comes only from its unified-diff role (add/del/context), never from the
// language it happens to be written in. The diff viewer has to render every file type the swarm might touch.
function HunkBlock({ hunk, file }: { hunk: DiffHunk; file: string }) {
  const addresses = lineAddresses(hunk);
  return (
    <div>
      <div className="bg-[var(--color-surface-raised)] px-3 py-1 font-mono text-[11px] text-[var(--color-text-dim)]">
        {hunk.header}
      </div>
      {hunk.lines.map((line, i) => (
        <div
          key={i}
          className={`flex whitespace-pre-wrap break-all px-3 font-mono text-[12px] leading-5 ${
            line.kind === "add"
              ? "bg-[var(--color-accent)]/10 text-[var(--color-text)]"
              : line.kind === "del"
                ? "bg-[var(--color-danger)]/10 text-[var(--color-text)]"
                : "text-[var(--color-text-dim)]"
          }`}
        >
          <span className="mr-2 inline-block w-3 shrink-0 select-none text-[var(--color-text-dim)]">
            {line.kind === "add" ? "+" : line.kind === "del" ? "-" : ""}
          </span>
          <span className="mr-2 w-16 shrink-0 text-[var(--color-text-dim)]">{addresses[i] ? `${addresses[i]!.side}:${addresses[i]!.line}` : ""}</span>
          <span className="min-w-0 flex-1">{line.text}</span>
          {addresses[i] && <LineNotes file={file} {...addresses[i]!} />}
        </div>
      ))}
    </div>
  );
}

// Each file starts collapsed to its stat line: a task's diff can span dozens of files, and nobody wants every
// one of them open at once. The gap between two hunks of the same file -- context git already left out of the
// patch -- gets its own marker rather than reading as a hunk that lost its neighbours.
export function DiffFilePanel({ file }: { file: FileDiff }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-raised)]"
      >
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-[var(--color-text-dim)]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[var(--color-text-dim)]" />
        )}
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-[var(--color-text)]" title={file.path}>
          {file.path}
        </span>
        {file.binary && (
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--color-text-dim)]">binary</span>
        )}
        {file.too_large && (
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--color-warn)]">truncated</span>
        )}
        <span className="shrink-0 font-mono text-[11px] text-[var(--color-accent)]">+{fixed(file.added, 0)}</span>
        <span className="shrink-0 font-mono text-[11px] text-[var(--color-danger)]">-{fixed(file.removed, 0)}</span>
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)]">
          {file.binary && (
            <p className="px-4 py-3 text-xs text-[var(--color-text-dim)]">Binary file: content is not shown.</p>
          )}
          {!file.binary && file.hunks.length === 0 && (
            <p className="px-4 py-3 text-xs text-[var(--color-text-dim)]">
              {file.too_large ? "This file's diff was too large to include." : "No line-level changes."}
            </p>
          )}
          {!file.binary &&
            file.hunks.map((hunk, i) => (
              <div key={i}>
                {i > 0 && <div className="px-3 py-1 text-center text-[10px] text-[var(--color-text-dim)]">⋯</div>}
                <HunkBlock hunk={hunk} file={file.path} />
              </div>
            ))}
          {file.too_large && file.hunks.length > 0 && (
            <p className="border-t border-[var(--color-border)] px-4 py-2 text-[11px] text-[var(--color-warn)]">
              This file&apos;s diff was capped at 2000 lines; not every changed line is shown.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
