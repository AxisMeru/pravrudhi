#!/usr/bin/env python3
"""Full-history attribution-trailer check, against a committed allowlist.

House rule (CHARTER; enforced locally by .githooks/commit-msg): no `Co-Authored-By:` and no
`Claude-Session:` trailer in any commit message in this repository, ever. That rule overrides any
harness attribution default. This script is the after-the-fact check that the rule held, over the
WHOLE history rather than over one diff.

WHY WHOLE HISTORY, AND WHY A SEPARATE FILE FROM THE PR GATE
-----------------------------------------------------------
A diff-scoped check cannot see what has already landed: a commit that is already on `main` appears
in no later pull request's diff, so no PR gate will ever report it. That is a general design rule
for every guard in these repos, not a quirk of this one. The pull-request gate and this job are
therefore both needed and neither replaces the other.

WHY AN ALLOWLIST WITH REASONS RATHER THAN A HARDCODED LIST
----------------------------------------------------------
The offenders that are already in published history cannot be removed: rewriting them means a
force-push of `main`, and the standing decision on that is no, full stop. So they are waived by
exact sha -- but a waiver with no written reason is a waiver nobody re-reads, and a list embedded
in a workflow file is a list that drifts from the next copy of itself. Entries live in
`scripts/attribution_trailer_allowlist.txt`, one sha per line with a reason, and EVERY allowlisted
entry is printed with its reason on every run.

Two distinct mechanisms produce these trailers and they are reasoned separately in that file,
because the fix for one is not the fix for the other:

  * GITHUB-COMPOSED. GitHub itself adds `Co-authored-by:` (note its own lower-case spelling of
    "authored-by") to a squash-merge commit it composes, to credit a second party when the person
    performing the merge is not the author of the branch commits. Nothing in a committing checkout
    can prevent this: the commit is composed server-side, so a local hook never sees it.
  * AGENT-WRITTEN. An agent following a harness attribution reminder wrote the trailer into its own
    commit message without checking this repo's house rule first. `.githooks/commit-msg` strips
    exactly this, but only in a checkout where `core.hooksPath` is set to `.githooks` -- so the fix
    is `git config core.hooksPath .githooks` in every checkout and worktree you commit from, and a
    NEW entry of this kind means that fix did not take somewhere.

FAILS CLOSED
------------
A shallow checkout is a hard error, not a pass. `actions/checkout` defaults to depth 1, and
`git log --format=%H` in a depth-1 clone reports exactly one commit -- a full-history check that
inspects one commit and exits 0 is the most expensive kind of green tick, because it looks like
coverage. Run this with `fetch-depth: 0`. Likewise an allowlist key with no reason text after it
waives nothing and is itself reported.

USAGE
-----
    check_no_attribution_trailers_in_history.py [--root DIR] [--allowlist FILE] [--rev-range RANGE]

Exit status: 0 if every trailer-bearing commit is allowlisted with a reason, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

DEFAULT_ALLOWLIST = "scripts/attribution_trailer_allowlist.txt"

# Anchored at line start, case-insensitive: these are trailers, not prose mentioning them.
TRAILER_RE = re.compile(r"^(Co-Authored-By|Claude-Session):", re.IGNORECASE | re.MULTILINE)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(root: str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def is_shallow(root: str) -> bool:
    out = _git(root, "rev-parse", "--is-shallow-repository").strip()
    return out == "true"


def load_allowlist(path: str) -> tuple[dict[str, str], list[str]]:
    """Return ({sha: reason}, [problems]). A key with no reason is a problem, not a waiver."""
    allowed: dict[str, str] = {}
    problems: list[str] = []
    if not os.path.exists(path):
        return allowed, [f"allowlist file not found: {path}"]
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            sha = parts[0].lower()
            reason = parts[1].strip() if len(parts) > 1 else ""
            if not SHA_RE.match(sha):
                problems.append(
                    f"{path}:{lineno}: not a full 40-character sha: {parts[0]!r} "
                    f"(abbreviations are ambiguous as history grows; write the full sha)"
                )
                continue
            if not reason:
                problems.append(
                    f"{path}:{lineno}: allowlist entry for {sha} has NO REASON after it, so it "
                    f"waives nothing. Say what mechanism produced the trailer and where that is "
                    f"recorded."
                )
                continue
            if sha in allowed:
                problems.append(f"{path}:{lineno}: duplicate allowlist entry for {sha}")
                continue
            allowed[sha] = reason
    return allowed, problems


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", default=".")
    ap.add_argument("--allowlist", default=None, help=f"default: <root>/{DEFAULT_ALLOWLIST}")
    ap.add_argument(
        "--rev-range",
        default=None,
        help="restrict the scan (default: HEAD, i.e. every commit reachable from the checked-out "
        "branch -- deliberately NOT --all, which would sweep unmerged topic branches)",
    )
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    allowlist_path = args.allowlist or os.path.join(root, DEFAULT_ALLOWLIST)

    print("full-history attribution-trailer check")
    print(f"  root:      {root}")
    print(f"  allowlist: {allowlist_path}")

    # Fail closed on a shallow clone: one commit inspected is not a history scan.
    if is_shallow(root):
        print(
            "FAIL: this is a SHALLOW clone, so full history is not present and this check cannot "
            "do its job. A depth-1 checkout reports a single commit and would exit 0 while seeing "
            "nothing. Set `fetch-depth: 0` on actions/checkout, or run "
            "`git fetch --unshallow`."
        )
        return 1

    allowed, problems = load_allowlist(allowlist_path)

    # HEAD, not --all. `fetch-depth: 0` fetches every branch, and these repos carry a hundred-odd
    # unmerged topic branches whose commits are not (yet) part of this repository's history. Scanning
    # them would report trailers that landing a PR will never produce and make this job red for
    # reasons its owner cannot act on. The scope is the history of the checked-out branch, which is
    # the same scope the existing pull-request and governance checks use.
    scope = args.rev_range or "HEAD"
    revs = _git(root, "log", "--format=%H", scope).split()

    print(f"  scope:     {scope} ({len(revs)} commits)")
    print(f"  allowlist: {len(allowed)} entry/entries with reasons")
    print()

    violations: list[str] = []
    waived: list[str] = []
    seen_shas = set(revs)

    for sha in revs:
        body = _git(root, "log", "-1", "--format=%B", sha)
        hits = [m.group(0).rstrip(":") for m in TRAILER_RE.finditer(body)]
        if not hits:
            continue
        subject = _git(root, "log", "-1", "--format=%s", sha).strip()
        if sha in allowed:
            waived.append(f"ALLOWED  {sha}  [{', '.join(sorted(set(hits)))}]  {subject}")
            waived.append(f"         reason: {allowed[sha]}")
            continue
        violations.append(
            f"VIOLATION  {sha}  [{', '.join(sorted(set(hits)))}]  {subject}"
        )

    # Print every waiver's reason on every run. A waiver that disappears from the output is a
    # waiver nobody re-reads.
    if waived:
        print("Allowlisted trailer-bearing commits (reasons re-read on every run):")
        for line in waived:
            print(f"  {line}")
        print()

    # A stale entry is a note, not a failure: it should shrink this file, not break a build.
    stale: list[tuple[str, str]] = []
    for sha in allowed:
        if sha not in seen_shas:
            stale.append((sha, "not reachable in this scope"))
            continue
        if not TRAILER_RE.search(_git(root, "log", "-1", "--format=%B", sha)):
            stale.append((sha, "no longer carries a trailer"))
    if stale:
        print("NOTE: stale allowlist entries -- remove these lines:")
        for sha, why in stale:
            print(f"  {sha}  ({why})")
        print()

    if problems:
        print("Allowlist file problems:")
        for p in problems:
            print(f"  {p}")
        print()

    if violations:
        print("Unallowlisted attribution trailers in history:")
        for v in violations:
            print(f"  {v}")
        print()
        print(
            "Each of these is already in published history and cannot be removed without a "
            "force-push of a shared branch, which is not done here. Either add the sha to "
            f"{DEFAULT_ALLOWLIST} WITH a written reason naming the mechanism that produced it, or "
            "-- if the commit is not yet pushed -- amend it. If this is a NEW agent-written "
            "trailer, the real fix is `git config core.hooksPath .githooks` in the checkout it "
            "came from; the hook strips this automatically when it is active."
        )

    if violations or problems:
        print(
            f"\nFAIL: {len(violations)} unallowlisted commit(s), {len(problems)} allowlist "
            f"problem(s)."
        )
        return 1

    print(f"OK: {len(revs)} commits scanned, {len(waived) // 2} allowlisted, 0 unallowlisted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
