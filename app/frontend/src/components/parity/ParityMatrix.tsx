// The matrix itself: one row per capability, one column per product. Ours carries its evidence underneath the
// verdict, linked out to the source when that evidence is a repository path rather than a sentence of prose.

import { RIVAL_COLUMNS, evidenceHref, type ParityRow } from "@/lib/parity";
import { StatusPill } from "./StatusPill";
import { Empty } from "@/components/system/Section";

function OursCell({ row }: { row: ParityRow }) {
  // Evidence is a list. It was treated as one string, so a row's whole evidence array was handed to the link
  // text and to `evidenceHref`, and a row claiming nothing had `evidence: []` — falsy checks on an array are
  // always true, so an empty list rendered an empty link. Each piece gets its own line, and the first
  // repository path among them is what the row links to.
  const evidence = Array.isArray(row.evidence) ? row.evidence : [];
  const href = evidenceHref(evidence);
  return (
    <div className="flex flex-col items-start gap-1">
      <StatusPill status={row.ours} />
      {evidence.length > 0 && (
        <div className="flex flex-col items-start gap-0.5">
          {evidence.map((item, i) =>
            i === 0 && href ? (
              <a
                key={item}
                href={href}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-[10px] text-[var(--color-accent)] underline decoration-dotted"
              >
                {item}
              </a>
            ) : (
              <span key={item} className="font-mono text-[10px] text-[var(--color-text-dim)]">
                {item}
              </span>
            ),
          )}
        </div>
      )}
    </div>
  );
}

export function ParityMatrix({ rows }: { rows: ParityRow[] }) {
  if (rows.length === 0) return <Empty text="No capabilities are recorded in the matrix yet." />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead>
          <tr className="text-[var(--color-text-dim)]">
            <th className="pb-2 pr-3 font-normal">Capability</th>
            <th className="pb-2 pr-3 font-normal">Ours</th>
            {RIVAL_COLUMNS.map((c) => (
              <th key={c.key} className="pb-2 pr-3 font-normal">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-t border-[var(--color-border)] align-top">
              <td className="py-2 pr-3">
                <div className="font-medium text-[var(--color-text)]">{row.capability}</div>
                {row.why_it_matters && (
                  <p className="mt-0.5 max-w-xs leading-5 text-[var(--color-text-dim)]">{row.why_it_matters}</p>
                )}
                {row.notes && <p className="mt-0.5 max-w-xs leading-5 text-[var(--color-text-dim)]">{row.notes}</p>}
              </td>
              <td className="py-2 pr-3">
                <OursCell row={row} />
              </td>
              {RIVAL_COLUMNS.map((c) => (
                <td key={c.key} className="py-2 pr-3">
                  <StatusPill status={row.rivals[c.key]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
