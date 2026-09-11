// Data + join logic for the Models page: a promoted model is a candidate that survived the loop's gate, so
// everything the ledger already knows about that candidate -- its cost, its edit family, the night and policy
// that produced it, and the external scorer's paired items -- lives in three separate endpoints (/api/models,
// /api/candidates, /api/external) that this file joins by candidate id and by track+night. Kept out of
// lib/api.ts so this join logic does not contend with pages already built against it, the same reason
// lib/candidates.ts and lib/objective.ts keep their own join logic instead of extending it.

import {
  external,
  models as fetchModels,
  runs as fetchRuns,
  type ExternalRow,
  type PromotedModel,
  type RunHandle,
} from "./api";
import { candidatesList, type Candidate } from "./candidates";
import { knownEdition, STUDIO } from "./edition";
import { percent } from "./num";

// A percentage already screened by lib/num.ts's `percent`, with an explicit sign prepended -- every delta this
// page shows is a change against a baseline, so the sign is the point (mirrors lib/objective.ts's signedPercent).
export function signedPercent(v: number | null | undefined, digits = 1): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const s = percent(v, digits);
  return v > 0 ? `+${s}` : s;
}

// Wilson score interval for a rate -- ported from pravrudhi_kernel.stats.wilson_ci so the paired confidence
// interval this page shows is the same interval the objectives page and the kernel itself compute, never an
// approximation invented for this screen.
function wilsonCi(k: number, n: number, z = 1.96): [number, number] {
  if (n <= 0) return [0, 0];
  const p = k / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z / denom) * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  let lo = Math.max(0, centre - half);
  let hi = Math.min(1, centre + half);
  if (k === 0) lo = 0;
  if (k === n) hi = 1;
  return [lo, hi];
}

// lm-eval names a metric `<name>,<filter>` and its standard error `<name>_stderr,<filter>` -- mirrors
// application/external.py's `stderr_key` so a metric on a task with no comma is still matched correctly.
function stderrKey(key: string): string {
  const i = key.indexOf(",");
  return i === -1 ? `${key}_stderr` : `${key.slice(0, i)}_stderr${key.slice(i)}`;
}

interface Headline {
  name: string;
  value: number;
  stderr: number;
  n: number;
  itemsTask: string | null;
}

// Every metric a metrics record carries, in the shape application/external.py's `headlines()` renders them --
// one row per lm-eval task, or one combined pass@1 row for an EvalPlus dataset (recognised by its sibling
// `<dataset>_counts` key, the same convention `parse_evalplus` writes). Works on a full ExternalRow's `metrics`
// or on the trimmed `external_before`/`external_after` map `/api/models` sends, so a model still shows every
// benchmark it has even when the richer per-item row cannot be found.
function inferHeadlines(
  metrics: Record<string, Record<string, number>>,
  nSamples: Record<string, number | null> | null,
): Headline[] {
  const out: Headline[] = [];
  for (const task of Object.keys(metrics)) {
    if (task.endsWith("_counts")) continue;
    const m = metrics[task];
    if (!m || Object.keys(m).length === 0) continue;
    const counts = metrics[`${task}_counts`];
    if (counts) {
      const n = counts.n ?? 0;
      const p = m["pass@1_plus"];
      if (typeof p === "number" && Number.isFinite(p) && n > 0) {
        const [lo, hi] = wilsonCi(Math.round(p * n), n);
        out.push({ name: `${task}+ pass@1`, value: p, stderr: (hi - lo) / 2, n, itemsTask: task });
      }
      continue;
    }
    const key =
      "exact_match,strict-match" in m ? "exact_match,strict-match" : Object.keys(m).find((k) => !k.includes("stderr"));
    if (!key || typeof m[key] !== "number") continue;
    out.push({ name: `${task} ${key}`, value: m[key], stderr: m[stderrKey(key)] ?? 0, n: nSamples?.[task] ?? 0, itemsTask: task });
  }
  return out;
}

function itemsOf(row: ExternalRow | null): Record<string, number> | null {
  const raw = row ? (row as Record<string, unknown>).items : undefined;
  return raw && typeof raw === "object" ? (raw as Record<string, number>) : null;
}

// A Wilson-style interval for the paired delta -- mirrors application/objectives.py's `_paired_stats` exactly:
// wins and losses over the discordant pairs alone, modelled as a Bernoulli trial, transformed back to
// delta = (wins+losses)/n * (2p-1). The exact McNemar p-value that module also computes needs a log-space
// binomial bisection this page has no use for; `significant` below is decided the same way objectives.py
// decides it -- by whether the interval excludes zero, never by a p-value this file does not compute.
function pairedStats(baseItems: Record<string, number>, afterItems: Record<string, number>) {
  const shared = Object.keys(baseItems).filter((k) => k in afterItems);
  const n = shared.length;
  let wins = 0;
  let losses = 0;
  for (const k of shared) {
    if (afterItems[k] === 1 && baseItems[k] === 0) wins++;
    else if (afterItems[k] === 0 && baseItems[k] === 1) losses++;
  }
  const total = wins + losses;
  if (total === 0 || n === 0) return { delta: 0, lo: 0, hi: 0, wins, losses, n };
  const [loP, hiP] = wilsonCi(wins, total);
  const scale = total / n;
  return { delta: (wins - losses) / n, lo: scale * (2 * loP - 1), hi: scale * (2 * hiP - 1), wins, losses, n };
}

export interface ModelBenchmark {
  name: string;
  before: number | null;
  after: number | null;
  n: number | null;
  delta: number | null;
  deltaLo: number | null;
  deltaHi: number | null;
  significant: boolean;
  paired: boolean;
  pairedN: number | null;
  wins: number | null;
  losses: number | null;
}

function buildBenchmarks(
  beforeH: Headline[],
  afterH: Headline[],
  beforeItems: Record<string, number> | null,
  afterItems: Record<string, number> | null,
): ModelBenchmark[] {
  const names = new Set<string>([...beforeH.map((h) => h.name), ...afterH.map((h) => h.name)]);
  const out: ModelBenchmark[] = [];
  for (const name of names) {
    const b = beforeH.find((h) => h.name === name) ?? null;
    const a = afterH.find((h) => h.name === name) ?? null;
    let delta: number | null = null;
    let lo: number | null = null;
    let hi: number | null = null;
    let paired = false;
    let wins: number | null = null;
    let losses: number | null = null;
    let pairedN: number | null = null;
    if (b && a) {
      const canPair = beforeItems && afterItems && b.itemsTask && a.itemsTask && b.itemsTask === a.itemsTask;
      if (canPair) {
        const stats = pairedStats(beforeItems, afterItems);
        if (stats.n > 0 && stats.wins + stats.losses > 0) {
          paired = true;
          delta = stats.delta;
          lo = stats.lo;
          hi = stats.hi;
          wins = stats.wins;
          losses = stats.losses;
          pairedN = stats.n;
        }
      }
      if (!paired) {
        delta = a.value - b.value;
        const half = 1.96 * Math.sqrt(a.stderr ** 2 + b.stderr ** 2);
        lo = delta - half;
        hi = delta + half;
      }
    }
    out.push({
      name,
      before: b?.value ?? null,
      after: a?.value ?? null,
      n: a?.n ?? b?.n ?? null,
      delta,
      deltaLo: lo,
      deltaHi: hi,
      significant: lo !== null && hi !== null && (lo > 0 || hi < 0),
      paired,
      pairedN,
      wins,
      losses,
    });
  }
  return out.sort((x, y) => x.name.localeCompare(y.name));
}

export interface RecipeField {
  key: string;
  value: string;
}

export function describeRecipe(recipe: Record<string, unknown>): RecipeField[] {
  return Object.entries(recipe)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([key, v]) => {
      if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return { key, value: String(v) };
      const s = JSON.stringify(v);
      return { key, value: s.length > 80 ? `${s.slice(0, 77)}...` : s };
    });
}

export interface ModelCard {
  id: string;
  track: "model" | "harness";
  night: number;
  policy: string | null;
  artefact: string | null;
  baseModelName: string | null;
  editFamily: string | null;
  recipe: RecipeField[];
  costGpuH: number | null;
  benchmarks: ModelBenchmark[];
  // Sorted item ids for this model's "after" measurement of each benchmark, or null when no per-item vector
  // was found -- the comparison view uses this to tell whether two models were actually scored on the same set.
  itemsByBenchmark: Record<string, string[] | null>;
}

function runPolicyFor(run: RunHandle | undefined): string | null {
  if (!run) return null;
  const request = (run as Record<string, unknown>).request;
  const policy = request && typeof request === "object" ? (request as Record<string, unknown>).policy : undefined;
  return typeof policy === "string" && policy ? policy : null;
}

export async function modelCards(): Promise<ModelCard[]> {
  // /api/candidates and /api/external are Studio's (api/roles.py ADMIN_ONLY): a product engine answers 404 to
  // both, and the join below already degrades to the metrics /api/models carries, so a product install is not
  // asked -- two refused requests on every visit to /models was the only effect (edition e2e, 2026-09-11).
  const studio = (await knownEdition())?.edition === STUDIO;
  const [promoted, candidates, externalRows, runRows] = await Promise.all([
    fetchModels(),
    studio ? candidatesList().catch(() => [] as Candidate[]) : Promise.resolve([] as Candidate[]),
    studio ? external().catch(() => [] as ExternalRow[]) : Promise.resolve([] as ExternalRow[]),
    fetchRuns().catch(() => [] as RunHandle[]),
  ]);

  const candidateById = new Map(candidates.map((c) => [c.id, c] as const));

  // Reproduces the join application/api/runs.py's `models_listing` performs server-side: a `base` row is keyed
  // by its literal track code ("M" model / "H" harness, per `ext-record --track`), an "after" row by the
  // candidate id named after the colon in its condition ("adapter:c-0045" / "harness:c-0012"). The latest by
  // ledger sequence wins for each key, same as the Python version's forward-overwrite through the same rows.
  const baseByTrack = new Map<string, ExternalRow>();
  const afterByCid = new Map<string, ExternalRow>();
  for (const r of externalRows) {
    if (r.condition === "base") {
      const prev = baseByTrack.get(r.track);
      if (!prev || r.seq > prev.seq) baseByTrack.set(r.track, r);
    } else if (r.condition.includes(":")) {
      const cid = r.condition.slice(r.condition.indexOf(":") + 1);
      const prev = afterByCid.get(cid);
      if (!prev || r.seq > prev.seq) afterByCid.set(cid, r);
    }
  }

  return promoted.map((m: PromotedModel): ModelCard => {
    const letter = m.track === "harness" ? "H" : "M";
    const beforeRow = baseByTrack.get(letter) ?? null;
    const afterRow = afterByCid.get(m.id) ?? null;
    // Falls back to the trimmed metrics `/api/models` itself carries when the richer per-item row cannot be
    // found (an older engine build without /api/external, or a row this join missed) -- the page still shows
    // the before/after value pair it always could, just without a paired significance test.
    const beforeMetrics = beforeRow?.metrics ?? m.external_before ?? null;
    const afterMetrics = afterRow?.metrics ?? m.external_after ?? null;
    const beforeH = beforeMetrics ? inferHeadlines(beforeMetrics, beforeRow?.n_samples ?? null) : [];
    const afterH = afterMetrics ? inferHeadlines(afterMetrics, afterRow?.n_samples ?? null) : [];
    const beforeItems = itemsOf(beforeRow);
    const afterItems = itemsOf(afterRow);
    const benchmarks = buildBenchmarks(beforeH, afterH, beforeItems, afterItems);

    const itemsByBenchmark: Record<string, string[] | null> = {};
    for (const h of afterH) {
      itemsByBenchmark[h.name] = afterItems && h.itemsTask ? Object.keys(afterItems).sort() : null;
    }

    const candidate = candidateById.get(m.id) ?? null;
    // A night's own record does not name the objective it served, so the run this model's night belongs to is
    // found by (night, track) alone -- the same join lib/objective.ts uses for a run's night. A live run's
    // `target` field reads "model"/"harness" directly; a closed night's reads "lora"/"harness" (the internal
    // track code `next_night` uses), so "model" is matched against either spelling.
    const run = runRows.find((r) => {
      const rec = r as Record<string, unknown>;
      if (rec.night !== m.night) return false;
      const target = rec.target;
      return target === m.track || (m.track === "model" && target === "lora");
    });

    return {
      id: m.id,
      track: m.track,
      night: m.night,
      policy: runPolicyFor(run),
      artefact: m.artefact,
      baseModelName: beforeRow?.model || null,
      editFamily: candidate?.edit_family ?? null,
      recipe: describeRecipe(m.recipe ?? {}),
      costGpuH: candidate?.cost_gpu_h ?? null,
      benchmarks,
      itemsByBenchmark,
    };
  });
}
