// Data layer for the global command palette: the static page list (mirrors Sidebar.tsx's NAV, which is not
// exported so this list is kept here instead), the actions the palette can run, and the searchable records it
// pulls from the engine's own typed clients (lib/api.ts, lib/swarm.ts, lib/requests.ts). No React here — the
// component owns rendering and keyboard handling, this file owns what there is to find and what happens when
// something is chosen.

import {
  IS_DEMO,
  objectives as fetchObjectives,
  candidates as fetchCandidates,
  runs as fetchRuns,
  models as fetchModels,
  recipeLibrary,
  dispatchSubagents,
  stopRun,
  updateStatus,
  applyUpdate,
  type Objective,
  type Candidate,
  type RunHandle,
  type PromotedModel,
  type Recipe,
} from "./api";
import { requests as fetchRequests, type RequestItem } from "./requests";
import { swarm as fetchSwarm, type SwarmAgent } from "./swarm";
import { fixed } from "./num";

export interface PalettePage {
  id: string;
  label: string;
  href: string;
  icon: string;
  // Only the first nine sidebar entries get a bare-digit shortcut — "where sensible" per the ask, and ten-plus
  // single-digit slots would leave no digit free for anything else.
  digit?: number;
}

// Mirrors Sidebar.tsx's NAV verbatim. Sidebar does not export it, and this file is not allowed to touch
// Sidebar.tsx, so the label/href/icon list is kept here in step with it by hand.
// Which pages belong to Pravrudhi improving *itself* rather than to a user improving their own work. A product
// install does not serve these surfaces at all — the engine refuses them by edition, not by role
// (src/pravrudhi/api/roles.py::gate) — so listing them would offer a reader doors that answer 404.
//
// The line is the same one the engine draws: the ledger's candidates and nights, the promotion inbox, the swarm
// that builds the engine, the diffs its agents produced, and the appetite driving it.
export const STUDIO_ONLY_PAGES: ReadonlySet<string> = new Set([
  "appetite", "inbox", "candidates", "swarm", "diffs", "heartbeat",
]);

// The sidebar keeps its own copy of this list, so the rule lives here and both ask it rather than each
// carrying its own idea of which pages are Studio's.
export function isStudioOnlyHref(href: string): boolean {
  return PALETTE_PAGES.some((p) => p.href === href && STUDIO_ONLY_PAGES.has(p.id));
}

export function pagesFor(isStudio: boolean): PalettePage[] {
  return isStudio ? PALETTE_PAGES : PALETTE_PAGES.filter((p) => !STUDIO_ONLY_PAGES.has(p.id));
}

export const PALETTE_PAGES: PalettePage[] = [
  { id: "start", label: "Start", href: "/start", icon: "Rocket", digit: 1 },
  { id: "improve", label: "Improve", href: "/", icon: "Sparkles", digit: 2 },
  { id: "tour", label: "Tour", href: "/tour", icon: "Compass", digit: 3 },
  { id: "appetite", label: "Appetite", href: "/appetite", icon: "Flame", digit: 4 },
  { id: "objectives", label: "Objectives", href: "/objectives", icon: "Target", digit: 5 },
  { id: "progress", label: "Progress", href: "/progress", icon: "LineChart", digit: 6 },
  { id: "inbox", label: "Inbox", href: "/inbox", icon: "Inbox", digit: 7 },
  { id: "requests", label: "Requests", href: "/requests", icon: "ListChecks", digit: 8 },
  { id: "candidates", label: "Candidates", href: "/candidates", icon: "Layers", digit: 9 },
  { id: "swarm", label: "Swarm", href: "/swarm", icon: "Bot" },
  { id: "diffs", label: "Diffs", href: "/diffs", icon: "FileDiff" },
  { id: "memory", label: "Memory", href: "/memory", icon: "Brain" },
  { id: "heartbeat", label: "Heartbeat", href: "/heartbeat", icon: "Activity" },
  { id: "catalogue", label: "Catalogue", href: "/catalogue", icon: "Library" },
  { id: "chat", label: "Chat", href: "/chat", icon: "MessageSquare" },
  { id: "runs", label: "Runs", href: "/runs", icon: "History" },
  { id: "models", label: "Models", href: "/models", icon: "Package" },
  { id: "machines", label: "Machines", href: "/machines", icon: "Server" },
  { id: "settings", label: "Settings", href: "/settings", icon: "Settings" },
  { id: "install", label: "Install", href: "/install", icon: "Download" },
];

export type PaletteGroup =
  | "Pages"
  | "Actions"
  | "Objectives"
  | "Candidates"
  | "Requests"
  | "Runs"
  | "Models"
  | "Recipes"
  | "Tools";

export const GROUP_ORDER: PaletteGroup[] = [
  "Pages",
  "Actions",
  "Objectives",
  "Candidates",
  "Requests",
  "Runs",
  "Models",
  "Recipes",
  "Tools",
];

export interface PaletteRunResult {
  ok: boolean;
  message: string;
}

export interface PaletteResult {
  id: string;
  group: PaletteGroup;
  title: string;
  subtitle?: string;
  hint?: string;
  href?: string;
  run?: () => Promise<PaletteRunResult>;
  disabledReason?: string;
}

export interface PaletteIndex {
  objectives: Objective[];
  candidates: Candidate[];
  requests: RequestItem[];
  runs: RunHandle[];
  models: PromotedModel[];
  recipes: Recipe[];
  agents: SwarmAgent[];
}

export const EMPTY_INDEX: PaletteIndex = {
  objectives: [],
  candidates: [],
  requests: [],
  runs: [],
  models: [],
  recipes: [],
  agents: [],
};

function settled<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === "fulfilled" ? result.value : fallback;
}

// Every fetch here can fail independently (no engine, an older build missing an endpoint) — Promise.allSettled
// so one missing surface never blanks out the rest of the palette.
export async function loadPaletteIndex(): Promise<PaletteIndex> {
  const [objectivesRes, candidatesRes, requestsRes, runsRes, modelsRes, recipesRes, swarmRes] =
    await Promise.allSettled([
      fetchObjectives(),
      fetchCandidates(),
      fetchRequests(),
      fetchRuns(),
      fetchModels(),
      recipeLibrary(),
      fetchSwarm(),
    ]);
  const objectivesData = settled(objectivesRes, { objectives: [], problems: [] });
  const requestsData = settled(requestsRes, null);
  const swarmData = settled(swarmRes, null);
  return {
    objectives: objectivesData.objectives,
    candidates: settled(candidatesRes, []),
    requests: requestsData?.requests ?? [],
    runs: settled(runsRes, []),
    models: settled(modelsRes, []),
    recipes: settled(recipesRes, []),
    agents: swarmData?.agents ?? [],
  };
}

const DEMO_REASON = "Demo mode — actions are disabled on this recording";

function pageResults(isStudio: boolean): PaletteResult[] {
  return pagesFor(isStudio).map((p) => ({
    id: `page:${p.id}`,
    group: "Pages",
    title: p.label,
    href: p.href,
    hint: p.digit ? String(p.digit) : undefined,
  }));
}

function globalActions(isDemo: boolean): PaletteResult[] {
  return [
    {
      id: "action:start-night",
      group: "Actions",
      title: "Start a night",
      subtitle: "Open the guided flow to launch a new run",
      href: "/start",
    },
    {
      id: "action:run-beat",
      group: "Actions",
      title: "Run a beat",
      subtitle: "Open the heartbeat log",
      href: "/heartbeat",
    },
    {
      id: "action:check-updates",
      group: "Actions",
      title: "Check for updates",
      subtitle: "Compare this checkout against the newest tagged release",
      disabledReason: isDemo ? DEMO_REASON : undefined,
      run: async () => {
        const s = await updateStatus();
        return s.update_available
          ? { ok: true, message: `Update available: ${s.latest?.tag ?? "unknown"}` }
          : { ok: true, message: `Up to date (${s.current.version})` };
      },
    },
    {
      id: "action:publish-update",
      group: "Actions",
      title: "Publish update",
      subtitle: "Apply the newest release to this engine",
      disabledReason: isDemo ? DEMO_REASON : undefined,
      run: async () => {
        const r = await applyUpdate();
        return {
          ok: r.applied,
          message: r.applied ? `Applied ${r.version ?? "update"}` : r.reason || "No update applied",
        };
      },
    },
  ];
}

function objectiveResults(items: Objective[], isDemo: boolean): PaletteResult[] {
  return items.flatMap((o) => {
    const base: PaletteResult = {
      id: `objective:${o.id}`,
      group: "Objectives",
      title: o.id,
      subtitle: `${o.track} · ${o.domain} — ${o.intent}`,
      href: "/objectives",
    };
    const dispatch: PaletteResult = {
      id: `action:dispatch:${o.id}`,
      group: "Actions",
      title: `Dispatch subagents — ${o.id}`,
      subtitle: "Run this objective's plan through the swarm now",
      disabledReason: isDemo ? DEMO_REASON : undefined,
      run: async () => {
        const res = await dispatchSubagents(o.id);
        const n = res.runs.length;
        return { ok: true, message: `Dispatched ${n} step${n === 1 ? "" : "s"}` };
      },
    };
    return [base, dispatch];
  });
}

function candidateResults(items: Candidate[]): PaletteResult[] {
  return items.map((c) => ({
    id: `candidate:${c.id}`,
    group: "Candidates",
    title: c.id,
    subtitle: `${c.badge}${c.surface ? ` · ${c.surface}` : ""} · ${fixed(c.cost_gpu_h, 2)} GPU-h`,
    href: "/candidates",
  }));
}

function requestResults(items: RequestItem[]): PaletteResult[] {
  return items.map((r) => ({
    id: `request:${r.id}`,
    group: "Requests",
    title: r.text.length > 60 ? `${r.text.slice(0, 57)}…` : r.text,
    subtitle: `${r.state} · ${fixed(r.progress[0], 0)}/${fixed(r.progress[1], 0)} criteria`,
    href: "/requests",
  }));
}

function runResults(items: RunHandle[], isDemo: boolean): PaletteResult[] {
  return items.flatMap((r) => {
    const base: PaletteResult = {
      id: `run:${r.id}`,
      group: "Runs",
      title: r.id,
      subtitle: "Run",
      href: "/runs",
    };
    const stop: PaletteResult = {
      id: `action:stop:${r.id}`,
      group: "Actions",
      title: `Stop run — ${r.id}`,
      subtitle: "Send a stop signal to this run",
      disabledReason: isDemo ? DEMO_REASON : undefined,
      run: async () => {
        await stopRun(r.id);
        return { ok: true, message: `Stop requested for ${r.id}` };
      },
    };
    return [base, stop];
  });
}

function modelResults(items: PromotedModel[]): PaletteResult[] {
  return items.map((m) => ({
    id: `model:${m.id}`,
    group: "Models",
    title: m.id,
    subtitle: `${m.track} · night ${fixed(m.night, 0)}`,
    href: "/models",
  }));
}

function recipeResults(items: Recipe[]): PaletteResult[] {
  return items.map((r) => ({
    id: `recipe:${r.id}`,
    group: "Recipes",
    title: r.title,
    subtitle: `${r.capability} · ${r.available ? "installed" : "not installed"}`,
    href: "/catalogue",
  }));
}

function agentResults(items: SwarmAgent[]): PaletteResult[] {
  return items.map((a) => ({
    id: `agent:${a.name}`,
    group: "Tools",
    title: a.name,
    subtitle: a.available ? "available" : a.reason || "unavailable",
    href: "/swarm",
  }));
}

// The full, unfiltered catalogue for one render pass. Cheap to rebuild on every index change — the arrays
// involved are all small (an operator's own objectives/candidates/runs, not a public dataset).
export function buildCatalogue(
  index: PaletteIndex, isDemo: boolean = IS_DEMO, isStudio: boolean = true,
): PaletteResult[] {
  return [
    ...pageResults(isStudio),
    ...globalActions(isDemo),
    ...objectiveResults(index.objectives, isDemo),
    ...candidateResults(index.candidates),
    ...requestResults(index.requests),
    ...runResults(index.runs, isDemo),
    ...modelResults(index.models),
    ...recipeResults(index.recipes),
    ...agentResults(index.agents),
  ];
}

export function filterResults(all: PaletteResult[], query: string): PaletteResult[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return all.filter((r) => r.title.toLowerCase().includes(q) || (r.subtitle?.toLowerCase().includes(q) ?? false));
}

// ---------------------------------------------------------------------------
// Recents: the last few things chosen, kept per-browser so repeat work is one keystroke. Only navigable choices
// (pages and records) are remembered — a run() closure for an action like "Check for updates" cannot be
// reconstructed from JSON, so action-only entries are simply left out of this list.

const RECENTS_KEY = "pravrudhi.command-palette.recents";
const MAX_RECENTS = 5;

export interface PaletteRecent {
  id: string;
  group: PaletteGroup;
  title: string;
  subtitle?: string;
  href: string;
}

export function getRecents(): PaletteRecent[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as PaletteRecent[]) : [];
  } catch {
    return [];
  }
}

export function pushRecent(entry: PaletteRecent): void {
  if (typeof window === "undefined") return;
  try {
    const next = [entry, ...getRecents().filter((r) => r.id !== entry.id)].slice(0, MAX_RECENTS);
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    /* localStorage unavailable (private mode, quota) — recents just don't persist this session */
  }
}
