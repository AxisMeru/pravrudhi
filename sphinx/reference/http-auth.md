# HTTP route access: who may call what

This page says **which kind of caller each HTTP route accepts**. The routes themselves (method, path, handler) are in the generated HTTP route reference; the request and response shapes of the partner API are in the OpenAPI file (`docs/api/openapi-v1.json`). Names only: no keys, secrets, hosts or account details appear here. The classification below is the engine's own, kept in `src/pravrudhi/api/roles.py` and `src/pravrudhi/api/identity.py`, and a test fails if a route is added without being classified.

## Who is who

| Caller | How the engine knows |
|---|---|
| **Nobody (open)** | no credential; only the routes in "Open routes" below |
| **Member** | a signed-in account: a verified bearer token (the identity provider's session) |
| **Operator** | a member whose user id or e-mail is on the allowlist named by `PRAVRUDHI_ADMINS`. The role is resolved from that list and is designed not to come from anything the caller sends (a `role` claim in a token is ignored). An empty or unset list names nobody |
| **Partner key** | a request carrying `X-Pravrudhi-Api-Key` that verifies (a key header that does not verify is a 401, never an anonymous call) |
| **Tenancy admin** | a request carrying the tenancy secret header (`X-Pravrudhi-Tenancy-Secret`) for organisation and key management |
| **Waker** | the box-side waker's signed request (`X-Waker-Signature`: a 60-second window, each nonce once); only the two wake-queue routes |

## The switches that change the answer

- `PRAVRUDHI_AUTH` = `disabled` (the default, a local single-user engine): there is nobody to identify, the local caller is the operator, no sign-in is asked. `optional`: a bearer token is verified when present and not required. `required` (every hosted deployment): a gate in front of the whole `/api` surface refuses an anonymous caller with 401 (WebSocket close 4401), except the open routes below. The engine refuses to start in any other mode when a deployment-platform marker is set, and an unrecognised value is treated as `required`.
- `PRAVRUDHI_EDITION` (`product` or `studio`; a released build forces `product`): in the **product** edition the operator-only "engine" routes do not exist (404); in the **Studio** edition every `/api` route, other than the liveness check, answers only the operator (401 without identity, 403 for a member or a partner key).
- `PRAVRUDHI_DEMO_ANON_PATHS`: the deployment may open at most `/api/v1/analyse-facts` and `/api/nyaya/registry/contracts` to anonymous callers; anything else listed there stops the engine from starting.
- Feature flags in `partner_api.yaml` (the `wf_*` workflow switches, `cold_start_wake`, the assisted and intake routes): a route behind a flag that is off does not exist (404) and is not classified until it does.

## Open routes (no credential)

- `/api/health` (liveness and configuration health; no state, no address or secret)
- `/ping` (outside `/api`; the load-balancer liveness check)
- the static interface and every other non-`/api` path
- CORS preflight (`OPTIONS`) passes so a browser can learn the answer
- `/api/v1/analyse-facts` and `/api/nyaya/registry/contracts` **only** if the deployment lists them in `PRAVRUDHI_DEMO_ANON_PATHS`

## Routes with their own credential (no sign-in token needed)

Partner key routes (the gate lets a key-only caller through these exact method and path pairs; a request that also carries an identity token goes through the ordinary check):

- `GET /api/v1/audit`
- `POST /api/v1/analyse-facts`
- `POST /api/v1/analyse-facts/assist`
- `POST /api/v1/analyse-facts/jobs`
- `POST /api/v1/intake`
- `POST /api/v1/intake/assist`
- `POST /api/v1/verify-citations`

- `GET /api/v1/analyse-facts/jobs/{job_id}` (the result of a job started with a key)
- `GET /api/v1/orgs/{org_id}/usage` (an organisation's own key may read that organisation's usage)

Tenancy-secret routes (organisation and key management; the route validates the secret):

- `POST /api/v1/orgs`, `GET` and `POST /api/v1/orgs/{org_id}/keys`, `POST /api/v1/orgs/{org_id}/keys/{key_id}/revoke`, `GET /api/v1/orgs/{org_id}/usage` and `GET /api/v1/orgs/{org_id}/usage/summary`

Waker routes (they exist only when `cold_start_wake` is on; plain 404 otherwise):

- `GET /api/v1/internal/wake-requests`
- `POST /api/v1/internal/wake-results`

## Member routes (sign-in required, any signed-in account)

These answer a signed-in member in both editions (in the Studio edition only the operator reaches them). A bearer token is required in `required` mode; the partner routes among them also accept a partner key where listed above.

- `/api/assistant`
- `/api/me`
- `/api/messaging/telegram`
- `/api/nyaya/corpus`
- `/api/nyaya/registry/contracts`
- `/api/status`
- `/api/v1/analyse-facts`
- `/api/v1/analyse-facts/assist`
- `/api/v1/analyse-facts/jobs`
- `/api/v1/analyse-facts/jobs/{job_id}`
- `/api/v1/audit`
- `/api/v1/intake`
- `/api/v1/intake/assist`
- `/api/v1/status`
- `/api/v1/verify-citations`

The cold-start wake routes (`POST /api/v1/analyse-facts/wake` and `GET /api/v1/analyse-facts/wake/status`) need sign-in and answer "not allowed" (503 `judges_offline`) to an account that may not ask for a wake: with `PRAVRUDHI_WAKE_ANY_SIGNED_IN` set to exactly `1` any verified, non-anonymous account with an e-mail may; otherwise only ids on the `PRAVRUDHI_WAKERS` allowlist. They only exist when `cold_start_wake` is on.

The workflow routes (`/api/v1/...`, one per switched-on `wf_*` flag, for example the matter store, the chronology and the hearing pack) answer a signed-in user or a partner key as each workflow's contract states (the matter store, for one, answers 401 to a caller with neither); they are classified when their flag is on.

## Operator-only routes, both editions (a member gets 403, nobody gets 401)

These exist in the product too, but answer only the operator: they are engine-wide or spend the engine's own hardware and secrets.

- `/api/app-token`
- `/api/chat`
- `/api/chat/stream`
- `/api/chat/threads`
- `/api/chat/threads/{thread_id}`
- `/api/doctor`
- `/api/memory`
- `/api/memory/notes`
- `/api/memory/notes/{note_id}`
- `/api/models`
- `/api/notifications`
- `/api/notifications/read`
- `/api/nyaya/ask`
- `/api/nyaya/asks`
- `/api/nyaya/audit`
- `/api/nyaya/registry/check`
- `/api/nyaya/registry/{contract_id}/elements`
- `/api/nyaya/vendors`
- `/api/objectives`
- `/api/objectives/plan-preview`
- `/api/objectives/{oid}`
- `/api/objectives/{oid}/loom`
- `/api/objectives/{oid}/plan`
- `/api/objectives/{oid}/subagents`
- `/api/panel/vendors`
- `/api/providers`
- `/api/providers/{provider_id}/key`
- `/api/recipes`
- `/api/runs`
- `/api/runs/{run_id}`
- `/api/runs/{run_id}/events`
- `/api/runs/{run_id}/stop`
- `/api/tools`
- `/api/update`
- `/api/update/apply`
- `/api/update/config`
- `/api/update/last-check`
- `/api/update/rollback`
- `/api/workspaces`

## Operator-only routes, Studio edition only (404 in the product)

These are about Pravrudhi improving itself (the ledger and nights, the swarm, the promotion inbox, the fleet, the evidence for its hypotheses):

- `/api/agent-trace`
- `/api/agents`
- `/api/agents/cooldowns`
- `/api/appetite`
- `/api/benchmarks`
- `/api/candidates`
- `/api/candidates/{cid}`
- `/api/diffs`
- `/api/diffs/{task_id}`
- `/api/evidence/{name}`
- `/api/external`
- `/api/fleet`
- `/api/h1/{track}/{nights}`
- `/api/health-state`
- `/api/heartbeat`
- `/api/hosts`
- `/api/inbox`
- `/api/inbox/sign`
- `/api/jobs`
- `/api/jobs/{job_id}/cancel`
- `/api/nights`
- `/api/observations`
- `/api/parity`
- `/api/requests`
- `/api/requests/{rid}`
- `/api/requests/{rid}/advance`
- `/api/requests/{rid}/criteria/{index}/evidence`
- `/api/routes`
- `/api/sandboxes`
- `/api/search`
- `/api/svasthya`
- `/api/swarm`
- `/api/swarm/live`

## The schema and docs pages

`/openapi.json`, `/docs`, `/docs/oauth2-redirect` and `/redoc` describe every route, so they are the operator's in every edition whenever authentication is on (401 for nobody, 403 for a member).
