"""The product wording for a citation check (#533, PREVIEW): the one place a `VerifyResult` becomes a status a reader sees.

The engine never calls a model here and never widens what the verifier resolves. `verified` is True ONLY for
`VerifyResult.VERIFIED`, which `verify()` returns only for exactly one parseable citation that resolves to ONE indexed case
AND a non-empty quote that occurs verbatim in that case's text (an empty or whitespace-only quote never verifies, #331).
The labels are the verifier's statuses verbatim; none says a citation is fake, false or invalid: "not in index" is "no
evidence either way". The mapping covers every enum member, and a value it does not know raises instead of guessing, so a
future member cannot be shown as verified by default.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pravrudhi.application.verify import VerifyResult, verify

#: Always true while the check is a preview; one flag so the app's "preview" badge and its later removal are one change.
PREVIEW = True

_LABELS: dict[VerifyResult, tuple[str, str]] = {
    VerifyResult.VERIFIED: (
        "verified",
        "Verified: the citation resolves to an indexed case and the quote appears in the indexed text of the case, "
        "word for word apart from line breaks, quote marks, dashes and spacing.",
    ),
    VerifyResult.EXISTS_QUOTE_NOT_FOUND: ("quote_not_found", "quote not found in the record"),
    VerifyResult.NOT_IN_INDEX: ("not_in_index", "not in index"),
    VerifyResult.CONFLICT: ("conflict", "conflict: the citation matches more than one indexed case"),
    VerifyResult.MALFORMED: ("malformed", "Exactly one parseable citation is required."),
}

#: Words no label may contain: a failed lookup is never a finding about the citation itself.
FORBIDDEN_WORDS = ("fake", "fabricated", "hallucinat", "invalid", "false", "bogus")


@dataclass(frozen=True)
class CitationStatus:
    result: str  # the verifier's own enum value, unchanged
    status: str
    label: str
    verified: bool
    preview: bool = PREVIEW


def status_for(result: VerifyResult) -> CitationStatus:
    """The status for one verifier result; raises KeyError for a result this module does not know."""
    status, label = _LABELS[result]
    return CitationStatus(result=result.value, status=status, label=label, verified=result is VerifyResult.VERIFIED)


def check(conn: sqlite3.Connection, citation: str, quote: str) -> CitationStatus:
    """Run the verifier and return its status in product wording."""
    return status_for(verify(conn, citation, quote))
