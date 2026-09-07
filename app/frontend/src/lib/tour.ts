// Data for the guided tour: every step reads from the same recorded snapshot the rest of the public site runs
// on, through the same typed accessors those other pages already use. Nothing here is invented — a step whose
// section is missing from this recording says so, rather than filling the gap with a plausible-looking number.

import {
  ApiError,
  IS_DEMO,
  candidates as fetchCandidatesApi,
  models as fetchModels,
  objectivePlan,
  objectives as fetchObjectives,
  recipeLibrary,
  status as fetchStatus,
  type Candidate,
  type Measurement,
  type Objective,
  type Plan,
  type PromotedModel,
  type Recipe,
} from "@/lib/api";
import { inbox as fetchInbox, type InboxItem } from "@/lib/inbox";
import { swarm as fetchSwarm, type SwarmSnapshot } from "@/lib/swarm";
import { demo } from "@/lib/demo";

// This site is served beside the app rather than at the origin root on the deployed host (see lib/demo.ts), so
// any asset fetched or linked by a root-absolute path — the walkthrough frames, the desktop capture — needs this
// prefix compiled in too.
export const TOUR_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export interface EngineVersion {
  commit: string;
  engine: string;
  kernel: string;
  exported_at: string;
}

export interface CapabilityTool {
  id: string;
  kind: string;
  available: boolean;
}

export interface Capabilities {
  agents: { name: string; available: boolean }[];
  tools: CapabilityTool[];
  recipes: number;
  policies: string[];
  pages: string[];
}

export interface BenchmarkMove {
  objectiveId: string;
  benchmark: string;
  baseline: Measurement;
  latest: Measurement;
  delta: number | null;
  deltaLo: number | null;
  deltaHi: number | null;
  significant: boolean;
  targetDelta: number | null;
  met: boolean | null;
}

export interface WalkthroughFrame {
  image: string;
  caption: string;
  path: string;
}

export interface TourData {
  objective: Objective | null;
  recipeTitles: Map<string, Recipe>;
  plan: Plan | null;
  planError: string | null;
  swarm: SwarmSnapshot | null;
  featuredNight: { night: number; track: string } | null;
  candidates: Candidate[];
  incumbentId: string | null;
  promoted: PromotedModel | null;
  pruned: Candidate | null;
  inboxItems: InboxItem[];
  benchmarkMoves: BenchmarkMove[];
  version: EngineVersion | null;
  capabilities: Capabilities | null;
  walkthroughFrames: WalkthroughFrame[];
}

async function loadCandidates(): Promise<Candidate[]> {
  if (!IS_DEMO) return fetchCandidatesApi();
  const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { candidates?: Candidate[] };
  return bundle.candidates ?? [];
}

async function loadVersion(): Promise<EngineVersion | null> {
  if (!IS_DEMO) return null;
  const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { version?: EngineVersion };
  return bundle.version ?? null;
}

async function loadCapabilities(): Promise<Capabilities | null> {
  if (!IS_DEMO) return null;
  const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & { capabilities?: Capabilities };
  return bundle.capabilities ?? null;
}

// The walkthrough frames are static PNGs captured by driving a real browser through the running app, served
// beside this build rather than baked into demo.json. A build without them (or a host that hasn't deployed the
// public/walkthrough/ folder yet) shows an empty list rather than failing the whole tour.
async function loadWalkthrough(): Promise<WalkthroughFrame[]> {
  try {
    const res = await fetch(`${TOUR_BASE_PATH}/walkthrough/frames.json`, { cache: "force-cache" });
    if (!res.ok) return [];
    const body = (await res.json()) as { frames?: WalkthroughFrame[] };
    return body.frames ?? [];
  } catch {
    return [];
  }
}

let cache: Promise<TourData> | null = null;

export function loadTour(): Promise<TourData> {
  if (!cache) cache = build();
  return cache;
}

async function build(): Promise<TourData> {
  const [
    objectivesRes,
    swarmRes,
    statusRes,
    modelsRes,
    inboxItems,
    candidates,
    version,
    capabilities,
    recipes,
    bundle,
    walkthroughFrames,
  ] = await Promise.all([
    fetchObjectives(),
    fetchSwarm(),
    fetchStatus(),
    fetchModels(),
    fetchInbox(),
    loadCandidates(),
    loadVersion(),
    loadCapabilities(),
    recipeLibrary(),
    demo(),
    loadWalkthrough(),
  ]);

  const objective = objectivesRes.objectives[0] ?? null;
  let plan: Plan | null = null;
  let planError: string | null = null;
  if (objective) {
    try {
      plan = await objectivePlan(objective.id);
    } catch (err) {
      planError = err instanceof ApiError ? "This recording has no compiled plan for this objective." : "The plan could not be loaded.";
    }
  }

  const recipeTitles = new Map(recipes.map((r) => [r.id, r]));

  const nightEntries = statusRes.initialised ? Object.entries(statusRes.nights) : [];
  const incumbentId = nightEntries.length
    ? nightEntries.reduce((a, b) => (Number(a[0]) > Number(b[0]) ? a : b))[1].incumbent
    : null;

  const promoted = modelsRes.length ? modelsRes.reduce((a, b) => (a.night >= b.night ? a : b)) : null;

  const prunedCandidates = candidates.filter((c) => c.pruned);
  const pruned = prunedCandidates.length
    ? prunedCandidates.reduce((a, b) => (a.proposed_seq >= b.proposed_seq ? a : b))
    : null;

  const benchmarkMoves: BenchmarkMove[] = [];
  for (const obj of objectivesRes.objectives) {
    for (const p of obj.progress) {
      if (p.state === "measured" && p.baseline && p.latest) {
        benchmarkMoves.push({
          objectiveId: obj.id,
          benchmark: p.benchmark,
          baseline: p.baseline,
          latest: p.latest,
          delta: p.delta,
          deltaLo: p.delta_lo,
          deltaHi: p.delta_hi,
          significant: p.significant,
          targetDelta: p.target_delta,
          met: p.met,
        });
      }
    }
  }

  const featuredNight = bundle.featured_run ? { night: bundle.featured_run.night, track: bundle.featured_run.track } : null;

  return {
    objective,
    recipeTitles,
    plan,
    planError,
    swarm: swarmRes,
    featuredNight,
    candidates,
    incumbentId,
    promoted,
    pruned,
    inboxItems,
    benchmarkMoves,
    version,
    capabilities,
    walkthroughFrames,
  };
}

// A candidate's `xs` is its own history of paired deltas against whatever the incumbent was at the time each
// observation ran; the mean is the single number a scatter can place it by.
export function meanDelta(xs: number[]): number | null {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

export interface TourStepMeta {
  n: number;
  slug: string;
  title: string;
  lede: string;
}

export const TOUR_STEPS: TourStepMeta[] = [
  { n: 1, slug: "intent", title: "State an intent", lede: "An objective goes in; a compiled plan comes out." },
  { n: 2, slug: "swarm", title: "The swarm takes it", lede: "The plan's steps are dispatched to real agents, one task at a time." },
  { n: 3, slug: "night", title: "A night runs", lede: "The engine proposes, trains and measures candidates unattended." },
  { n: 4, slug: "scored", title: "Candidates are scored", lede: "Every candidate is measured against the current best on held-out problems." },
  { n: 5, slug: "boundary", title: "The boundary decides", lede: "Most candidates are pruned; a rare one is promoted." },
  { n: 6, slug: "signoff", title: "A human signs off", lede: "A promotion waits on a person before it counts as done." },
  { n: 7, slug: "benchmark", title: "The benchmark moves", lede: "An independent tool scores the model before and after." },
  { n: 8, slug: "version", title: "The engine updates itself", lede: "What shipped, and what this build can do." },
  {
    n: 9,
    slug: "walkthrough",
    title: "See it click through",
    lede: "The eight screens above, replayed as a real browser saw them — not a mock-up.",
  },
  {
    n: 10,
    slug: "desktop",
    title: "The desktop app",
    lede: "The native shell that runs the engine on your machine, caught mid-update-check.",
  },
];
