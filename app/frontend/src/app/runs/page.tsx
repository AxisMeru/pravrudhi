"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Sparkles, Square, Star, Wrench } from "lucide-react";
import { ApiError, run, runs, stopRun, streamRun, type RunEvent, type RunHandle } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";

// RunHandle only guarantees `id` — everything else is whatever the engine's run view sends. These
// helpers narrow the unknown extras defensively instead of trusting the shape.
function asStr(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}
function asNum(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}
function asArr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

const RUNNING_STATUSES = new Set(["running", "stopping"]);

function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "var(--color-accent)";
    case "stopping":
      return "var(--color-warn)";
    case "failed":
      return "var(--color-danger)";
    default:
      return "var(--color-text-dim)";
  }
}

function relativeTime(unixSeconds: number | undefined): string {
  if (unixSeconds === undefined) return "unknown time";
  const diffMin = Math.round((Date.now() - unixSeconds * 1000) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.round(diffH / 24)}d ago`;
}

function EventRow({ event }: { event: RunEvent }) {
  const time = event.t
    ? new Date(event.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "";
  const base = "flex items-center gap-3 border-b border-[var(--color-border)] px-4 py-2 text-sm last:border-b-0";
  const timeCol = <span className="w-20 shrink-0 text-xs text-[var(--color-text-dim)]">{time}</span>;

  switch (event.type) {
    case "paired": {
      const positive = (event.delta ?? 0) >= 0;
      return (
        <div className={base}>
          {timeCol}
          <span className="w-20 shrink-0 truncate font-mono text-xs text-[var(--color-text-dim)]">
            {event.candidate}
          </span>
          <span className="text-[var(--color-text-dim)]">
            {event.incumbent?.toFixed(3) ?? "—"} → {event.candidate_score?.toFixed(3) ?? "—"}
          </span>
          <span className={`font-medium ${positive ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"}`}>
            {event.delta !== undefined ? `${positive ? "+" : ""}${event.delta.toFixed(3)}` : "—"}
          </span>
          <span className="ml-auto text-xs uppercase tracking-wide text-[var(--color-text-dim)]">
            {event.decision}
          </span>
        </div>
      );
    }
    case "promoted":
      return (
        <div className={`${base} bg-[var(--color-accent-dim)]/20`}>
          {timeCol}
          <Star size={13} className="shrink-0 text-[var(--color-accent)]" />
          <span className="font-medium text-[var(--color-text)]">{event.candidate} promoted</span>
        </div>
      );
    case "proposed":
      return (
        <div className={base}>
          {timeCol}
          <span className="text-[var(--color-text-dim)]">
            proposer: {event.raw} raw, {event.accepted} accepted
          </span>
        </div>
      );
    case "round":
      return (
        <div className={base}>
          {timeCol}
          <span className="text-[var(--color-text-dim)]">
            round {event.round}: {event.selected} selected, {event.remaining_gpu_h?.toFixed(1) ?? "—"} GPU-h remaining
          </span>
        </div>
      );
    case "closed":
      return (
        <div className={base}>
          {timeCol}
          <span className="text-[var(--color-text-dim)]">
            night {event.night} {event.status}
          </span>
        </div>
      );
    case "end":
      return (
        <div className={base}>
          {timeCol}
          <span className="text-[var(--color-text-dim)]">
            run {event.status} (exit {event.exit_code})
          </span>
        </div>
      );
    case "log":
    default:
      return (
        <div className={base}>
          {timeCol}
          <span className="truncate font-mono text-xs text-[var(--color-text-dim)]">{event.text ?? ""}</span>
        </div>
      );
  }
}

function RunCard({
  handle,
  expanded,
  events,
  streamFailed,
  onToggle,
  onStop,
}: {
  handle: RunHandle;
  expanded: boolean;
  events: RunEvent[] | undefined;
  streamFailed: boolean;
  onToggle: () => void;
  onStop: () => void;
}) {
  const target = asStr(handle.target) ?? "model";
  const night = asNum(handle.night);
  const status = asStr(handle.status) ?? "unknown";
  const startedAt = asNum(handle.started_at);
  const request = asRecord(handle.request);
  const budget = asNum(request.budget_gpu_h);
  const bestDelta = asNum(handle.best_delta);
  const promotedCount = asArr(handle.promoted).length;
  const running = RUNNING_STATUSES.has(status);
  const Icon = target === "harness" ? Wrench : Sparkles;

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        className="flex w-full cursor-pointer items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-[var(--color-surface-raised)]"
      >
        {expanded ? (
          <ChevronDown size={16} className="shrink-0 text-[var(--color-text-dim)]" />
        ) : (
          <ChevronRight size={16} className="shrink-0 text-[var(--color-text-dim)]" />
        )}
        <Icon size={16} className="shrink-0 text-[var(--color-text-dim)]" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium capitalize text-[var(--color-text)]">{target}</span>
            {night !== undefined && <span className="text-sm text-[var(--color-text-dim)]">night {night}</span>}
          </div>
          <div className="mt-0.5 text-xs text-[var(--color-text-dim)]">
            started {relativeTime(startedAt)}
            {budget !== undefined && ` · ${budget} GPU-h budget`}
          </div>
        </div>
        <div className="hidden shrink-0 items-center gap-6 sm:flex">
          <div className="text-right">
            <div className="text-xs text-[var(--color-text-dim)]">best improvement</div>
            <div
              className={`text-sm font-medium ${
                bestDelta === undefined
                  ? "text-[var(--color-text-dim)]"
                  : bestDelta >= 0
                    ? "text-[var(--color-accent)]"
                    : "text-[var(--color-danger)]"
              }`}
            >
              {bestDelta !== undefined ? `${bestDelta >= 0 ? "+" : ""}${bestDelta.toFixed(3)}` : "—"}
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-[var(--color-text-dim)]">promoted</div>
            <div className="text-sm font-medium text-[var(--color-text)]">{promotedCount}</div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-2.5 py-1 text-xs text-[var(--color-text-dim)]">
            <span className="h-2 w-2 rounded-full" style={{ background: statusColor(status) }} aria-hidden />
            {status}
          </span>
          {running && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onStop();
              }}
              className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1.5 text-xs text-[var(--color-text-dim)] transition-colors hover:border-[var(--color-danger)] hover:text-[var(--color-danger)]"
            >
              <Square size={12} />
              Stop
            </button>
          )}
        </div>
      </div>
      {expanded && (
        <div className="border-t border-[var(--color-border)]">
          {streamFailed && (
            <p className="px-5 py-3 text-xs text-[var(--color-danger)]">lost connection to this run&apos;s event stream</p>
          )}
          {!streamFailed && events === undefined && (
            <p className="px-5 py-3 text-xs text-[var(--color-text-dim)]">loading timeline…</p>
          )}
          {events !== undefined && events.length === 0 && (
            <p className="px-5 py-3 text-xs text-[var(--color-text-dim)]">no events yet</p>
          )}
          {events !== undefined && events.length > 0 && (
            <div className="max-h-96 overflow-y-auto">
              {events.map((ev, i) => (
                <EventRow key={i} event={ev} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function RunsPage() {
  const [rows, setRows] = useState<RunHandle[] | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  // Set on any poll failure that is not the "older engine, no runs endpoint" 404; cleared the moment a poll
  // next succeeds. Left unset before 2026-09-12, so any such failure fell through the catch block silently and
  // `rows` stayed null forever — the page never left "Loading…" on the very outage this exists to report.
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [eventsByRun, setEventsByRun] = useState<Record<string, RunEvent[]>>({});
  const [streamFailed, setStreamFailed] = useState<Record<string, boolean>>({});
  const closersRef = useRef<Record<string, () => void>>({});

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const data = await runs();
        if (cancelled) return;
        const sorted = [...data].sort((a, b) => (asNum(b.started_at) ?? 0) - (asNum(a.started_at) ?? 0));
        setRows(sorted);
        setUnsupported(false);
        setFailed(false);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setUnsupported(true);
          return;
        }
        setFailed(true);
      }
    }

    poll();
    const id = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Every open stream must die with the page, not just with the tab that opened it. closersRef.current
  // is never reassigned (only mutated), so capturing the object once here is safe.
  useEffect(() => {
    const closers = closersRef.current;
    return () => {
      Object.values(closers).forEach((close) => close());
    };
  }, []);

  function closeStream(id: string) {
    closersRef.current[id]?.();
    delete closersRef.current[id];
  }

  function openStream(id: string) {
    setStreamFailed((prev) => ({ ...prev, [id]: false }));
    setEventsByRun((prev) => ({ ...prev, [id]: prev[id] ?? [] }));
    const close = streamRun(
      id,
      (ev) => {
        setEventsByRun((prev) => ({ ...prev, [id]: [...(prev[id] ?? []), ev] }));
        if (ev.type === "end") closeStream(id);
      },
      () => setStreamFailed((prev) => ({ ...prev, [id]: true })),
    );
    closersRef.current[id] = close;
  }

  async function loadRecent(id: string) {
    try {
      const detail = await run(id);
      setEventsByRun((prev) => ({ ...prev, [id]: detail.recent }));
    } catch {
      setStreamFailed((prev) => ({ ...prev, [id]: true }));
    }
  }

  function toggle(handle: RunHandle) {
    const id = handle.id;
    if (expanded[id]) {
      setExpanded((prev) => ({ ...prev, [id]: false }));
      closeStream(id);
      return;
    }
    setExpanded((prev) => ({ ...prev, [id]: true }));
    const status = asStr(handle.status) ?? "";
    if (RUNNING_STATUSES.has(status)) {
      openStream(id);
    } else {
      void loadRecent(id);
    }
  }

  async function handleStop(id: string) {
    try {
      await stopRun(id);
    } catch {
      /* the next poll reflects whatever the engine actually did */
    }
  }

  return (
    <div>
      <PageHeader title="Runs" subtitle="Every run the engine has started, newest first." />
      <div className="space-y-3 p-8">
        {unsupported && <p className="text-sm text-[var(--color-text-dim)]">engine does not report runs yet.</p>}
        {!unsupported && failed && rows === null && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s runs API.</p>
        )}
        {!unsupported && !failed && rows === null && <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>}
        {!unsupported && rows !== null && rows.length === 0 && (
          <p className="text-sm text-[var(--color-text-dim)]">No runs yet — start one from Improve.</p>
        )}
        {!unsupported &&
          rows !== null &&
          rows.map((handle) => (
            <RunCard
              key={handle.id}
              handle={handle}
              expanded={!!expanded[handle.id]}
              events={eventsByRun[handle.id]}
              streamFailed={!!streamFailed[handle.id]}
              onToggle={() => toggle(handle)}
              onStop={() => handleStop(handle.id)}
            />
          ))}
      </div>
    </div>
  );
}
