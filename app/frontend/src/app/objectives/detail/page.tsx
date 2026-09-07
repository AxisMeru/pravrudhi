import { Suspense } from "react";
import { ObjectiveDetailView } from "@/components/objective/ObjectiveDetailView";

// An objective id is a user-chosen slug, not a runtime-only value, but this route still reads it as
// `?id=<id>` rather than a `[id]` path segment, matching runs/[id]/page.tsx: a static export pre-renders one
// fixed path, so the query string is the only way an id travels to a page that also has to work on a hard
// reload and on GitHub Pages.
export default function ObjectiveDetailPage() {
  return (
    <Suspense fallback={<p className="p-8 text-sm text-[var(--color-text-dim)]">Loading…</p>}>
      <ObjectiveDetailView />
    </Suspense>
  );
}
