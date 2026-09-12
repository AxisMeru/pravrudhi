"use client";

// The objective detail page: everything the engine already knows about one objective, in one place -- the
// intent verbatim, each benchmark's standing, the compiled plan and its dispatch, the Loom program it lowers
// to, and the runs and candidates that were scored under it. Every fetch here is scoped to one objective id, so
// a workspace that has never run a night still renders real content instead of a spinner that never ends: an
// objective with no progress shows its benchmarks as unmeasured, its plan as proposed but not run, and its
// linked activity as empty -- never a blank page.

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ListChecks, Target, Terminal, Waypoints } from "lucide-react";
import { IS_DEMO, objective, type ObjectiveDetail } from "@/lib/api";
import { signedPercent } from "@/lib/objective";
import { BenchmarkDetail } from "./BenchmarkDetail";
import { PlanSteps } from "./PlanSteps";
import { LoomProgram } from "./LoomProgram";
import { LinkedActivity } from "./LinkedActivity";

function asStr(v: string | null): string | undefined {
  return v && v.length > 0 ? v : undefined;
}

function BackBar() {
  return (
    <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-6 py-4">
      <Link href="/objectives" className="flex items-center gap-1.5 text-sm text-[var(--color-text-dim)] hover:text-[var(--color-text)]">
        <ArrowLeft size={14} />
        Objectives
      </Link>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div>
      <BackBar />
      <p className="p-8 text-sm text-[var(--color-text-dim)]">{text}</p>
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: typeof Target;
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <div className="flex items-center gap-2">
        <Icon size={16} className="text-[var(--color-text-dim)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">{title}</h2>
      </div>
      {subtitle && <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function useObjective(id: string) {
  // Tagged with the id it was fetched for, so a still-loading fetch from an objective the reader has already
  // navigated away from never gets returned as if it belonged to the current one — derived at render time
  // rather than reset with an extra synchronous commit at the top of the effect.
  const [fetched, setFetched] = useState<{ id: string; obj: ObjectiveDetail | null } | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    objective(id)
      .then((o) => {
        if (!cancelled) setFetched({ id, obj: o });
      })
      .catch(() => {
        if (!cancelled) setFetched({ id, obj: null });
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return fetched?.id === id ? fetched.obj : undefined;
}

function Detail({ id }: { id: string }) {
  const obj = useObjective(id);

  if (obj === undefined) return <Empty text="Loading…" />;
  if (obj === null) return <Empty text="No such objective — it may belong to a different engine instance." />;

  return (
    <div>
      <BackBar />
      <div className="border-b border-[var(--color-border)] px-6 py-6">
        <div className="flex flex-wrap items-center gap-2">
          <Target size={18} className="text-[var(--color-text-dim)]" />
          <h1 className="text-lg font-semibold text-[var(--color-text)]">{obj.id}</h1>
          {obj.domain && (
            <span className="rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[11px] text-[var(--color-text-dim)]">
              {obj.domain}
            </span>
          )}
          <span className="ml-auto font-mono text-xs text-[var(--color-text-dim)]">track {obj.track}</span>
        </div>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-[var(--color-text)]">{obj.intent}</p>
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--color-text-dim)]">
          {obj.created && <span>stated {obj.created}</span>}
          {obj.target_delta !== null && <span>target {signedPercent(obj.target_delta)}</span>}
        </div>
        {obj.notes && <p className="mt-3 text-xs leading-5 text-[var(--color-text-dim)]">{obj.notes}</p>}
        {IS_DEMO && (
          <p className="mt-3 text-xs text-[var(--color-text-dim)]">
            This is a recording. Dispatch is disabled — run the engine on your own machine to act on this objective.
          </p>
        )}
      </div>

      <div className="grid gap-5 p-6">
        <Section icon={Target} title="Benchmarks">
          {obj.progress.length === 0 ? (
            <p className="text-sm text-[var(--color-text-dim)]">This objective declares no benchmark.</p>
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">
              {obj.progress.map((p) => (
                <BenchmarkDetail key={p.benchmark} p={p} />
              ))}
            </div>
          )}
        </Section>

        <Section
          icon={ListChecks}
          title="How this intent becomes work"
          subtitle="A proposed decomposition, not a record."
        >
          <PlanSteps id={obj.id} />
        </Section>

        <Section icon={Terminal} title="The plan as Loom">
          <LoomProgram id={obj.id} />
        </Section>

        <Section icon={Waypoints} title="Linked activity">
          <LinkedActivity track={obj.track} />
        </Section>
      </div>
    </div>
  );
}

export function ObjectiveDetailView() {
  const params = useSearchParams();
  const id = asStr(params.get("id"));

  if (!id) return <Empty text="No objective id was given." />;
  return <Detail id={id} />;
}
