import type { HeartbeatBeat } from "@/lib/heartbeat";
import { secs } from "@/lib/num";

function sortNewestFirst(beats: HeartbeatBeat[]): HeartbeatBeat[] {
  return [...beats].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}

export function HeartbeatTimeline({ beats }: { beats: HeartbeatBeat[] }) {
  const ordered = sortNewestFirst(beats);

  return (
    <div className="space-y-2">
      {ordered.map((beat, i) => (
        <div
          key={`${beat.at}-${i}`}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
        >
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono text-[11px] text-[var(--color-text-dim)]">{beat.at}</span>
            {beat.chose ? (
              <span className="font-mono text-[13px] text-[var(--color-text)]">
                {beat.chose.objective} · {beat.chose.step}
              </span>
            ) : (
              <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-dim)]">no dispatch</span>
            )}
            {beat.result && (
              <span
                className={`ml-auto inline-flex items-center gap-1.5 text-xs font-medium ${
                  beat.result.accepted ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"
                }`}
              >
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: "currentColor" }} />
                {beat.result.accepted ? "accepted" : "rejected"}
              </span>
            )}
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text-dim)]">
            looked at: {beat.looked_at.length > 0 ? beat.looked_at.join(", ") : "—"}
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text-dim)]">{beat.reason}</div>
          {/* A beat's result is not one shape. A dispatch records an agent, a wall time and the files it
              touched; a survey beat records what it looked at and found nothing to do, and carries none of
              those fields. Reading `result.files.length` on the second kind threw and took the whole page
              down, so each field is checked for itself rather than inferred from the result being present. */}
          {beat.result && (beat.result.agent || Array.isArray(beat.result.files)) && (
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[var(--color-text-dim)]">
              {beat.result.agent && <span className="font-mono">{beat.result.agent}</span>}
              {typeof beat.result.wall_s === "number" && <span>{secs(beat.result.wall_s)}</span>}
              {Array.isArray(beat.result.files) && beat.result.files.length > 0 && (
                <span className="truncate font-mono">{beat.result.files.join(", ")}</span>
              )}
            </div>
          )}
          {beat.result && Array.isArray(beat.result.reasons) && beat.result.reasons.length > 0 && (
            <ul className="mt-1 list-inside list-disc text-[11px] text-[var(--color-text-dim)]">
              {beat.result.reasons.map((reason, j) => (
                <li key={j}>{reason}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
