"use client";

import { useCallback, useEffect, useState } from "react";
import { Command } from "lucide-react";
import { focusPrimaryInput, isMacPlatform, isModalOverlayOpen, isTypingTarget } from "@/lib/shortcuts";
import { dismissTopmost } from "./dismiss";
import { ShortcutsDialog } from "./ShortcutsDialog";

export function KeyboardShortcuts() {
  const [helpOpen, setHelpOpen] = useState(false);
  const [mac, setMac] = useState(false);

  useEffect(() => {
    setMac(isMacPlatform());
  }, []);

  const openHelp = useCallback(() => setHelpOpen(true), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.isComposing || e.keyCode === 229) return;

      const bare = !e.ctrlKey && !e.metaKey && !e.altKey;

      if (e.key === "Escape" && bare) {
        if (dismissTopmost()) e.preventDefault();
        return;
      }

      if (!bare) return;

      if (helpOpen) {
        if (e.key === "?" && !isTypingTarget(e.target)) {
          e.preventDefault();
          e.stopPropagation();
          closeHelp();
        } else if (e.key === "/" || /^[1-9]$/.test(e.key)) {
          e.preventDefault();
          e.stopPropagation();
        }
        return;
      }

      if (isTypingTarget(e.target)) return;

      if (isModalOverlayOpen()) return;

      if (e.key === "?") {
        e.preventDefault();
        e.stopPropagation();
        openHelp();
        return;
      }

      if (e.key === "/") {
        e.preventDefault();
        focusPrimaryInput();
      }
    }

    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [helpOpen, openHelp, closeHelp]);

  return (
    <>
      {!helpOpen && (
        <button
          type="button"
          onClick={openHelp}
          aria-label="Show keyboard shortcuts"
          title="Keyboard shortcuts (?)"
          className="fixed bottom-4 right-4 z-40 flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-[11px] text-[var(--color-text-dim)] shadow-lg transition-colors hover:text-[var(--color-text)]"
        >
          <Command size={12} />
          Shortcuts
          <kbd className="rounded border border-[var(--color-border)] px-1 py-px text-[10px]">?</kbd>
        </button>
      )}
      <ShortcutsDialog open={helpOpen} onClose={closeHelp} mac={mac} />
    </>
  );
}
