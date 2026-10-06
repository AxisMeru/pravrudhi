# Partner API quickstart (`/api/v1`)

The machine-readable contract is [`openapi-v1.json`](openapi-v1.json), generated from the code and checked in
CI (`python -m pravrudhi.api.partner_openapi --write` regenerates it). This page shows the calls and the
error codes. **Every `example:` block below is executed by `tests/test_partner_openapi.py` against the app
with a stub judge**, so a documented request, status and response field cannot drift from the code. The facts
are invented ("TOY:"); responses show the fields a caller can rely on, not every field.

A runnable client and a curl walkthrough: [`partner-quickstart-client.md`](partner-quickstart-client.md).

What each `reason`, `quote_check` and the rule-text fields mean, in plain words: [`reason-codes.md`](reason-codes.md).

Claim tier of this page: unit-tested against a stub judge, not run live. A real judge's outcomes are a
different thing and are not shown here.

## 1. Authenticate

Send your organisation's API key in the `X-Pravrudhi-Api-Key` header. A key is created by the operator
(`POST /api/v1/orgs/{org_id}/keys`, behind the provisioning secret) and its secret is shown once. A header
that is present but wrong or revoked is a **401**, never treated as anonymous. Without a key the endpoint
still answers where the deployment allows anonymous use, limited per client IP.

## 2. Analyse facts

`POST /api/v1/analyse-facts` takes 1-8 facts (at most 4000 characters each), an optional narrative, and 1-5
`contract_ids`.

```json example:analyse-facts-proof
{
  "headers": {
    "X-Pravrudhi-Api-Key": "$PRAVRUDHI_API_KEY"
  },
  "method": "POST",
  "path": "/api/v1/analyse-facts",
  "request": {
    "facts": [
      "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
      "TOY: Kiran had already decided never to marry Lata when he made that promise.",
      "TOY: Relying on the promise, Lata had sexual intercourse with Kiran."
    ],
    "narrative": "TOY narrative.",
    "contract_ids": [
      "bns69"
    ]
  },
  "status": 200,
  "response": {
    "judge": "scripted-test-judge",
    "contracts": [
      {
        "contract_id": "bns69",
        "outcome": "PROOF",
        "reason": "all_elements_established"
      }
    ]
  }
}
```

Enumerations in the schema:

* element `status`: `established`, `not_confirmed`, `not_established`, `not_evaluated_second_unavailable`,
  `not_evaluated_gate1_unavailable`
* contract `outcome` and `lean_outcome`: `PROOF`, `DENIAL`, `ABSTAIN`, `REFER_TO_LAWYER`
* contract `reason`: see the `Reason` enum in the OpenAPI file. A `REFER_TO_LAWYER` or `ABSTAIN` is a refusal
  to give a verdict, not a finding about the law.

## 3. Status and the offline window

`GET /api/v1/status` needs no key and reports the engine version, the service window (when one is configured)
and the last judge reading.

```json example:status
{
  "method": "GET",
  "path": "/api/v1/status",
  "status": 200,
  "response": {
    "service_window": null,
    "judge": {
      "state": "unknown",
      "checked_at": null
    }
  }
}
```

When the deployment enforces a service window, a call outside it is a **503** with `Retry-After` and the next
opening time:

```json example:outside-window
{
  "headers": {
    "X-Pravrudhi-Api-Key": "$PRAVRUDHI_API_KEY"
  },
  "method": "POST",
  "path": "/api/v1/analyse-facts",
  "request": {
    "facts": [
      "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
      "TOY: Kiran had already decided never to marry Lata when he made that promise.",
      "TOY: Relying on the promise, Lata had sexual intercourse with Kiran."
    ],
    "narrative": "TOY narrative.",
    "contract_ids": [
      "bns69"
    ]
  },
  "status": 503,
  "headers_out": {
    "retry-after": "*"
  },
  "response": {
    "error": "outside_service_window",
    "window": {
      "timezone": "Europe/London",
      "open": "09:00",
      "close": "21:00"
    }
  }
}
```

## 4. Errors

| Status | Meaning | Body |
|---|---|---|
| 401 | key header present but invalid or revoked | `{"detail": "Invalid or revoked API key"}` |
| 422 | input refused (no non-empty fact, a fact over 4000 characters, unknown contract) | `{"detail": "..."}` |
| 404 | jobs only: no such job for this key | `{"detail": "no such job"}` |
| 429 | over the rate limit; wait `Retry-After` seconds. On `POST /analyse-facts/jobs` it means too many unfinished jobs for this key | `{"detail": "rate limit exceeded"}` or `{"detail": "too many unfinished jobs for this key"}` |
| 503 | `outside_service_window`, `judges_offline` (the judges are switched off: no worker and nothing queued, so nothing was queued by this call; try later. An idle scale-from-zero endpoint reads the same, so this is only returned while the judges are deliberately parked), `judge_unavailable` (optionally `reason: judges_warming` with `retry_after_s`), `service_config_missing`, `agent_at_capacity` (the agent is at its concurrency cap; retry shortly) or `agent_unavailable` (the agent could not run: a configuration, binary or system error; retry later). A 503 body has one of three shapes. (1) `{"error": <code>, "detail": <fixed message>}`: `agent_at_capacity`, `agent_unavailable` (a configuration, binary or system error in the agent), and on the other API routes `checker_unavailable`, `registry_checker_unavailable`, `chat_endpoint_unreachable` (and the 403 `vendor_not_allowed`). (2) `{"error": <code>}` plus that code's own fields and NO `detail`: `outside_service_window` (with `window`), `judges_offline`, `judge_unavailable` (optionally `reason` and `retry_after_s`), `service_config_missing` on the POST routes, `citation_index_unavailable`, `verify_at_capacity` (with `Retry-After: 5`) and `verify_timeout`. (3) `{"detail": "service_config_missing"}` with no `error` key: the GET job-poll and the GET audit routes. Exception text is not included in any response, and a failed async job stores the same code | see below |

```json example:invalid-key
{
  "headers": {
    "X-Pravrudhi-Api-Key": "not-a-real-key"
  },
  "method": "POST",
  "path": "/api/v1/analyse-facts",
  "request": {
    "facts": [
      "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
      "TOY: Kiran had already decided never to marry Lata when he made that promise.",
      "TOY: Relying on the promise, Lata had sexual intercourse with Kiran."
    ],
    "narrative": "TOY narrative.",
    "contract_ids": [
      "bns69"
    ]
  },
  "status": 401,
  "response": {
    "detail": "Invalid or revoked API key"
  }
}
```

```json example:empty-fact
{
  "headers": {
    "X-Pravrudhi-Api-Key": "$PRAVRUDHI_API_KEY"
  },
  "method": "POST",
  "path": "/api/v1/analyse-facts",
  "request": {
    "facts": [
      "   "
    ],
    "narrative": "TOY narrative.",
    "contract_ids": [
      "bns69"
    ]
  },
  "status": 422,
  "response": {
    "detail": "at least one non-empty fact is required"
  }
}
```

```json example:rate-limited
{
  "headers": {
    "X-Pravrudhi-Api-Key": "$PRAVRUDHI_API_KEY"
  },
  "method": "POST",
  "path": "/api/v1/analyse-facts",
  "request": {
    "facts": [
      "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
      "TOY: Kiran had already decided never to marry Lata when he made that promise.",
      "TOY: Relying on the promise, Lata had sexual intercourse with Kiran."
    ],
    "narrative": "TOY narrative.",
    "contract_ids": [
      "bns69"
    ]
  },
  "status": 429,
  "headers_out": {
    "retry-after": "*"
  },
  "response": {
    "detail": "rate limit exceeded"
  },
  "after": "one earlier call in the same minute from the same client"
}
```

```json example:judge-unavailable
{
  "headers": {
    "X-Pravrudhi-Api-Key": "$PRAVRUDHI_API_KEY"
  },
  "method": "POST",
  "path": "/api/v1/analyse-facts",
  "request": {
    "facts": [
      "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
      "TOY: Kiran had already decided never to marry Lata when he made that promise.",
      "TOY: Relying on the promise, Lata had sexual intercourse with Kiran."
    ],
    "narrative": "TOY narrative.",
    "contract_ids": [
      "bns69"
    ]
  },
  "status": 503,
  "response": {
    "error": "judge_unavailable"
  }
}
```

A 503 is an infrastructure state, never a legal outcome: retry after `Retry-After`.

## 5. What a reply also carries

Beyond `contracts[].outcome` and `reason`, a 200 carries these fields (all in the contract; the examples above
show only the ones they assert):

* `standard`: the standard of proof the request asked for and whether the judge was told it
  (`requested`, `applied`, `source`, `in_judge_prompt`, `proceeding_posture`). `in_judge_prompt` false means the
  standard is recorded but the verdict did not depend on it. Say that to your users. The request field
  `proceeding_posture` is optional; omitting it applies the stricter `proved` default.
  [`partner-quickstart-client.md`](partner-quickstart-client.md) section 3 has the full reading.
* `contracts[].citations`: the statute sources listed for the contract, or `null` when they could not be read.
* `contracts[].lean_attestation`: the pinned Lean binary and the exact input it scored, for the contract's
  `lean_outcome`. The top-level `score_sha256` is the binary's SHA-256.
* `run_id`: identifies the run; an authenticated caller can look it up in the audit log (section 6).
* `retention_notice`: how long the engine keeps the request. **Show it to your users verbatim.**
* The request field `sections` optionally limits the statute sections considered; omit it for the contract's own.

Every call made with an API key also returns `X-RateLimit-Limit` (calls per minute for the key),
`X-RateLimit-Remaining` (left in the current one-minute window) and `X-RateLimit-Reset` (seconds until the
window ends), on the 200 and on the 429. A 429 adds `Retry-After`. Anonymous calls are limited per client IP
and carry no key headers.

## 6. Asynchronous jobs, audit and usage

All of these need an API key (`X-Pravrudhi-Api-Key`); without one they answer 401.

* `POST /api/v1/analyse-facts/jobs` takes the same body as `analyse-facts` and answers **202**
  `{"job_id": "...", "status": "pending"}`. `GET /api/v1/analyse-facts/jobs/{job_id}` then returns
  `status` (`pending`, `running`, `done`, `failed`). On `done`, `result` is exactly the body the synchronous
  call would have returned; on `failed`, `error` carries the HTTP status and body it would have returned. A
  job id belongs to the key that made it (another key gets 404). A key may have at most 8 unfinished jobs by default
  (the deployment can change it): past that, a 429 with `Retry-After`.
* A keyed request is metered when the key is admitted, before the body is checked: a request refused with 422 (no
  non-empty fact, a fact over 4000 characters) still counts toward the key's usage and the audit log.
* `GET /api/v1/audit?offset=0&limit=50` pages the key's own call log (`rows`, `next_offset`), within the
  deployment's retention window.
* `GET /api/v1/orgs/{org_id}/usage` is a key's own usage counters. `GET /api/v1/orgs/{org_id}/usage/summary`
  and `POST /api/v1/orgs/{org_id}/keys/{key_id}/revoke` are operator routes (admin or provisioning credential);
  a partner key is refused.

## 7. Versions

`info.version` in the contract (`v1`) is the API version: it changes only when the request or response shape
breaks. The engine release (for example 0.5.44) is `GET /api/v1/status`'s `engine_version`; pin to `/api/v1`, not to
the engine release.
