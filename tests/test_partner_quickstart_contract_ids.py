"""The partner quickstart lists the checkable contract ids; the list must equal the registry's, so it cannot go stale."""

from __future__ import annotations

import re
from pathlib import Path

from pravrudhi.application import nyaya_lean_registry as reg

DOC = Path(__file__).resolve().parent.parent / "docs" / "api" / "partner-quickstart.md"


def test_the_quickstart_lists_exactly_the_registry_contract_ids() -> None:
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- contract-ids:start -->", 1)[1].split("<!-- contract-ids:end -->", 1)[0]
    listed = re.findall(r"`([a-z0-9_]+)`", block)
    assert len(listed) == len(set(listed)) and set(listed) == set(reg.KNOWN_CONTRACT_IDS)
    assert f"Today those {len(listed)} ids are" in text
