import type { ReactNode } from "react";
import { Star, XCircle } from "lucide-react";
import type { RunEvent } from "@/lib/api";
import { fixed, percent } from "@/lib/num";

// One event, one line of plain English — never the raw JSON the engine actually sent. A `log`
// line is the one exception: it *is* the engine's own words, verbatim, because the parser in
// runs.py guarantees nothing it doesn't recognise is ever dropped.
function describe(event: RunEvent): { text: string; icon?: ReactNode; tone?: "good" | "bad" | "dim" } {
  switch (event.type) {
    case "proposed":
      return { text: `Proposer generated ${event.raw ?? "?"} candidates, ${event.accepted ?? "?"} accepted.` };
    case "proposed_one":
      return { text: `Candidate ${event.candidate ?? "?"} proposed.` };
    case "round":
      return {
        text: `Round ${event.round ?? "?"}: ${event.selected ?? "?"} candidates selected, `
          + `${fixed(event.remaining_gpu_h, 1)} GPU-h remaining.`,
      };
    case "paired": {
      const positive = (event.delta ?? 0) >= 0;
      const nTxt = event.n !== undefined ? ` over ${event.n} problems` : "";
      return {
        text: `${event.candidate ?? "?"} evaluated${nTxt}: ${percent(event.incumbent, 1)} → `
          + `${percent(event.candidate_score, 1)} (${positive ? "+" : ""}${percent(event.delta, 1)}) `
          + `— boundary: ${event.decision ?? "?"}.`,
        tone: positive ? "good" : "bad",
      };
    }
    case "promoted":
      return { text: `${event.candidate ?? "?"} promoted — this is the new incumbent.`, icon: <Star size={13} />, tone: "good" };
    case "pruned":
      return { text: `${event.candidate ?? "?"} rejected.`, icon: <XCircle size={13} />, tone: "dim" };
    case "closed":
      return { text: `Night ${event.night ?? "?"} ${event.status ?? "closed"}.` };
    case "end":
      return { text: `Run ${event.status ?? "finished"} (exit code ${event.exit_code ?? "?"}).` };
    case "log":
    default:
      return { text: event.text ?? "" };
  }
}

const TONE_CLASS: Record<string, string> = {
  good: "text-[var(--color-accent)]",
  bad: "text-[var(--color-danger)]",
  dim: "text-[var(--color-text-dim)]",
};

function Row({ event }: { event: RunEvent }) {
  const { text, icon, tone } = describe(event);
  const time = event.t
    ? new Date(event.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "";
  return (
    <li className="flex items-start gap-3 border-b border-[var(--color-border)] px-4 py-2 text-sm last:border-b-0">
      <span className="w-20 shrink-0 pt-px text-xs text-[var(--color-text-dim)]">{time}</span>
      {icon && <span className={`mt-0.5 shrink-0 ${tone ? TONE_CLASS[tone] : "text-[var(--color-text-dim)]"}`}>{icon}</span>}
      <span className={tone ? TONE_CLASS[tone] : "text-[var(--color-text)]"}>{text}</span>
    </li>
  );
}

export function Timeline({ events }: { events: RunEvent[] }) {
  if (events.length === 0) {
    return <p className="px-4 py-6 text-sm text-[var(--color-text-dim)]">No events yet.</p>;
  }
  return (
    <ol className="max-h-[32rem] overflow-y-auto">
      {events.map((event, i) => (
        <Row key={i} event={event} />
      ))}
    </ol>
  );
}
