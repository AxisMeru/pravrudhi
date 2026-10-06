"""#308: the published schema carries the full ContractReason and quote_check enums, the docs table matches them exactly,
and partner-facing text uses plain words only."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from pravrudhi.application.nyaya_agent import ContractReason
from pravrudhi.application.nyaya_quote import Reason as QuoteCheck

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = json.loads((ROOT / "docs/api/openapi-v1.json").read_text())["components"]["schemas"]
DOC = (ROOT / "docs/api/reason-codes.md").read_text()

# The fifteen contract reasons, written out so a change to either list has to be made twice, on purpose.
FIFTEEN = [
    "all_elements_established", "denial_established", "missing_element", "no_training_statute_text", "judge_error",
    "assembly_lean_mismatch", "denial_unquotable", "second_judge_defeater_disagreement", "uncertain",
    "uncertain_second_judge", "second_judge_unavailable", "gate1_unavailable", "gate1_not_entailed",
    "gate1_contradiction", "contract_not_validated",
]
EIGHT = [
    "ok", "not_established", "no_quote", "unknown_fact", "empty_quote", "non_evidential_quote", "quote_not_found",
    "ambiguous_quote",
]
SANSKRIT = ("agama", "pramana", "hetu", "pratijna", "nigamana", "udaharana", "upanaya", "karaka", "kāraka", "hetvabhasa")


def _enum(schema: dict) -> list[str]:
    return schema["enum"] if "enum" in schema else next(a["enum"] for a in schema["anyOf"] if "enum" in a)


def test_the_published_reason_enum_is_exactly_the_fifteen_in_the_code() -> None:
    published = _enum(SCHEMAS["ContractResultOut"]["properties"]["reason"])
    assert published == FIFTEEN == list(get_args(ContractReason))


def test_the_published_quote_check_enum_is_exactly_the_eight_in_the_code() -> None:
    published = _enum(SCHEMAS["ElementResultOut"]["properties"]["quote_check"])
    assert published == EIGHT == list(get_args(QuoteCheck))


def test_the_rule_text_fields_are_published_as_nullable_strings() -> None:
    props = SCHEMAS["ContractResultOut"]["properties"]
    for name in ("rule_text", "judge_rule_text", "rule_text_source"):
        assert {"type": "string"} in props[name]["anyOf"] and {"type": "null"} in props[name]["anyOf"], name


def _table_codes(section: str) -> list[str]:
    body = DOC.split(section, 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| `([a-z_0-9]+)` \|", body, re.M)


def test_the_docs_table_lists_every_code_once_and_no_other() -> None:
    assert _table_codes("## Contract `reason`") == FIFTEEN
    assert _table_codes("## Element `quote_check`") == EIGHT


def test_every_table_row_has_text() -> None:
    for code, text in re.findall(r"^\| `([a-z_0-9]+)` \| (.+) \|$", DOC, re.M):
        assert len(text) > 20, code


def test_the_ambiguous_quote_row_says_the_element_is_not_counted() -> None:
    row = next(t for c, t in re.findall(r"^\| `([a-z_0-9]+)` \| (.+) \|$", DOC, re.M) if c == "ambiguous_quote")
    assert "not counted as shown" in row


def _partner_text() -> str:
    quote_check = SCHEMAS["ElementResultOut"]["properties"]["quote_check"]
    parts = [DOC, json.dumps(SCHEMAS["ContractResultOut"]), json.dumps(quote_check)]
    return "\n".join(parts).lower()


def test_no_sanskrit_label_reaches_the_partner_text() -> None:
    low = _partner_text()
    assert not [w for w in SANSKRIT if w in low]


def test_the_rule_text_is_never_called_official() -> None:
    """India Code text from `--describe-source` is unofficial. The word official appears only as 'unofficial' or in 'not the
    official text of the law'."""
    text = DOC + json.dumps(SCHEMAS["ContractResultOut"]["properties"]["rule_text"]) + json.dumps(
        SCHEMAS["ContractResultOut"]["properties"]["rule_text_source"])
    stripped = re.sub(r"unofficial|not the official text of the law", "", text, flags=re.I)
    assert "official" not in stripped.lower()


def test_judge_rule_text_is_documented_as_the_text_as_sent_on_mismatch_or_cut() -> None:
    assert "as sent in the judge's prompt" in DOC
    assert "when `statute_text_mismatch` is true" in DOC and "or when the judge's text was cut" in DOC
    desc = SCHEMAS["ContractResultOut"]["properties"]["judge_rule_text"]["description"]
    assert "AS SENT in the judge prompt" in desc and "statute_text_mismatch is true or when the judge's text was cut" in desc


def test_partner_text_uses_the_final_help_page_wording_and_never_names_config_c() -> None:
    assert "did not find enough support for it" in DOC
    assert "This provision is not on the validated list, so we give a referral, not a proof or denial." in DOC
    assert "config C" not in DOC and "deployments that use two judges" in DOC


def test_the_rule_text_fields_are_documented_as_off_by_default() -> None:
    assert "off by default" in DOC and "absent from the response unless the deployment enables them" in DOC
    for name in ("rule_text", "judge_rule_text", "rule_text_source"):
        desc = SCHEMAS["ContractResultOut"]["properties"][name]["description"]
        assert "expose_rule_text" in desc and "otherwise absent" in desc


def test_published_text_has_no_config_c_and_does_not_call_the_rule_text_india_code_text() -> None:
    spec = (ROOT / "docs/api/openapi-v1.json").read_text()
    assert "config-C" not in spec and "config C" not in spec
    assert "India Code text" not in spec and "India Code text" not in DOC
