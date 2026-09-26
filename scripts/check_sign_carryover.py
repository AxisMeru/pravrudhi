"""CI/merge guard (Lead-2, 2026-09-26, from Tag's governance suggestion): verifies a reviewer sign made on
one commit still covers a later commit before that sign is relied on for a merge. Use before any merge that
relies on a sign made on an earlier head than the one actually being merged.

See `pravrudhi.application.sign_carryover` for the comparison logic and the sign-record shape.

Usage: check_sign_carryover.py <sign-record.json> <target-sha> [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pravrudhi.application.sign_carryover import SignRecordError, check_carryover  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sign_record", type=Path, help="path to a sign record JSON file")
    parser.add_argument("target_sha", help="the commit sha being merged, to check the sign against")
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="git repo root to resolve refs/paths against"
    )
    args = parser.parse_args(argv)

    try:
        raw = json.loads(args.sign_record.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: could not read sign record {args.sign_record}: {e}", file=sys.stderr)
        return 2

    try:
        result = check_carryover(raw, args.target_sha, repo_root=args.repo_root)
    except SignRecordError as e:
        print(f"FAIL: malformed sign record: {e}", file=sys.stderr)
        return 2

    if result.carries:
        print(f"CARRIES: every path in the sign still matches at {args.target_sha}.")
        return 0

    print(f"RE-SIGN NEEDED: {len(result.changed_paths)} path(s) changed since the sign:")
    for path in result.changed_paths:
        print(f"  {path}: {result.reasons[path]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
