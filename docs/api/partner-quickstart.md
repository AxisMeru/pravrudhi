# Partner API quickstart (`/api/v1`)

The machine-readable contract is [`openapi-v1.json`](openapi-v1.json), generated from the code and checked in
CI (`python -m pravrudhi.api.partner_openapi --write` regenerates it). This page shows the calls and the
error codes. **Every `example:` block below is executed by `tests/test_partner_openapi.py` against the app
with a stub judge**, so a documented request, status and response field cannot drift from the code. The facts
are invented ("TOY:"); responses show the fields a caller can rely on, not every field.

A runnable client and a curl walkthrough: [`partner-quickstart-client.md`](partner-quickstart-client.md).

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
| 429 | over the rate limit; wait `Retry-After` seconds | `{"detail": "rate limit exceeded"}` |
| 503 | `outside_service_window`, `judge_unavailable` (optionally `reason: judges_warming` with `retry_after_s`), `service_config_missing`, or the agent at capacity | see below |

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
