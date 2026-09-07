// The recorded demo the public site runs on.
//
// A visitor has no engine, and a browser will not let a public page reach one on their machine: Chrome blocks the
// loopback address space from public origins outright. So the site does not pretend to be connected. It plays a
// real run that this project actually performed, from a snapshot exported by the engine, and tells the visitor
// plainly that it is recorded.

import type {
  AgentStatus,
  LoomResponse,
  ObjectivesResponse,
  Plan,
  Recipe,
  Candidate,
  ExternalRow,
  HostsResponse,
  NightSummary,
  PromotedModel,
  RunEvent,
  RunHandle,
  StatusResponse,
  SubagentsResponse,
} from "./api";

export interface DemoRun extends RunHandle {
  policy: string | null;
  spent_gpu_h: number;
  candidates: number;
  pruned: number;
}

export interface DemoBundle {
  recorded: true;
  engine: { version: string; candidates: number };
  status: StatusResponse;
  models: PromotedModel[];
  external: ExternalRow[];
  nights: NightSummary[];
  runs: DemoRun[];
  featured_run: { id: string; night: number; track: string; events: RunEvent[] };
  // Added after the first recordings, so an older snapshot may not carry them.
  objectives?: ObjectivesResponse;
  recipes?: Recipe[];
  plans?: Record<string, Plan>;
  loom?: Record<string, LoomResponse>;
  subagents?: Record<string, SubagentsResponse>;
}

export { IS_DEMO as DEMO } from "./api";

let cache: Promise<DemoBundle> | null = null;

// The snapshot is served beside the app, not at the origin root. GitHub Pages puts this site under
// /pravrudhi/app/, so a root-absolute "/demo.json" asked the wrong origin path and 404ed: every page that reads
// recorded data failed there while the same build worked on a root-served host. The base path is compiled in.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

// "force-cache" was wrong here and hid every publish from anyone who had already visited. The snapshot is
// rewritten on each publish while its URL stays the same, so a cached copy is not merely stale, it is the whole
// point of the site going out of date: a returning visitor kept the bundle they first loaded and saw none of the
// pages added since. Caught live on the deployed site, where the browser served an eleven-section bundle while
// the network had twenty-three, so a page reading a newer section rendered its empty state.
//
// "no-cache" still uses the cached bytes when they are current — it revalidates rather than re-downloads, so an
// unchanged snapshot costs a 304 — but it can never serve a copy the server has replaced.
export function demo(): Promise<DemoBundle> {
  if (!cache) {
    cache = fetch(`${basePath}/demo.json`, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error("demo snapshot missing");
      return r.json() as Promise<DemoBundle>;
    });
  }
  return cache;
}

export async function demoCandidates(): Promise<Candidate[]> {
  return [];
}

export async function demoHosts(): Promise<HostsResponse> {
  return {
    hosts: [
      {
        host: { name: "the author's workstation", transport: "local", address: "", user: "", workdir: "", orca_host_id: "" },
        capabilities: {
          os: "Linux", arch: "x86_64", cpu_count: 32, ram_gb: 128, gpu_name: "NVIDIA GeForce RTX 5090",
          gpu_vram_gb: 31.8, accel_mem_gb: 31.8, accelerator: "cuda", docker: true, python: "3.13", agents: [], local_models: [],
          reachable: true, error: "", can_train: true, can_serve_open_models: true, usable_model_gb: 31.8,
        },
      },
      {
        host: { name: "a Mac mini", transport: "ssh", address: "", user: "", workdir: "", orca_host_id: "" },
        capabilities: {
          os: "Darwin", arch: "arm64", cpu_count: 10, ram_gb: 24, gpu_name: "Apple M4", gpu_vram_gb: 0,
          accelerator: "metal", accel_mem_gb: 17.8, docker: false, python: "3.9", agents: [], local_models: [], reachable: true,
          error: "", can_train: false, can_serve_open_models: true, usable_model_gb: 17.8,
        },
      },
    ],
  };
}

export async function demoAgents(): Promise<AgentStatus[]> {
  return [
    { name: "claude-code", available: true, reason: "ready" },
    { name: "codex", available: true, reason: "ready" },
    { name: "local (GLM-4.7-Flash)", available: true, reason: "ready" },
  ];
}
