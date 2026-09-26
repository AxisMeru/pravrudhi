"""NEGATIVE fixture: every line here is a shape both repositories genuinely use and NONE of them is a
credential. A change to the guard that starts flagging any of these is a regression in the guard, not a
newly-found secret. Each is cited to the real occurrence it was taken from.

Read with the non-vacuity assertions in the guard's tests: "no findings" from this file means nothing
unless the file was actually scanned, so every test that uses it also asserts it appears in
`Report.scanned`. The first draft of `scripts/check_fail_open_defaults.py` skipped what it could not parse
and made exactly this kind of assertion pass over a file it had never read.
"""

import os

# Tokeniser ids, not credentials. The single largest false-positive class in both repos: 22 of 31 raw hits
# on prabhasa-nyaya (src/prabhasa_nyaya/p2b_sft.py:627, tests/test_nyaya_wire_grammar_decoder.py:190, ...)
# and 5 of 25 on pravrudhi (scripts/ext_iltur_generate.py:178, src/pravrudhi/serving/nyaya_local_shim.py:106).
pad_token_id = "prediction_tokenizer.pad_token"
eos_token_id = "predictions.eos_token_identifier"
token_ids = "_QWEN_WIRE_ROLE_TOKENS"

# The NAME of a credential, or the PATH to one -- never the credential. pravrudhi
# src/pravrudhi/application/nyaya_agent.py:954, src/pravrudhi/application/tenancy.py:381,
# src/pravrudhi/api/partner.py:365, src/pravrudhi/application/panel.py:215.
api_key_env = "NYAYA_JUDGE_ANTHROPIC_API_KEY"
TENANCY_PROVISION_SECRET_ENV = "PRAVRUDHI_TENANCY_PROVISION_SECRET"
CLIENT_IP_SECRET_HEADER = "x-pravrudhi-client-ip-secret"
credential_file = "~/.config/llm/dashscope-plan-credentials.env"

# A credential read from the environment rather than written down. app/frontend/src/lib/auth.ts:217,
# app/frontend/e2e/live-admin-boundary.spec.ts:13.
E2E_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD_FOR_LIVE_RUN")
supabase_apikey = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY_VALUE"]

# Illustrative values in docs and configs. pravrudhi .github/workflows/ci.yml's `ci-placeholder`.
DOC_EXAMPLE_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
DOC_EXAMPLE_KEY = "your-api-key-goes-here-replace-me"
README_SNIPPET = "export GITHUB_TOKEN=<your-personal-access-token>"
YAML_TEMPLATE = "api_key: ${NYAYA_JUDGE_API_KEY}"

# Not a credential despite the name: a dictionary key, a sort key, a cache key.
cache_key = "nyaya-corpus-chunk-index-v3-by-section"
sort_key = "descending_by_confidence_then_identifier"
