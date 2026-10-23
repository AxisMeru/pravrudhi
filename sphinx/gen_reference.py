#!/usr/bin/env python3
# ruff: noqa: E501  (the hand-written notes below are prose, one line each)
"""Generate sphinx/reference/*.md from the code, or check that the committed pages match.

    python gen_reference.py --write    rewrite the pages
    python gen_reference.py --check    exit 1 and print a diff if a committed page differs from the code

Pages: cli (the Typer command tree), configuration (configs/*.yaml: purpose and key names, never values),
environment (the environment variable names the code reads), http-api (the FastAPI routes), packages (the module
map from module docstrings). Everything is read statically or from the CLI tree; nothing starts a server and no
value of any setting or secret is read or printed. The one-line notes in CONFIG_NOTES and ENV_NOTES are written by
hand: a name with no note is listed with a pointer to where it is read.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "reference"
ENGINE = ROOT / "src" / "pravrudhi"
KERNEL = ROOT / "pravrudhi_kernel" / "src" / "pravrudhi_kernel"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "pravrudhi_kernel" / "src")]

# --------------------------------------------------------------------------------------------- hand-written notes
CONFIG_NOTES = {
    "assistant_tier.yaml": "Whether the optional assistant tier can be offered right now, and the budget around it. Read by the health report.",
    "delegation.yaml": "Who may sign a gate, and under which conditions. Switching it off stops every autonomous sign-off.",
    "hosts.example.yaml": "Example of the fleet file for more than one machine. Copy to `configs/hosts.yaml` (not committed) or use `pravrudhi hosts add`; a fresh install needs no file.",
    "nyaya_agent.yaml": "The Nyaya agentic loop: the judge endpoints and their parameters, the structure-check binary pin, the audit-trail settings and the switches for each check. Constants live here, not in code.",
    "nyaya_corpus.yaml": "Retrieval settings for the statute corpus behind `ask`: the score below which nothing is returned.",
    "nyaya_gold.yaml": "Where the scorer binary for the constructed gold set lives. An environment variable overrides it.",
    "panel.yaml": "Per-vendor parameters for `panel run` and `panel sweep`; anything here replaces the defaults in code.",
    "partner_api.yaml": "The partner API's own safety limits (request size and rate), which apply whether or not authentication is on.",
    "routing.yaml": "What each model route costs relatively and what it can do, used by `pravrudhi routing` to choose a route for a difficulty tier.",
    "sealed_control_manifest.yaml": "Size and hash pins for the sealed sets that the second-judge positive control loads, so a truncated set is refused.",
    "seats.yaml": "Which account the command line spends, and what happens when that account stops being usable.",
    "tenant_vendors.yaml": "Which panel vendors an API caller may use. Default-closed: a vendor not listed is refused.",
    "usage_gate.yaml": "Usage thresholds for scripted vendor calls: a call is refused when the latest reading is at or above a threshold, missing or stale.",
}

ENV_NOTES = {
    "CODEX_HOME": "Where the Codex command line keeps its state; read to find its usage records.",
    "HERMES_HOME": "Where the Hermes agent keeps its state.",
    "HF_HOME": "Model cache folder (also set inside job containers).",
    "NYAYA_DEBUG_SECOND_JUDGE_FIELDS": "Turns on extra second-judge fields in the partner API response, for one deployment's diagnosis.",
    "NYAYA_GATE1_ENABLED": "Switches the first gate check on or off.",
    "NYAYA_GATE1_MODE": "Which of the first gate's two check functions runs.",
    "NYAYA_GATE1_MODEL": "Model id the first gate uses.",
    "NYAYA_GATE1_TAU_C": "Threshold used by the first gate's contradiction check.",
    "NYAYA_GATE1_THRESHOLD": "Threshold used by the first gate's entailment check.",
    "NYAYA_HOUSE_JUDGE_API_KEY": "Credential for the house judge endpoint (a secret; set it in the deployment, never in the repository).",
    "NYAYA_HOUSE_JUDGE_BASE_URL": "Overrides the house judge's base URL, for container deployments.",
    "NYAYA_HOUSE_JUDGE_ENFORCE_SERVED_MODEL": "Opt-in: refuse an answer whose served model id differs from the configured one.",
    "NYAYA_HOUSE_JUDGE_FALLBACK_URLS": "Ordered fallback endpoints for the house judge.",
    "NYAYA_HOUSE_JUDGE_MAX_INPUT_CHARS": "Longest judge prompt, in characters, the house judge accepts.",
    "NYAYA_HOUSE_JUDGE_MODEL": "Names the house judge's model id, so no model-list round trip is needed on a cold worker.",
    "NYAYA_HOUSE_JUDGE_TAU": "Overrides the house judge's decision threshold; lowering it below the configured value needs the next variable.",
    "NYAYA_HOUSE_JUDGE_TAU_ALLOW_LOWER": "Must be 1 for an override to set the threshold below the configured one (the loosening guard).",
    "NYAYA_HOUSE_JUDGE_TIMEOUT_S": "Request timeout, in seconds, for the house judge. Per call, not per request: a screening makes many calls.",
    "NYAYA_JUDGE_MAX_CONCURRENCY": "Cap on concurrent judge requests.",
    "NYAYA_SECOND_JUDGE_BASE_URL": "Overrides the second judge's base URL.",
    "NYAYA_SECOND_JUDGE_FALLBACK_ENDPOINT_IDS": "Comma list of endpoint ids to which the fallback credential may be sent.",
    "NYAYA_SECOND_JUDGE_FALLBACK_RECORD_DIR": "Folder holding the record a fallback endpoint needs before it is used.",
    "NYAYA_SECOND_JUDGE_FALLBACK_URLS": "Ordered fallback endpoints for the second judge; a malformed value stops startup.",
    "NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR": "Minimum probability mass the second judge's labels must hold.",
    "NYAYA_SECOND_JUDGE_MAX_INPUT_CHARS": "Longest prompt, in characters, the second judge accepts.",
    "NYAYA_SECOND_JUDGE_MAX_TOKENS": "Output token cap for the second judge.",
    "NYAYA_SECOND_JUDGE_MODEL": "Names the second judge's model id.",
    "NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_MAX_AGE_HOURS": "How old the second judge's positive-control record may be before it counts as stale.",
    "NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_RECORD_PATH": "Path to the positive-control record; the gate is off unless a record path is set.",
    "NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA": "Margin that turns a close second-judge answer into a referral.",
    "NYAYA_SECOND_JUDGE_STATUTE_CHARS": "How many statute characters go into the second judge's prompt.",
    "NYAYA_SECOND_JUDGE_TAU": "Overrides the second judge's decision threshold.",
    "NYAYA_SECOND_JUDGE_TIMEOUT_S": "Request timeout, in seconds, for the second judge. Per call, not per request: a screening makes many calls.",
    "NYAYA_SECOND_JUDGE_TOP_LOGPROBS": "How many top log-probabilities to request from the second judge.",
    "NYAYA_SHIM_BACKEND": "Backend of the local shim server (a stub unless set).",
    "NYAYA_SHIM_MODEL": "Model the local shim loads when it has a real backend.",
    "NYAYA_SPAN_RELEVANCE_ENABLED": "Switches the span-relevance check on or off.",
    "PRAVRUDHI_ALLOWED_ORIGINS": "Cross-origin sites the local engine may answer; off unless named.",
    "PRAVRUDHI_AUTH": 'Authentication mode of the deployment: `disabled` (default; a local single-user engine, nobody is identified and the local caller is the operator), `optional` (a bearer token is verified when present) or `required` (every `/api` route except the liveness check needs a verified sign-in). An unrecognised value is treated as `required`; the engine refuses to start in a non-`required` mode when a deployment-platform marker is set.',
    "PRAVRUDHI_CHAT_API_KEY": "Credential for the chat endpoint (a secret).",
    "PRAVRUDHI_CHAT_ENDPOINT": "OpenAI-compatible endpoint the chat surface talks to.",
    "PRAVRUDHI_CHAT_MODEL": "Model id the chat surface asks for.",
    "PRAVRUDHI_CITATION_INDEX": "Path to the citation index the partner API looks citations up in.",
    "PRAVRUDHI_CITATION_WARM": "Set to 0 to skip loading the citation index at startup.",
    "PRAVRUDHI_CLI": "Names the engine command to run for server-started jobs.",
    "PRAVRUDHI_CODEX_EFFORT": "Reasoning effort passed to the Codex agent.",
    "PRAVRUDHI_DEMO_ANON_PATHS": 'Routes the deployment answers without sign-in. At most `/api/v1/analyse-facts` and `/api/nyaya/registry/contracts` may be listed; any other path stops the engine from starting (and the variable is refused on a deployment platform).',
    "PRAVRUDHI_DISABLE_LOCAL_GUARD": "Turns the local request guard off; honoured only inside a container.",
    "PRAVRUDHI_EDITION": 'Edition of the build: a released build sets `product` (forces the product edition whatever the role); `studio` is the engine that improves Pravrudhi itself, where every `/api` route answers only operators.',
    "PRAVRUDHI_FREELLM_PATH": "Path to the hosted free-model client.",
    "PRAVRUDHI_IDENTITY_HEADER": "Name of the request header that carries a trusted identity.",
    "PRAVRUDHI_JWT_LEEWAY_S": "Clock-skew allowance, in seconds, when checking sign-in tokens.",
    "PRAVRUDHI_MATTER_OBJECT_BUCKET": "Object-store bucket for matter papers; with the endpoint variable it selects the object store.",
    "PRAVRUDHI_MATTER_OBJECT_ENDPOINT": "Object-store endpoint for matter papers.",
    "PRAVRUDHI_MATTER_SPANS_DIR": "Folder for the matter span files.",
    "PRAVRUDHI_NO_UPDATE": "Set to 1 to stop the engine applying updates.",
    "PRAVRUDHI_OPERATOR": "Name recorded as the operator when a request carries none.",
    "PRAVRUDHI_ROOT": "Default repository root for the assist backstop command.",
    "PRAVRUDHI_SERVICE_WINDOW_ENFORCE": "Turns the partner API's service-window check on.",
    "PRAVRUDHI_STUDIO_LOOPBACK_ONLY": "Marks a container deployment as reachable only from the local machine (Studio edition).",
    "PRAVRUDHI_WORKSPACES": "Folder of workspaces the engine may serve.",
    "SUPABASE_JWT_SECRET": "Secret that verifies sign-in tokens (a secret; deployment only).",
    "SUPABASE_SERVICE_KEY": "Service credential for the memory store (a secret; deployment only).",
    "SUPABASE_TENANCY_DB_URL": "Connection string of the tenancy database (a secret; deployment only).",
    "SUPABASE_URL": "Base URL of the hosted database service.",
    "TELEGRAM_BOT_TOKEN": "Bot credential for the notification inbox (a secret).",
    "TELEGRAM_CHAT_ID": "Chat the notification inbox writes to.",
    "TESSDATA_PREFIX": "Folder of the character-recognition language data, for scanned-page intake.",
    "HF_HUB_OFFLINE": "Set to 1 inside job containers so model downloads are refused (jobs run offline).",
    "NYAYA_SECOND_JUDGE_API_KEY": "Credential for the second judge endpoint (a secret; deployment only).",
    "PRAVRUDHI_ADMINS": 'The operators of the deployment: a comma-separated list of user ids or e-mail addresses (compared case-insensitively). Empty or unset names nobody. The role is resolved from this list; it is designed not to come from anything a caller sends.',
    "PRAVRUDHI_ADMIN_EMAIL": "Identity of the administrator the hosted-model switch recognises.",
    "PRAVRUDHI_BAD_KEY_BUDGET_PER_MINUTE": "How many failed partner-API key attempts are tolerated per minute before callers are throttled.",
    "PRAVRUDHI_CHAT_DAILY_CAP": "Daily cap on chat turns per caller.",
    "PRAVRUDHI_CHAT_RATE_PER_MINUTE": "Per-minute rate limit on chat turns per caller.",
    "PRAVRUDHI_CHECKPOINT_SYNC_ROOT": "Folder that checkpoints are synchronised to between machines.",
    "PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR": "Configuration folder the Claude command-line vendor runs with.",
    "PRAVRUDHI_CLAUDE_CLI_EXPECTED_EMAIL": "The account that scripted model calls must be signed in as; a different account is refused. The value is an address and belongs to the deployment.",
    "PRAVRUDHI_CLAUDE_CONFIG_DIR": "Configuration folder the engine pins for its model calls, so no call can pick up an account by accident.",
    "PRAVRUDHI_CLIENT_IP_SECRET": "Secret used to hash client addresses for the partner API's rate limits (deployment only).",
    "PRAVRUDHI_COLD_START_WAKE": 'Set to exactly `1` to switch the cold-start wake on without editing the packaged yaml (`cold_start_wake: false`); it can only turn the flag on, anything else leaves the yaml in force. The wake routes also need the waker secret to be set.',
    "PRAVRUDHI_DEMO_PRIVATE_NAMES": "Names the demo-publishing check must keep out of any published file (the value lists names; deployment only).",
    "PRAVRUDHI_DEMO_PRIVATE_NAMES_SHA256": "Hash that the private-names list must match before it is used.",
    "PRAVRUDHI_FRONTEND_DIR": "Folder holding the browser interface that the server serves.",
    "PRAVRUDHI_GITHUB_TOKEN": "Credential the update check may use against the source host (a secret; deployment only).",
    "PRAVRUDHI_HOSTED_IMAGE": "Marks the process as running inside the hosted image.",
    "PRAVRUDHI_HOSTED_MODELS": "Opt-in switch for hosted models.",
    "PRAVRUDHI_MATTER_OBJECT_ACCESS_KEY": "Access key for the matter-paper object store (a secret; deployment only).",
    "PRAVRUDHI_MATTER_OBJECT_PREFIX": "Key prefix inside the matter-paper bucket.",
    "PRAVRUDHI_MATTER_OBJECT_REGION": "Region name used when talking to the matter-paper object store.",
    "PRAVRUDHI_MATTER_OBJECT_SECRET_KEY": "Secret key for the matter-paper object store (a secret; deployment only).",
    "PRAVRUDHI_ORCA_PROMPT_DIR": "Folder of prompts for the Orca agent adapter.",
    "PRAVRUDHI_PROPOSER_ENDPOINT": "Endpoint of the proposer model, used as a fallback for the chat endpoint.",
    "PRAVRUDHI_PROPOSER_ENDPOINT_HOSTS": "Hosts a server-started job may borrow a proposer endpoint from.",
    "PRAVRUDHI_RUN_ENV_PASSTHROUGH": "Names of extra environment variables to pass through to a server-started job.",
    "PRAVRUDHI_SCRIPTED_CLAUDE_CONFIG_DIR": "Configuration folder used for scripted model calls.",
    "PRAVRUDHI_SCRIPTED_CLAUDE_EMAIL": "The account that scripted model calls are expected to use (an address; deployment only).",
    "PRAVRUDHI_SEATS_LOCAL": "Path to a local file that overrides the account settings.",
    "PRAVRUDHI_SKILL_DIRS": "Extra folders searched for skills and recipes.",
    "PRAVRUDHI_TENANCY_BACKEND": "Selects the tenancy store backend.",
    "PRAVRUDHI_TENANCY_CA_FILE": "Certificate bundle used to verify the tenancy database connection.",
    "PRAVRUDHI_TENANCY_KEY_CACHE_S": "How long, in seconds, a looked-up partner key may be cached.",
    "PRAVRUDHI_TENANCY_PROVISION_SECRET": "Secret that authorises creating organisations and keys (deployment only).",
    "PRAVRUDHI_TESSERACT_BIN": "Path to the character-recognition program used for scanned-page intake.",
    "PRAVRUDHI_WAKERS": 'Comma-separated user ids (never e-mail addresses or roles) that may ask for the judges to be woken. The fallback rule: used whenever `PRAVRUDHI_WAKE_ANY_SIGNED_IN` is not exactly `1`. Empty or unset names nobody.',
    "PRAVRUDHI_WAKER_SECRET": 'The shared secret the box-side waker signs its poll and result requests with (the engine checks a 60-second window and each nonce once). Held as a deployment secret by reference; the variable name itself is configurable (`cold_start_waker_secret_env`).',
    "PRAVRUDHI_WAKE_ANY_SIGNED_IN": 'Set to exactly `1` (anything else is off) to let any verified, non-anonymous signed-in account with an e-mail ask for a wake; the allowlist in `PRAVRUDHI_WAKERS` still applies as a fallback. Read once at startup. It changes nothing unless the cold-start wake routes exist.',
}
ENV_EXTERNAL = {
    "PATH",
    "HOME",
    "RENDER",
    "VERCEL",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "LD_LIBRARY_PATH",
    "CUDA_HOME",
    "CUDA_VISIBLE_DEVICES",
}
ENV_RE = re.compile(r"^(?:NYAYA|PRAVRUDHI|SUPABASE|TELEGRAM|CODEX|HERMES|HF|TESSDATA)_[A-Z0-9_]+$")

PACKAGE_NOTES = {
    "pravrudhi.application": "The engine's logic: the night, replay, the gate, screening, matters, citations, panels and the other command and server behaviours.",
    "pravrudhi.api": "The FastAPI server and its routers: the browser interface's routes, the partner API, chat, Nyaya and runs.",
    "pravrudhi.agents": "Coding-agent adapters (which agents can run here and how they are invoked).",
    "pravrudhi.cli": "The `pravrudhi` command line (Typer).",
    "pravrudhi.hosts": "The optional multi-machine layer: enrolling, probing and placing jobs on machines.",
    "pravrudhi.models": "Model access through OpenAI-compatible clients, local and hosted.",
    "pravrudhi.targets": "The `Target` protocol: what a harness exposes so a night can improve it.",
    "pravrudhi.serving": "Small local serving shims.",
    "pravrudhi.client": "A client library for the HTTP API.",
    "pravrudhi_kernel.ledger": "The hash-chained ledger: writer, verifier and replay.",
    "pravrudhi_kernel.schema": "Typed schema of ledger rows and the contracts around them.",
    "pravrudhi_kernel.sandbox": "The sandbox runner that executes a job in a disposable container.",
    "pravrudhi_kernel.stats": "Vendored statistics: sequential tests, intervals and parity fixtures.",
    "pravrudhi_kernel.metrics": "Benchmark scorers and sealed pools.",
    "pravrudhi_kernel.efe": "The expected-free-energy controller mathematics.",
}



# ------------------------------------------------------------------------------ public-prose scrubbing
# Docstrings in this repository carry planning references (issue and decision numbers, team and session names). The
# documentation is public prose, so a description taken from a docstring or a help string is scrubbed before it is
# printed; a description that still names the team after scrubbing is replaced by MODULE_NOTES or a plain pointer.
_BANNED = re.compile(
    r"track[- ][abc]\b|lead[- ]?2|\bsession[- ]\d|\breviewer\b|\bR[12]\b|dual-signed|\bslice [a-z0-9]\b|"
    r"\bADR-\d|#\d{2,}|this machine's own|production test|\bgoal\b|\bvia the operator\b",
    re.I,
)
_PAREN = re.compile(r"\s*\((?:[^()]|\([^()]*\))*\)")
_ISSUE = re.compile(r"\b(?:pravrudhi(?:-app)?|prabhasa-nyaya)?#\d+(?:\s*(?:slice|step|PR)\s*[A-Za-z0-9-]+)?")
_ADR = re.compile(r"\bADR-\d+(?:\s*§\s*\d+)?'?s?:?")
_LEAD = re.compile(r"^(?:Track[- ][ABC][^:]{0,160}:|[A-Z]\d+[a-z]?(?:/[A-Z]\d+[a-z]?)? \([^)]*\):|#\d+[^:]{0,80}:|PR-?\d[^:]{0,40}:)\s*")


_PREVIEW = re.compile(r"\bPREVIEW\b")


REWRITES = [
    (" (`research/prereg/p3-citation-verification-2026-09-25.md`)", ""),
    (" (prereg §3/§4)", ""),
    (" (§1 of the mutation spec)", ""),
    ("Writes research/prereg/measured_stack.json from one real job.", "Writes its measurements from one real job."),
    ("Preview or dispatch a self-build plan", "Show or dispatch a self-build plan"),
    ("and never read back by any route.", "and is designed not to be read back by any route."),
    ("Never the key or a prefix of it - only", "It is designed not to return the key or a prefix of it, only"),
    ("Never the key or a prefix of it \u2014 only", "It is designed not to return the key or a prefix of it, only"),
]


def scrub(text: str, cap: bool = True) -> str:
    """Remove planning references from a description; empty string if nothing presentable is left."""
    t = text
    for _a, _b in REWRITES:
        t = t.replace(_a, _b)
    t = re.sub(r"\s*\((?:T\d\w*)[^()]*\)", "", t)
    t = re.sub(r"\s*\([^()]*docs/decisions/[^()]*\)", "", t)
    t = re.sub(r"docs/decisions/\S+", "", t)
    t = _PREVIEW.sub("", t)
    t = re.sub(r"(?i)prabhasa-nyaya's", "the external", t)
    t = re.sub(r"(?i)^prabhasa-nyaya\b", "Nyaya", t)
    t = _LEAD.sub("", t)
    t = _PAREN.sub(lambda m: "" if _BANNED.search(m.group(0)) or re.search(r"#\d", m.group(0)) or "ADR-" in m.group(0) else m.group(0), t)
    t = _ISSUE.sub("", t)
    t = _ADR.sub("", t)
    t = re.sub(r"\bseat's\b", "account's", t)
    t = re.sub(r"\bseats\b", "accounts", t)
    t = re.sub(r"\bseat\b", "account", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;:-")
    if not t or _BANNED.search(t):
        return ""
    return t[0].upper() + t[1:] if cap and t[0].islower() and not t.startswith("`") else t


ROUTE_NOTES: dict[tuple[str, str], str] = {
    ('GET', '/api/v1/status'): 'Engine version and, when one is configured, the service window. Needs no key and answers on a fresh install.',
    ('POST', '/api/v1/analyse-facts'): "Element-by-element analysis of the facts against the chosen contracts; returns each element's status and the fact it cites. Request and status values are in the partner quickstart.",
    ('POST', '/api/v1/analyse-facts/assist'): 'The assisted variant of analyse-facts (the optional assistant, with consent); present only when the assisted route is enabled.',
    ('POST', '/api/v1/intake'): 'Turns pasted text into numbered facts and lists the citations it finds; works on pasted text only.',
    ('POST', '/api/v1/intake/assist'): 'The assisted variant of intake (the optional assistant, with consent); present only when the assisted route is enabled.',
    ('POST', '/api/v1/analyse-facts/jobs'): 'Submit the same body as analyse-facts as an asynchronous job; answers 202 with a job id.',
    ('GET', '/api/v1/analyse-facts/jobs/{job_id}'): 'Status of a job (pending, running, done or failed) and, when done, the result the synchronous call would have returned.',
    ('GET', '/api/v1/audit'): "Page through the calling key's own call log.",
    ('POST', '/api/v1/verify-citations'): 'Look up citations in the citation index and report whether each case is in it (existence only, not what the case held).',
    ('POST', '/api/v1/orgs'): 'Create an organisation (provisioning credential).',
    ('POST', '/api/v1/orgs/{org_id}/keys'): 'Create an API key for an organisation; the secret is shown once.',
    ('GET', '/api/v1/orgs/{org_id}/keys'): "List an organisation's keys.",
    ('POST', '/api/v1/orgs/{org_id}/keys/{key_id}/revoke'): 'Revoke a key (operator or provisioning credential).',
    ('GET', '/api/v1/orgs/{org_id}/usage/summary'): 'Organisation-wide usage summary (operator or provisioning credential).',
    ('GET', '/api/v1/orgs/{org_id}/usage'): "A key's own usage counters.",
    ('GET', '/api/v1/internal/wake-requests'): 'For the box-side waker: fetch pending wake requests (signed request; always registered, and answers 404 when the cold-start wake is off).',
    ('POST', '/api/v1/internal/wake-results'): 'For the box-side waker: post the result of a wake (signed request; always registered, and answers 404 when the cold-start wake is off).',
    ('POST', '/api/v1/analyse-facts/wake'): 'Start a cold-start wake of the judges for the signed-in caller; refused with a 503 (judges_offline) when a wake is not allowed.',
    ('GET', '/api/v1/analyse-facts/wake/status'): "Progress of the signed-in caller's wake.",
}

MODULE_NOTES: dict[str, str] = {
    "pravrudhi.application.audit": "Partner-visible audit log of analyse-facts calls: who (key id), when, which run, which contracts, what outcome and HTTP status. It is designed not to hold fact text, narrative, quotes or any judge output beyond the outcome label: the writer takes only the fields below, and contract ids are stored only when they look like ids.",
    "pravrudhi.application.route_scope": "A guard that flags a route whose handler is identity-aware (it takes a `user` parameter) but never resolves that identity into a per-caller project or store in its own body, so that it would read the engine's own root instead.",
    "pravrudhi.application.typed.validators": "Mechanical validation for id-ref and span fields: schema-valid is necessary but not sufficient, so these checks stay separate from decoding and always run.",
    "pravrudhi_kernel.stats.core": "Vendored statistics code; the kernel keeps to this one statistics implementation rather than a third library.",
    "pravrudhi.application": "The engine's logic: the night, replay, the gate, screening, matters, citations, panels and the other command and server behaviours.",
    "pravrudhi.cli": 'The command line package; the commands are defined in `pravrudhi.cli.app`.',
    "pravrudhi.api.nyaya": 'The Nyaya routes over HTTP: ask a question of Indian law, the audit trail, the corpus and the history. Open to users in both editions.',
    "pravrudhi.application.nyaya": 'Nyaya in the product: a question of Indian law, answered from sources and checked.',
    "pravrudhi.application.agenda": 'The review agenda for cheque dishonour under section 138 of the Negotiable Instruments Act. Pure: plain document records in, a page out.',
    "pravrudhi.application.quashing": 'The quashing categories agenda (relief: quashing of an FIR or complaint under section 528 of the BNSS). Pure: plain records in, a page out.',
    "pravrudhi.application.tabular": 'The matter table. Pure: plain document and passage records in, a table out.',
    "pravrudhi.application.loom_interp": "Carries an objective's monitors and gates into a Loom program so the program states them itself.",
    "pravrudhi.application.maturity_signals": "Tests that watch the engine's own accumulated history and flag when a loop's real operating record crosses a point where a design should be revisited.",
    "pravrudhi.application.nyaya_loosening_guard": 'A guard for the night loop: a candidate configuration may not loosen the safety operating point.',
    "pravrudhi.application.scanned_intake_engine": 'The pure core of scanned and garbled-page intake: parsing the character-recognition output, the page floor, the garbled-layer detector and the subprocess runner. Nothing runs while the feature flag is off.',
    "pravrudhi.application.scanned_intake_ingest": 'The ingest step of scanned-page intake: one document request in, the text for the next stage plus the additional response fields out. Reached only when the feature flag is on.',
    "pravrudhi.application.scanned_intake_strings": "The customer-visible lines of scanned-page intake; the engine's text must equal the approved lines exactly.",
    "pravrudhi.application.second_judge_positive_control": 'A standing positive control for the second judge: an endpoint that answers can still be silently miscalibrated or serving the wrong weights, so a known case is checked against it.',
    "pravrudhi.application.sign_carryover": 'Whether a review sign made on one commit still covers a later commit.',
    "pravrudhi.application.nyaya_gold_score": "Scores the constructed gold set with an external Lean checker, independent of this repository's own generator, per class with Wilson intervals and never as one aggregate.",
    "pravrudhi.application.nyaya_lean": 'The live-path Lean checker, generalised to any contract the external compiled `score` binary knows, selected by the wire tag `A3N`.',
    "pravrudhi.application.nyaya_lean_composition": "The Python consumer for the `COMP` wire tag: scores an answer's element assertions against one of the composed cross-contract predicates.",
    "pravrudhi.application.nyaya_lean_elements": 'The Python consumer for the `A3E` wire tag, which builds a contract entirely from the wire line instead of looking one up per section.',
    "pravrudhi.application.nyaya_lean_registry": "The Python consumer for the `REG` wire tag: scores an answer's element assertions against one of the registry contracts.",
    "pravrudhi.application.nyaya_judge_warmup": "A non-blocking warm-up request to the house judge at startup, so the first real request does not wait out the judge's full cold start on top of the engine's own.",
    "pravrudhi.application.legalbench_harness": "Closed-book unaided-accuracy scoring for LegalBench's rule-application tasks: reads a task file and compares a vendor's raw yes/no answers with the stored answers.",
    "pravrudhi.application.rjbench_runner": 'Turns a benchmark row into a judge request.',
    "pravrudhi_kernel.efe.types": 'The typed records of the controller: belief keys, precision, the expected-free-energy terms, shares, a selection batch and the decorative-controller verdict.',
    "pravrudhi_kernel.schema": 'The typed schema of ledger rows and the records around them.',
    "pravrudhi_kernel.schema.candidate": 'A candidate and the prediction committed for it before it runs.',
    "pravrudhi_kernel.schema.citta": "The controller's belief state: normal beliefs, per-candidate beliefs and the combined record.",
    "pravrudhi_kernel.schema.evidence_plan": 'The plan for what evidence a candidate or study will produce.',
    "pravrudhi_kernel.schema.gate_report": 'The gate report: each side of a gate, its layers, the closure report, deviations and sign-offs.',
    "pravrudhi_kernel.schema.ledger_event": 'The ledger event: the one row type the hash-chained ledger stores.',
    "pravrudhi_kernel.schema.observation": 'One observation: a measured result recorded against a candidate.',
    "pravrudhi_kernel.schema.preferences": 'The preferences the controller scores outcomes against.',
}

# --------------------------------------------------------------------------------------------------- helpers
def first_line(text: str | None) -> str:
    if not text:
        return ""
    para = text.strip().split("\n\n")[0]
    return re.sub(r"\s+", " ", para).strip()


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(str(c)) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------------- CLI
def page_cli() -> str:
    import click
    import typer

    from pravrudhi.cli.app import app

    root = typer.main.get_command(app)
    lines = [
        "# Command line",
        "",
        "Generated from the Typer command tree by `sphinx/gen_reference.py`; do not edit by hand. Run any command with",
        "`--help` for the same text. Every command takes `--root` where it works on a project folder.",
        "",
    ]

    def params(cmd: click.Command) -> list[list[str]]:
        rows = []
        for p in cmd.params:
            if p.name == "help":
                continue
            flags = ", ".join(getattr(p, "opts", []) or [f"<{p.name}>"])
            kind = getattr(p, "param_type_name", "option")
            dflt = p.default
            show = "" if dflt in (None, ()) or callable(dflt) else str(dflt)
            if getattr(p, "required", False):
                show = "required"
            rows.append([flags, kind, show, scrub(first_line(getattr(p, "help", "") or ""), cap=False)])
        return rows

    def walk(cmd: click.Command, path: list[str]) -> None:
        for name in sorted(getattr(cmd, "commands", {})):
            sub = cmd.commands[name]  # type: ignore[attr-defined]
            full = path + [name]
            lines.append(f"## `pravrudhi {' '.join(full)}`")
            lines.append("")
            lines.append(scrub(first_line(sub.help)) or "(no description yet)")
            lines.append("")
            if hasattr(sub, "commands"):
                lines.append("Subcommands: " + ", ".join(f"`{n}`" for n in sorted(sub.commands)) + ".")
                lines.append("")
                walk(sub, full)
                continue
            rows = params(sub)
            if rows:
                lines.append(table(["Flag or argument", "Kind", "Default", "Meaning"], rows))
                lines.append("")

    walk(root, [])
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------------------------ configuration
def yaml_keys(path: Path) -> list[str]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    keys: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            keys.append(str(k))
            if isinstance(v, dict):
                keys += [f"{k}.{kk}" for kk in v]
    return keys


def page_configuration() -> str:
    lines = [
        "# Configuration files",
        "",
        "Generated by `sphinx/gen_reference.py`. The files live in `configs/`. This page lists what each file is for and",
        "the names of its keys; values are not shown here, and a value that is a secret belongs to the deployment, never",
        "to a file in the repository. Constants live in these files, not in code.",
        "",
    ]
    for f in sorted((ROOT / "configs").glob("*.yaml")):
        lines.append(f"## `configs/{f.name}`")
        lines.append("")
        lines.append(CONFIG_NOTES.get(f.name, "(not yet described; see the comments at the top of the file)"))
        lines.append("")
        keys = yaml_keys(f)
        if keys:
            lines.append("Keys: " + ", ".join(f"`{k}`" for k in keys) + ".")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------------------------- environment
def env_names() -> dict[str, str]:
    """name -> the module that reads it (a line that reads the environment or assigns an *_ENV constant wins over a mere mention)."""
    best: dict[str, tuple[int, str]] = {}
    for root, rel in ((ENGINE, "src/pravrudhi"), (KERNEL, "pravrudhi_kernel/src/pravrudhi_kernel")):
        for f in sorted(root.rglob("*.py")):
            text = f.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            lines = text.splitlines()
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and ENV_RE.match(n.value):
                    line = lines[n.lineno - 1]
                    rank = 0 if re.search(r"environ|getenv|env\.get|_ENV\b|_env\(", line) else 1
                    mod = f"{rel}/{f.relative_to(root).as_posix()}"
                    if n.value not in best or rank < best[n.value][0]:
                        best[n.value] = (rank, mod)
    return {k: v[1] for k, v in best.items()}


def page_environment() -> str:
    names = env_names()
    rows = []
    for name in sorted(names):
        if name in ENV_EXTERNAL:
            continue
        rows.append([f"`{name}`", ENV_NOTES.get(name, "(not yet described)"), f"`{names[name]}`"])
    return "\n".join(
        [
            "# Environment variables",
            "",
            "Generated by `sphinx/gen_reference.py` from the names the code mentions. The table gives each name, what it",
            "controls and the module that reads it. Defaults and values are deliberately not listed: a deployment sets",
            "them, and a secret is never written to the repository. A name marked as a secret must be set only in the",
            "deployment's own secret store.",
            "",
            table(["Name", "What it controls", "Read in"], rows),
            "",
        ]
    )


# ------------------------------------------------------------------------------------------------ HTTP API
def page_http() -> str:
    routes: list[tuple[str, str, str, str, str]] = []
    for f in sorted((ENGINE / "api").glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        prefixes: dict[str, str] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "APIRouter":
                pref = ""
                for kw in n.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        pref = str(kw.value.value)
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        prefixes[t.id] = pref
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in n.decorator_list:
                    if (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr in {"get", "post", "put", "delete", "patch"}
                        and isinstance(d.func.value, ast.Name)
                        and d.args
                        and isinstance(d.args[0], ast.Constant)
                        and isinstance(d.args[0].value, str)
                    ):
                        base = prefixes.get(d.func.value.id, "")
                        routes.append(
                            (f.name, d.func.attr.upper(), base + d.args[0].value, n.name, scrub(first_line(ast.get_docstring(n))))
                        )
    routes.sort(key=lambda r: (r[2], r[1]))
    rows = []
    seen: set[tuple[str, str]] = set()
    for mod, m, p, h, summ in routes:
        summ = summ or ROUTE_NOTES.get((m, p), "")
        if not summ or (m, p) in seen:  # a route with no description is left out rather than published as a placeholder
            continue
        seen.add((m, p))
        rows.append([f"`{m}`", f"`{p}`", summ, f"`{h}` in `api/{mod}`"])
    return "\n".join(
        [
            "# HTTP routes",
            "",
            "Generated by `sphinx/gen_reference.py` from the route decorators under `src/pravrudhi/api/`; do not edit by",
            "hand. The partner API (`/api/v1/...`) has its own machine-readable contract in `docs/api/openapi-v1.json` and",
            "two quickstarts under `docs/api/`; its routes also appear here. Which routes are reachable, and by whom, depends",
            "on the deployment's edition and authentication mode (see the environment page).",
            "",
            table(["Method", "Path", "What it does (first line of the handler's docstring)", "Handler"], rows),
            "",
        ]
    )


# ----------------------------------------------------------------------------------------------- packages
def module_doc(path: Path) -> str:
    try:
        return first_line(ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))))
    except SyntaxError:
        return ""


def page_packages() -> str:
    out = [
        "# Packages and module map",
        "",
        "Generated by `sphinx/gen_reference.py`. For each package: what it is for, then every module with the first",
        "paragraph of its docstring. A module without a docstring is listed as such; adding one is the way to improve",
        "its line here and its page under *API reference*.",
        "",
    ]
    groups: list[tuple[str, Path]] = []
    for pkg in sorted(p for p in ENGINE.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        groups.append((f"pravrudhi.{pkg.name}", pkg))
    for pkg in sorted(p for p in KERNEL.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        groups.append((f"pravrudhi_kernel.{pkg.name}", pkg))
    for name, pkg in groups:
        out.append(f"## `{name}`")
        out.append("")
        out.append(PACKAGE_NOTES.get(name, "(not yet described)"))
        out.append("")
        rows = []
        for f in sorted(pkg.rglob("*.py")):
            if f.name == "__init__.py" and f.parent == pkg:
                mod = name
            else:
                rel = f.relative_to(pkg).with_suffix("").as_posix().replace("/", ".")
                mod = name + "." + rel.removesuffix(".__init__")
            raw = module_doc(f)
            desc = MODULE_NOTES.get(mod) or (scrub(raw) if raw else "")
            if not desc:
                desc = "(no module docstring yet)" if not raw else "(described in the module docstring)"
            rows.append([f"`{mod}`", desc])
        out.append(table(["Module", "What it is"], rows))
        out.append("")
    return "\n".join(out)


PAGES = {
    "cli.md": page_cli,
    "configuration.md": page_configuration,
    "environment.md": page_environment,
    "http-api.md": page_http,
    "packages.md": page_packages,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    bad = 0
    for name, fn in PAGES.items():
        text = fn()
        path = OUT / name
        if args.write:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
            continue
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old != text:
            bad += 1
            sys.stdout.writelines(
                list(difflib.unified_diff(old.splitlines(True), text.splitlines(True), f"committed/{name}", f"generated/{name}"))[
                    :60
                ]
            )
    if bad:
        print("\nreference pages are stale; fix: cd sphinx && python gen_reference.py --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
