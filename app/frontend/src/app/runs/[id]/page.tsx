import { Suspense } from "react";
import { RunView } from "@/components/run/RunView";

// A run id is a fresh UUID minted when a run starts, so it does not exist at build time the way a
// static export's dynamic segments need to. This route therefore pre-renders exactly one fixed
// path (`/runs/view`); the real id travels as `?run=<id>` (see lib/run.ts `runHref`) and is read
// client-side in RunView. That keeps the page reachable on a hard reload and on GitHub Pages,
// which a `[id]` segment resolved from the URL path alone could not be.
export function generateStaticParams() {
  return [{ id: "view" }];
}

export default function RunPage() {
  return (
    <Suspense fallback={<p className="p-8 text-sm text-[var(--color-text-dim)]">Loading…</p>}>
      <RunView />
    </Suspense>
  );
}
