"use client";

// The home page: four bands built only from data the engine already computes, in order of what a person
// looking at the screen actually wants to know -- what this engine has achieved, what it is doing right now,
// what it has done overall, and how to point it at your own work. Every number comes from loadHome()'s sources;
// a workspace that has never run a night renders each band's honest "nothing yet" state instead of a fabricated
// number or a spinner that never ends.

import { useEffect, useState } from "react";
import { IS_DEMO } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { RecordedRun } from "@/components/RecordedRun";
import { ResultBand } from "@/components/home/ResultBand";
import { NowBand } from "@/components/home/NowBand";
import { DoneBand } from "@/components/home/DoneBand";
import { StartBand } from "@/components/home/StartBand";
import { loadHome, biggestResult, runningRun, latestBeat, doneStrip, type HomeData } from "@/lib/home";

const POLL_MS = 10000;

export default function HomePage() {
  const [data, setData] = useState<HomeData | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => loadHome().then((d) => !cancelled && setData(d));
    refresh();
    // Nothing changes in a recording, so only a live engine is worth re-polling.
    if (IS_DEMO) return;
    const id = setInterval(refresh, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <PageHeader
        title="Pravrudhi"
        subtitle="What this engine has produced, what it's doing right now, and how to point it at your own work."
      />
      <div className="space-y-6 p-8">
        {data ? (
          <>
            <ResultBand result={biggestResult(data.objectives)} />
            <NowBand
              appetite={data.appetite}
              beat={latestBeat(data.heartbeats)}
              running={runningRun(data.runHandles)}
              agentsWorking={data.agentsWorking.length}
            />
            <DoneBand strip={doneStrip(data)} />
          </>
        ) : (
          <div className="h-64 animate-pulse rounded-lg bg-[var(--color-surface)]" />
        )}
        <RecordedRun />
        <StartBand />
      </div>
    </div>
  );
}
