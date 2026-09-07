// Typed fetch client for the Pravrudhi engine's JSON API.
//
// Every function here can fail — there may be no engine running, or an endpoint may not exist yet on an
// older engine build. Callers are expected to handle rejection; nothing here retries or hides a failure.

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

function detectBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE?.replace(/\/+$/, "");
  if (configured) return configured;
  if (typeof window !== "undefined" && LOOPBACK.has(window.location.hostname)) return "";
  return "http://localhost:8008";
}

// Resolve at request time so static prerendering cannot freeze the browser's base.
export { detectBase as apiBase };

// Whether this page is a recording rather than a live engine.
//
// Decided at runtime, from where the page is being served, because that is what actually determines it: a browser
// blocks a page on a public origin from reaching an engine on the visitor's machine, so a public page trying
// anyway produces nothing but console errors. A page served by the engine itself is on localhost and is live.
// NEXT_PUBLIC_DEMO forces the recording on for local preview of the public site.
function detectDemo(): boolean {
  if (process.env.NEXT_PUBLIC_DEMO === "1") return true;
  if (typeof window === "undefined") return false;
  if (process.env.NEXT_PUBLIC_API_BASE) return false;
  return !LOOPBACK.has(window.location.hostname);
}

export const IS_DEMO = detectDemo();

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly path: string,
  ) {
    super(`${path}: HTTP ${status}`);
    this.name = "ApiError";
  }
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${detectBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

// The engine refuses state-changing requests without its local token, so that no page a user happens to be
// visiting can start work on their GPU. The token is readable only by a same-origin caller, which this app is
// when the engine serves it. It is fetched once and kept in memory; it is never stored anywhere a script on
// another page could reach.
let cachedToken: string | null = null;

export async function localToken(): Promise<string | null> {
  if (cachedToken !== null) return cachedToken;
  try {
    const res = await fetch(`${detectBase()}/api/app-token`, { cache: "no-store" });
    if (!res.ok) return null;
    cachedToken = ((await res.json()) as { token: string }).token;
    return cachedToken;
  } catch {
    return null;
  }
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const token = await localToken();
  const res = await fetch(`${detectBase()}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const token = await localToken();
  const res = await fetch(`${detectBase()}${path}`, {
    method: "PUT",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

async function deleteJSON<T>(path: string): Promise<T> {
  const token = await localToken();
  const res = await fetch(`${detectBase()}${path}`, {
    method: "DELETE",
    headers: token ? { "x-pravrudhi-token": token } : {},
  });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

export interface HealthResponse {
  ok: boolean;
  version: string;
  kernel: string;
  ledger: boolean;
}

export interface BadgeCounts {
  grey: number;
  amber: number;
  green: number;
  red: number;
}

export interface NightSummary {
  spent_gpu_h: number | null;
  outcomes: unknown;
  incumbent: string | null;
}

export type StatusResponse =
  | { initialised: false }
  | {
      initialised: true;
      chain_ok: boolean;
      events: number;
      ledger_head: string | null;
      state_hash: string;
      candidates: number;
      badges: BadgeCounts;
      promoted: Record<string, string[]>;
      pruned: number;
      nights: Record<string, NightSummary>;
      inbox_pending: string[];
      locks: unknown;
    };

export interface Candidate {
  id: string;
  badge: "grey" | "amber" | "green" | "red";
  surface: string | null;
  bucket: Record<string, string> | null;
  proposed_seq: number;
  edit_family: string | null;
  xs: number[];
  n_obs: number;
  cost_gpu_h: number;
  last_boundary: string | null;
  promoted: boolean;
  pruned: string | null;
  audit_high: boolean;
  skipped: boolean;
  rebased: number;
  incumbent_hash: string | null;
}

export interface HostInfo {
  name: string;
  transport: string;
  address: string;
  user: string;
  workdir: string;
  orca_host_id: string;
}

export interface HostCapabilities {
  os: string;
  arch: string;
  cpu_count: number;
  ram_gb: number;
  gpu_name: string;
  gpu_vram_gb: number;
  accelerator: "cuda" | "metal" | "none" | string;
  accel_mem_gb: number;
  docker: boolean;
  python: string;
  agents: string[];
  local_models: string[];
  reachable: boolean;
  error: string;
  can_train: boolean;
  can_serve_open_models: boolean;
  usable_model_gb: number;
}

export interface HostRow {
  host: HostInfo;
  capabilities: HostCapabilities;
}

export interface HostsResponse {
  hosts: HostRow[];
}

export interface AgentStatus {
  name: string;
  available: boolean;
  reason: string;
}

export interface ExternalRow {
  seq: number;
  night: number;
  kind: string;
  severity: string;
  tier: string;
  track: string;
  condition: string;
  model: string;
  tool: string;
  tool_version?: string | null;
  dataset?: string;
  metrics: Record<string, Record<string, number>>;
  n_samples?: Record<string, number | null>;
  sha256: string;
  [extra: string]: unknown;
}

export interface RunRequest {
  target: "model" | "harness";
  model: string;
  bench: string;
  budget_gpu_h: number;
  proposer: string;
  policy: string;
}

export interface RunHandle {
  id: string;
  [extra: string]: unknown;
}

export async function health(): Promise<HealthResponse> {
  if (IS_DEMO) {
    const d = await (await import("./demo")).demo();
    return { ok: true, version: d.engine.version, kernel: d.engine.version, ledger: true };
  }
  return getJSON<HealthResponse>("/api/health");
}

export async function status(): Promise<StatusResponse> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).status;
  return getJSON<StatusResponse>("/api/status");
}

export async function candidates(): Promise<Candidate[]> {
  if (IS_DEMO) return [];
  return getJSON<Candidate[]>("/api/candidates");
}

export async function nights(): Promise<NightSummary[]> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).nights;
  return getJSON<NightSummary[]>("/api/nights");
}

export async function hosts(): Promise<HostsResponse> {
  if (IS_DEMO) return (await import("./demo")).demoHosts();
  return getJSON<HostsResponse>("/api/hosts");
}

export async function agents(): Promise<AgentStatus[]> {
  if (IS_DEMO) return (await import("./demo")).demoAgents();
  return getJSON<AgentStatus[]>("/api/agents");
}

// ---------------------------------------------------------------------------
// Bring-your-own provider keys. The engine stores a key (see `application.credentials`) but never returns
// it — a provider row says only whether a key is configured, never what it is.

export interface ProviderInfo {
  id: string;
  title: string;
  configured: boolean;
}

// `reason` explains a rejected key (from the provider's own probe) and is empty on success.
export interface ProviderKeyResult {
  ok: boolean;
  reason: string;
}

export async function providers(): Promise<ProviderInfo[]> {
  if (IS_DEMO) return [];
  return getJSON<ProviderInfo[]>("/api/providers");
}

export async function putProviderKey(id: string, key: string): Promise<ProviderKeyResult> {
  if (IS_DEMO) throw new ApiError(501, `/api/providers/${id}/key`);
  return postJSON<ProviderKeyResult>(`/api/providers/${encodeURIComponent(id)}/key`, { key });
}

export async function deleteProviderKey(id: string): Promise<ProviderKeyResult> {
  if (IS_DEMO) throw new ApiError(501, `/api/providers/${id}/key`);
  return deleteJSON<ProviderKeyResult>(`/api/providers/${encodeURIComponent(id)}/key`);
}

// A workspace's own Telegram bot. The engine's credential belongs to the operator and a workspace cannot reach
// it (application/messaging.py::resolve_telegram), so bringing your own is the only way notifications reach a
// phone. `configured` is all the server will say about the token — it is never readable back.
export interface MessagingStatus {
  configured: boolean;
  enabled: boolean;
  chat_id: string;
  /** True on the operator's own engine, whose bot comes from its service environment rather than settings. */
  from_environment?: boolean;
}

export async function messagingStatus(): Promise<MessagingStatus> {
  return getJSON<MessagingStatus>("/api/messaging/telegram");
}

// Fields left out are left alone, so the on/off switch does not require re-posting the token.
export async function putMessaging(body: {
  token?: string;
  chat_id?: string;
  enabled?: boolean;
}): Promise<MessagingStatus> {
  if (IS_DEMO) throw new ApiError(501, "/api/messaging/telegram");
  return putJSON<MessagingStatus>("/api/messaging/telegram", body);
}

export async function clearMessaging(): Promise<MessagingStatus> {
  if (IS_DEMO) throw new ApiError(501, "/api/messaging/telegram");
  return deleteJSON<MessagingStatus>("/api/messaging/telegram");
}

// Whether this checkout is behind the newest tagged release (see `application.updates`), and the exact
// command that would catch it up. Its own endpoint: the check reaches GitHub, and /api/status is polled.
export interface UpdateStatus {
  current: { version: string; kernel_version: string; git_describe?: string };
  latest: { tag: string; url: string } | null;
  update_available: boolean;
  how: string;
}

export async function updateStatus(): Promise<UpdateStatus> {
  if (IS_DEMO) {
    const d = await (await import("./demo")).demo();
    return {
      current: { version: d.engine.version, kernel_version: d.engine.version },
      latest: null,
      update_available: false,
      how: "",
    };
  }
  return getJSON<UpdateStatus>("/api/update");
}

// The operator's saved update policy, and applying or rolling back an update. Distinct from `updateStatus`
// above: that check only reaches GitHub to compare versions, these actually touch the checkout.

export interface UpdateConfig {
  channel: "dev" | "release";
  auto_apply: boolean;
  check_interval_min: number;
  keep_previous: number;
}

export interface ApplyResult {
  applied: boolean;
  version: string | null;
  reason: string;
  rolled_back: boolean;
}

export async function updateConfig(): Promise<UpdateConfig> {
  if (IS_DEMO) throw new ApiError(501, "/api/update/config");
  return getJSON<UpdateConfig>("/api/update/config");
}

export async function putUpdateConfig(config: UpdateConfig): Promise<UpdateConfig> {
  if (IS_DEMO) throw new ApiError(501, "/api/update/config");
  return putJSON<UpdateConfig>("/api/update/config", config);
}

export async function applyUpdate(channel?: "dev" | "release"): Promise<ApplyResult> {
  if (IS_DEMO) throw new ApiError(501, "/api/update/apply");
  return postJSON<ApplyResult>("/api/update/apply", { channel: channel ?? null });
}

export async function rollbackUpdate(): Promise<ApplyResult> {
  if (IS_DEMO) throw new ApiError(501, "/api/update/rollback");
  return postJSON<ApplyResult>("/api/update/rollback", {});
}

export async function external(): Promise<ExternalRow[]> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).external;
  return getJSON<ExternalRow[]>("/api/external");
}

export async function startRun(req: RunRequest): Promise<RunHandle> {
  if (IS_DEMO) throw new ApiError(501, "/api/runs");
  return postJSON<RunHandle>("/api/runs", req);
}

export async function stopRun(runId: string): Promise<RunHandle> {
  return postJSON<RunHandle>(`/api/runs/${encodeURIComponent(runId)}/stop`, {});
}

// Everything a page needs to show a run as it happens, or what the loop produced. Added centrally so that pages
// built in parallel never contend for this file.

export interface RunEvent {
  // "proposed_one" and "pruned" appear in a recorded run, which is replayed from the engine's own record
  // rather than from the live log, so it carries per-candidate detail the live stream summarises.
  type: "paired" | "promoted" | "proposed" | "proposed_one" | "pruned" | "round" | "closed" | "log" | "end";
  t?: number;
  candidate?: string;
  seed?: number;
  incumbent?: number;
  candidate_score?: number;
  delta?: number;
  decision?: string;
  n?: number;
  raw?: number;
  accepted?: number;
  round?: number;
  selected?: number;
  remaining_gpu_h?: number;
  night?: number;
  status?: string;
  exit_code?: number;
  text?: string;
  strategy?: string | null;
  family?: string | null;
}

export interface RunDetail extends RunHandle {
  recent: RunEvent[];
}

export interface PromotedModel {
  id: string;
  track: "model" | "harness";
  night: number;
  recipe: Record<string, unknown>;
  artefact: string | null;
  external_before: Record<string, Record<string, number>> | null;
  external_after: Record<string, Record<string, number>> | null;
}

export async function runs(): Promise<RunHandle[]> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).runs;
  return getJSON<RunHandle[]>("/api/runs");
}

export async function run(runId: string): Promise<RunDetail> {
  return getJSON<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function models(): Promise<PromotedModel[]> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).models;
  return getJSON<PromotedModel[]>("/api/models");
}

/**
 * Subscribe to a run's live events. Returns a function that closes the stream.
 * The engine sends server-sent events; a closed or failed stream is reported through onError rather than thrown,
 * because a page showing a long run must survive a dropped connection.
 */
export function streamRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError?: (error: Event) => void,
): () => void {
  const source = new EventSource(`${detectBase()}/api/runs/${encodeURIComponent(runId)}/events`);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as RunEvent);
    } catch {
      /* a malformed frame is skipped rather than killing the stream */
    }
  };
  source.onerror = (error) => {
    onError?.(error);
    source.close();
  };
  return () => source.close();
}

// ---------------------------------------------------------------------------
// Objectives: what the user is trying to achieve, and whether it is happening.

export interface Measurement {
  value: number;
  stderr: number;
  n: number;
  model: string;
  night: number;
  seq: number;
  sha256: string;
}

export type ProgressState = "unmeasured" | "baseline_only" | "measured";

export interface BenchmarkProgress {
  benchmark: string;
  state: ProgressState;
  reason: string;
  baseline: Measurement | null;
  latest: Measurement | null;
  delta: number | null;
  delta_lo: number | null;
  delta_hi: number | null;
  target_delta: number | null;
  met: boolean | null;
  significant: boolean;
}

export interface BenchmarkSpec {
  id: string;
  tool: string;
  metric: string;
  direction: string;
}

export interface Objective {
  id: string;
  intent: string;
  track: string;
  domain: string;
  benchmarks: BenchmarkSpec[];
  recipes: string[];
  target_delta: number | null;
  created: string;
  notes: string;
  progress: BenchmarkProgress[];
}

export interface Recipe {
  id: string;
  capability: string;
  title: string;
  skill: string;
  summary: string;
  source: string;
  available: boolean;
}

export interface ObjectiveDetail extends Objective {
  recipe_detail: { available: Recipe[]; absent: Recipe[]; unknown: string[] };
}

export interface ObjectivesResponse {
  objectives: Objective[];
  problems: { file: string; reason: string }[];
}

export async function objectives(): Promise<ObjectivesResponse> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).objectives ?? { objectives: [], problems: [] };
  return getJSON<ObjectivesResponse>("/api/objectives");
}

export async function objective(id: string): Promise<ObjectiveDetail> {
  if (IS_DEMO) {
    const d = await (await import("./demo")).demo();
    const found = (d.objectives?.objectives ?? []).find((o) => o.id === id);
    if (!found) throw new ApiError(404, `/api/objectives/${id}`);
    return { ...found, recipe_detail: { available: [], absent: [], unknown: found.recipes } };
  }
  return getJSON<ObjectiveDetail>(`/api/objectives/${encodeURIComponent(id)}`);
}

export async function recipeLibrary(): Promise<Recipe[]> {
  if (IS_DEMO) return (await (await import("./demo")).demo()).recipes ?? [];
  return (await getJSON<{ recipes: Recipe[] }>("/api/recipes")).recipes;
}

export interface ObjectiveInput {
  id: string;
  intent: string;
  track: string;
  domain: string;
  benchmarks: { id: string; tool: string; metric: string; direction: string }[];
  recipes: string[];
  target_delta: number | null;
  notes: string;
}

export async function postObjective(body: ObjectiveInput): Promise<Objective> {
  if (IS_DEMO) throw new ApiError(501, "/api/objectives");
  return postJSON<Objective>("/api/objectives", body);
}

// The compiled plan: an intent turned into ordered work. A proposal, never evidence.

export interface PlanStep {
  id: string;
  capability: string;
  recipe_ids: string[];
  available_recipe_ids: string[];
  availability: "available" | "uninstalled" | "no_recipe";
  consumes: string[];
  produces: string[];
  check: { criterion: string; benchmarks: BenchmarkSpec[]; target_delta: number | null };
  quantities: { name: string; value: number | null }[];
  reason: string;
}

export interface Plan {
  objective: string;
  steps: PlanStep[];
  external_inputs?: string[];
  unknown_recipes?: string[];
  assumptions?: string[];
  review_notes?: string[];
}

export async function objectivePlan(id: string): Promise<Plan> {
  if (IS_DEMO) {
    const d = await (await import("./demo")).demo();
    const found = (d.plans ?? {})[id];
    if (!found) throw new ApiError(404, `/api/objectives/${id}/plan`);
    return found;
  }
  return getJSON<Plan>(`/api/objectives/${encodeURIComponent(id)}/plan`);
}

// The plan compiled to Loom source: what the engine would actually run, not just the step outline above.

export interface LoomStep {
  id: string;
  text: string;
}

export interface LoomResponse {
  objective: string;
  source: string;
  steps: LoomStep[];
}

export async function objectiveLoom(id: string): Promise<LoomResponse> {
  if (IS_DEMO) {
    const d = await (await import("./demo")).demo();
    return (d.loom ?? {})[id] ?? { objective: id, source: "", steps: [] };
  }
  return getJSON<LoomResponse>(`/api/objectives/${encodeURIComponent(id)}/loom`);
}

// Subagent routing: which step would go to which agent/model at what tier, and inside what worktree path,
// plus whatever runs have actually been dispatched so far.

export interface SubagentPreviewRow {
  step: string;
  tier: string;
  agent: string;
  allowed_path: string;
}

export interface SubagentRunRow {
  step: string;
  route: string;
  accepted: boolean;
  wall: number;
}

export interface SubagentsResponse {
  preview: SubagentPreviewRow[];
  runs: SubagentRunRow[];
}

export async function objectiveSubagents(id: string): Promise<SubagentsResponse> {
  if (IS_DEMO) {
    const d = await (await import("./demo")).demo();
    return (d.subagents ?? {})[id] ?? { preview: [], runs: [] };
  }
  return getJSON<SubagentsResponse>(`/api/objectives/${encodeURIComponent(id)}/subagents`);
}

export async function dispatchSubagents(id: string): Promise<SubagentsResponse> {
  if (IS_DEMO) throw new ApiError(501, `/api/objectives/${id}/subagents/dispatch`);
  return postJSON<SubagentsResponse>(`/api/objectives/${encodeURIComponent(id)}/subagents/dispatch`, {});
}

// ---------------------------------------------------------------------------
// Chat: the assistant may state a number only if a tool call in this turn returned it. Anything the model's
// draft states that no tool backed is stripped before this response is built, and listed in `refusals` instead.

export interface ChatCitation {
  seq: number;
  what: string;
}

export interface ChatToolCall {
  tool: string;
  args: Record<string, unknown>;
  result_summary: string;
}

export interface ChatResponse {
  thread_id: string;
  reply: string;
  citations: ChatCitation[];
  tool_calls: ChatToolCall[];
  refusals: string[];
}

export async function chat(message: string, threadId: string | null): Promise<ChatResponse> {
  if (IS_DEMO) throw new ApiError(501, "/api/chat");
  return postJSON<ChatResponse>("/api/chat", { message, thread_id: threadId });
}

/**
 * Stream a chat response as server-sent events.
 * Yields events as they arrive from the server: tool calls, tokens, citations, and finally a done event.
 */
export async function* chatStream(
  message: string,
  threadId: string | null,
): AsyncGenerator<Record<string, unknown>, void, unknown> {
  if (IS_DEMO) throw new ApiError(501, "/api/chat/stream");

  const token = await localToken();
  const res = await fetch(`${detectBase()}/api/chat/stream`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { "x-pravrudhi-token": token } : {}),
    },
    body: JSON.stringify({ message, thread_id: threadId }),
  });

  if (!res.ok) throw new ApiError(res.status, "/api/chat/stream");

  const reader = res.body?.getReader();
  if (!reader) throw new Error("Response has no readable body");

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");

      // Keep the last incomplete line in the buffer
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.trim()) continue;
        if (!line.startsWith("data: ")) continue;

        try {
          const jsonStr = line.slice("data: ".length).trim();
          const event = JSON.parse(jsonStr) as Record<string, unknown>;
          yield event;
        } catch {
          // Malformed JSON in SSE frame; skip it
        }
      }
    }

    // Process any remaining data
    if (buffer.trim()) {
      if (buffer.startsWith("data: ")) {
        try {
          const jsonStr = buffer.slice("data: ".length).trim();
          const event = JSON.parse(jsonStr) as Record<string, unknown>;
          yield event;
        } catch {
          // Ignore
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export interface ChatThreadSummary {
  id: string;
  updated: string;
  turns: number;
}

export async function chatThreads(): Promise<ChatThreadSummary[]> {
  if (IS_DEMO) return [];
  return (await getJSON<{ threads: ChatThreadSummary[] }>("/api/chat/threads")).threads;
}

// Present on a stored turn once the engine started recording it alongside the reply; absent on turns written
// before that, so a stored turn must render correctly with or without it.
export interface ChatTurnMeta {
  citations: ChatCitation[];
  refusals: string[];
  tool_calls: ChatToolCall[];
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  created: string;
  meta?: ChatTurnMeta;
}

export async function chatThread(id: string): Promise<ChatTurn[]> {
  if (IS_DEMO) throw new ApiError(501, `/api/chat/threads/${id}`);
  return (await getJSON<{ id: string; turns: ChatTurn[] }>(`/api/chat/threads/${encodeURIComponent(id)}`)).turns;
}
