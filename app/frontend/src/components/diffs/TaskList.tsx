"use client";

import { useLayoutEffect } from "react";
import { selectAnnotationTask } from "./AnnotationSelection";
import { fixed } from "@/lib/num";
import type { TaskSummary } from "@/lib/diffs";

export function TaskList({
  tasks,
  selected,
  onSelect,
}: {
  tasks: TaskSummary[];
  selected: string | null;
  onSelect: (taskId: string) => void;
}) {
  useLayoutEffect(() => {
    selectAnnotationTask(selected);
    return () => selectAnnotationTask(null);
  }, [selected]);

  if (tasks.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">No dispatched task has a worktree here yet.</p>;
  }

  return (
    <ul className="space-y-1">
      {tasks.map((t) => (
        <li key={t.task_id}>
          <button
            type="button"
            onClick={() => onSelect(t.task_id)}
            className={`flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors ${
              selected === t.task_id
                ? "border-[var(--color-accent)] bg-[var(--color-surface-raised)]"
                : "border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-raised)]"
            }`}
          >
            <span className="truncate font-mono text-[13px] text-[var(--color-text)]" title={t.task_id}>
              {t.task_id}
            </span>
            <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-dim)]">
              <span>
                {fixed(t.files, 0)} file{t.files === 1 ? "" : "s"}
              </span>
              <span className="font-mono text-[var(--color-accent)]">+{fixed(t.added, 0)}</span>
              <span className="font-mono text-[var(--color-danger)]">-{fixed(t.removed, 0)}</span>
              {t.truncated && <span className="uppercase tracking-wide text-[var(--color-warn)]">truncated</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
