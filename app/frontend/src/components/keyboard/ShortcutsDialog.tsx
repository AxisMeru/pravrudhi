"use client";

import { useEffect, useRef } from "react";
import { Command, X } from "lucide-react";
import { SHORTCUT_GROUPS, keyLabel, type KeyPart } from "@/lib/shortcuts";
import { onDismiss } from "./dismiss";

function Keys({ parts, mac }: { parts: KeyPart[]; mac: boolean }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      {parts.map((part, i) => (
        <kbd
          key={i}
          className="rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] text-[var(--color-text)]"
        >
          {keyLabel(part, mac)}
        </kbd>
      ))}
    </span>
  );
}

export function ShortcutsDialog({ open, onClose, mac }: { open: boolean; onClose: () => void; mac: boolean }) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    panelRef.current?.focus();
    const off = onDismiss(onClose);
    return () => {
      off();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        className="w-full max-w-md rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-2xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text)]">
            <Command size={16} />
            Keyboard shortcuts
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
          >
            <X size={16} />
          </button>
        </div>
        <div className="max-h-[70vh] space-y-4 overflow-y-auto">
          {SHORTCUT_GROUPS.map((group) => (
            <section key={group.title}>
              <h2 className="mb-2 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
                {group.title}
              </h2>
              <dl className="space-y-2">
                {group.items.map((s) => (
                  <div key={s.id} className="flex items-start justify-between gap-4 text-sm">
                    <dt className="min-w-0 text-[var(--color-text-dim)]">
                      {s.description}
                      {s.detail && (
                        <span className="mt-1 block text-xs leading-relaxed text-[var(--color-text-dim)]">
                          {s.detail}
                        </span>
                      )}
                    </dt>
                    <dd>
                      <Keys parts={s.keys} mac={mac} />
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <p className="mt-4 border-t border-[var(--color-border)] pt-3 text-xs leading-relaxed text-[var(--color-text-dim)]">
          None of these fire while you are typing in a field. A page can mark its own primary input with{" "}
          <code className="text-[var(--color-text)]">data-shortcut-focus=&quot;primary&quot;</code> to claim{" "}
          <kbd className="rounded border border-[var(--color-border)] px-1 py-px text-[10px] text-[var(--color-text)]">
            /
          </kbd>
          .
        </p>
      </div>
    </div>
  );
}
