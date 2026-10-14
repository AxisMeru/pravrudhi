# Changelog

Release notes for the engine (`pravrudhi`). Versions before 0.5.44 are described in their GitHub release notes and commit
messages.

## Unreleased

### Fixed

- **The first citation check after a start is no longer slowed by the coverage summary (caller-visible).** The coverage summary that comes with a citation answer is now computed with a query that stays fast on the container image's SQLite (3.46); on the slim index the old query took about 30 seconds, and the first citation check after each start waited for it. The answers and the coverage figures are unchanged. The change is under `src/`, so it reaches a running container only by a rebuild of the engine image.

## 0.6.7 (2026-10-14)

Release on 0.6.6.1: the partner API key works on the partner routes of the hosted deployment (#436), the intake and hearing-preparation workflows are added, each with its own pinned assistant agent (#430, #420, #431, #437), the steps appendix on an assisted screening arrives (#421), the audit page answers for a key that called the assisted route (#428), the gateway client-address diagnostic and the bad-key budget knob arrive (#426), and partner keys, usage counts and the audit log can live in the Supabase database in hosted deployments (#439). **Rebuild the image.** The changes are under `src/`, `configs/` and `docs/api/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.7.

### Added

- **Intake: pasted text becomes numbered facts (caller-visible).** The intake route takes pasted text and returns numbered facts, each a contiguous piece of the text with its position, the citations found in it with an existence-only status, and a count of what was left out (the heading of a court order, repeated lines, lines with no words). Nothing is rewritten. Two limits: heading detection is built for court orders with a recognised heading, so for other text, or an unrecognised heading, heading text such as names and addresses can stay as facts; and a sentence split by a page break can become two facts. Real-text fixes (#437): the order's heading is excluded, the assistant is given only the numbered facts, and the stated range of facts is returned. The assisted form answers with the intake agent configured under `workflows:` (#431) when the assistant tier is available, and otherwise returns the deterministic result.
- **Steps appendix for an assisted screening (#416).** A finished assisted run (`POST /api/v1/analyse-facts/assist`) now also returns `appendix`: the steps it took, counted from the recorded tool calls only: each stage with how many times it ran, the quote and Lean check results, and per contract the elements judged, quotes checked and found valid, and whether the Lean check agreed with the engine's own assembly. No text from the assistant is in it. It is absent whenever the run falls back to the ordinary result.
- **Hearing-preparation pack (#415).** `POST /api/v1/analyse-facts/assist` takes `workflow: "hearing_prep"` (default `screening`; the workflow runs with its own agent under `workflows:` in `configs/assistant_tier.yaml`, and without one it stays dark). The response gains a `pack`: a checklist built by fixed rules from the screening result. It lists the ingredients still open (each with a fixed prompt id and default text), the cited facts that may defeat the claim (prompts for review, never findings), the plain reason sentence from the signed reason table (the machine reason code travels in a separate field for the app's mapping, never in a display field), and the legal citations found in the facts, each as a ready body for `POST /verify-citations`. Nothing in it is written by a model, and it adds no fact and no law. A list of more than 50 citations is cut and says so (`citations_truncated`). When the assisted run does not finish, or the workflow has no agent, the ordinary result comes back with the same pack built from it (`assistant.used: false`).
- **Client-address diagnostic on `GET /api/health` (response headers only; no body change).** `X-Pravrudhi-Client-Ip-Source` says where the engine took the caller's address from (`signed`, `proxy`, `caller` or `peer`), and `X-Pravrudhi-Client-Ip-Proof` says whether the gateway's signed-address proof arrived and held (`valid`, `none`, `mismatch`, `no_ip`, `engine_unset`, `engine_short`). Labels only: never the address and never the secret. Added so the client-address check can be confirmed end to end through the gateway.
- **`bad_key_budget_per_minute` (config) and `PRAVRUDHI_BAD_KEY_BUDGET_PER_MINUTE` (environment, wins).** The failed-key attempts allowed per address per minute (#396). Default unchanged: the same number as `rate_limit_per_minute`. Raising it stops a flood of bad keys from refusing valid keyed callers behind a shared gateway address; the anonymous limit is not affected.
- **Partner organisations, API keys, usage counts and the audit log can live in the Supabase database (hosted deployments; #920).** With `PRAVRUDHI_TENANCY_BACKEND=supabase` and `SUPABASE_TENANCY_DB_URL` (a secret reference) they are kept in a separate `tenancy` schema, reached only through its own database role, instead of the engine's local disk, so a key, its usage and its audit rows no longer disappear when the engine restarts. Only the hash of a key's secret is stored; the secret is checked in the engine. A key row is cached for up to 15 seconds (`PRAVRUDHI_TENANCY_KEY_CACHE_S`; 0 turns the cache off); a key revoked on one engine stops working there at once and on others within that time. If the database cannot answer, a keyed call gets `503 key service unavailable` (never a "wrong key" answer and never access), and the hosted setting never falls back to local files. Usage counts and audit rows are written in the background and may be dropped, and counted, if the database is down. `GET /api/health` gains `tenancy_store` (`file`, `ok`, `unavailable` or `checking`: a label only). Without the setting nothing changes: a local engine keeps its files.

### Fixed

- **A partner key now works on the partner routes (caller-visible).** In the hosted deployment a partner API key is accepted on `analyse-facts`, the asynchronous jobs, the assisted route, `verify-citations`, intake, the audit log and the usage of the organisation's own key. The organisation and key admin routes now accept the provisioning secret in the hosted deployment. The offence registry is read-only and is reachable without a key where the deployment allows anonymous demo access; a key is not required for it. `GET /api/v1/status` still needs a signed-in user. A bad or revoked key gets 401 and is never treated as anonymous, and another organisation's key on the usage route gets 403.
- **`GET /api/v1/audit` no longer answers 500 for a key that has called the assisted route (#422, #430, caller-visible).** The assisted route's audit rows carry the mode `assist`; the audit page's `mode` enum did not declare it, so the whole page failed. The enum now reads `sync`, `job`, `verify`, `assist`, `intake` and `intake_assist` (values added to the public OpenAPI response enum: a client generated with a closed enum must accept the new values). A guard test compares every mode the engine writes with the declared enum.

## 0.6.6.1 (2026-10-14)

PATCH release on 0.6.6, assisted route only: a screening or hearing pack over about five contracts can now finish with the assistant, and a run that does not finish says where it stopped. It carries exactly two changes, #432 and #433, and nothing else from main. **Rebuild the image.** The changes are under `src/` and `configs/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.6.1.

### Changed

- **The assisted route allows a longer run (#433, caller-visible).** The assistant's wall limit (`call_timeout_s`) is 150 s, up from 60 s, and its stream-event cap (`max_events`) is 1000, up from 400. A screening or hearing pack over about five contracts took about 57 s of agent turns (dev stack measurement), so 60 s left almost no margin and the run fell back to the ordinary result with `assistant.used: false`; it can now finish with the assistant. A run that still exceeds the limit falls back exactly as before. The route stays synchronous.

### Added

- **A run that did not finish records where it stopped (#432, caller-visible).** On such a run the response's `assistant` block carries `progress`: `tool_calls`, `refused_calls`, `events`, `max_events`, `elapsed_s`, `wall_limit_s` and `last_tool`. Counts, seconds and one tool name only (a name that is not a plain identifier is not echoed); never text. It is absent when no session ran. Building it cannot turn a clean fallback into an error.

## 0.6.6 (2026-10-14)

PATCH release: the assisted route now hands the pinned product agent its earlier results by reference (#423), an accepted job waits for a free agent slot (#412), and the anonymous verify-citations 429 no longer tells a caller to sign in (#413). **Not every existing route behaves exactly as in 0.6.2** (true since 0.6.4): see the caller-visible lines below.

**Rebuild the image.** The changes are under `src/` and `configs/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.6. The release build passes `--build-arg REQUIRE_CITATION_INDEX=1`, as for 0.6.5.

### Added

- **By-reference hand-over for the assisted route (#423).** `A0RefDriver` (`src/pravrudhi/application/a0_assist/ref_driver.py`) runs the pinned product agent's by-reference tools: every tool result carries a driver-assigned `call_ref`, later tools take refs, and the driver resolves them to the stored outputs with their sha256 re-checked, so the agent cannot alter a stage output between calls. A refused call is recorded with its code and the agent may call again. The screening route uses it; a copy-style agent still falls back to the unassisted result. The agent id and version are unchanged.

### Changed

- **An accepted job waits for a free agent slot (#412, caller-visible).** A job accepted with 202 stays `pending` until it holds a slot and waits for one up to `job_slot_wait_s` (600 seconds in the shipped `configs/partner_api.yaml`, capped at one day), then fails with `agent_at_capacity`. The synchronous route never waits (503 at once). A value of 0 means jobs do not wait either.

- **`/api/v1/verify-citations` 429 wording (#413, caller-visible).** The `message` of its anonymous 429 no longer says to sign in: that route knows only API keys, so it now reads "You have reached the limit for callers without an API key ({limit} requests per minute). Try again in {retry} seconds, or use an API key to get your own limit." The limit is the same per-address limit (the same limiter as analyse-facts, 6 per minute per address in the shipped config); the analyse-facts text, the machine fields and `Retry-After` are unchanged.

## 0.6.5 (2026-10-14)

PATCH release: the hosted citation check gets its metadata-only index and a stricter existence-only answer (#398, #401, #402); failed API keys are throttled per address (#396) and a 429 says what to do (#410); a second-check decline refers (#407); a second RunPod endpoint can serve as the fallback second judge (#405, off until configured); and the assisted route is switched ON in the shipped configuration (#408; it still serves only when the store is reachable, the delete canary has passed and the tier is available, and the app's entry stays behind its own flag). **Not every existing route behaves exactly as in 0.6.2** (true since 0.6.4; the changes below are what a caller can see).

**Rebuild the image.** The changes are under `src/`, `configs/`, `deploy/` and `docs/api/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.5. The Dockerfile default (`REQUIRE_CITATION_INDEX=0`) only warns when the slim citation index is missing; the release build passes `--build-arg REQUIRE_CITATION_INDEX=1`, so a build without the index fails instead of quietly answering 503.

Citation lookup without a quote (existence only), and the metadata-only index the hosted engine serves (#908, #717).

### Changed

- **Failed API keys are throttled per address before the hash check (#396, caller-visible).** On `/api/v1/analyse-facts` and `/api/v1/verify-citations`, an address that has presented bad keys at least `rate_limit_per_minute` times in a minute gets HTTP 429 before any password-hash check is spent on it. A valid key is never charged and a request with no key is untouched. Until `PRAVRUDHI_CLIENT_IP_SECRET` is configured, the shared proxy address is the bucket, so valid keyed callers behind it can also be limited for that minute. This ends the unthrottled garbage-key 401 noted in 0.6.4.

- **The 429 body says what to do (#410, caller-visible).** The per-address and per-key limiters now add `message`, `limit_per_minute`, `window_seconds`, `retry_after_seconds` and `scope`. `detail` stays `"rate limit exceeded"`, `Retry-After` is kept, and the anonymous limit of 6 per minute per address is unchanged.

- **A second-check decline no longer reads as a missing ingredient (#404, caller-visible).** In deployments with a second check, an ingredient that one check supported and the second declined now returns `REFER_TO_LAWYER` with `reason` `second_judge_not_confirmed` where it returned `ABSTAIN` with `missing_element`. Only required ingredients count (a second check declining a defeating fact is unchanged), only where the contract would otherwise abstain, and every other referral reason keeps its precedence. The value is added to the public `reason` enum; no existing value changes.

- **Existence-only answers are stricter (#717).** `POST /api/v1/verify-citations` without a `quote` answers `IN_INDEX` only when exactly ONE case survives the year window and the party-title test and its title carries no distinctive word beyond the cited parties' (`EXISTENCE_MAX_EXTRA_TITLE_TOKENS`, 0). Otherwise it answers `NOT_IN_INDEX`, which now also means "several cases match" or "the only match has a longer title": no evidence either way, never a finding that the citation is wrong. On a 400-citation sample of the dev index, 154 answered `IN_INDEX` before and 124 do now; the other 30 are `NOT_IN_INDEX`. A short config list (`assets/citation/nondistinctive_tokens.json`, 89 words, 80 of which the matcher does not already ignore: Indian state and union-territory names, party-status and corporate-suffix words) keeps words like "Madhya Pradesh" (for "M.P."), "Etc", "Dead by Lrs" or "Ltd" from counting as extra title words; an entity word such as "Industries" is never on it. A missing or unreadable list is the strict rule (no words free). A lookup WITH a quote is unchanged (the verbatim check still stands). This closes the documented gap where a different case whose title only adds words to the cited parties' could be found without a quote.

- **`NOT_IN_INDEX` wording (caller-visible).** The human `label` is now "Not found in our index" (was "not in index") and the `note` reads "We could not match this citation to exactly one case in our index. That does not show whether the citation is real: the index does not hold every judgment." (was "The case was not found in our index. …"): under the #717 rule `NOT_IN_INDEX` also covers several matches and a longer title, so the old note was inexact. The machine fields (`result`, `status`, the enum values) are unchanged.

### Added

- **A second RunPod endpoint can be the fallback second judge (#405).** Off unless configured. The funded key goes to a fallback only if its host equals an exact host built from our endpoint ids; redirects never carry the key; an endpoint needs its own passing positive-control record before the engine will call it. Nothing changes until `NYAYA_SECOND_JUDGE_FALLBACK_URLS` and the matching endpoint-id and record settings are set.

- **The assisted route is on in the shipped configuration (#408).** `configs/assistant_tier.yaml` now carries `assist_route_enabled: true` and the product agent ids (ids only, no secret). The route still needs the spend store reachable, the delete canary passed and the tier available.

- **The slim citation index is baked into the image at build time (#401).** `deploy/docker/Dockerfile` copies `slim_all.sqlite3` (sha256-pinned; a different file fails the build) and sets `PRAVRUDHI_CITATION_INDEX`. The file is not in the repository.

- **Slim citation index builder.** `scripts/build_slim_citation_index.py` (`pravrudhi.application.case_index_slim`) writes a metadata-only copy of the case index: titles, courts, years and mined citation strings; no judgment text and no source path (10.8 MB for 38,657 judgments, against 2.0 GB). Its `meta` table marks it `existence_only`, records the source index's modification time (`source_mtime_utc`), the sources, the counts and the provenance record's sha256. An optional source filter builds a one-source copy.

## 0.6.4 (2026-10-14)

PATCH release: the assisted-screening backstop works under its own narrow database role (#399), and the public API gains two caller-visible changes (#394, #395). **From 0.6.4 not every existing route behaves exactly as in 0.6.2** (0.6.3's statement that it did stops being true here): the rate-limit bucket change (#394) and the `quote_not_served` answer (#395) are what a caller can see. The assisted route stays dark.

**Rebuild the image.** The changes are under `src/` and `docs/api/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.4.

### Fixed

- **Backstop sweeper no longer alerts on every run (#399).** In backstop mode the sweeper called `abandon_stale` and `sweep_ok`, which belong to the engine role; the sweeper role is granted only `list_pending`, `mark_deleted`, `prune`, `canary` and `spend_summary`, so every backstop and canary pass failed with a store error and exited 2. Those two calls now run only in engine mode. The database roles and grants are unchanged.

### Changed (what a caller sees)

- **Rate-limit bucket (#394).** Behind the gateway, a request with no signed client address is bucketed by its caller identity (an API key, or a signed-in user) instead of by the shared proxy peer; anonymous callers keep the shared peer bucket. An unsigned client-address header is never read. Applies to `/api/v1/analyse-facts` and `/api/v1/verify-citations`; signed-in users now each get their own per-address bucket.
- **Known limit (the bad-key 429 is held, #396).** A garbage API key on `/api/v1/verify-citations` still answers 401 without being throttled per address in 0.6.4; the 429 for bad-key floods ships in a later release.
- **`verify-citations` with a quote against an existence-only index (#395).** The answer is HTTP 422 `{"error": "quote_not_served"}` (documented in `docs/api/openapi-v1.json`); a request without a quote is unchanged, and an unreadable index metadata table answers 503.

## 0.6.3 (2026-10-14)

PATCH release: the optional assistant tier ships DARK. The engine gains the code, configuration and backstop tooling for an Anthropic-backed assisted screening path, but the assisted route is disabled by default: `POST /v1/analyse-facts/assist` answers 404, is the only new route outside the public OpenAPI contract, and `assist_route_enabled` is false. Screening, its results and every existing route behave exactly as in 0.6.2. No existing field, reason code or route changes meaning.

**Rebuild the image.** The changes are under `src/`, `configs/` and `deploy/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.3. The image now installs `pravrudhi[assist]` (`pg8000==1.31.5`, imported lazily and never a core dependency) and ships the public Supabase root CA at `/opt/pravrudhi/certs/supabase-prod-ca.crt` (sha256 pinned as a build argument; the build fails on a mismatch).

### Added

- **Assisted screening path, disabled by default (#390, #391).** The assistant drives the same engine stages through custom tools, and the verdict a firm sees is always the engine's own decision result; the route stays off until it is switched on in a later, separately recorded step. Per-request consent is required, and a failed, slow or over-budget assistant falls back to the core result.
- **Spend and session store.** A small Postgres store (schema `assist`; ids, integer cents and timestamps only, never facts or session content) keeps a per-session budget and a per-day ceiling (4.00 USD) and records each assistant session so it can be deleted. It is reached over TLS verified against the pinned CA, with a narrow login role.
- **Backstop and canary.** `python -m pravrudhi.application.a0_assist.backstop` (with `--canary`, `--prune`, `--spend`) deletes any assistant session still pending after one hour (a pending session older than two hours raises an alert), runs the standing privilege canary, and prints counts only; two systemd unit pairs for the host (15 minutes; hourly) are in `deploy/systemd`.

- **`GET /api/assistant` (new public route, in the OpenAPI contract).** It returns what the app needs for the consent box: the provider name, the per-call timeout, the per-session budget (`session_budget_usd`), that consent is required, and the `assistant_tier` state and reason; signed-in callers also get the age of the oldest pending delete and of the last sweep (cents and ages only, never session content).

### Changed

- **`assistant_tier` reasons.** `upstream_error` now also covers the assistant's guard pauses: a spend store that is unreadable or not initialised, a canary that failed, never ran or is stale, a stale sweep, too many or too-old pending deletes, and a failed startup workspace check. The field's shape is unchanged.
- **`configs/assistant_tier.yaml`** carries the new limits (`session_budget_usd` 0.25, `daily_ceiling_usd` 4.00, store and CA settings). `/api/health` keeps `{state, reason}` for `assistant_tier`; nothing here changes screening.

## 0.6.2 (2026-10-14)

PATCH release: a faster verify-citations lookup (#380), and one additive field on `/api/health` for the optional assistant (#122, engine half). No existing field, reason code or route changes meaning. No call to Anthropic's session or message endpoints exists in the engine in this release (the only Anthropic request is the existing key check on the models endpoint); the assisted route ships later under its own release.

**Rebuild the image.** The changes are under `src/` and `configs/`, so they reach a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.2.

### Changed

- **verify-citations: the year of each candidate case is resolved with one batched lookup per candidate set instead of one full scan per case (#380, #387).** A derived `case_year` table is used only while `case_year_meta` matches `max(rowid)` of `cases`; otherwise the previous path runs. Answers are unchanged: 12,000 calls on 2,000 citations were compared with 0.6.1; the only differences were 5 calls that reached the 60 s limit on the new engine under load and gave the same answer when re-run, and 597 calls where 0.6.1 itself reached the limit have no comparison. Median lookup time in the #380 timing summary fell from 2.74 s to 0.29 s on one citation set and from 0.27 s to 0.15 s on another.

### Added

- **`/api/health` gains the additive field `assistant_tier` (#122, #388):** `{"state": "available" | "unavailable", "reason": null | "no_key" | "upstream_error" | "quota" | "budget_floor" | "disabled"}`. It reports whether the optional Anthropic-backed assistant can be offered right now: unavailable when the configuration switches it off or cannot be read, when no configured key variable is set, when a recent Anthropic-backed call got a 4xx/5xx/quota answer, or when a configured balance is below the floor. `frontier_reader_available` is unchanged and stays false in every state. A client that does not know the field ignores it.
- **`configs/assistant_tier.yaml`** (shipped in the wheel): the switches and limits behind that field, including a live-read override (`force_unavailable`) and the timeout and per-session budget any future assistant call must use.

## 0.6.1 (2026-10-13)

PATCH release: one fix to the quote check. Nothing else changes; the only addition is an audit-record field `match` on the quote_check step; no response field, reason code or route is added or changed.

**Rebuild the image.** The change is under `src/`, so it reaches a running container only by a rebuild of the engine image and the RunPod worker template from 0.6.1.

### Fixed

- **quote_check / locate_quote: a quote copied from hard-wrapped or indented text is no longer rejected for whitespace (#876, #385).** The strict exact find still runs first, so every result that was valid before is unchanged. Only when the strict find misses is the quote searched with whitespace runs collapsed on both sides, and the start and end offsets are mapped back to the raw fact. Case, punctuation and zero-width characters are not folded, and a quote that differs by a real word still fails. Reason: the shipped configuration serves collapsed facts to the judges (#369), so a model quote copied from a source with line breaks or indentation was rejected by an exact find on the raw fact. A quote that occurs exactly once in the raw fact is still accepted even if the collapsed fact has it twice (unchanged behaviour).

## 0.6.0 (2026-10-12)

MINOR bump (includes the #379 safety fix): one additive response field (`citation_note`) and one additive `reason` value (`input_too_long`); no existing field changes meaning or type.

### Fixed

- **verify-citations: a typed name with no distinctive word no longer acts like a bare citation (#379).** When the party names typed before a citation consist only of initials, one- or two-letter pieces and common words, the name test could not apply and a quote found in exactly one candidate of a shared citation key returned VERIFIED, even for a different case. The CONFLICT now stays. A bare citation (no typed name) and a name with distinctive words behave as before. The agent's `verify_citation` tool now has the same 5 s per-lookup deadline as the API route; running out is a typed error (`CitationLookupTimeout`), never a verdict. A typed name with distinctive words that fits both candidate cases can still return VERIFIED for the case that holds the quote (#373 as decided); this fix covers names with no distinctive word.
- **verify-citations: fewer false CONFLICTs on exact-text citations (#823).** (1) The party names typed in front of the citation ("A v. B, (1977) 3 SCC 247") are now used as the claimed name (before, only an explicit `claimed_name` argument was; the route never passed one). (2) A CONFLICT is kept only while at least TWO candidates survive both the name test and the year test (the case's decision year within one of the citation's year) and the exact quote is not found in exactly one of them; a quote found in exactly one surviving candidate is VERIFIED. A surviving alias group that resolves to no case, nothing surviving, and an empty quote all keep the CONFLICT (#331, unchanged). No other status changes. Tests use new constructed fixtures.

### Added

- **Screening signal per element (#832 S1).** A new response field `screening_signal` (`{supported: bool, label}`, null when the screening judge produced no score): whether the FIRST (screening) judge's own score cleared its threshold, shown even when the element is not a proof. A suggestion to check, never a finding: it carries no probability, never changes status, outcome or reason, and is from the screening judge alone (screening signal, CAL CHEAT set of 70 rows, dev stack, one look: accepted 8 of 41 established and 4 of 29 not-established rows). Additive; default behaviour unchanged.
- **Existence-only citation check (#832 E1/E2).** `POST /verify-citations` accepts an absent or null `quote` and then answers `IN_INDEX` ("Found in the index (existence only): no quote was checked, so this is not a verification."); a blank or whitespace-only quote is a 422. The response carries a `coverage` object (null if it cannot be read): `courts` (list of courts a citation can resolve to), `resolvable_cases`, `year_min`/`year_max` over those resolvable cases, plus `judgments_held`, `courts_held`, `courts_resolvable` and `held_year_min`/`held_year_max` for everything held; cached per index file mtime. Additive; a supplied quote behaves exactly as before.
- **Set versions and frontier-reader flag (#832 S7, F1-lite).** `POST /analyse-facts` returns `contract_set_version` (SHA-256 over the sorted contract id and source-text hash pairs; order-independent, null if the sources cannot be read) and `validated_set_version` (SHA-256 over the `validated_contracts` allowlist). `GET /health` returns `frontier_reader_available: false` (no frontier reader exists yet; a provider field arrives with batch 2). Additive.

- **Wording: an element's citation says what it is (#813).** A new response field `citation_note` (additive, null when the element is not established) is chosen by
  `quote_source` alone: `model` (a judge-written quote that passed the word-for-word quote check) or `whole_fact` (the house judges: "Cites your fact Fn in full (the judge
  names the fact; it does not quote words)."). The `all_elements_established` reason and the `quote_check` `ok` row in `docs/api/reason-codes.md` no longer say the judge quoted
  words from your facts for every source, and `docs/api/reason-codes.md` has a `citation_note` section. Five more rows no longer say the judge quoted on the house-judge path (R1): `denial_established` ("...and cited it"), `gate1_unavailable` / `gate1_not_entailed` / `gate1_contradiction` ("a separate check of the cited fact against the claim"; the last: "found the fact contradicts it"), `denial_unquotable` ("could not cite it in a way we can check"); the `reason` codes themselves are unchanged. No verdict changes. `openapi-v1.json` gains the field. **Schema note:** `ElementResultOut` is response-only; `citation_note` is a computed field, so the schema's `required` list for it grows by one. A strict client that validates responses against the OLD schema with `additionalProperties: false` would reject the new field; regenerate the client or relax that check.

### Changed

- **Serve-time fact collapse is ON in the shipped configuration (#375, flag from #371).** `configs/nyaya_agent.yaml` sets `house_judge.collapse_facts: true`: the fact texts under `Available facts:` are whitespace-collapsed in the judge prompt (the quote check still uses the raw fact). The code default stays OFF, so a config without the key is unchanged; the typed layer refuses the key. A deployment's second-judge block must set the same key. It counts for production only after the 0.6.0 release and a passing production E2E. Evidence is thin: #815 (CAL, one look, dev stack, not independent; a weak test, not a safety result): Config C accepted 0 of 26 not-established rows with collapse on and off (exact upper bound 10.9%); the 4B alone accepted 4 of 26 in both; established rows accepted 8 of 38 off, 9 of 38 on.
- **Behaviour change: a partly configured second judge refuses to start (#361, #362, #363).** `run_gateway.sh` forwards the second judge's `NYAYA_SECOND_JUDGE_*` env into the engine containers. A `second_judge` block that carries any of `base_url`, `model`, `tau` or `timeout_s` counts as an attempt to turn the second judge on, and the engine then refuses to start unless all seven required keys are set: `base_url` (`NYAYA_SECOND_JUDGE_BASE_URL`), `tau` (`_TAU`), `timeout_s` (`_TIMEOUT_S`), `statute_chars` (`_STATUTE_CHARS`), `top_logprobs` (`_TOP_LOGPROBS`), `max_tokens` (`_MAX_TOKENS`) and `label_mass_floor` (`_LABEL_MASS_FLOOR`), each in the config or the env (the code comment says the ones not set by env are inherited from the `house_judge` block). A blank, whitespace-only, empty-list or empty-mapping value counts as missing. `refer_logit_delta` (`_REFER_LOGIT_DELTA`) is a tuning key and is optional; a block that carries only tuning keys is left alone. A deployment that sets none of the second-judge keys is unchanged.
- **Behaviour change: a judge input too long for the judge is a REFER, not an error.** A prompt longer than the configured
  `house_judge.max_input_chars` (`NYAYA_HOUSE_JUDGE_MAX_INPUT_CHARS`; second judge `NYAYA_SECOND_JUDGE_MAX_INPUT_CHARS`) is refused
  before the judge call, and a judge server's own context-length 400 maps to the same result: the contract becomes
  `REFER_TO_LAWYER` with the new reason `input_too_long` (additive public value: the contract `reason` enum in `openapi-v1.json` goes
  from 15 to 16, and `docs/api/reason-codes.md` has the plain-words row; a client with a strict enum validator needs the new schema).
  The committed limit, 10,800 characters, fits the 4096-token judges; a 2048-token deployment sets its own (7,600, the longest input known to succeed on that window). Not covered: the
  typed-layer judge path. `application/nyaya_judges.py`, `application/nyaya_agent.py`, `configs/nyaya_agent.yaml`.

## 0.5.46

Stable error codes for the partner API's 503s, the citation lookup's status contract and its wrong-case fixes, the demo export's
hardening, and the personal-data scrub. Nothing here changes a correctly configured deployment except the items marked
**behaviour change**. `PRAVRUDHI_CITATION_INDEX` must stay UNSET on every production and partner-facing engine: with it unset,
`POST /api/v1/verify-citations` answers 503 `citation_index_unavailable`. Issue #717 (a same-year wrong case can still verify) is not
closed by this release.

**Rebuild the image.** Engine entries under `src/` or `configs/` reach a running container only by a rebuild: rebuild Studio, the
engine containers and the RunPod worker template from 0.5.46. Entries marked tests, docs, CI or tooling do not ship in the image.

### Image: engine behaviour

- **Behaviour change: stable error codes for 503s; no exception text in responses or stored jobs.** `api/partner.py`. (#318, #319)
- **verify(): an empty quote never verifies; a multi-candidate key is disambiguated by claimed party names and year before CONFLICT;
  typographic quote and dash variants fold; the lookup considers only candidates within one year of the cited year.** (#331, #335)
- **Behaviour change (new response fields): `POST /api/v1/verify-citations` adds `status`, `label`, `verified`, `preview`** (additive;
  `result` and `note` unchanged; VERIFIED reads "word for word apart from line breaks, quote marks, dashes and spacing"; no case key).
  `application/citation_status.py`, `openapi-v1.json`. (#335)
- **The NOT_IN_INDEX note no longer uses the word "fake":** "The case was not found in our index. That does not show whether the
  citation is real: the index does not hold every judgment." (#334)
- **Behaviour change (deployment): seat identities now come only from local configuration.** `agents/seat_identity.py` takes seat
  addresses from the environment (`PRAVRUDHI_SCRIPTED_CLAUDE_EMAIL`, `PRAVRUDHI_CLAUDE_CLI_EXPECTED_EMAIL`) or from
  `~/.config/pravrudhi/seats.local.yaml`, and raises `SeatIdentityMissing` wherever an identity is needed; `configs/seats.yaml` now
  holds placeholders. **A deployment that runs scripted `claude` / CLI seat checks must supply that file or those variables before
  upgrading.** Also a CI personal-data guard. (#327)
- **Typed Clef adapter and grounding backend (additive, off by default).** (#316, #317)

### Image: demo export (not served by the engine routes)

- **Behaviour change: the demo export decodes before checking (percent, HTML entity, `\u` escapes, NFKC, zero-width characters),
  REQUIRES a private-name list (`PRAVRUDHI_DEMO_PRIVATE_NAMES` and `PRAVRUDHI_DEMO_PRIVATE_NAMES_SHA256`; the export fails closed
  without them) and drops captured-material keys.** `publish.py`, `demo_export.py`. (#336)

### Tests, docs, CI, tooling (not in the image)

- Demo-export tests use invented identifiers only; the usage-gate stale-reading test pins its clock. (#340, #342)
- Evidence renderers: bases keyed by track, model and metric, and a tiny exact p prints as `< 0.001`. In the image (`external.py`,
  `paper_data.py` ship), but not on a served route: the served `external_rows()` is unchanged and the dedupe is in the paper and
  evidence renderers. (#333)

## 0.5.45

Partner API additions (a citation lookup route, a clear "judges are off" answer, a typed `quote_check` and published reason
codes), the legal-MVP route closures, a corpus-notice change and new tool-runner modules. Nothing here changes a correctly
configured deployment except the items marked **behaviour change**.

**Rebuild the image.** Every engine entry below is under `src/` or `configs/`, so none of it reaches a running container by a
restart or a pip upgrade: rebuild Studio, the engine containers and the RunPod worker template from 0.5.45. Entries marked
docs, tests, CI or tooling do not ship in the image. Image-relevant status was checked against `git log v0.5.44..main`.

### Image: engine behaviour

- **Behaviour change: the legal-MVP hidden APIs are closed to non-admins in both editions, and the schema and docs pages are
  gated.** `api/roles.py`, `application/route_scope.py`. (#300)
- **Behaviour change (new response): HTTP 503 `{"error": "judges_offline"}` when a needed judge endpoint is parked** (no worker,
  nothing queued or running). It is returned by `POST /api/v1/analyse-facts` and `POST /api/v1/analyse-facts/jobs` (no job is
  created), at once and before anything is queued, with no `Retry-After`. It is distinct from `judge_unavailable` (a judge that
  is down or cold). Controlled by `judge_health_check` (default true) and `judge_health_ttl_s` (default 5) in
  `configs/partner_api.yaml`. It reads each RunPod endpoint's `/health` with the judge's own key
  (`NYAYA_HOUSE_JUDGE_API_KEY`, `NYAYA_SECOND_JUDGE_API_KEY`); when the endpoint cannot be read, or the check errors, the request
  proceeds as before. **An idle scale-from-zero endpoint (min 0, max above 0)
  reads the same as a parked one: set `judge_health_check: false` if a judge is put back on scale-from-zero, and for an
  authorised warm either set min >= 1 or turn the check off for that window, then wait about 5 s for the cache.** Local
  (non-RunPod) judges are never checked. `api/partner.py`, `application/judge_endpoint_state.py`, `configs/partner_api.yaml`. (#312)
- **Partner API: `POST /api/v1/verify-citations`.** A bounded citation lookup over a configured case index: title-only fuzzy scan,
  its own concurrency gate and time bound, per-key rate limit with `X-RateLimit-*` headers, usage metering and one audit row (mode
  `verify`) with no citation or quote text. **It answers 503 until `PRAVRUDHI_CITATION_INDEX` points at a case index.** New
  optional keys in `configs/partner_api.yaml`: `verify_max_concurrent` (default 2) and `verify_timeout_s` (default 5.0). Adds a
  user-facing route and regenerates the contract. `api/partner.py`, `api/roles.py`, `application/verify.py`, `application/statute_citations.py`. (#181)
- **Partner API: typed `quote_check` and published reason codes.** `quote_check` is an enumerated field in the contract,
  `docs/api/reason-codes.md` explains every `reason`, and the three rule-text fields (`rule_text`, `judge_rule_text`,
  `rule_text_source`) exist behind `expose_rule_text`, **which is false by default** (licence hold): with it off the three fields
  are absent from the response. Do not turn it on. `api/partner.py`, `application/nyaya_agent.py`, `application/nyaya_quote.py`,
  `configs/nyaya_agent.yaml`. (#309)
- **Partner API: the contract states what the docs state.** Declared 401/503 and job 404/429 responses, the 4000-character per-fact
  limit (documented in the schema; enforcement and the 422 body are unchanged); the route annotations in `api/partner.py` changed.
  The quickstart additions are docs only. (#287)
- **`/api/nyaya/corpus` carries the India Code notice, and each hit a recorded `source_url`** (an India Code page the corpus recorded
  for the act, else null) and a `source_fallback_url` (the India Code home page). Never a constructed link. `api/nyaya.py`,
  `application/nyaya.py`. (#313)
- **Primary judge tau can be overridden by environment** (`NYAYA_HOUSE_JUDGE_TAU`; a non-numeric value refuses to start), and a swap
  of the primary model by environment (`NYAYA_HOUSE_JUDGE_MODEL` other than the one the yaml tau was set for) is refused without
  an explicit tau; lowering the threshold by environment needs `NYAYA_HOUSE_JUDGE_TAU_ALLOW_LOWER=1`. `application/nyaya_agent.py`.
  (#185)
- **Second-judge positive control: engine trigger wiring, OFF by default.** Only a deployment that sets
  `second_judge_positive_control.record_path` is affected; with none set the second judge serves as before, and the run-start audit
  row records whether the record gate is on. `application/nyaya_agent.py`, `application/second_judge_positive_control.py`,
  `configs/nyaya_agent.yaml`. (#54)
- **Demo-snapshot export publishes an allowlist of product-edition sections and its team-vocabulary markers are wider.**
  `application/demo_export.py`. (#306)
- **A loosening guard for night-loop config candidates.** New module, used by the night loop only.
  `application/nyaya_loosening_guard.py`. (#175)
- **Per-key rate limiter takes an injected clock** (a parameter `rate_clock` on `build_partner_router`; production leaves it unset,
  so behaviour is unchanged). `api/partner.py`. (#304)
- **Tool runner library: a bounded tool-call runner, a citation-verifier tool, calculator and date tools** (Decimal arithmetic from
  the literal's source text; `%` takes the sign of the dividend; the call log carries argument names and a digest, never values).
  New modules, not wired to any route. `application/tool_runner.py`, `application/citation_tool.py`,
  `application/deterministic_tools.py`. (#183, #184, #190)

### Config only (in the image, no code)

- **Usage gate: the #306 B-opus and G-opus arms registered** (`m4-306-b-opus` cap 650, `m4-306-g-opus` cap 152).
  `configs/usage_gate.yaml`. (#302, #305)

### Not in the image

- **Tests and docs only:** the fake-clock 429 tests (#304), the partner quickstart sections on reply fields, rate-limit headers,
  jobs, audit and usage (#287), `docs/api/reason-codes.md` (#309), a handover note (#306).

### Release notes for operators

- Rebuild the image (above). Regenerate a client if one is generated from `docs/api/openapi-v1.json`: the contract gained
  `verify-citations`, `judges_offline`, the typed `quote_check` and the 401/503/job responses.
- `judges_offline` and `judge_health_check`: see the entry above for the scale-from-zero limit and the warm-window procedure. With
  both production judges parked on purpose, callers get this 503 at once instead of a long wait.
- `verify-citations` is 503 until `PRAVRUDHI_CITATION_INDEX` is set.
- `expose_rule_text` stays false.
- The app (pravrudhi-app) gates its pages by edition independently; #300 closes `/api/workspaces`, `/api/notifications` and the
  update routes to members, which the app already tolerates (pravrudhi-app#50).
- Not in this release: #295, #294, #61 (0.5.46) and #299 unless it is signed in time.

## 0.5.44

Fail-closed deployment hardening, release probe and gateway hardening. Nothing here changes a correctly configured
deployment, with one exception: a hosted image with an explicit `PRAVRUDHI_AUTH=disabled` (or auth unset or blank) now
refuses to start. A misconfigured deployment refuses to start instead of running open.

**Rebuild before you deploy.** The hosted-image marker below is baked into images BUILT FROM THIS RELEASE. An image built
from an earlier release has no marker, so none of the hosted-image refusals apply to it. Rebuild Studio, the engine containers
and the RunPod worker template from 0.5.44; do not rely on a pip upgrade or a restart of the old image.

### Boot refusals (breaking for misconfigured environments)

- **Unrecognised `PRAVRUDHI_AUTH` refuses to start.** Only `disabled`, `optional` and `required` (case-insensitive, trimmed)
  are accepted; unset or blank means `disabled` on a LOCAL install only. Before, any other value (`requried`, `off`, `true`,
  a quoted `"required"`) silently meant `disabled`, so every anonymous caller was the operator. In process an unrecognised
  value is treated as `required`. (#282)
- **Hosted engines refuse unset, blank and `disabled` `PRAVRUDHI_AUTH`.** Only `required` and `optional` start on a hosted
  image; there is no opt-in for `disabled`. A hosted image is identified by the file `/etc/pravrudhi/hosted-image`, baked in by the
  Dockerfile and not removable by any environment value (`PRAVRUDHI_HOSTED_IMAGE=0` or blank cannot switch it off; it needs no
  `/.dockerenv`, so it works on containerd, Kubernetes and RunPod). Only images built from this release carry the file. (#282)
- **Unrecognised `PRAVRUDHI_EDITION` refuses to start.** Editions are `studio`, `product` and `dev`. (#282)
- **Unrecognised `PRAVRUDHI_HOSTED_IMAGE` refuses to start, but only on an image WITHOUT the baked marker file.** The value
  is true (`1`, `true`, `yes`, `on`) or false (`0`, `false`, `no`, `off`). On an image that has `/etc/pravrudhi/hosted-image`
  the file decides and this variable cannot change the outcome. (#282)
- **One resolved edition.** The edition used by the routes, the whole-surface Studio gate and the vendor carve-out is now a single
  function. An unlabelled container resolves to the product ONLY when it is recognised as hosted (the baked marker, a true
  `PRAVRUDHI_HOSTED_IMAGE`, an older image's local-guard-off container, or a release install); a plain unlabelled container or a source checkout still resolves to `dev`. A hosted or release install
  can therefore no longer serve the Studio routes without the gate. (#282)

### Deployment

- **The gateway pins `PRAVRUDHI_AUTH=required` for the product container as well as Studio** (`-e`, after `--env-file`), so the
  product engine cannot be set to `AUTH=optional` or weakened through `chat.env`. (#283, #266)
- **`STUDIO_HOLD_KV=1`** starts the Studio tunnel without writing `engine_url_studio` to KV; the flag is read on every start, so keep
  it in `gateway.env` or the unit and in the restart checklist. (#279, #281)
- **Do not quote values in `chat.env`.** Docker keeps the quote characters, so `PRAVRUDHI_AUTH="required"` reaches the engine as
  `"required"` including the quotes and now refuses to boot (before it silently meant `disabled`).

### Engine

- **Hosted image: unavailable agents say "hosted image: agents run on the host"** instead of "CLI not installed" / "needs xvfb".
  The image sets `PRAVRUDHI_HOSTED_IMAGE=1`. (#277)
- **Request context reaches the worker threads.** The ContextVars of a request (the serving organisation and the like) are
  carried into the async-job pool and the agent's judge pool, so the serving guards hold inside those threads. (#256, #236;
  `api/partner.py`, `application/nyaya_agent.py`)
- **Tri-state element status in the agent.** The per-element map that feeds the assertions is now tri-state (`True`,
  `False`, `None`): an element nobody evaluated (`not_evaluated_second_unavailable`) can no longer read as a verdict.
  This changes `nyaya_agent`'s per-element status handling only; the wire format and the `assertions` the API returns are
  still booleans, so #56 is not closed for callers that read `assertions`. (#60)
- **Demo-snapshot export refuses to publish private or licensed text.** `demo-export` (`application/demo_export.py`) now
  refuses to write the snapshot if it still carries a private marker (home paths, personal and project emails, seat names,
  relay and team-session text, hostnames and addresses, endpoint ids; any case), or any 64-character window of statute text
  from the corpus; statute text in recorded prompts is replaced by a marker and team chatter strings are dropped whole.
  The public `demo.json` was regenerated with it. (#290, #292, #293)

### Tooling (not in the image)

Of the entries added after the boot hardening (#282/#283), only `api/partner.py`, `application/nyaya_agent.py` and
`application/demo_export.py` change the engine image (#256, #60, #290, #292); the boot-refusal and deployment entries above are
image behaviour too. The items below are scripts and CI and do not ship in the image.

- **Release probe** (`scripts/release_probe.py`): `PROBE_ADMIN_TOKEN` is optional; the admin checks print `SKIPPED` and a run
  without them exits `3` (INCOMPLETE). Exit codes: 0 all passed, 1 a check failed (wins over 3), 2 configuration error or
  unreachable, 3 incomplete. The probe never prints a token, and `PROBE_BASE_URL` must be an origin only. (#278)
- **Partner API smoke script** (`scripts/partner_smoke.py`): anonymous demo paths and a non-admin test key only, read-only
  or deliberately invalid requests, exit codes as the probe. Note that with a key the empty-fact request is metered before
  its 422, and the product Worker may refuse POSTs under its write block. (#288)
- **Scheduled whole-tree guard audit** with an alert that fails closed. (#88)
- **Fail-closed Hugging Face revision verifier** for the house judge. (#117)
- **CI guard for private data in fixtures:** rejects recorded-looking ids, emails, home paths and account fields in test fixtures. (#219)
- **Commit identity checks:** an identity allowlist for the commit hook (author and committer), a pre-push guard, and an
  identity-neutral `make init` (#188); a push-time identity check for all new commits (#224).
