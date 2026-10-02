# Design note #230: partner API matters, document upload and streaming analyse

**Status: revised after Lead-2's answers (section 9); items marked "proposed, pending operator" are not decided. No code until Lead-2 has read this revision and the operator has answered the retention and backup terms.** Public repo: hosts are placeholders
(`https://api.example.com`), no client data, no internal paths.

## 0. Starting point

Today's `/api/v1` is synchronous JSON: `POST /analyse-facts` takes 1-8 facts of at most 4000 characters, the
caller splits their own documents, and nothing the caller sends is kept beyond the audit record, which is purged
after `retention_days` (7) and is marked `client_data=true` so no corpus builder may read it (#39). Partners ask for
(a) a *matter* to group work, (b) *uploading a document* instead of splitting it themselves, and (c) not holding a
connection open for minutes. This note proposes routes and, more importantly, the rules that must hold before any
of it ships. Lead-2's answers and the remaining operator items are in section 9.

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

* Types: a strict allowlist of `text/plain`, DOCX and `application/pdf`; everything else is 415. Type is decided
  by content sniffing, never by the filename or the client's `Content-Type`. No macros, no archives (other than the
  DOCX container itself), no embedded-object extraction.
* Size: 5 MB per file, 20 files and 25 MB per matter (config, per-key override). Exceeded is 413 before the body
  is fully read (enforce the limit while streaming to disk, not after buffering).
* Extraction: facts are capped like today (at most 8 per analysis call, 4000 characters each), so a long document is
  analysed in batches, which is a billing and latency question (section 6). Extracted text above a cap (500k
  characters) is refused, not truncated, so no one gets a verdict on text the engine silently dropped.
* PDF: text layer only, extracted with `pdfminer.six` (already a dependency), in a separate low-privilege
  subprocess with page (200), size, CPU-time, memory and wall-clock limits and no network; no JavaScript, no
  embedded-file extraction. There is no OCR: a PDF with no text layer is refused with 422 `no_text_layer` and a clear
  message, not guessed at.
* DOCX (a zip container): reject a compressed-to-uncompressed ratio over 100, total uncompressed over 20 MB, more
  than 100 entries, nested archives, macro-bearing content (`vbaProject.bin`) and any path containing `..`. Only the
  main document text part is read; embedded objects and external relationships are ignored.
* Malware: files are never executed or rendered, are stored with no execute bit and a server-generated name, and
  are served back only as metadata. **There is no AV scanner in the MVP** (Lead-2). The defence is the strict type
  allowlist, content sniffing and the sandboxed extractor above. The docs state explicitly: **no malware-scanning
  claim**. Revisit if a partner requires scanning; a scanner, if added, fails closed (503 `scan_unavailable`).
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
* Retention (**proposed, pending operator**): a matter has `expires_at`; default 30 days, ceiling 90, configurable
  down per matter. (Today's analyse-facts audit record keeps its own 7-day window; the notice text must say which
  applies to what.) A purge runs opportunistically on each write, as `purge_stale_runs` does now, plus a
  periodic sweep; the response retention notice is generated from the same config value so it cannot drift.
* Deletion (**proposed, pending operator**): hard delete on request, immediate, with an audit record of the delete.
  `DELETE` removes files, extracted text, facts and run records; the audit row keeps
  only ids, hashes and counts (no text) so usage and billing stay checkable. Revoking a key does not delete data;
  deleting an org does (operator route, behind the provisioning secret).
* Backups (**proposed, pending operator**): client uploads are NOT backed up, so a delete is real; derived
  facts and results follow the same retention as the matter.
* At rest: volume-level encryption at minimum; per-org keys are a later option.

## 5. SSE or poll

Decision (Lead-2): **poll first, SSE last.** Poll (202, then `GET .../runs/{run}` with `Retry-After`) works through
every proxy, survives client reconnects, is easy to test, and is the shape #193 already builds for async jobs. SSE
(`GET /matters/{id}/runs/{run}/events`, `text/event-stream`, events `element`, `contract`, `done`, `error`, with
`Last-Event-ID` resume) comes last in the order (section 7); it must never be the only way to get a
result. A dropped stream never cancels a run. Events carry statuses and outcomes, not document text.

## 6. Effect on metering and rate limits

* Metering: the unit stays the *analyse call*, counted once when a run is admitted (as #187 does). A document that
  expands to N batches is N calls, stated up front in the 202 body (`calls_charged`). Upload is not an analyse call
  but has its own per-key limit and a storage quota.
* Rate limits: separate limiters per key for analyse (existing), upload (calls and bytes per minute) and concurrent
  streams (small cap, e.g. 2), so one slow stream cannot starve the judge (`max_concurrent`). All return 429 with
  `Retry-After` and the `X-RateLimit-*` headers from #229.
* Capacity: queued runs are bounded; beyond the bound the caller gets 503 `at_capacity` with `Retry-After`.

## 7. Slicing (order set by Lead-2: poll, upload txt/docx, PDF, SSE)

1. This note (revised; Lead-2 reads, operator answers items 4-5 of section 9).
2. Matters + poll route (202, then `GET .../runs/{run}`) on facts the caller already split, with isolation,
   retention, deletion and the CLIENT_DATA guard tests.
3. Upload for txt and docx, with the limits in section 2.
4. PDF (text layer, `pdfminer.six`, sandboxed extractor).
5. SSE.

No slice 2 until Lead-2 has read this revision and the operator has answered the retention and backup terms.
Each slice is its own PR with its own OpenAPI regeneration and executed examples.

## 8. Risks

Storing client documents changes the data-protection posture (controller/processor roles, a deletion SLA, breach
duties); that is a legal/operator decision, not an engineering one. Long documents multiply judge cost and latency.
Extraction quality (a bad PDF parse) can change a verdict, so extracted facts must be returnable for the caller to
check.

## 9. Decisions and open items

Answered by Lead-2:

1. Storing client documents is in MVP scope, after the poll route.
2. PDF is in the first release: text layer only via `pdfminer.six`, sandboxed extractor with page, size and time
   limits, no OCR, a PDF with no text layer is refused with a clear error.
3. No AV scanner in the MVP; strict allowlist (txt, docx, pdf), content sniffing, no macros, archives or
   embedded-object extraction, sandbox; the docs say "no malware-scanning claim"; revisit if a partner requires it.

Proposed, pending operator (legal/business terms, not engineering decisions):

4. Retention: default 30 days, ceiling 90, immediate hard delete on request plus an audit record of the delete.
5. Backups: client uploads are not backed up; derived facts and results follow the same retention.
