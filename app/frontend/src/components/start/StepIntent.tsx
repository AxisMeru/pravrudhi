"use client";

import { Lightbulb } from "lucide-react";
import { ID_PATTERN, WORKED_EXAMPLES, type WorkedExample } from "@/lib/start";

const FIELD =
  "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)]";

interface Props {
  id: string;
  intent: string;
  domain: string;
  onChangeId: (v: string) => void;
  onChangeIntent: (v: string) => void;
  onChangeDomain: (v: string) => void;
  onUseExample: (example: WorkedExample) => void;
}

function idError(id: string): string | null {
  if (!id) return null;
  return ID_PATTERN.test(id) ? null : "lowercase letters, digits and hyphens only, 2-63 characters";
}

export function StepIntent({ id, intent, domain, onChangeId, onChangeIntent, onChangeDomain, onUseExample }: Props) {
  const error = idError(id);
  return (
    <div>
      <h2 className="text-sm font-medium text-[var(--color-text)]">State the intent</h2>
      <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--color-text-dim)]">
        Write what you want the loop to achieve, in your own words. It is recorded verbatim and never interpreted
        by anything downstream of this screen.
      </p>

      <div className="mt-4 grid gap-3">
        <label className="grid gap-1.5">
          <span className="text-xs text-[var(--color-text-dim)]">Intent</span>
          <textarea
            className={`${FIELD} min-h-28 resize-y leading-6`}
            placeholder="A legal-reasoning assistant that answers a question of law with the statute it relied on, and says it does not know rather than inventing a citation."
            value={intent}
            onChange={(e) => onChangeIntent(e.target.value)}
          />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Short name</span>
            <input
              className={FIELD}
              placeholder="legal-mvp"
              value={id}
              onChange={(e) => onChangeId(e.target.value)}
            />
            {error && <span className="text-[11px] text-[var(--color-danger)]">{error}</span>}
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-[var(--color-text-dim)]">Domain (optional)</span>
            <input
              className={FIELD}
              placeholder="legal"
              value={domain}
              onChange={(e) => onChangeDomain(e.target.value)}
            />
          </label>
        </div>
      </div>

      <div className="mt-6">
        <div className="flex items-center gap-2">
          <Lightbulb size={14} className="text-[var(--color-text-dim)]" />
          <h3 className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
            What a good intent reads like
          </h3>
        </div>
        <p className="mt-1 text-[11px] leading-4 text-[var(--color-text-dim)]">
          Three objectives this engine has actually been pointed at, shipped with it.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {WORKED_EXAMPLES.map((example) => (
            <div
              key={example.id}
              className="flex flex-col rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
            >
              <span className="font-mono text-[11px] text-[var(--color-text-dim)]">{example.id}</span>
              <p className="mt-1.5 flex-1 text-[11px] leading-4 text-[var(--color-text)]">{example.intent}</p>
              <button
                onClick={() => onUseExample(example)}
                className="mt-3 self-start rounded-md border border-[var(--color-border)] px-2.5 py-1 text-[11px] text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
              >
                Use this as a starting point
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
