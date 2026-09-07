// What the shell actually does for you, drawn from app/desktop/main.js rather than invented.

import { WHAT_IT_DOES } from "@/lib/desktop";

export function WhatItDoes() {
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {WHAT_IT_DOES.map((fact) => (
        <li key={fact.title} className="rounded-md border border-[var(--color-border)] p-4">
          <div className="text-sm font-medium text-[var(--color-text)]">{fact.title}</div>
          <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">{fact.detail}</p>
        </li>
      ))}
    </ul>
  );
}
