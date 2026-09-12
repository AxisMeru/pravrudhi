"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Pause, Play, RotateCcw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { IS_DEMO, run, type RunEvent, type RunHandle, streamRun } from "@/lib/api";
import { demo, type DemoBundle } from "@/lib/demo";
import { asStr, RUNNING_STATUSES, runStatus } from "@/lib/run";
import { RunHeader } from "./RunHeader";
import { CandidatePanel } from "./CandidatePanel";
import { StopControl } from "./StopControl";
import { Timeline } from "./Timeline";

const REPLAY_MS = 420;

function BackBar({ id }: { id: string | null }) {
  return (
    <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-6 py-4">
      <Link href="/runs" className="flex items-center gap-1.5 text-sm text-[var(--color-text-dim)] hover:text-[var(--color-text)]">
        <ArrowLeft size={14} />
        Runs
      </Link>
      {id && <span className="font-mono text-xs text-[var(--color-text-dim)]">{id}</span>}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div>
      <BackBar id={null} />
      <p className="p-8 text-sm text-[var(--color-text-dim)]">{text}</p>
    </div>
  );
}

// The live engine path: fetch the run once for its handle, then either tail the event stream
// (running/stopping — the server always replays a run's full history to a fresh connection, so no
// events are missed by not having polled earlier) or fall back to the last recorded events for a
// run that has already finished, matching how the runs list page already treats the same split.
function useLiveRun(id: string) {
  const [handle, setHandle] = useState<RunHandle | null | undefined>(undefined);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamFailed, setStreamFailed] = useState(false);
  const closerRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;

    run(id)
      .then((detail) => {
        if (cancelled) return;
        setHandle(detail);
        if (RUNNING_STATUSES.has(runStatus(detail))) {
          const close = streamRun(
            id,
            (ev) => {
              setEvents((prev) => [...prev, ev]);
              if (ev.type === "end") {
                setHandle((h) => (h ? { ...h, status: ev.status ?? h.status, finished_at: ev.t } : h));
                closerRef.current?.();
                closerRef.current = null;
              }
            },
            () => setStreamFailed(true),
          );
          closerRef.current = close;
        } else {
          setEvents(detail.recent);
        }
      })
      .catch(() => {
        if (!cancelled) setHandle(null);
      });

    return () => {
      cancelled = true;
      closerRef.current?.();
      closerRef.current = null;
    };
  }, [id]);

  return { handle, events, streamFailed, setHandle };
}

function LiveRun({ id }: { id: string }) {
  const { handle, events, streamFailed, setHandle } = useLiveRun(id);
  const [, setTick] = useState(0);

  // A running run's elapsed time is read from Date.now() at render time, so nothing needs to be
  // computed here — a periodic tick just forces that render to happen while the run is live.
  useEffect(() => {
    if (!handle || !RUNNING_STATUSES.has(runStatus(handle))) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [handle]);

  if (handle === undefined) return <Empty text="Loading…" />;
  if (handle === null) return <Empty text="No such run — it may belong to a different engine instance." />;

  const status = runStatus(handle);
  return (
    <div>
      <BackBar id={id} />
      <RunHeader handle={handle} events={events} />
      {RUNNING_STATUSES.has(status) && (
        <StopControl runId={id} status={status} onStopped={(h) => setHandle((prev) => (prev ? { ...prev, ...h } : h))} />
      )}
      {streamFailed && (
        <p className="border-b border-[var(--color-border)] px-6 py-3 text-xs text-[var(--color-danger)]">
          Lost connection to this run&apos;s event stream. What is shown below may be incomplete.
        </p>
      )}
      <CandidatePanel events={events} />
      <Timeline events={events} />
    </div>
  );
}

// The demo path: no engine exists to stream from, so a run is either the one recording the site
// ships in full (replayed on a timer, same cadence as the landing page's RecordedRun) or one of
// the other listed runs, for which only the aggregate the list page already shows is available.
function DemoRunView({ id }: { id: string }) {
  const [bundle, setBundle] = useState<DemoBundle | null | undefined>(undefined);
  const [shown, setShown] = useState(0);
  const [playing, setPlaying] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    demo()
      .then(setBundle)
      .catch(() => setBundle(null));
  }, []);

  let isFeatured = false;
  let fullEvents: RunEvent[] = [];
  if (bundle && bundle.featured_run.id === id) {
    isFeatured = true;
    fullEvents = bundle.featured_run.events;
  }

  useEffect(() => {
    if (!isFeatured || !playing || fullEvents.length === 0) return;
    timer.current = setInterval(() => {
      setShown((n) => {
        if (n >= fullEvents.length) {
          setPlaying(false);
          return n;
        }
        return n + 1;
      });
    }, REPLAY_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [isFeatured, playing, fullEvents.length]);

  if (bundle === undefined) return <Empty text="Loading the recording…" />;
  if (bundle === null) return <Empty text="Could not load the recorded demo." />;

  const handle = bundle.runs.find((r) => r.id === id);
  if (!handle) return <Empty text="This recording has no run with that id." />;

  const done = shown >= fullEvents.length && fullEvents.length > 0;

  return (
    <div>
      <BackBar id={id} />
      <RunHeader handle={handle} events={isFeatured ? fullEvents.slice(0, shown) : []} />
      {isFeatured ? (
        <>
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-6 py-3">
            <button
              onClick={() => setPlaying((p) => !p)}
              disabled={done}
              className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-white/5 disabled:opacity-40"
            >
              {playing ? <Pause size={13} /> : <Play size={13} />}
              {playing ? "Pause" : "Play"}
            </button>
            <button
              onClick={() => {
                setShown(0);
                setPlaying(true);
              }}
              className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-white/5"
            >
              <RotateCcw size={13} />
              Replay
            </button>
            <span className="ml-auto text-xs text-[var(--color-text-dim)]">
              Replaying a recorded night — every line is from the engine&apos;s own record.
            </span>
          </div>
          <CandidatePanel events={fullEvents.slice(0, shown)} />
          <Timeline events={fullEvents.slice(0, shown)} />
        </>
      ) : (
        <p className="px-6 py-6 text-sm text-[var(--color-text-dim)]">
          This recording did not capture this run&apos;s full event log — only its summary is available.
        </p>
      )}
    </div>
  );
}

export function RunView() {
  const params = useSearchParams();
  const id = asStr(params.get("run"));

  if (!id) return <Empty text="No run id was given." />;
  // Keyed by id: switching between runs (e.g. via a link, without a full page load) should start each run's
  // view from scratch rather than reset it in place — the same outcome useLiveRun's removed top-of-effect
  // resets produced, but for free, and without a synchronous setState directly in an effect body.
  if (IS_DEMO) return <DemoRunView key={id} id={id} />;
  return <LiveRun key={id} id={id} />;
}
