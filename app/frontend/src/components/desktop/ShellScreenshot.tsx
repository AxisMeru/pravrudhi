"use client";

// The one piece of proof that the desktop shell is a real window and not a mock-up: an actual capture of it
// connected to a running engine (see main.js's PRAVRUDHI_DESKTOP_SHOT capture handler). If that capture is not
// present in this build, the section still renders honestly — a labelled placeholder, not a broken image icon
// or an endless spinner.

import { useState } from "react";
import { SCREENSHOT_SRC } from "@/lib/desktop";

export function ShellScreenshot() {
  const [failed, setFailed] = useState(false);

  return (
    <figure className="mt-4">
      {failed ? (
        <div
          className={
            "flex aspect-video max-w-2xl items-center justify-center rounded-md border border-dashed " +
            "border-[var(--color-border)] bg-[var(--color-bg)] p-6 text-center text-xs text-[var(--color-text-dim)]"
          }
        >
          This build does not carry a captured window image.
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- a static public asset, not a remote image
        <img
          src={SCREENSHOT_SRC}
          onError={() => setFailed(true)}
          alt="The Pravrudhi desktop shell window, connected to a running engine"
          className="max-w-2xl rounded-md border border-[var(--color-border)]"
        />
      )}
      <figcaption className="mt-2 text-xs text-[var(--color-text-dim)]">
        A real capture of the running window, taken from the shell itself — not a mock-up.
      </figcaption>
    </figure>
  );
}
