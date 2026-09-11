// Typed fetch client for the engine's catalogue: every tool, recipe and sandbox policy it knows about, and
// what is actually usable on this machine. A new file rather than additions to api.ts, so pages built in
// parallel never contend for that one — the same reasoning lib/swarm.ts already follows.

import { apiBase, IS_DEMO, recipeLibrary, type Recipe } from "./api";

export type { Recipe } from "./api";

export interface CatalogueTool {
  id: string;
  category: string;
  title: string;
  provides: string;
  available: boolean;
  reason: string;
}

export interface SandboxPolicy {
  id: string;
  allowed_paths: string[];
  denied_paths: string[];
  network: "none" | "provider-only" | "open";
  tools: string[];
  max_wall_s: number;
}

interface ToolDetect {
  kind: string;
  value: string;
}

interface ToolApiRow {
  id: string;
  category: string;
  title: string;
  provides: string;
  detect: ToolDetect;
  available: boolean;
}

interface DemoCapabilityTool {
  id: string;
  kind: string;
  available: boolean;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

// A tool's presence is decided by exactly one of two honest detectors (application/tools.py): an executable
// on PATH, or a named environment variable being set. The reason shown here states which one fired.
function toolReason(detect: ToolDetect, available: boolean): string {
  if (detect.kind === "path") {
    return available ? `found on PATH as \`${detect.value}\`` : `\`${detect.value}\` not found on PATH`;
  }
  if (detect.kind === "env") {
    return available ? `environment variable ${detect.value} is set` : `environment variable ${detect.value} is not set`;
  }
  return available ? "available" : "not available";
}

// Live mode reads the full catalogue from the engine's own /api/tools. Demo mode has no engine to ask, so it
// reads the reduced `capabilities.tools` snapshot the recording carries — id, kind and availability only, no
// detector detail — and reports that plainly rather than inventing a reason it cannot know.
export async function tools(): Promise<CatalogueTool[]> {
  if (IS_DEMO) {
    const { demo } = await import("./demo");
    const bundle = (await demo()) as Awaited<ReturnType<typeof demo>> & {
      capabilities?: { tools?: DemoCapabilityTool[] };
    };
    const rows = bundle.capabilities?.tools ?? [];
    return rows.map((t) => ({
      id: t.id,
      category: t.kind,
      title: t.id,
      provides: "",
      available: t.available,
      reason: t.available ? "available in this recording" : "not available in this recording",
    }));
  }
  const { tools: rows } = await getJSON<{ tools: ToolApiRow[] }>("/api/tools");
  return rows.map((t) => ({
    id: t.id,
    category: t.category,
    title: t.title,
    provides: t.provides,
    available: t.available,
    reason: toolReason(t.detect, t.available),
  }));
}

// The recipe library already carries everything the catalogue page needs (title, summary, skill, and whether
// that skill is installed), in both live and demo mode — recipeLibrary() handles that split already.
export async function recipes(): Promise<Recipe[]> {
  return recipeLibrary();
}

// Paths every sandbox policy refuses regardless of what it declares — application/sandbox_policy.py's
// `ALWAYS_DENIED`, merged in so a missing or mistyped entry in the config can never reopen them.
const ALWAYS_DENIED: readonly string[] = ["pravrudhi_kernel/**", "research/**", "gates/**", ".pravrudhi/**"];

// Mirrors assets/configs/sandbox_policies.yaml. These are declared, packaged policies, not host state: no
// engine call resolves them, so this is the same list whether the page is live or a recording. Keep in sync
// with that file and with sandbox_policy.py's ALWAYS_DENIED if either changes.
const SANDBOX_POLICIES: SandboxPolicy[] = [
  {
    id: "proposal",
    allowed_paths: ["proposals/**"],
    denied_paths: [...ALWAYS_DENIED],
    network: "none",
    tools: ["agent-claude-code", "agent-codex", "agent-orca", "runtime-uv"],
    max_wall_s: 1800,
  },
  {
    id: "selfbuild",
    allowed_paths: ["src/pravrudhi/**", "tests/**", "app/frontend/src/**", "scripts/**", "docs/**", "configs/**", "plugin/**"],
    denied_paths: [...ALWAYS_DENIED],
    network: "provider-only",
    tools: ["agent-claude-code", "runtime-uv", "mcp-git"],
    max_wall_s: 3600,
  },
  {
    id: "review",
    allowed_paths: [],
    denied_paths: [...ALWAYS_DENIED],
    network: "none",
    tools: ["mcp-git"],
    max_wall_s: 900,
  },
];

export function sandboxPolicies(): SandboxPolicy[] {
  return SANDBOX_POLICIES;
}
