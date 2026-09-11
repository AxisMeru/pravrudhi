"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { edition, STUDIO } from "@/lib/edition";
import { isStudioOnlyHref } from "@/lib/palette";

/**
 * The page-level half of edition separation. The sidebar hides Studio's pages from a product install, but a
 * typed URL, a bookmark or a link inside a page walks straight past a hidden sidebar entry, and the engine's
 * 404 then arrives as a broken-looking page rather than as an answer. Here the answer is stated: this surface
 * belongs to Pravrudhi improving itself, and this edition does not have one.
 *
 * Fails open while the edition is unknown (first paint, or an engine that cannot be reached): a product user
 * whose engine is down should see the connection banner, not a wrong "not in this edition".
 */
export function EditionGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [isStudio, setIsStudio] = useState<boolean | null>(null);

  useEffect(() => {
    let off = false;
    edition()
      .then((e) => !off && setIsStudio(e.edition === STUDIO))
      .catch(() => !off && setIsStudio(null));
    return () => {
      off = true;
    };
  }, []);

  if (isStudio === false && pathname && isStudioOnlyHref(pathname)) {
    return (
      <div className="mx-auto max-w-xl p-8 text-[var(--color-text)]">
        <h1 className="text-lg font-semibold tracking-tight">Not part of this edition</h1>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          This page belongs to Pravrudhi Studio, where the engine improves itself. Your install is the product:
          your own goals, runs, models, chat and Nyaya. Nothing is broken; this surface does not exist here.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
