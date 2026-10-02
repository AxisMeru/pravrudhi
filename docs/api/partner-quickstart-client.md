# Partner API: client sample and curl walkthrough

A working client and the same flow in `curl`, written against the contract that exists today
([`openapi-v1.json`](openapi-v1.json); call details and error codes in [`partner-quickstart.md`](partner-quickstart.md)).
Every host below is the placeholder `https://api.example.com`; substitute your deployment's. Facts are invented
(`TOY:`), and no real client data belongs in an example.

**Claim tier:** unit-tested. `tests/test_partner_client_sample.py` runs the sample client against the app with a
scripted stand-in judge, so the sample cannot drift from the API. It has not been run against a live judge, and
a real judge's outcomes are a different thing from the stand-in's.

## What this contract does not have

The partner API is JSON. `POST /analyse-facts` is synchronous; `POST /analyse-facts/jobs` (202, then poll `GET /analyse-facts/jobs/{job_id}`) is the async form of the same call, and this sample does not use it. These are **not** in it, and the sample does not pretend otherwise:

| Not in the contract | What to do instead |
|---|---|
| Matters (a server-side object grouping documents and runs) | Keep your own matter records; send the facts with each call |
| Server-side document upload | Read the file yourself and split it into facts on your side (the "upload" below is that step) |
| Streaming (SSE) responses | Call `POST /analyse-facts` and wait for the JSON reply |

Limits that do exist: 1-8 facts per call, each at most 4000 characters, 1-5 `contract_ids`.

## 1. Organisation and key (operator, once)

Provisioning is done by the deployment's operator, behind a provisioning secret (`X-Pravrudhi-Tenancy-Secret`)
or an admin identity. An integrating partner is normally handed a key. A key's secret is shown once; keep it out of
source control and pass it in an environment variable.

```bash
curl -s -X POST https://api.example.com/api/v1/orgs \
  -H "X-Pravrudhi-Tenancy-Secret: $PRAVRUDHI_TENANCY_SECRET" -H "Content-Type: application/json" \
  -d '{"org_id": "acme-test", "name": "acme-test"}'

curl -s -X POST https://api.example.com/api/v1/orgs/acme-test/keys \
  -H "X-Pravrudhi-Tenancy-Secret: $PRAVRUDHI_TENANCY_SECRET" -H "Content-Type: application/json" -d '{}'
# -> {"secret": "...", ...}   export PRAVRUDHI_API_KEY=<secret>
```

Use a test organisation for trials. The same two calls are `provision()` in the sample client.

## 2. Split a local file into facts, then analyse

[`examples/partner_client.py`](examples/partner_client.py) reads a text file, splits it into facts (one per
blank-line-separated paragraph), refuses locally what the API would refuse (no facts, more than 8, over 4000
characters), and posts to `/api/v1/analyse-facts`:

```bash
export PRAVRUDHI_API_KEY=...        # from step 1
python docs/api/examples/partner_client.py --base-url https://api.example.com \
  --facts-file docs/api/examples/toy_facts.txt --contract bns69
```

The same call in `curl`, with the file's paragraphs as the `facts` array:

```bash
curl -s -X POST https://api.example.com/api/v1/analyse-facts \
  -H "X-Pravrudhi-Api-Key: $PRAVRUDHI_API_KEY" -H "Content-Type: application/json" \
  -d '{"facts": ["TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
                 "TOY: Kiran had already decided never to marry Lata when he made that promise.",
                 "TOY: Relying on the promise, Lata had sexual intercourse with Kiran."],
       "narrative": "TOY narrative.", "contract_ids": ["bns69"], "proceeding_posture": "trial"}'
```

## 3. Reading the reply

* `contracts[].outcome` is `PROOF`, `DENIAL`, `ABSTAIN` or `REFER_TO_LAWYER`, with a `reason`. **`REFER_TO_LAWYER`
  and `ABSTAIN` are refusals to give a verdict, not findings about the law.** The sample prints a line for
  `REFER_TO_LAWYER` so it is not read as a result.
* `standard` says which standard of proof was applied: `applied` (`proved` or `prima_facie_disclosed`),
  `source` (`proceeding_posture`, `proceeding_type` or `default`), and `in_judge_prompt`.
  **`in_judge_prompt` false means the standard is recorded but the judge was never told it**, so the verdict did
  not depend on it. Say that to your users instead of describing the posture as having shaped the result.
  `proceeding_posture` is optional; omitting it applies the stricter `proved` default.
* Errors: 401 (bad or revoked key), 422 (input refused), 429 (over the rate limit; wait `Retry-After`), 503
  (outside the service window, judge unavailable). The sample raises `ApiError` carrying the status, body and
  `Retry-After`.
