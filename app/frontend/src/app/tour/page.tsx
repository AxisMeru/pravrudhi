"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { TourNav } from "@/components/tour/TourNav";
import { StepIntent } from "@/components/tour/StepIntent";
import { StepSwarm } from "@/components/tour/StepSwarm";
import { StepNight } from "@/components/tour/StepNight";
import { StepScored } from "@/components/tour/StepScored";
import { StepBoundary } from "@/components/tour/StepBoundary";
import { StepSignoff } from "@/components/tour/StepSignoff";
import { StepBenchmark } from "@/components/tour/StepBenchmark";
import { StepVersion } from "@/components/tour/StepVersion";
import { StepWalkthrough } from "@/components/tour/StepWalkthrough";
import { StepDesktop } from "@/components/tour/StepDesktop";
import { loadTour, TOUR_STEPS, type TourData } from "@/lib/tour";

const STEP_COMPONENTS = [
  StepIntent,
  StepSwarm,
  StepNight,
  StepScored,
  StepBoundary,
  StepSignoff,
  StepBenchmark,
  StepVersion,
  StepWalkthrough,
  StepDesktop,
];

const AUTOPLAY_MS = 7000;

function clampStep(n: number): number {
  return Math.min(Math.max(Math.trunc(n) || 1, 1), TOUR_STEPS.length);
}

export default function TourPage() {
  // The prerendered HTML has no window and no query string, so the first render always shows step 1; the actual
  // ?step=N is read after mount, the same reasoning the Improve page uses to pick demo vs. live mode.
  const [current, setCurrent] = useState(1);
  const [data, setData] = useState<TourData | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const fromUrl = Number(new URLSearchParams(window.location.search).get("step"));
    // eslint-disable-next-line react-hooks/set-state-in-effect -- window.location is unavailable during SSR
    if (Number.isInteger(fromUrl)) setCurrent(clampStep(fromUrl));
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadTour()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const goTo = useCallback((n: number) => {
    const clamped = clampStep(n);
    setCurrent(clamped);
    // Autoplay stops here, on the transition that reaches the last step, rather than in the effect below —
    // called from a timer/event callback, not synchronously during the effect body, so it also covers the
    // manual "Next" click reaching the end while playing.
    if (clamped >= TOUR_STEPS.length) setPlaying(false);
    const url = new URL(window.location.href);
    url.searchParams.set("step", String(clamped));
    window.history.replaceState(null, "", url);
  }, []);

  useEffect(() => {
    if (!playing || current >= TOUR_STEPS.length) return;
    const id = setTimeout(() => goTo(current + 1), AUTOPLAY_MS);
    return () => clearTimeout(id);
  }, [playing, current, goTo]);

  const step = TOUR_STEPS[current - 1];
  const StepComponent = STEP_COMPONENTS[current - 1];

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Guided tour" subtitle="Every screen here is built from a run this engine actually did, not a script." />
      <div className="flex-1 overflow-y-auto p-8">
        {failed && <p className="text-sm text-[var(--color-text-dim)]">Could not load the recorded snapshot.</p>}
        {!failed && data === undefined && <div className="h-48 animate-pulse rounded-lg bg-[var(--color-surface)]" />}
        {!failed && data && (
          <div className="mx-auto max-w-3xl space-y-4">
            <div>
              <div className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
                Step {step.n} of {TOUR_STEPS.length}
              </div>
              <h2 className="text-lg font-semibold text-[var(--color-text)]">{step.title}</h2>
              <p className="mt-1 text-sm text-[var(--color-text-dim)]">{step.lede}</p>
            </div>
            <StepComponent data={data} />
          </div>
        )}
      </div>
      <TourNav
        steps={TOUR_STEPS}
        current={current}
        playing={playing}
        onSelect={(n) => {
          setPlaying(false);
          goTo(n);
        }}
        onPrev={() => {
          setPlaying(false);
          goTo(current - 1);
        }}
        onNext={() => {
          setPlaying(false);
          goTo(current + 1);
        }}
        onTogglePlay={() => setPlaying((p) => !p)}
      />
    </div>
  );
}
