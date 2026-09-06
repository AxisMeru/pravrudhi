export function AppetiteSentence({ sentence, asOf }: { sentence: string; asOf: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-6">
      <p className="text-[11px] uppercase tracking-wide text-[var(--color-text-dim)]">what it wants right now</p>
      <p className="mt-2 text-2xl font-medium leading-snug text-[var(--color-text)]">{sentence}</p>
      {asOf && <p className="mt-3 text-[11px] text-[var(--color-text-dim)]">as of {asOf}</p>}
    </div>
  );
}
