"""HTTP resources were opaque objects, leaving clients to guess the ledger's wire shapes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from pravrudhi.application.parity import Capability, Coverage, Verification
from pravrudhi_kernel.ledger.replay import Badge, CandidateView, Locks
from pravrudhi_kernel.schema import LedgerEvent


class HealthResponse(BaseModel):
    """Service identity and ledger presence were invisible to generated clients."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"ok": True, "version": "0.1.0", "kernel": "0.1.0", "ledger": False}]}
    )

    ok: bool
    version: str
    kernel: str
    ledger: bool


class UninitialisedStatus(BaseModel):
    """A workspace without a ledger must not acquire invented replay fields."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"initialised": False}]})
    initialised: Literal[False]


class NightResult(BaseModel):
    """Night closure fields could be absent in audit payloads and surface as null."""

    spent_gpu_h: int | float | None
    outcomes: dict[str, JsonValue] | None
    incumbent: str | None


class InitialisedStatus(BaseModel):
    """Ledger replay, badges and locks previously had no discoverable response contract."""

    initialised: Literal[True]
    chain_ok: bool
    events: int
    ledger_head: str | None
    state_hash: str
    candidates: int
    badges: dict[Badge, int]
    promoted: dict[str, list[str]]
    pruned: int
    nights: dict[int, NightResult]
    inbox_pending: list[str]
    locks: Locks


class UpdateCurrent(BaseModel):
    version: str
    kernel_version: str
    git_describe: str | None = None
    install: str | None = None


class UpdateLatest(BaseModel):
    tag: str
    url: str
    published_at: str | None = None


class UpdateStatusResponse(BaseModel):
    """The settings page read update fields off /api/status, which never carried them: the version line threw
    in the browser on every visit. The check reaches GitHub, so it is its own endpoint and not part of a status
    that the interface polls."""

    current: UpdateCurrent
    latest: UpdateLatest | None
    update_available: bool
    how: str


class UpdateConfigResponse(BaseModel):
    """The operator's update policy: which channel to track, whether to apply automatically, how often to
    check, and how many previous installs to keep around for rollback."""

    channel: Literal["dev", "release"]
    auto_apply: bool
    check_interval_min: int
    keep_previous: int


class UpdateLastCheckResponse(BaseModel):
    """When this install last actually looked for a release, or null if it never has — invisible entirely
    before this: the machines page had no way to say whether its update check is even running."""

    last_checked: str | None


class ApplyResultResponse(BaseModel):
    """What an apply or rollback attempt actually did. `reason` is shown verbatim to the operator: it is the
    only place a refused apply explains itself."""

    applied: bool
    version: str | None
    reason: str
    rolled_back: bool


class BeatOut(BaseModel):
    at: str
    looked_at: list[str]
    chose: dict[str, str] | None
    reason: str
    result: dict[str, JsonValue] | None


class HeartbeatResponse(BaseModel):
    beats: list[BeatOut]


class SandboxViolationResponse(BaseModel):
    """One out-of-policy write an agent made: the task that made it, the path it touched, and the policy
    (its declared allowed paths) that forbade it. Persisted at `.pravrudhi/violations.jsonl`."""

    task_id: str
    path: str
    allowed_paths: list[str]
    at: str



class SandboxObservationResponse(BaseModel):
    """What one worktree has touched against its base commit, and how that stacks up against its policy."""

    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    bytes_written: int = 0
    allowed_count: int = 0
    violations: list[SandboxViolationResponse] = []



class LiveSandboxResponse(BaseModel):
    """A live agent process was invisible once dispatched: nothing showed what its worktree was touching, how
    much of its wall-clock budget it had spent, or whether it had written outside its declared policy."""

    pid: int
    kind: str
    task_id: str
    worktree: str
    elapsed_s: int
    budget_s: int
    budget_fraction: float | None = None
    allowed_paths: list[str] = []
    observation: SandboxObservationResponse



class SandboxesResponse(BaseModel):
    """Every live agent joined to its worktree and policy, plus the persisted history of every write a policy
    has forbidden -- the record that makes a policy real rather than decorative."""

    live: list[LiveSandboxResponse]
    recent_violations: list[SandboxViolationResponse]



class DriveResponse(BaseModel):
    """One appetite drive. `unknown` with a `blocked_reason` is a real answer: a measurement nobody could take
    must never arrive as a number."""

    id: str
    wire_name: str
    value: float | None
    target: float
    deficit: float | None
    weight: float
    eligible: bool
    blocked_reason: str
    sources: list[str]
    unknown: bool


class AppetiteStateResponse(BaseModel):
    as_of: str
    policy_version: str
    drives: list[DriveResponse]
    largest_unmet: str | None
    selected: str | None
    action: dict[str, JsonValue] | None
    next_wake: str
    resting_reason: str | None


class AppetiteResponse(BaseModel):
    drives: list[DriveResponse]
    appetite: AppetiteStateResponse
    sentence: str


class InstallResponse(BaseModel):
    """One install's update policy and what it is actually running, read from its own workspace directory.
    The two end-user installs used to prove the update path works had no way to be asked this without SSHing
    in and reading logs by hand."""

    root: str
    channel: Literal["dev", "release"]
    auto_apply: bool
    current_version: str | None
    available_versions: list[str]
    last_check: float | None
    last_result: str | None
    healthy: bool
    due: bool




class FleetInstallsResponse(BaseModel):
    """Every install a configured fleet root names and that actually exists on this machine."""

    installs: list[InstallResponse]




class HealthCheckResponse(BaseModel):
    name: str
    ok: bool
    detail: str


class HealthStateResponse(BaseModel):
    """The engine's own survival state. A failing check is named with its detail, never summarised away."""

    state: str
    checks: list[HealthCheckResponse]


class StatusResponse(RootModel[UninitialisedStatus | InitialisedStatus]):
    """Missing and replayed ledgers were conflated by an untyped status object."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"initialised": False}]})


class DoctorCheck(BaseModel):
    """Readiness failures had no declared place for their diagnostic explanation."""

    name: str
    ok: bool
    detail: str


class DoctorResponse(BaseModel):
    """Installation readiness and its individual checks were opaque to API consumers."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ok": False,
                    "checks": [
                        {
                            "name": "initialised",
                            "ok": False,
                            "detail": "Missing: .pravrudhi/config.yaml, research/ledger.jsonl",
                        },
                        {
                            "name": "ledger",
                            "ok": False,
                            "detail": "Cannot read research/ledger.jsonl: No such file or directory",
                        },
                        {
                            "name": "docker",
                            "ok": False,
                            "detail": (
                                "Permission denied running 'docker info': add your user to the docker group "
                                "(then log out and back in) or use sudo."
                            ),
                        },
                        {"name": "gpu", "ok": True, "detail": "No GPU detected: 'nvidia-smi' failed (exited 9)."},
                        {
                            "name": "pools",
                            "ok": False,
                            "detail": "No sealed pool manifest under .pravrudhi/kernel/pools.",
                        },
                        {
                            "name": "prereg",
                            "ok": False,
                            "detail": (
                                "Missing pre-registration files: lora_night.yaml, harness_night.yaml, "
                                "controller.yaml, canaries.md"
                            ),
                        },
                    ],
                }
            ]
        }
    )

    ok: bool
    checks: list[DoctorCheck]


class HostSpecResponse(BaseModel):
    """Enrolled machine addresses and transports were hidden inside an untyped fleet."""

    name: str
    transport: str
    address: str
    user: str
    workdir: str
    orca_host_id: str


class HostCapabilitiesResponse(BaseModel):
    """Measured host capabilities, including derived capabilities, lacked a wire contract."""

    os: str
    arch: str
    cpu_count: int
    ram_gb: float
    gpu_name: str
    gpu_vram_gb: float
    accelerator: str
    accel_mem_gb: float
    docker: bool
    python: str
    agents: list[str]
    local_models: list[str]
    reachable: bool
    error: str
    can_train: bool
    can_serve_open_models: bool
    usable_model_gb: float


class HostResponse(BaseModel):
    """A fleet entry did not distinguish enrollment from measured capabilities in its schema."""

    host: HostSpecResponse
    capabilities: HostCapabilitiesResponse


class FleetResponse(BaseModel):
    """The fleet's machine collection was previously an opaque object."""

    hosts: list[HostResponse]


class AgentResponse(BaseModel):
    """Agent availability had no declared diagnostic reason for an unavailable agent."""

    name: str
    available: bool
    reason: str


class AgentCooldownResponse(BaseModel):
    """An agent currently cooling down after a vendor usage limit, and when it returns."""

    agent: str
    until: str


class SvasthyaCheckResponse(BaseModel):
    """One named survival check (design §5.1: health endpoints, request capture, scheduler freshness, disk,
    reaped processes, ledger integrity, release restorability, route fallback), with its own detail — the
    machines page had no way to show why the engine's health is what it is, only that it was."""

    name: str
    ok: bool
    detail: str
    integrity: bool


class SvasthyaResponse(BaseModel):
    """The engine's own survival state (`application.svasthya.assess`) had no HTTP contract at all: an operator
    could not see a degraded or integrity-halted engine from the interface, only from the CLI."""

    state: Literal["ready", "degraded", "recovering", "integrity_halt", "paused"]
    as_of: str
    checks: list[SvasthyaCheckResponse]
    reason: str


class ExternalResponse(BaseModel):
    """External scorer records lost their contract and scorer-specific audit extensions."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    seq: int
    night: int
    kind: str
    severity: str | None = None
    tier: str | None = None
    track: str | None = None
    condition: str | None = None
    model: str | None = None
    seed: int | None = None
    file: str | None = None
    sha256: str | None = None
    tool: str | None = None
    tool_version: str | None = None
    metrics: dict[str, dict[str, int | float]] | None = None
    n_samples: dict[str, int | None] | None = None
    transformers_version: str | None = None
    n_shot: dict[str, JsonValue] | None = None
    model_args: str | dict[str, JsonValue] | None = None
    dataset: str | None = None


class NightResponse(NightResult):
    """Completed nights had no declared link to their track and starting selection policy."""

    night: int
    track: str
    selection_policy: str | None


class MarkdownResponse(BaseModel):
    """Rendered evidence text was advertised only as an arbitrary string dictionary."""

    markdown: str


class EvidenceResponse(MarkdownResponse):
    """Named evidence documents lacked a declared document name beside their text."""

    name: str


class CandidateResponse(CandidateView):
    """Candidate replay fields had no API identity or badge in the generated schema."""

    id: str
    badge: Badge


class EventResponse(LedgerEvent):
    """Ledger envelopes were opaque despite having fixed metadata and extensible JSON payloads."""

    payload: dict[str, JsonValue]


class CandidateDetailResponse(BaseModel):
    """A candidate's replay view and supporting events were indistinguishable opaque objects."""

    id: str
    badge: Badge
    view: CandidateView
    events: list[EventResponse]


class BenchmarkResponse(BaseModel):
    """An objective's measuring instrument had no discoverable metric or direction."""

    id: str
    tool: str
    metric: str
    direction: str


class MeasurementResponse(BaseModel):
    """External measurements lacked declared provenance alongside their reported values."""

    value: float
    stderr: float
    n: int
    model: str
    night: int
    seq: int
    sha256: str


class ProgressResponse(BaseModel):
    """Unmeasured objectives could not be distinguished from measured progress in the schema."""

    benchmark: str
    state: Literal["unmeasured", "baseline_only", "measured"]
    reason: str
    baseline: MeasurementResponse | None
    latest: MeasurementResponse | None
    delta: float | None
    delta_lo: float | None
    delta_hi: float | None
    target_delta: float | None
    met: bool | None
    significant: bool
    paired: bool
    wins: int | None
    losses: int | None
    p_mcnemar: float | None


class ObjectiveResponse(BaseModel):
    """User intent, measuring instruments and replayed progress had no shared wire contract."""

    id: str
    intent: str
    track: str
    benchmarks: list[BenchmarkResponse]
    domain: str
    recipes: list[str]
    target_delta: float | None
    created: str
    notes: str
    progress: list[ProgressResponse]


class ObjectiveProblem(BaseModel):
    """Malformed objective files needed a visible filename and reason instead of disappearing."""

    file: str
    reason: str


class ObjectivesResponse(BaseModel):
    """The objective collection and load failures previously lacked a declared response shape."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"objectives": [], "problems": []}]})
    objectives: list[ObjectiveResponse]
    problems: list[ObjectiveProblem]


class RecipeResponse(BaseModel):
    """Catalogued recipes lacked a contract separating their description from local availability."""

    id: str
    capability: str
    title: str
    skill: str
    summary: str
    source: str
    available: bool


class RecipeResolution(BaseModel):
    """Absent and unknown objective recipes were not distinguishable in generated clients."""

    available: list[RecipeResponse]
    absent: list[RecipeResponse]
    unknown: list[str]


class ObjectiveDetailResponse(ObjectiveResponse):
    """An objective's resolved recipes were missing from its documented resource shape."""

    recipe_detail: RecipeResolution


class RecipesResponse(BaseModel):
    """The packaged recipe catalogue was hidden behind an arbitrary object response."""

    recipes: list[RecipeResponse]


class BenchmarkCatalogEntry(BaseModel):
    """Stating an objective's success in benchmark terms required already knowing an external scorer's own task
    syntax; this instead names only what the external tier has actually measured, with its last value."""

    id: str
    tool: str
    metric: str
    track: str
    value: float | None
    n: int | None


class BenchmarksResponse(BaseModel):
    """Every task the external tier has ever scored in this workspace, so a person choosing what success means
    picks from what the engine can measure rather than typing a metric name from memory."""

    benchmarks: list[BenchmarkCatalogEntry]


class InboxResponse(BaseModel):
    """Review packs lacked a declared signature state and nullable candidate badge."""

    pack: str
    candidate: str
    badge: Badge | None
    night: str
    signed: bool


class SignResponse(BaseModel):
    """An operator decision's ledger receipt had no discoverable identity or hash fields."""

    seq: int
    this_hash: str
    decision: str
    by: str


class TokenResponse(BaseModel):
    """The local guard's token response was absent from the API's resource contracts."""

    token: str


class AgentsResponse(RootModel[list[AgentResponse]]):
    """The agent collection previously left its entries untyped."""


class AgentCooldownsResponse(RootModel[list[AgentCooldownResponse]]):
    """Which agents are cooling down after a vendor usage limit had no endpoint; the router already tracks it
    in `application.availability`."""


class ExternalResultsResponse(RootModel[list[ExternalResponse]]):
    """The external collection previously left its entries untyped."""


class NightsResponse(RootModel[list[NightResponse]]):
    """The night collection previously left its entries untyped."""


class CandidatesResponse(RootModel[list[CandidateResponse]]):
    """The candidate collection previously left its entries untyped."""


class ObservationsResponse(RootModel[list[EventResponse]]):
    """The event collection previously left its entries untyped."""


class InboxListingResponse(RootModel[list[InboxResponse]]):
    """The inbox collection previously left its entries untyped."""


class QuantityResponse(BaseModel):
    """A quantity a step needs that the objective did not supply. `value` is null because it is unspecified, not
    because it is zero; the compiler names what is missing rather than guessing it."""

    name: str
    value: float | None = None


class SuccessCheckResponse(BaseModel):
    """What would justify keeping a step's output."""

    criterion: str
    benchmarks: list[BenchmarkResponse] = []
    target_delta: float | None = None


class PlanStepResponse(BaseModel):
    """One proposed step. Nothing here has run; `availability` reports whether a recipe for it exists on this
    machine, not whether it was used."""

    id: str
    capability: str
    recipe_ids: list[str]
    available_recipe_ids: list[str]
    availability: str
    consumes: list[str]
    produces: list[str]
    check: SuccessCheckResponse
    quantities: list[QuantityResponse]
    reason: str


class PlanResponse(BaseModel):
    """A decomposition of an objective's intent into work. A proposal, never evidence."""

    objective: str
    steps: list[PlanStepResponse]
    external_inputs: list[str] = []
    unknown_recipes: list[str] = []
    assumptions: list[str] = []
    review_notes: list[str] = []


class ToolResponse(BaseModel):
    """One catalogued tool, connector or plugin, marked available or not on this machine."""

    id: str
    category: str
    title: str
    provides: str
    detect: dict[str, str]
    available: bool


class ToolsResponse(BaseModel):
    """The tool catalogue. Not evidence: listing a tool is not a claim it has been invoked."""

    tools: list[ToolResponse]


class PreferenceResponse(BaseModel):
    """A key/value the user set, with the provenance of when and how."""

    key: str
    value: JsonValue
    source: str
    set_at: str


class MemoryNoteResponse(BaseModel):
    """A durable fact the user asked to remember. Never a benchmark number: that belongs to the ledger."""

    id: str
    text: str
    source: str
    created: str
    revised: str = ""
    """When the note was last edited, empty for one nobody has edited."""


class MessagingStatusResponse(BaseModel):
    """Whether this workspace has a Telegram bot and where it delivers. Carries no token: `configured` answers
    "is there a credential" without being one, which is the only thing a route may say about a secret."""

    configured: bool
    enabled: bool
    chat_id: str
    from_environment: bool = False
    """True on the operator's own engine, whose bot is set in its service environment rather than stored here."""


class MessagingRequest(BaseModel):
    """A change to this workspace's bot. Every field is optional so the on/off switch can be flipped without the
    token being posted again — a form that must re-send a secret to change an unrelated field teaches people to
    keep the secret somewhere convenient."""

    token: str | None = None
    chat_id: str | None = None
    enabled: bool | None = None


class ForgottenResponse(BaseModel):
    """The note that was deleted. Naming it back is what lets a caller confirm the deletion it asked for went
    through rather than inferring it from a status code."""

    forgotten: str


class ChatThreadResponse(BaseModel):
    """A conversation thread's turns."""

    thread_id: str
    turns: list[dict[str, str]]


class MemoryResponse(BaseModel):
    """What belongs to the user in this workspace: preferences, notes, and chat threads. Not the ledger."""

    preferences: list[PreferenceResponse]
    notes: list[MemoryNoteResponse]
    threads: list[str]


class LoomResponse(BaseModel):
    """An objective's plan rendered as Loom source. A proposal: nothing in it has run."""

    objective: str
    source: str
    steps: list[str]


class SubagentPreviewResponse(BaseModel):
    """One task the engine would dispatch for a plan step, before anything is dispatched."""

    objective: str
    step: str
    task_id: str
    tier: str
    agent: str
    model: str | None
    model_config = ConfigDict(populate_by_name=True)
    allowed_paths: list[str]
    validate_cmd: str = Field(alias="validate")  # `validate` is reserved on BaseModel
    why: str


class SubagentRunResponse(BaseModel):
    """What one dispatched subagent did. A record of the swarm's work, not evidence."""

    objective: str
    step: str
    task_id: str
    route: str
    accepted: bool
    wall_s: float
    files: list[str]
    reasons: list[str]
    at: str


class SubagentsResponse(BaseModel):
    preview: list[SubagentPreviewResponse]
    runs: list[SubagentRunResponse]


class DispatchResponse(BaseModel):
    """Acknowledgement that a plan's tasks were handed to the swarm in the background."""

    objective: str
    started: int


class DiffLineResponse(BaseModel):
    """One rendered line of a hunk, with its unified-diff role."""

    kind: Literal["context", "add", "del"]
    text: str


class DiffHunkResponse(BaseModel):
    """One `@@ ... @@` region of a file's diff."""

    header: str
    lines: list[DiffLineResponse]


class FileDiffResponse(BaseModel):
    """One file's diff against its worktree's base commit. `too_large` marks a file whose own 2000-line cap, or
    the diff's overall 400 KB cap, cut its hunks short -- never silently."""

    path: str
    added: int
    removed: int
    hunks: list[DiffHunkResponse]
    binary: bool = False
    too_large: bool = False


class DiffResponse(BaseModel):
    """A dispatched task's worktree diffed against the commit its branch forked from. `reason` is set, and
    `files` empty, when the worktree no longer exists or could not be read."""

    files: list[FileDiffResponse]
    base: str
    head: str
    truncated: bool = False
    reason: str = ""


class TaskSummaryResponse(BaseModel):
    """One dispatched task with a readable worktree, summarised for a diff list."""

    task_id: str
    files: int
    added: int
    removed: int
    truncated: bool


class DiffsResponse(RootModel[list[TaskSummaryResponse]]):
    """The recent dispatched tasks with a readable worktree, newest first."""


class SelfBuildRunResponse(BaseModel):
    """A dispatched self-build task's outcome had no declared response contract."""

    task_id: str
    route: str
    accepted: bool
    wall_s: float
    files: list[str]
    reasons: list[str]
    at: str


class RoutingRecordResponse(BaseModel):
    """What the routing log says about one route at one tier had no declared response contract."""

    route_id: str
    tier: str
    trials: int
    successes: int
    rate: float
    lo: float
    hi: float
    mean_wall_s: float
    relative_cost: float


class RoutingReportRowResponse(BaseModel):
    """A tier's current routing choice, or the reason it has none, had no declared response contract."""

    tier: str
    route: str | None = None
    agent: str | None = None
    model: str | None = None
    relative_cost: float | None = None
    reason: str | None = None
    records: list[RoutingRecordResponse] = []
    error: str | None = None


class SwarmResponse(BaseModel):
    """Nothing in the API showed the swarm itself: which agents are routed where, what has been dispatched, and
    what was accepted. This brings the agent survey, the routing table's live choices, and the last runs of both
    the objective swarm and the self-build swarm together in one place."""

    agents: list[AgentResponse]
    routing: list[RoutingReportRowResponse]
    subagent_runs: list[SubagentRunResponse]
    selfbuild_runs: list[SelfBuildRunResponse]


class LiveAgentResponse(BaseModel):
    """A dispatched task's worker process was invisible between "started" and "recorded": the run logs show what
    was dispatched and what came back, but nothing running in between, so an operator watching a long dispatch
    could not tell a live worker from a stalled one. `worktree` is null when the process's cwd is not a
    `.worktrees/` checkout, since not every agent process runs one."""

    pid: int
    elapsed_s: int
    kind: str
    worktree: str | None = None


class LiveAgentsResponse(RootModel[list[LiveAgentResponse]]):
    """The live agent-process collection previously left its entries untyped."""


class MeResponse(BaseModel):
    """Who is asking, as far as this engine can tell. Identity, not evidence and not authorisation."""

    mode: str
    authenticated: bool
    id: str | None = None
    email: str | None = None
    role: str | None = None
    edition: str = "Pravrudhi"
    """Which of the two products this caller is looking at: Pravrudhi Studio builds Pravrudhi, Pravrudhi builds
    the user's own work. Derived from the role rather than compiled in, so one binary introduces itself
    correctly to whoever opened it."""
    tagline: str = ""


class WorkspaceResponse(BaseModel):
    slug: str
    path: str


class WorkspacesResponse(BaseModel):
    """The workspaces the caller may use. With identity disabled, only the engine's own checkout."""

    owner: str
    workspaces: list[WorkspaceResponse]


class ProviderResponse(BaseModel):
    """The bring-your-own-key registry had no HTTP contract, and a naive one could type a field to carry
    the key itself rather than just its shape."""

    id: str
    title: str
    configured: bool
    key_prefix: str


class ProvidersResponse(RootModel[list[ProviderResponse]]):
    """The provider collection previously left its entries untyped."""


class ProviderKeyResponse(BaseModel):
    """Storing a bring-your-own key had no response contract, and one typed loosely could carry the key
    itself back to the caller instead of just whether it validated."""

    provider: str
    configured: Literal[True]
    validated: bool
    reason: str


class ProviderKeyRemovedResponse(BaseModel):
    """Removing a bring-your-own key had no declared response distinguishing it from a stored one."""

    provider: str
    configured: Literal[False]


class CitationResponse(BaseModel):
    """The ledger row a chat reply stands on. Prose could previously assert a result with nothing behind it;
    a citation is the sequence number a reader can replay for themselves."""

    seq: int
    what: str


class ChatToolCallResponse(BaseModel):
    """One tool the assistant actually ran this turn. The raw result stays server-side: what the client needs
    is that the call happened and what it found, not a second copy of the replayed state."""

    tool: str
    args: dict[str, JsonValue]
    result_summary: str


class ChatResponse(BaseModel):
    """One conversational turn, after the honesty pass. `refusals` was the missing half: a reply that had a
    number silently deleted from it looked like a reply that never made the claim."""

    thread_id: str
    reply: str
    citations: list[CitationResponse]
    tool_calls: list[ChatToolCallResponse]
    refusals: list[str]


class ChatThreadSummaryResponse(BaseModel):
    """A conversation in the thread list. Listing bare ids gave a client no way to order them or to show how
    much was said."""

    id: str
    updated: str
    turns: int


class ChatThreadsResponse(BaseModel):
    """The caller's conversations, most recently updated first."""

    threads: list[ChatThreadSummaryResponse]


class ChatTurnResponse(BaseModel):
    """One turn of a conversation. `created` is the storage layer's `ts` under the name the wire contract
    uses, so a client is not made to learn two spellings for one field. `meta` is empty for a user's turn and,
    for the assistant's, carries the citations, refusals and tool calls the honesty pass produced - the record
    that used to vanish once the reply was rendered, leaving a reopened thread with an answer and no receipt."""

    role: Literal["user", "assistant"]
    content: str
    created: str
    meta: dict[str, JsonValue] = Field(default_factory=dict)


class ChatThreadDetailResponse(BaseModel):
    """A conversation replayed in full."""

    id: str
    turns: list[ChatTurnResponse]


class EvidenceItemResponse(BaseModel):
    """One fact supporting a criterion: a commit, a ledger sequence, a file, or a command whose output was seen."""

    kind: str
    ref: str
    note: str


class CriterionResponse(BaseModel):
    """One checkable part of an ask. `source` distinguishes what the operator said from the engine's reading."""

    text: str
    source: Literal["operator", "engine"]
    met: bool
    evidence: list[EvidenceItemResponse]


class RequestNoteResponse(BaseModel):
    at: str
    note: str


class RequestResponse(BaseModel):
    """One captured ask, with its acceptance criteria and how long it has waited."""

    id: str
    asked_at: str
    text: str
    state: Literal["captured", "clarified", "planned", "in_progress", "delivered", "verified", "declined"]
    criteria: list[CriterionResponse]
    notes: list[RequestNoteResponse]
    session: str
    staleness_days: float
    progress: list[int]


class BacklogResponse(BaseModel):
    """What is outstanding: the operator's requests, oldest-waiting-open first."""

    total: int
    open: int
    by_state: dict[str, int]
    oldest_open_days: float
    requests: list[RequestResponse]


class RequestAdvanceRequest(BaseModel):
    """Move a request to a new state, with a note explaining why."""

    state: Literal["captured", "clarified", "planned", "in_progress", "delivered", "verified", "declined"]
    note: str = ""


class RequestEvidenceRequest(BaseModel):
    """Evidence that marks one acceptance criterion met."""

    kind: str
    ref: str
    note: str = ""


class JobRequest(BaseModel):
    """An ad hoc brief for the dispatch board: what to do, where it may write, and how it is checked. `validate`
    is reserved on BaseModel (see `SubagentPreviewResponse`), so the wire field keeps its name and the Python
    attribute takes `validate_cmd` instead."""

    title: str
    brief: str
    allowed_paths: list[str]
    model_config = ConfigDict(populate_by_name=True)
    validate_cmd: str = Field(default="uv run pytest -q", alias="validate")
    tier: str = "standard"
    policy: str = "proposal"
    agent: str | None = None


class JobResponse(BaseModel):
    """One dispatch-board job: what was asked, where it stands, and -- once it has run -- its verdict. Not
    evidence: a record of what the swarm did with this brief, like a `SubagentRunResponse`."""

    id: str
    title: str
    brief: str
    allowed_paths: list[str]
    model_config = ConfigDict(populate_by_name=True)
    validate_cmd: str = Field(alias="validate")
    tier: str
    policy: str
    agent: str | None
    state: Literal["queued", "running", "accepted", "rejected", "cancelled"]
    created: str
    started: str | None
    ended: str | None
    route: str | None
    accepted: bool | None
    reasons: list[str]
    files: list[str]
    wall_s: float


class JobsResponse(RootModel[list[JobResponse]]):
    """The dispatch board's jobs, newest first."""


class NotificationResponse(BaseModel):
    """One thing that happened while nobody was watching: a run ending, a job's verdict, a criterion being met.
    `ref` is a path into the app pointing at where it happened, or empty when there is nowhere to link."""

    id: str
    at: str
    kind: str
    title: str
    detail: str
    ref: str
    read: bool


class NotificationsResponse(BaseModel):
    """The notification feed: recent entries newest first, and how many of them are still unread."""

    notifications: list[NotificationResponse]
    unread: int


class MarkReadRequest(BaseModel):
    """Which notifications to mark read. An empty list marks every unread notification read."""

    ids: list[str] = []


class ParityResponse(BaseModel):
    """Checked-in capabilities with evidence outcomes and explicit coverage counts."""

    rows: list[Capability]
    verification: list[Verification]
    coverage: Coverage
    gaps: list[Capability]
    next_gap: Capability | None


class Ancestry(BaseModel):
    """The shape of the candidate graph: how many starting points the search has actually had."""

    nodes: int
    roots: int
    max_depth: int
    distinct_parents: int
    widest: tuple[str, int] | None


class NightPressure(BaseModel):
    """One night's choice: what was available on the bench it worked, against what the budget ran."""

    night: int
    live: int
    selected: int
    declined: int
    binding: bool


class SearchResponse(BaseModel):
    """Branching and selection pressure, folded from the ledger.

    Selection pressure is the load-bearing one. A night whose budget covered its whole live pool put no question
    to the controller, so no selection rule can be told apart from another on it.
    """

    ancestry: Ancestry
    pressure: list[NightPressure]
    binding_nights: int
    declined: int


class Seat(BaseModel):
    """One model route as it stands right now."""

    id: str
    agent: str
    model: str
    relative_cost: float
    tiers: list[str]
    sentinel: bool
    trials: int
    successes: int
    success_rate: float | None
    usable: bool
    returns_at: str | None
    note: str


class RosterResponse(BaseModel):
    """Every route, usable ones first. The one place that answers which model can be used right now, how well it
    has done, and when a spent one comes back."""

    seats: list[Seat]
    ready: int
    total: int


class RunView(BaseModel):
    """One run, live or already closed in the ledger.

    `extra="allow"` because a closed night carries fields a live run does not, and the two are deliberately
    returned in one list: a workspace with twenty-three finished nights once answered "no runs yet".
    """

    model_config = ConfigDict(extra="allow")

    id: str
    target: str
    status: str
    night: int | None = None
    request: dict[str, JsonValue] = {}
    started_at: float | None = None
    finished_at: float | None = None
    best_delta: float | None = None
    promoted: JsonValue = None
    events: int | None = None


class RunEvent(BaseModel):
    """One thing a run said, parsed from its output. The contract the event stream delivers, declared the same
    way `/chat/stream` declares its own: the response is a stream, and this is the shape of what comes down it.

    `extra="allow"` because the kinds carry different fields — a paired evaluation names candidate, seed and
    delta; a log line carries text — and flattening them into one rigid model would lose what makes each useful.
    """

    model_config = ConfigDict(extra="allow")

    type: str


class PromotedModel(BaseModel):
    """One promotion, with whatever external before-and-after exists for it. Both may be absent: a promotion is
    a decision the ledger recorded, and an external benchmark is a separate measurement that may not have run."""

    id: str
    track: str
    night: int | None = None
    recipe: dict[str, JsonValue] = {}
    artefact: str | None = None
    external_before: dict[str, JsonValue] | None = None
    external_after: dict[str, JsonValue] | None = None


class RunsResponse(RootModel[list[RunView]]):
    """Every run this engine has performed: live ones and the nights already closed in the ledger, in one list."""


class RunEventsResponse(RootModel[list[RunEvent]]):
    """What a run has said so far, as events rather than raw log lines."""


class PromotedModelsResponse(RootModel[list[PromotedModel]]):
    """What the loop produced, with the external before and after that exists for each."""
