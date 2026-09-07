import { PALETTE_PAGES, type PalettePage } from "./palette";

export type KeyPart = string | { mac: string; other: string };

export interface ShortcutDef {
  id: string;
  description: string;
  keys: KeyPart[];
  detail?: string;
}

export interface ShortcutGroup {
  title: string;
  items: ShortcutDef[];
}

const DIGIT_PAGES = PALETTE_PAGES.filter(
  (p): p is PalettePage & { digit: number } => typeof p.digit === "number",
).sort((a, b) => a.digit - b.digit);

export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "Anywhere",
    items: [
      {
        id: "open-palette",
        description: "Open the command palette",
        keys: [{ mac: "⌘", other: "Ctrl" }, "K"],
      },
      {
        id: "go-to-page",
        description: "Go to a sidebar page",
        keys: ["1–9"],
        detail: DIGIT_PAGES.map((p) => `${p.digit} ${p.label}`).join("  ·  "),
      },
      {
        id: "focus-primary",
        description: "Focus the page’s primary input",
        keys: ["/"],
      },
      {
        id: "toggle-help",
        description: "Show or hide this list",
        keys: ["?"],
      },
      {
        id: "dismiss",
        description: "Dismiss whatever is open",
        keys: ["Esc"],
      },
    ],
  },
  {
    title: "While the palette is open",
    items: [
      { id: "palette-move", description: "Move the selection", keys: ["↑", "↓"] },
      { id: "palette-enter", description: "Open the page or run the action", keys: ["Enter"] },
      { id: "palette-close", description: "Close the palette", keys: ["Esc"] },
    ],
  },
];

export function keyLabel(part: KeyPart, mac: boolean): string {
  return typeof part === "string" ? part : mac ? part.mac : part.other;
}

export function isMacPlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Macintosh|Mac OS X|iPhone|iPad|iPod/.test(navigator.userAgent);
}

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

export function isModalOverlayOpen(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector("div.fixed.inset-0.z-50") !== null;
}

const PRIMARY_SELECTORS = [
  '[data-shortcut-focus="primary"]',
  'input[type="search"]',
  "textarea",
  'input[type="text"]',
  "input:not([type])",
];

function isVisible(el: HTMLElement): boolean {
  return el.checkVisibility ? el.checkVisibility() : el.getClientRects().length > 0;
}

export function focusPrimaryInput(): boolean {
  const scope = document.querySelector("main") ?? document.body;
  for (const selector of PRIMARY_SELECTORS) {
    for (const el of Array.from(scope.querySelectorAll<HTMLElement>(selector))) {
      if (el.hasAttribute("disabled")) continue;
      if (!isVisible(el)) continue;
      el.focus();
      if (el instanceof HTMLInputElement) el.select();
      return true;
    }
  }
  return false;
}
