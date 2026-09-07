// Part of section 4: the recent dispatches and what each one produced — a subagent run against an objective's
// plan, or a self-build task the engine set for itself — merged into one chronological log so "who built what,
// and did it stick" reads as a single feed instead of two disconnected tables.

import { CheckCircle2, XCircle } from "lucide-react";
import { secs } from "@/lib/num";
import type { SelfBuildRun, SubagentRun } from "@/lib/swarm";

interface DispatchRow {
  key: string;
  kind: "subagent" | "selfbuild";
  label: string;
  route: string;
  accepted: boolean;
  wall_s: number;
  files: string[];
  reasons: string[];
  at: string;
}

function toRows(subagent: SubagentRun[], selfbuild: SelfBuildRun[]): DispatchRow[] {
  const a: DispatchRow[] = subagent.map((r) => ({
    key: `s:${r.task_id}:${r.at}`,
    kind: "subagent",
    label: `${r.objective} · ${r.step}`,
    route: r.route,
    accepted: r.accepted,
    wall_s: r.wall_s,
    files: r.files,
    reasons: r.reasons,
    at: r.at,
  }));
  const b: DispatchRow[] = selfbuild.map((r) => ({
    key: `b:${r.task_id}:${r.at}`,
    kind: "selfbuild",
    label: r.task_id,
    route: r.route,
    accepted: r.accepted,
    wall_s: r.wall_s,
    files: r.files,
    reasons: r.reasons,
    at: r.at,
  }));
  return [...a, ...b].sort((x, y) => (x.at < y.at ? 1 : x.at > y.at ? -1 : 0));
}

const SHOWN = 10;

export function DispatchLog({ subagent, selfbuild }: { subagent: SubagentRun[]; selfbuild: SelfBuildRun[] }) {
  const rows = toRows(subagent, selfbuild).slice(0, SHOWN);
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">Nothing has been dispatched yet.</p>;
  }
  return (
    <ul className="grid gap-2">
      {rows.map((r) => (
        <li key={r.key} className="rounded-md border border-[var(--color-border)] p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            {r.accepted ? (
              <CheckCircle2 size={14} className="shrink-0 text-[var(--color-accent)]" />
            ) : (
              <XCircle size={14} className="shrink-0 text-red-500" />
            )}
            <span className="font-mono text-[var(--color-text)]">{r.label}</span>
            <span className="rounded-full border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-dim)]">
              {r.kind === "selfbuild" ? "self-build" : "subagent"}
            </span>
            <span className="text-[var(--color-text-dim)]">via {r.route}</span>
            <span className="ml-auto text-[var(--color-text-dim)]">{secs(r.wall_s, 0)}</span>
          </div>
          {r.accepted && r.files.length > 0 && (
            <p className="mt-1.5 text-[var(--color-text-dim)]">{r.files.length} file(s) produced</p>
          )}
          {!r.accepted && r.reasons.length > 0 && (
            <p className="mt-1.5 text-[var(--color-text-dim)]">{r.reasons.join("; ")}</p>
          )}
          <p className="mt-1 text-[10px] text-[var(--color-text-dim)]">{r.at}</p>
        </li>
      ))}
    </ul>
  );
}
