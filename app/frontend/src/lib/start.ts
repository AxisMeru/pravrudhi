// The guided flow's own data layer: turning a plain-English intent into a compiled plan and a created objective.
//
// An objective could already be created through /objectives, but only by someone who already knew a benchmark's
// on-disk syntax and the shape of a plan compiler nobody ever saw run. This flow exists to close that gap, so it
// draws on the same engine calls the Objectives page already trusts (lib/api.ts) and adds only what that page had
// no reason to carry: a catalogue of benchmarks the engine has actually measured, and a preview of the compiled
// plan for a draft that has not been created yet. Both new calls are written locally here rather than in
// lib/api.ts, because that file belongs to the Objectives page and is not this flow's to change.

import {
  apiBase,
  ApiError,
  IS_DEMO,
  localToken,
  objectives as fetchObjectives,
  recipeLibrary,
  postObjective,
  dispatchSubagents,
  type BenchmarkSpec,
  type Objective,
  type ObjectiveInput,
  type Plan,
  type Recipe,
} from "@/lib/api";

export { IS_DEMO, dispatchSubagents, postObjective, fetchObjectives, recipeLibrary };
export type { Objective, ObjectiveInput, Plan, Recipe, BenchmarkSpec };

// Mirrors objectives.py's ID_RE (lowercase letters, digits and hyphens, 2-63 characters), checked here only so a
// bad name is explained before it is submitted. The engine's own parser stays the one authority that enforces it.
export const ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,62}$/;

export interface WorkedExample {
  id: string;
  intent: string;
  domain: string;
  track: string;
  benchmarks: BenchmarkSpec[];
}

// Three objectives shipped with the engine itself (src/pravrudhi/assets/objectives/*.yaml), transcribed verbatim,
// so a person deciding how to phrase an intent sees intents this engine has actually been pointed at rather than
// a placeholder invented for this screen.
export const WORKED_EXAMPLES: WorkedExample[] = [
  {
    id: "math-reasoning",
    intent:
      "A small model that solves grade-school arithmetic word problems more reliably than the checkpoint it " +
      "started from, without losing what it already knew.",
    domain: "reasoning",
    track: "M",
    benchmarks: [{ id: "gsm8k", tool: "lm-eval", metric: "gsm8k exact_match,strict-match", direction: "up" }],
  },
  {
    id: "code-harness",
    intent:
      "A scaffolding harness that makes a fixed model write correct Python more often, changed only in how the " +
      "model is prompted and how its output is checked, never in the model's weights.",
    domain: "code",
    track: "H",
    benchmarks: [
      { id: "humaneval-plus", tool: "evalplus", metric: "humaneval+ pass@1", direction: "up" },
      { id: "mbpp-plus", tool: "evalplus", metric: "mbpp+ pass@1", direction: "up" },
    ],
  },
  {
    id: "prabhasa-nyaya",
    intent:
      "A legal-reasoning assistant for Indian jurisprudence that answers a question of law with the statute or " +
      "precedent it relied on, and that says it does not know rather than inventing a citation.",
    domain: "legal",
    track: "nyaya",
    benchmarks: [
      { id: "professional-law", tool: "lm-eval", metric: "mmlu_professional_law acc,none", direction: "up" },
      { id: "jurisprudence", tool: "lm-eval", metric: "mmlu_jurisprudence acc,none", direction: "up" },
    ],
  },
];

// What the engine's own /api/benchmarks route answers with -- see api/schemas.py's BenchmarkCatalogEntry.
export interface BenchmarkCatalogEntry {
  id: string;
  tool: string;
  metric: string;
  track: string;
  value: number | null;
  n: number | null;
}

interface BenchmarksPayload {
  benchmarks: BenchmarkCatalogEntry[];
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const token = await localToken();
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { "x-pravrudhi-token": token } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// Every benchmark the external tier has ever scored, so picking what success means is picking from this list
// rather than typing an external scorer's own task syntax from memory. A recording has never scored anything
// itself, so its list is drawn from the recorded objectives' own progress instead of a network call that would
// only fail on a public page with no engine behind it.
export async function fetchBenchmarks(): Promise<BenchmarkCatalogEntry[]> {
  if (IS_DEMO) {
    const d = await (await import("@/lib/demo")).demo();
    const seen = new Map<string, BenchmarkCatalogEntry>();
    for (const o of d.objectives?.objectives ?? []) {
      for (const p of o.progress) {
        const spec = o.benchmarks.find((b) => b.metric === p.benchmark);
        const value = p.latest?.value ?? p.baseline?.value ?? null;
        const n = p.latest?.n ?? p.baseline?.n ?? null;
        seen.set(p.benchmark, {
          id: spec?.id || p.benchmark.split(" ")[0] || p.benchmark,
          tool: spec?.tool ?? "lm-eval",
          metric: p.benchmark,
          track: o.track,
          value,
          n,
        });
      }
    }
    return [...seen.values()];
  }
  const res = await fetch(`${apiBase()}/api/benchmarks`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, "/api/benchmarks");
  return ((await res.json()) as BenchmarksPayload).benchmarks;
}

export interface ObjectiveDraft {
  id: string;
  intent: string;
  domain: string;
  track: string;
  benchmarks: BenchmarkSpec[];
  targetDelta: number | null;
}

// The compiled plan for a draft that has not been recorded yet -- the same compiler `/objectives/{id}/plan` calls
// after creation (application/intent.py's compile_intent), reached before creation so a person sees what would
// happen before anything does. A recording cannot compile anything live, so it shows one recorded plan instead,
// labelled here as an example rather than as this particular draft's own plan.
export async function previewPlan(draft: ObjectiveDraft): Promise<Plan> {
  if (IS_DEMO) {
    const d = await (await import("@/lib/demo")).demo();
    const example = Object.values(d.plans ?? {})[0];
    if (!example) throw new ApiError(501, "/api/objectives/plan-preview");
    return example;
  }
  return postJSON<Plan>("/api/objectives/plan-preview", {
    id: draft.id || "draft",
    intent: draft.intent,
    track: draft.track,
    domain: draft.domain,
    benchmarks: draft.benchmarks,
    recipes: [],
    target_delta: draft.targetDelta,
    notes: "",
  });
}
