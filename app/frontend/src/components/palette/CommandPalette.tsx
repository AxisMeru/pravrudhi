"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";
import { useRouter } from "next/navigation";
import {
  Sparkles,
  History,
  Package,
  Server,
  Settings,
  Download,
  Target,
  MessageSquare,
  LineChart,
  Bot,
  Brain,
  FileDiff,
  Activity,
  Inbox as InboxIcon,
  Library,
  Layers,
  ListChecks,
  Compass,
  Flame,
  Rocket,
  Scale,
  Search,
  Command,
} from "lucide-react";
import { IS_DEMO } from "@/lib/api";
import { edition, STUDIO } from "@/lib/edition";
import {
  PALETTE_PAGES,
  GROUP_ORDER,
  buildCatalogue,
  filterResults,
  getRecents,
  pushRecent,
  loadPaletteIndex,
  EMPTY_INDEX,
  type PaletteResult,
  type PaletteIndex,
} from "@/lib/palette";

const PAGE_ICONS: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  Sparkles,
  History,
  Package,
  Server,
  Settings,
  Download,
  Target,
  MessageSquare,
  LineChart,
  Bot,
  Brain,
  FileDiff,
  Activity,
  Inbox: InboxIcon,
  Library,
  Layers,
  ListChecks,
  Compass,
  Flame,
  Rocket,
  Scale,
};

const PAGE_ICON_BY_HREF = new Map(PALETTE_PAGES.map((p) => [p.href, PAGE_ICONS[p.icon]]));

const SHORTCUTS: { keys: string; description: string }[] = [
  { keys: "Ctrl/⌘ K", description: "Open the command palette" },
  { keys: "Esc", description: "Close whatever is open" },
  { keys: "↑ / ↓", description: "Move the selection" },
  { keys: "Enter", description: "Open the page or run the action" },
  { keys: "1–9", description: "Jump straight to that sidebar page" },
  { keys: "?", description: "Show this list" },
];

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

interface Section {
  group: string;
  items: PaletteResult[];
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [prevQuery, setPrevQuery] = useState(query);
  const [activeIndex, setActiveIndex] = useState(0);
  const [index, setIndex] = useState<PaletteIndex>(EMPTY_INDEX);
  const [recents, setRecents] = useState(() => getRecents());
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    loadPaletteIndex().then((next) => {
      if (!cancelled) setIndex(next);
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  // Reset the palette's own state as part of the action that opens it, rather than syncing it in an effect that
  // fires afterward — one render instead of an open-then-reset flash.
  const openPalette = useCallback(() => {
    setRecents(getRecents());
    setQuery("");
    setPrevQuery("");
    setActiveIndex(0);
    setStatus(null);
    setOpen(true);
  }, []);

  // Which edition this engine is, so the palette does not offer pages a product install answers 404 for.
  // Studio until the engine says otherwise: this checkout is Studio, and a momentary wrong answer that hides
  // a page is worse than one that shows it.
  const [isStudio, setIsStudio] = useState(true);
  useEffect(() => {
    let off = false;
    edition().then((e) => !off && setIsStudio(e.edition === STUDIO)).catch(() => {});
    return () => { off = true; };
  }, []);

  const catalogue = useMemo(() => buildCatalogue(index, IS_DEMO, isStudio), [index, isStudio]);
  const filtered = useMemo(() => filterResults(catalogue, query), [catalogue, query]);

  const sections = useMemo<Section[]>(() => {
    if (!query.trim()) {
      const out: Section[] = [];
      if (recents.length) out.push({ group: "Recent", items: recents });
      out.push({ group: "Pages", items: catalogue.filter((r) => r.group === "Pages") });
      return out;
    }
    return GROUP_ORDER.map((group) => ({ group, items: filtered.filter((r) => r.group === group) })).filter(
      (s) => s.items.length > 0,
    );
  }, [query, recents, catalogue, filtered]);

  const flatList = useMemo(() => sections.flatMap((s) => s.items), [sections]);
  const flatListRef = useRef(flatList);
  const activeIndexRef = useRef(activeIndex);
  useEffect(() => {
    flatListRef.current = flatList;
  }, [flatList]);
  useEffect(() => {
    activeIndexRef.current = activeIndex;
  }, [activeIndex]);

  // Adjust the selection during render when the query changes, rather than in a follow-up effect — the
  // React-endorsed pattern for state that must reset in step with another value the component already renders.
  if (query !== prevQuery) {
    setPrevQuery(query);
    setActiveIndex(0);
  }

  const execute = useCallback(
    async (item: PaletteResult) => {
      if (item.disabledReason) {
        setStatus({ ok: false, message: item.disabledReason });
        return;
      }
      if (item.run) {
        setPending(item.id);
        try {
          const result = await item.run();
          setStatus(result);
        } catch (err) {
          setStatus({ ok: false, message: err instanceof Error ? err.message : "Action failed" });
        } finally {
          setPending(null);
        }
        return;
      }
      if (item.href) {
        pushRecent({ id: item.id, group: item.group, title: item.title, subtitle: item.subtitle, href: item.href });
        setOpen(false);
        router.push(item.href);
      }
    },
    [router],
  );

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open) {
          setOpen(false);
        } else {
          openPalette();
        }
        return;
      }
      if (!open && !shortcutsOpen && !mod && !e.altKey && !isEditableTarget(e.target)) {
        if (e.key === "?") {
          e.preventDefault();
          setShortcutsOpen(true);
          return;
        }
        if (/^[1-9]$/.test(e.key)) {
          const page = PALETTE_PAGES.find((p) => p.digit === Number(e.key));
          if (page) {
            e.preventDefault();
            router.push(page.href);
          }
          return;
        }
      }
      if (shortcutsOpen && e.key === "Escape") {
        e.preventDefault();
        setShortcutsOpen(false);
        return;
      }
      if (!open) return;
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, flatListRef.current.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const item = flatListRef.current[activeIndexRef.current];
        if (item) void execute(item);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, shortcutsOpen, router, execute, openPalette]);

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[12vh]"
          onClick={() => setOpen(false)}
        >
          <div
            className="w-full max-w-xl overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
              <Search size={16} className="text-[var(--color-text-dim)]" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search pages, actions, objectives, runs…"
                className="flex-1 bg-transparent text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-dim)]"
              />
              <kbd className="rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-dim)]">
                esc
              </kbd>
            </div>
            <div className="max-h-[60vh] overflow-y-auto p-2">
              {sections.length === 0 && (
                <p className="px-3 py-6 text-center text-sm text-[var(--color-text-dim)]">No matches.</p>
              )}
              {sections.map((section) => (
                <div key={section.group} className="mb-2 last:mb-0">
                  <div className="px-3 py-1 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-dim)]">
                    {section.group}
                  </div>
                  {section.items.map((item) => {
                    const flatIndex = flatList.indexOf(item);
                    const active = flatIndex === activeIndex;
                    const disabled = Boolean(item.disabledReason);
                    const Icon = item.href ? PAGE_ICON_BY_HREF.get(item.href) : undefined;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        disabled={pending === item.id}
                        onMouseEnter={() => setActiveIndex(flatIndex)}
                        onClick={() => void execute(item)}
                        className={`flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                          active
                            ? "bg-[var(--color-surface-raised)] text-[var(--color-text)]"
                            : "text-[var(--color-text-dim)]"
                        } ${disabled ? "opacity-50" : "hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"}`}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          {Icon && <Icon size={14} className="shrink-0" />}
                          <span className="flex min-w-0 flex-col">
                            <span className="truncate">{item.title}</span>
                            {(item.subtitle || (disabled && item.disabledReason)) && (
                              <span className="truncate text-xs text-[var(--color-text-dim)]">
                                {disabled ? item.disabledReason : item.subtitle}
                              </span>
                            )}
                          </span>
                        </span>
                        {item.hint && (
                          <kbd className="shrink-0 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-dim)]">
                            {item.hint}
                          </kbd>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-dim)]">
              <span>↑↓ navigate · ↵ select · esc close · ? shortcuts</span>
              {status && (
                <span className={status.ok ? "text-[var(--color-accent)]" : "text-red-400"}>{status.message}</span>
              )}
            </div>
          </div>
        </div>
      )}
      {shortcutsOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => setShortcutsOpen(false)}
        >
          <div
            className="w-full max-w-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text)]">
              <Command size={16} />
              Keyboard shortcuts
            </div>
            <dl className="space-y-2">
              {SHORTCUTS.map((s) => (
                <div key={s.keys} className="flex items-center justify-between gap-4 text-sm">
                  <dt className="text-[var(--color-text-dim)]">{s.description}</dt>
                  <dd>
                    <kbd className="rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] text-[var(--color-text)]">
                      {s.keys}
                    </kbd>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
    </>
  );
}
