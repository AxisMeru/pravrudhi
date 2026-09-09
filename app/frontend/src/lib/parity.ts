// Data for the Parity page: how this engine's capabilities compare to rival agents, row by row, with the
// evidence behind our own claim and the gaps still open. /api/parity is being added in parallel with this page,
// so every shape below is provisional until that route lands — a missing route, an old build without it, or a
// malformed response all collapse to the same honest "nothing to show" rather than a crash.

import { apiBase, ApiError, IS_DEMO } from "@/lib/api";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export type ParityStatus = "have" | "partial" | "none" | "unknown";

export interface ParityRivals {
  orca: ParityStatus;
  claude_desktop: ParityStatus;
  codex: ParityStatus;
  openclaw: ParityStatus;
}

export interface ParityRow {
  id: string;
  capability: string;
  why_it_matters: string;
  rivals: ParityRivals;
  ours: ParityStatus;
  evidence: string[];
  notes: string;
}

// The engine counts met over tracked and also hands back the ratio. This was declared as {met, total}, which
// are not fields it has ever sent, so the headline read "NaN of NaN" against a live engine and against the
// recording alike.
export interface ParityCoverage {
  numerator: number;
  denominator: number;
  fraction: number;
}

export interface ParitySnapshot {
  rows: ParityRow[];
  coverage: ParityCoverage;
  // Whole rows, not ids. Typing these as ids handed each object straight to React and crashed the page.
  gaps: ParityRow[];
}

export const RIVAL_COLUMNS: { key: keyof ParityRivals; label: string }[] = [
  { key: "orca", label: "Orca" },
  { key: "claude_desktop", label: "Claude Desktop" },
  { key: "codex", label: "Codex" },
  { key: "openclaw", label: "OpenClaw" },
];

// `unknown` means this row was never checked against that rival, not that the rival lacks the capability — the
// two must never read the same to someone scanning the matrix for real gaps.
export const STATUS_LABEL: Record<ParityStatus, string> = {
  have: "have",
  partial: "partial",
  none: "none",
  unknown: "not checked",
};

// `demo.json` predates this page — the recorded snapshot may simply not carry a `parity` key — so a missing key
// is treated the same as a route this build doesn't answer: an honest empty state, never a crash.
export async function parity(): Promise<ParitySnapshot | null> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { parity?: ParitySnapshot };
    return bundle.parity ?? null;
  }
  try {
    return await getJSON<ParitySnapshot>("/api/parity");
  } catch {
    return null;
  }
}

// A relative repository path such as `src/pravrudhi/application/parity.py`, as opposed to freeform prose. Only
// evidence that looks like one earns a link — this constant already appears in `lib/desktop.ts`, so it is a
// reused fact about this repository, not a guessed URL.
const REPO_BLOB_BASE = "https://github.com/AxisMeru/pravrudhi/blob/main/";
const PATH_LIKE = /^[\w.-]+(\/[\w.-]+)+\.\w+$/;

// A row carries several pieces of evidence, some repository paths and some commands. The first path-like one is
// what a reader wants to click; a row of commands alone links nowhere, which is correct rather than a failure.
export function evidenceHref(evidence: readonly string[]): string | null {
  const path = evidence.map((e) => e.trim()).find((e) => PATH_LIKE.test(e));
  return path ? `${REPO_BLOB_BASE}${path}` : null;
}
