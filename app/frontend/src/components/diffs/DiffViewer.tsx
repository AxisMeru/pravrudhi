"use client";

import { AnnotationProvider } from "./Annotations";
import { useAnnotationTask } from "./AnnotationSelection";
import { fixed } from "@/lib/num";
import type { WorktreeDiff } from "@/lib/diffs";
import { DiffFilePanel } from "./DiffFilePanel";

export function DiffViewer({ diff }: { diff: WorktreeDiff }) {
  const task = useAnnotationTask();
  return task ? <AnnotationProvider key={task + ":" + diff.base + ":" + diff.head} task={task} revision={`${diff.base} → ${diff.head}`}><DiffContent diff={diff} /></AnnotationProvider> : <DiffContent diff={diff} />;
}

function DiffContent({ diff }: { diff: WorktreeDiff }) {
  if (diff.reason) {
    return <p className="text-sm text-[var(--color-text-dim)]">{diff.reason}</p>;
  }
  if (diff.files.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">This task has not changed anything yet.</p>;
  }

  const totalAdded = diff.files.reduce((n, f) => n + f.added, 0);
  const totalRemoved = diff.files.reduce((n, f) => n + f.removed, 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-[11px] text-[var(--color-text-dim)]">
        <span>
          {fixed(diff.files.length, 0)} file{diff.files.length === 1 ? "" : "s"} changed
        </span>
        <span className="font-mono text-[var(--color-accent)]">+{fixed(totalAdded, 0)}</span>
        <span className="font-mono text-[var(--color-danger)]">-{fixed(totalRemoved, 0)}</span>
        <span className="font-mono">
          {diff.base.slice(0, 10)} → {diff.head.slice(0, 10)}
        </span>
        {diff.truncated && (
          <span className="uppercase tracking-wide text-[var(--color-warn)]">diff truncated at 400 KB</span>
        )}
      </div>
      <div className="space-y-2">
        {diff.files.map((f) => (
          <DiffFilePanel key={f.path} file={f} />
        ))}
      </div>
    </div>
  );
}
