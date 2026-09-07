// Shared section chrome for the System page, matching the card style objectives/detail already established:
// a bordered panel, an icon-led heading, an optional one-line subtitle, then the section's own content.

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function Section({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: LucideIcon;
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <div className="flex items-center gap-2">
        <Icon size={16} className="text-[var(--color-text-dim)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">{title}</h2>
      </div>
      {subtitle && <p className="mt-1 text-xs leading-5 text-[var(--color-text-dim)]">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function Empty({ text }: { text: string }) {
  return <p className="text-sm text-[var(--color-text-dim)]">{text}</p>;
}
