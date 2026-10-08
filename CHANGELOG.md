# Changelog

Release notes for the engine (`pravrudhi`). Versions before 0.5.44 are described in their GitHub release notes and commit
messages.

## 0.6.0 (unreleased)

MINOR bump: one additive response field (`citation_note`) and one additive `reason` value (`input_too_long`); no existing field changes meaning or type.

### Fixed

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

- **Serve-time fact collapse is ON in the shipped configuration (#375, flag from #371).** `configs/nyaya_agent.yaml` sets `house_judge.collapse_facts: true`: the fact texts under `Available facts:` are whitespace-collapsed in the judge prompt (the quote check still uses the raw fact). The code default stays OFF, so a config without the key is unchanged; the typed layer refuses the key. A deployment's second-judge block must set the same key. Evidence is thin and stated (#815: one look, dev stack, no detected increase in CAL false accepts on 26 scored not-established rows; not a claim of improved coverage); a production E2E check gates the release.

### Changed

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
