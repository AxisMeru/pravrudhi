"""A page that reads a section the snapshot never carried is a page that is broken wherever there is no engine.

The public site runs on one recorded snapshot. Each page asks it for a named section, and a section the export
never wrote leaves that page showing an error or an empty state on the deployed site while working perfectly
against a live engine. That failure is invisible to every check the project had: the build succeeds, the page
answers 200, and the message is rendered by the browser afterwards, so reading the built HTML cannot see it.

It was not hypothetical. Four pages were reading sections the export had never produced, and the parity page —
the one whose whole job is to say honestly what this product can do — had been telling every visitor it could not
reach an engine since the day it shipped.

So this asserts the two halves match: every section a page reads is a section the export writes. A page whose
absent section is a designed empty state rather than a defect is named here with its reason, which keeps the
exemption visible instead of letting the rule quietly weaken.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

LIB = Path("app/frontend/src/lib")

DELIBERATELY_ABSENT = {
    # Memory belongs to whoever is signed in. A recording is public, so it carries none, and the page renders an
    # empty store rather than someone else's notes.
    "memory",
}


def _sections_pages_read() -> set[str]:
    """Every `bundle.<name>` a demo-aware library reads out of the snapshot."""
    found: set[str] = set()
    if not LIB.is_dir():
        return found
    for path in LIB.glob("*.ts"):
        found.update(re.findall(r"bundle\.(\w+)\s*\?\?", path.read_text()))
    return found


def _exported_sections(tmp: Path) -> set[str]:
    dest = tmp / "demo.json"
    subprocess.run(
        [sys.executable, "-m", "pravrudhi", "demo-export", "--root", ".", "--dest", str(dest)],
        check=True, capture_output=True, text=True, timeout=600,
    )
    return set(json.loads(dest.read_text()))


def test_every_section_a_page_reads_is_a_section_the_export_writes(tmp_path: Path) -> None:
    wanted = _sections_pages_read()
    if not wanted:
        return  # the interface is not present in every checkout
    written = _exported_sections(tmp_path)
    missing = sorted(wanted - written - DELIBERATELY_ABSENT)
    assert not missing, (
        f"these pages read a section the recorded snapshot does not carry, so they are broken on the public "
        f"site while working against a live engine: {missing}"
    )


def test_the_exemption_list_names_only_sections_that_are_really_absent(tmp_path: Path) -> None:
    """An exemption that stops being needed should be deleted, not left to hide a future regression."""
    if not _sections_pages_read():
        return
    written = _exported_sections(tmp_path)
    stale = sorted(DELIBERATELY_ABSENT & written)
    assert not stale, f"these are exported now and no longer need an exemption: {stale}"
