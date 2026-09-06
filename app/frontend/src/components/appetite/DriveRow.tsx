import { fixed } from "@/lib/num";
import type { Drive } from "@/lib/appetite";

// A drive with no measurement renders as prose, never as a bar sitting at zero — a zero-width bar and "not
// measurable yet" mean opposite things, and this engine has already shipped a page that blurred them once.
function barFraction(drive: Drive): number | null {
  if (drive.unknown || drive.value === null || drive.target === null || drive.target === 0) return null;
  return Math.max(0, Math.min(1, drive.value / drive.target));
}

export function DriveRow({ drive, selected }: { drive: Drive; selected: boolean }) {
  const fraction = barFraction(drive);
  const blocked = !drive.eligible;
  const fillColor = blocked ? "var(--color-text-dim)" : selected ? "var(--color-accent)" : "var(--color-warn)";

  return (
    <article
      className={`rounded-lg border p-4 ${
        selected
          ? "border-[var(--color-accent)] bg-[var(--color-surface-raised)]"
          : "border-[var(--color-border)] bg-[var(--color-surface)]"
      } ${blocked ? "opacity-60" : ""}`}
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm text-[var(--color-text)]">{drive.wire_name}</span>
        {selected && (
          <span className="rounded-full bg-[var(--color-accent)] px-1.5 py-0.5 text-[10px] font-medium text-[#06110c]">
            selected
          </span>
        )}
        <span className="ml-auto text-[11px] text-[var(--color-text-dim)]">weight {fixed(drive.weight, 2)}</span>
      </div>

      {drive.unknown ? (
        <p className="mt-3 text-xs text-[var(--color-text-dim)]">
          not measurable yet{drive.blocked_reason ? ` — ${drive.blocked_reason}` : ""}
        </p>
      ) : (
        <>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-[var(--color-border)]">
            <div
              className="h-full rounded-full"
              style={{ width: `${((fraction ?? 0) * 100).toFixed(1)}%`, background: fillColor }}
            />
          </div>
          <div className="mt-2 flex items-center gap-3 text-[11px] text-[var(--color-text-dim)]">
            <span className="tabular-nums text-[var(--color-text)]">{fixed(drive.value, 2)}</span>
            <span>/ target {fixed(drive.target, 2)}</span>
            <span className="ml-auto tabular-nums">deficit {fixed(drive.deficit, 2)}</span>
          </div>
        </>
      )}

      {blocked && (
        <p className="mt-2 text-xs text-[var(--color-danger)]">
          blocked{drive.blocked_reason ? ` — ${drive.blocked_reason}` : ""}
        </p>
      )}

      <details className="mt-3 text-[11px] text-[var(--color-text-dim)]">
        <summary className="cursor-pointer">
          {drive.sources.length} source{drive.sources.length === 1 ? "" : "s"}
        </summary>
        {drive.sources.length === 0 ? (
          <p className="mt-1 pl-3">no sources recorded</p>
        ) : (
          <ul className="mt-1 space-y-1 pl-3">
            {drive.sources.map((s, i) => (
              <li key={i} className="list-disc font-mono leading-4">
                {s}
              </li>
            ))}
          </ul>
        )}
      </details>
    </article>
  );
}
