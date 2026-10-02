# Design note #230: partner API matters, document upload and streaming analyse

**Status: proposal for Lead-2 to read. No code until it is accepted.** Public repo: hosts are placeholders
(`https://api.example.com`), no client data, no internal paths.

## 0. Starting point

Today's `/api/v1` is synchronous JSON: `POST /analyse-facts` takes 1-8 facts of at most 4000 characters, the
caller splits their own documents, and nothing the caller sends is kept beyond the audit record, which is purged
after `retention_days` (7) and is marked `client_data=true` so no corpus builder may read it (#39). Partners ask for
(a) a *matter* to group work, (b) *uploading a document* instead of splitting it themselves, and (c) not holding a
connection open for minutes. This note proposes routes and, more importantly, the rules that must hold before any
of it ships. Open questions are in section 9.

## 1. Routes and OpenAPI shape (all under `/api/v1`, key in `X-Pravrudhi-Api-Key`)

| Route | Purpose |
|---|---|
| `POST /matters` `{title, retention_days?}` -> `{matter_id, expires_at}` | create a matter owned by the key's org |
| `GET /matters`, `GET /matters/{id}` | list / read (org-scoped) |
| `DELETE /matters/{id}` | delete the matter, its documents and its run records now (204) |
| `POST /matters/{id}/documents` (multipart, one file) -> `{document_id, bytes, sha256, facts: n}` | upload; text is extracted and split server-side into facts |
| `GET /matters/{id}/documents/{doc}` | metadata only (the text only with `?facts=1`) |
| `DELETE /matters/{id}/documents/{doc}` | delete one document now |
| `POST /matters/{id}/analyse` `{contract_ids, document_ids?, proceeding_posture?, stream?: bool}` | run; `stream=false` returns a job (202), `stream=true` returns SSE |
| `GET /matters/{id}/runs/{run}` | the finished result (same body as today's analyse-facts response) |

`POST /analyse-facts` stays exactly as is: no matter, no storage. It remains the recommended path for callers who do
not want the engine to hold their documents. Every new response model is declared with enums in the OpenAPI
contract and covered by the executed-examples test, as for the current routes.

## 2. Upload limits and hostile files

* Types: `text/plain` and `application/pdf` first; DOCX only if asked for (it is a zip, see below). Type is decided
  by content sniffing, never by the filename or the client's `Content-Type`. Anything else is 415.
* Size: 5 MB per file, 20 files and 25 MB per matter (config, per-key override). Exceeded is 413 before the body
  is fully read (enforce the limit while streaming to disk, not after buffering).
* Extraction: facts are capped like today (at most 8 per analysis call, 4000 characters each), so a long document is
  analysed in batches, which is a billing and latency question (section 6). Extracted text above a cap (500k
  characters) is refused, not truncated, so no one gets a verdict on text the engine silently dropped.
* PDF: parsed in a separate low-privilege subprocess with CPU-time, memory and wall-clock limits and no network;
  page cap (200); no JavaScript, no embedded-file extraction; OCR is out of scope (a scan with no text layer is
  422 `no_text_layer`, not a guess).
* Zip bombs: no archive types are accepted before DOCX. If DOCX is added: reject a compressed-to-uncompressed ratio
  over 100, total uncompressed over 20 MB, more than 100 entries, nested archives and any path containing `..`.
* Malware: files are never executed or rendered, are stored with no execute bit and a server-generated name, and
  are served back only as metadata. If the deployment has an AV scanner, scan before extraction and fail closed
  (503 `scan_unavailable`) rather than skip. We must not claim malware scanning if none is configured.
* Prompt injection in uploaded text is already the engine's standing condition (every fact is untrusted input to the
  judge); upload adds no new trust, and the note records that extraction does not sanitise it.

## 3. CLIENT_DATA: no builder may ever ingest uploads

Uploads are the first place the engine holds client documents at rest, so the guard must be structural, not a
convention.

* Every stored artefact (file, extracted text, facts, run record, audit row) carries `client_data: true` set by the
  storage layer; no code path writes `false` for an upload and the API has no field to ask for it.
* Uploads live under a dedicated directory (`<engine_root>/client_data/<org_id>/<matter_id>/`) that no corpus,
  training, evaluation or scouting module is given a path to. Corpus builders take an allow-list of source
  directories; they never glob the engine root.
* A CI test fails if any module under `src/` that builds a corpus or training set opens a path under
  `client_data/` or reads an audit row with `client_data=true` (an AST/grep check, in the style of the existing
  `test_contract_classification_key_is_ci_only`), and a second test asserts every storage write sets the flag.
* Telemetry and logs never contain document text or facts, only ids, byte counts and hashes.

## 4. Per-org isolation, retention and deletion

* Isolation: the path and every query are keyed by the *authenticated key's* `org_id`, never by an id in the URL
  alone. A matter id belonging to another org is 404 (not 403) so existence does not leak. Tests: org A's key
  against org B's matter, document and run ids on every route.
* Retention: a matter has `expires_at` (default 7 days to match today's promise, configurable down per matter, up
  to a deployment ceiling). A purge runs opportunistically on each write, as `purge_stale_runs` does now, plus a
  periodic sweep; the response retention notice is generated from the same config value so it cannot drift.
* Deletion: `DELETE` is immediate and removes files, extracted text, facts and run records; the audit row keeps
  only ids, hashes and counts (no text) so usage and billing stay checkable. Revoking a key does not delete data;
  deleting an org does (operator route, behind the provisioning secret).
* Backups: any backup of the engine volume inherits the retention window, or uploads are excluded from backup
  (section 9).
* At rest: volume-level encryption at minimum; per-org keys are a later option.

## 5. SSE or poll

Recommendation: **poll first, SSE second.** Poll (202, then `GET .../runs/{run}` with `Retry-After`) works through
every proxy, survives client reconnects, is easy to test, and is the shape #193 already builds for async jobs. SSE
(`GET /matters/{id}/runs/{run}/events`, `text/event-stream`, events `element`, `contract`, `done`, `error`, with
`Last-Event-ID` resume) is added only if partners need progress display; it must never be the only way to get a
result. A dropped stream never cancels a run. Events carry statuses and outcomes, not document text.

## 6. Effect on metering and rate limits

* Metering: the unit stays the *analyse call*, counted once when a run is admitted (as #187 does). A document that
  expands to N batches is N calls, stated up front in the 202 body (`calls_charged`). Upload is not an analyse call
  but has its own per-key limit and a storage quota.
* Rate limits: separate limiters per key for analyse (existing), upload (calls and bytes per minute) and concurrent
  streams (small cap, e.g. 2), so one slow stream cannot starve the judge (`max_concurrent`). All return 429 with
  `Retry-After` and the `X-RateLimit-*` headers from #229.
* Capacity: queued runs are bounded; beyond the bound the caller gets 503 `at_capacity` with `Retry-After`.

## 7. Slicing

1. This note (accepted or amended by Lead-2).
2. Matters + text-only upload + poll, with isolation, retention, deletion and the CLIENT_DATA guard tests. No PDF.
3. PDF extraction in a sandboxed subprocess.
4. SSE, only if a partner needs it.
5. DOCX, only if asked for.

Each slice is its own PR with its own OpenAPI regeneration and executed examples.

## 8. Risks

Storing client documents changes the data-protection posture (controller/processor roles, a deletion SLA, breach
duties); that is a legal/operator decision, not an engineering one. Long documents multiply judge cost and latency.
Extraction quality (a bad PDF parse) can change a verdict, so extracted facts must be returnable for the caller to
check.

## 9. Questions for Lead-2

1. Is storing client documents in scope for the MVP, or should slice 2 stop at "matters hold facts the caller
   already split" (no files)?
2. Default retention (7 days) and the deployment ceiling.
3. Backups: excluded, or bound by the same window?
4. Is an AV scanner available on the host, or do we ship without a malware claim?
5. PDF in or out of the first release?
