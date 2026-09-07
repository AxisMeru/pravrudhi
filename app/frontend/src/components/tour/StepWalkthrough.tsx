"use client";

// Plays the walkthrough frames captured by driving a real browser through the running app (see
// public/walkthrough/frames.json) — the tour's answer to "show real clicks", not another data card.

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import { TOUR_BASE_PATH, type TourData } from "@/lib/tour";
import { Empty } from "@/components/tour/Empty";

const FRAME_AUTOPLAY_MS = 4000;

export function StepWalkthrough({ data }: { data: TourData }) {
  const frames = data.walkthroughFrames;
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing) return;
    if (idx >= frames.length - 1) {
      setPlaying(false);
      return;
    }
    const id = setTimeout(() => setIdx((i) => Math.min(i + 1, frames.length - 1)), FRAME_AUTOPLAY_MS);
    return () => clearTimeout(id);
  }, [playing, idx, frames.length]);

  if (frames.length === 0) {
    return <Empty>This build carries no captured walkthrough frames.</Empty>;
  }

  const frame = frames[idx];

  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--color-muted)]">
        These are real screens, captured by driving a running browser through this engine — not a design.
      </p>
      <figure>
        {/* eslint-disable-next-line @next/next/no-img-element -- a static public asset, not a remote image */}
        <img
          src={`${TOUR_BASE_PATH}/walkthrough/${frame.image}`}
          alt={frame.caption}
          className="w-full rounded-lg border border-[var(--color-border)]"
        />
        <figcaption className="mt-2 text-sm text-[var(--color-text)]">{frame.caption}</figcaption>
        <p className="mt-1 font-mono text-[11px] text-[var(--color-muted)]">{frame.path}</p>
      </figure>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setPlaying(false);
              setIdx((i) => Math.max(i - 1, 0));
            }}
            disabled={idx === 0}
            className="flex items-center gap-1 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft size={14} />
            Previous
          </button>
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-white/5"
          >
            {playing ? <Pause size={13} /> : <Play size={13} />}
            {playing ? "Pause" : "Play through"}
          </button>
          <button
            type="button"
            onClick={() => {
              setPlaying(false);
              setIdx((i) => Math.min(i + 1, frames.length - 1));
            }}
            disabled={idx === frames.length - 1}
            className="flex items-center gap-1 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
            <ChevronRight size={14} />
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          {frames.map((f, i) => (
            <button
              key={f.image}
              type="button"
              onClick={() => {
                setPlaying(false);
                setIdx(i);
              }}
              title={f.caption}
              aria-label={`Frame ${i + 1}`}
              aria-current={i === idx}
              className={`h-2 w-2 rounded-full transition-colors ${
                i === idx ? "bg-[var(--color-accent)]" : "bg-[var(--color-border)] hover:bg-[var(--color-text-dim)]"
              }`}
            />
          ))}
        </div>
        <span className="text-xs text-[var(--color-text-dim)]">
          Frame {idx + 1} of {frames.length}
        </span>
      </div>
    </div>
  );
}
