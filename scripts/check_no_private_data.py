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
import re
import subprocess
import sys
from pathlib import Path

MARKER = "CLIENT_DATA"

#: Extensions worth reading for the marker. A CI guard that opens every tracked file, including binary
#: checkpoints or images, would be slow and would still find nothing in a binary blob -- the marker is a
#: text convention, so only text-shaped files are scanned.
TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".csv",
        ".tsv",
        ".toml",
        ".cfg",
        ".ini",
    }
)

#: Never a private-data violation to contain the marker itself: this file, and any file whose job is to
#: document or test the rule, would otherwise fail on its own docstring or fixture.
EXEMPT_NAMES = frozenset({"check_no_private_data.py"})


#: Recorded-looking identifiers in fixture and research files (2026-10-02: two public PRs carried recorded
#: session, thread and response ids and a plan name). Scope is deliberately narrow -- a path with a `fixtures`
#: component, or under `research/` -- because the same shapes are legitimate in prose. Constructed forms are
#: allowed. A hit prints the pattern name, never the value.
IDENTIFIER_SCOPE_DIRS = ("research",)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_CONSTRUCTED_UUID = re.compile(r"00000000-0000-4000-8000-\d{12}|00000000-0000-0000-0000-000000000000")
_RESP = re.compile(r"\bresp_[0-9a-zA-Z]{20,}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_EMAIL_OK = re.compile(r"@example\.(?:com|org|net)$|@[A-Za-z0-9.-]+\.(?:invalid|test)$", re.I)
_HOME = re.compile(r"/home/([A-Za-z_][A-Za-z0-9_-]*)/")
_HOME_OK = frozenset({"user", "example", "runner", "me"})
_ACCOUNT_KEY = re.compile(r"""["'](plan_type|account_id|organization_id|org_id|user_id|email)["']\s*:\s*["']([^"']+)["']""")
_ACCOUNT_VALUE_OK = re.compile(r"(?:example|constructed)", re.I)
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")


#: Fixtures whose job is to hold token-shaped strings for `check_no_secrets_in_diff.py`'s own tests.
IDENTIFIER_EXEMPT_DIRS = frozenset({"secret_scan_fixtures"})


def _identifier_scope(rel: Path) -> bool:
    parts = rel.parts
    if IDENTIFIER_EXEMPT_DIRS.intersection(parts[:-1]):
        return False
    return any(p == "fixtures" or p.endswith("_fixtures") for p in parts[:-1]) or parts[0] in IDENTIFIER_SCOPE_DIRS


def identifier_hits(line: str) -> list[str]:
    hits = []
    if any(not _CONSTRUCTED_UUID.fullmatch(m) for m in _UUID.findall(line)):
        hits.append("uuid that is not the constructed 00000000-0000-4000-8000-<12 digits> form")
    if _RESP.search(line):
        hits.append("recorded-looking response id (use resp_constructed_NN)")
    if any(not _EMAIL_OK.search(m) for m in _EMAIL.findall(line)):
        hits.append("email address (use @example.com)")
    if any(u not in _HOME_OK for u in _HOME.findall(line)):
        hits.append("host home path (use /home/user/)")
    if any(not _ACCOUNT_VALUE_OK.search(v) for _, v in _ACCOUNT_KEY.findall(line)):
        hits.append("account/plan field with a non-constructed value (use an example-* value)")
    if _TOKEN.search(line):
        hits.append("token-shaped string")
    return hits


def _tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
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
        in_scope = _identifier_scope(path.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), start=1):
            if in_scope:
                for h in identifier_hits(line):
                    violations.append(f"{path.relative_to(root)}:{lineno}: {h} -- replace with a constructed value")
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
