"""CI guard: the checked-in partner OpenAPI contract must equal what the router produces.

Exit 0 when `docs/api/openapi-v1.json` matches `pravrudhi.api.partner_openapi.render()`; exit 1 otherwise,
printing a unified diff (first 60 lines) and the one command that fixes it. Runs in the `guards` job, which has
no pytest dependency, so a red test run never hides a stale contract.

Usage: check_openapi_contract.py [--root DIR]   (DIR holds `docs/api/openapi-v1.json`; default: cwd)
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

FIX = "python -m pravrudhi.api.partner_openapi --write"
MAX_DIFF_LINES = 60


def check(root: Path, rendered: str) -> list[str]:
    from pravrudhi.api.partner_openapi import CONTRACT_PATH

    path = root / CONTRACT_PATH
    if not path.exists():
        return [f"{CONTRACT_PATH}: missing; generate it with: {FIX}"]
    committed = path.read_text()
    if committed == rendered:
        return []
    diff = list(
        difflib.unified_diff(
            committed.splitlines(), rendered.splitlines(), "committed", "generated", lineterm="", n=1
        )
    )
    shown = diff[:MAX_DIFF_LINES]
    if len(diff) > MAX_DIFF_LINES:
        shown.append(f"... {len(diff) - MAX_DIFF_LINES} more diff lines")
    return [f"{CONTRACT_PATH}: out of date with the router; regenerate with: {FIX}", *shown]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    from pravrudhi.api.partner_openapi import render

    problems = check(args.root, render())
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
