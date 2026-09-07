// The desktop shell, proven with the one screenshot that exists of it: a real capture taken by the shell itself
// (see app/desktop/main.js's capture handler and lib/desktop.ts), not a mock-up. It happens to have been taken
// mid-update-check, which is the answer to "how does the desktop app update".

import { RELEASES_URL, SCREENSHOT_SRC } from "@/lib/desktop";
import { TOUR_BASE_PATH, type TourData } from "@/lib/tour";

export function StepDesktop({ data }: { data: TourData }) {
  // Every tour step renders through the same StepComponent slot in app/tour/page.tsx and takes the same `data`
  // prop as its siblings, even though this step's content — a live shell capture — isn't part of the recording.
  void data;

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-dim)]">
        Pravrudhi Desktop is a native shell that finds an installed engine, starts it, and keeps a window, menu
        and tray around it — it ships no Python of its own.
      </p>
      <figure>
        {/* eslint-disable-next-line @next/next/no-img-element -- a static public asset, not a remote image */}
        <img
          src={`${TOUR_BASE_PATH}${SCREENSHOT_SRC}`}
          alt="The Pravrudhi desktop shell's engine activity panel, caught mid-update-check"
          className="w-full rounded-lg border border-[var(--color-border)]"
        />
        <figcaption className="mt-2 text-sm text-[var(--color-text)]">
          A real capture of the running window, taken by the shell itself. Its Engine activity panel reads{" "}
          <span className="font-mono text-xs">Health · Healthy · 0.3.0</span> and{" "}
          <span className="font-mono text-xs">Version and updates · 0.3.0 · Update available: v0.3.1</span> — the
          engine has already found a newer release than the one it is running, caught mid-check before anyone
          clicked anything. That panel, not a popup, is how this shell surfaces an update.
        </figcaption>
      </figure>
      <p className="text-xs text-[var(--color-muted)]">Footer of the same window: Shell 0.3.1 · Engine 0.3.0.</p>
      <a
        href={RELEASES_URL}
        className="inline-block text-sm text-[var(--color-accent)] hover:underline"
        target="_blank"
        rel="noreferrer"
      >
        Download the desktop app from the releases page
      </a>
    </div>
  );
}
