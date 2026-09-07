// Data and derivations for the Machines page: what each enrolled host can actually do, the engine's own
// survival state, which coding agents are available right now, and this install's update standing.
//
// Every fetch here can fail exactly as the rest of `lib/api.ts` can — there may be no engine running, or an
// endpoint may not exist yet on an older build — so every export either resolves or rejects; nothing here hides
// a failure behind a fabricated value.

import { apiBase, ApiError, IS_DEMO, agents as fetchAgents, hosts as fetchHosts } from "@/lib/api";
import type { AgentStatus, HostCapabilities, HostRow } from "@/lib/api";
import { fixed } from "@/lib/num";

export type { AgentStatus, HostCapabilities, HostRow };
export { fetchAgents as agents, fetchHosts as hosts };

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Svāsthya: the engine's own survival state (`application.svasthya.assess`). The single most useful thing this
// page can say when something is wrong, so a failing check is shown by name with its own detail, never folded
// into a generic "unhealthy".

export type SvasthyaState = "ready" | "degraded" | "recovering" | "integrity_halt" | "paused";

export interface SvasthyaCheck {
  name: string;
  ok: boolean;
  detail: string;
  integrity: boolean;
}

export interface SvasthyaHealth {
  state: SvasthyaState;
  as_of: string;
  checks: SvasthyaCheck[];
  reason: string;
}

export async function svasthya(): Promise<SvasthyaHealth> {
  if (IS_DEMO) throw new ApiError(501, "/api/svasthya");
  return getJSON<SvasthyaHealth>("/api/svasthya");
}

// ---------------------------------------------------------------------------
// Which coding agents are routable right now, and which are sitting out a vendor usage limit.

export interface AgentCooldown {
  agent: string;
  until: string;
}

export async function agentCooldowns(): Promise<AgentCooldown[]> {
  if (IS_DEMO) return [];
  return getJSON<AgentCooldown[]>("/api/agents/cooldowns");
}

// ---------------------------------------------------------------------------
// This install's own update standing: only meaningful for the machine the engine is actually running on, since
// a fleet host enrolled for placement never runs its own copy of the engine.

export interface InstallStatus {
  version: string;
  kernel_version: string;
  git_describe: string | null;
  channel: "dev" | "release";
  update_available: boolean;
  latest_tag: string | null;
  last_checked: string | null;
}

interface RawUpdateStatus {
  current: { version: string; kernel_version: string; git_describe?: string | null };
  latest: { tag: string } | null;
  update_available: boolean;
}

interface RawUpdateConfig {
  channel: "dev" | "release";
}

interface RawUpdateLastCheck {
  last_checked: string | null;
}

export async function installStatus(): Promise<InstallStatus> {
  if (IS_DEMO) throw new ApiError(501, "/api/update");
  const [upd, cfg, checked] = await Promise.all([
    getJSON<RawUpdateStatus>("/api/update"),
    getJSON<RawUpdateConfig>("/api/update/config"),
    getJSON<RawUpdateLastCheck>("/api/update/last-check"),
  ]);
  return {
    version: upd.current.version,
    kernel_version: upd.current.kernel_version,
    git_describe: upd.current.git_describe ?? null,
    channel: cfg.channel,
    update_available: upd.update_available,
    latest_tag: upd.latest?.tag ?? null,
    last_checked: checked.last_checked ?? null,
  };
}

// ---------------------------------------------------------------------------
// What a host can do, and — for anything it cannot — the specific measured reason, so the page never has to
// fall back to a bare "no".

export interface CapabilityFact {
  label: string;
  can: boolean;
  reason: string;
}

export function capabilityFacts(cap: HostCapabilities): CapabilityFact[] {
  const accel = cap.accelerator || "none";
  const vram = fixed(cap.gpu_vram_gb);
  const ram = fixed(cap.ram_gb);
  return [
    {
      label: "Train models",
      can: cap.can_train,
      reason: cap.can_train
        ? `CUDA GPU (${vram}GB VRAM) with Docker available`
        : accel !== "cuda"
          ? `needs a CUDA GPU; this host reports "${accel}"`
          : cap.gpu_vram_gb < 8
            ? `GPU has ${vram}GB VRAM, training needs at least 8GB`
            : "needs Docker; the kernel only admits container-isolated training runs",
    },
    {
      label: "Serve open models",
      can: cap.can_serve_open_models,
      reason: cap.can_serve_open_models
        ? accel === "cuda"
          ? `CUDA GPU with ${vram}GB VRAM`
          : `Apple Metal with ${ram}GB RAM`
        : accel === "cuda"
          ? `GPU has ${vram}GB VRAM, serving needs at least 4GB`
          : accel === "metal"
            ? `${ram}GB RAM, serving needs at least 16GB`
            : `needs a GPU accelerator; this host reports "${accel}"`,
    },
    {
      label: "Run containers",
      can: cap.docker,
      reason: cap.docker ? "docker is installed and responding" : "docker is not installed, or not responding",
    },
  ];
}
