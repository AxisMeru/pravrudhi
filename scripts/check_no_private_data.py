"""CI guard (L4, `docs/decisions/LEG-PLAN-2026-09-23.md`): the public `pravrudhi` repo holds the product loop
and no gold data, weights, or partner material. This refuses a commit that would add a tracked file carrying
a `CLIENT_DATA` marker (real partner facts, case material, or eval rows staged for a data builder but never
meant to be committed) anywhere under the checkout, and a tracked file under `research/` written by a data
builder script rather than checked in by a person -- mirroring `import_guard.py`'s shape: exit 1 and print
`path:line: message` for every violation, exit 0 clean.

Usage: check_no_private_data.py [--root DIR]. Intended as a pre-commit / CI step over the files git is about
to track, not a full-disk scan -- it walks git's own tracked-file list (`git ls-files`), so a gitignored
local file (this project's `.gitignore` already keeps `research/` local and private, see
CLAUDE.md "Public/local split") is invisible to it by construction, exactly like every other tracked-file
check in this repo.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MARKER = "CLIENT_DATA"

#: Extensions worth reading for the marker. A CI guard that opens every tracked file, including binary
#: checkpoints or images, would be slow and would still find nothing in a binary blob -- the marker is a
#: text convention, so only text-shaped files are scanned.
TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv", ".toml", ".cfg", ".ini",
})

#: Never a private-data violation to contain the marker itself: this file, and any file whose job is to
#: document or test the rule, would otherwise fail on its own docstring or fixture.
EXEMPT_NAMES = frozenset({"check_no_private_data.py"})


def _tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return [root / line for line in out.splitlines() if line.strip()]


def check(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _tracked_files(root):
        if path.name in EXEMPT_NAMES:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if MARKER in line:
                violations.append(
                    f"{path.relative_to(root)}:{lineno}: contains the {MARKER!r} marker -- "
                    "partner/eval material may not be committed to the public repo"
                )
    return violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    violations = check(Path(args.root).resolve())
    for v in violations:
        print(v)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
