// Data and derivations for the System page: how the engine updates itself, which machines it keeps current,
// whether it is healthy right now, and what the swarm has been building. Four sections, four kinds of failure
// mode — a stale demo snapshot recorded before a shape existed, an engine build too old for an endpoint, a
// reachable engine with nothing to report yet — so every export distinguishes "no data was ever emitted" from
// "the fetch itself failed," the same discipline `lib/machines.ts` and `lib/swarm.ts` already follow.

import { ApiError, IS_DEMO, apiBase, engineFetch } from "@/lib/api";
import { swarm as fetchSwarm } from "@/lib/swarm";
import type { SwarmSnapshot } from "@/lib/swarm";

export type { SwarmSnapshot };
export { fetchSwarm as swarm };

async function getJSON<T>(path: string): Promise<T> {
  const res = await engineFetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export type Channel = "dev" | "release";

// ---------------------------------------------------------------------------
// How the engine updates itself: this install's own channel and cadence.

export interface UpdateCurrent {
  version: string;
  kernel_version: string;
  git_describe: string | null;
}

export interface UpdateConfig {
  channel: Channel;
  auto_apply: boolean;
  check_interval_min: number;
  keep_previous: number;
  current: UpdateCurrent;
}

// ---------------------------------------------------------------------------
// Every install the engine keeps current, dev checkout or release copy alike.

export interface FleetHost {
  root: string;
  channel: Channel;
  auto_apply: boolean;
  current_version: string;
  available_versions: string[];
  last_check: string | null;
  last_result: string | null;
  healthy: boolean;
}

// ---------------------------------------------------------------------------
// Svāsthya as the System page needs it: named checks, nothing folded into a bare "unhealthy".

export interface HealthCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface HealthState {
  state: string;
  checks: HealthCheck[];
}

// ---------------------------------------------------------------------------
// What the builders have on hand: tools, agents, policies, recipes, pages — a wider inventory than `swarm.agents`
// alone, since it also names connectors and MCP servers no route dispatches to directly.

export interface CapabilityAgent {
  name: string;
  available: boolean;
}

export interface CapabilityTool {
  id: string;
  kind: string;
  available: boolean;
}

export interface Capabilities {
  tools: CapabilityTool[];
  agents: CapabilityAgent[];
  policies: string[];
  recipes: number;
  pages: string[];
}

// ---------------------------------------------------------------------------
// The build this snapshot, or this live engine, actually is.

export interface VersionInfo {
  engine: string;
  kernel: string;
  commit: string;
  exported_at: string;
}

// The recorded demo predates `update`, `fleet` and `health` — those keys are simply absent from today's
// snapshot — so the bundle is typed as fully optional here rather than widening `DemoBundle` itself, which
// `lib/demo.ts` is not in scope to edit.
type DemoExtra = {
  update?: UpdateConfig;
  fleet?: FleetHost[];
  health?: HealthState;
  capabilities?: Capabilities;
  version?: VersionInfo;
};

async function demoBundle(): Promise<DemoExtra> {
  const { demo } = await import("./demo");
  return (await demo()) as Awaited<ReturnType<typeof demo>> & DemoExtra;
}

export async function updateConfig(): Promise<UpdateConfig> {
  if (IS_DEMO) {
    const bundle = await demoBundle();
    if (!bundle.update) throw new ApiError(501, "/api/update");
    return bundle.update;
  }
  // Two routes, two different things: /api/update reports the version and whether a newer one exists, while
  // /api/update/config holds the channel and cadence. Reading only the first left both channel cards claiming
  // this install runs neither, because the field they test for was never in that response.
  const [cfg, version] = await Promise.all([
    getJSON<Omit<UpdateConfig, "current">>("/api/update/config"),
    getJSON<{ current: UpdateConfig["current"] }>("/api/update"),
  ]);
  return { ...cfg, current: version.current };
}

// An empty fleet is a real, renderable answer — nothing enrolled yet — so this resolves to [] rather than
// rejecting when the section is simply unpopulated, in demo or live.
export async function fleet(): Promise<FleetHost[]> {
  if (IS_DEMO) {
    const bundle = await demoBundle();
    return bundle.fleet ?? [];
  }
  try {
    // The route answers {installs: [...]}, which is its typed contract; the recorded snapshot stores the bare
    // array. Reading only one of those shapes threw "e.map is not a function" and took the page down.
    const body = await getJSON<FleetHost[] | { installs?: FleetHost[] }>("/api/fleet");
    return Array.isArray(body) ? body : (body.installs ?? []);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return [];
    throw err;
  }
}

// `null` means the endpoint is not present on this build — the page must omit the section honestly rather than
// show a fabricated "unhealthy" — not that the engine has no health to report.
export async function healthState(): Promise<HealthState | null> {
  if (IS_DEMO) {
    const bundle = await demoBundle();
    return bundle.health ?? null;
  }
  try {
    return await getJSON<HealthState>("/api/health-state");
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function capabilities(): Promise<Capabilities> {
  if (IS_DEMO) {
    const bundle = await demoBundle();
    if (!bundle.capabilities) throw new ApiError(501, "/api/capabilities");
    return bundle.capabilities;
  }
  return getJSON<Capabilities>("/api/capabilities");
}

export async function versionInfo(): Promise<VersionInfo> {
  if (IS_DEMO) {
    const bundle = await demoBundle();
    if (!bundle.version) throw new ApiError(501, "/api/version");
    return bundle.version;
  }
  return getJSON<VersionInfo>("/api/version");
}
