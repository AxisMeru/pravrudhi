#!/usr/bin/env python3
"""Open or refresh the `guard-failure` issue for a failed scheduled guard audit.

THIS EXISTS BECAUSE A GUARD WHOSE ALARM NOBODY SEES IS NOT A GUARD. On 2026-09-26 a red push on
`main` fired exactly once, was overwritten by the next push, and nobody noticed for the rest of the
day. A nightly job that goes red in a tab nobody opens repeats that failure on a schedule. So a
failing audit must leave a durable, labelled artefact that something watches -- an issue labelled
`guard-failure`.

FOUR PROPERTIES, each of which is the whole point of the corresponding code below.

1. IT NEVER FAILS OPEN. This script exits non-zero on any failure of its own, and the workflow step
   that runs it carries no `continue-on-error`. It is also reached only via `if: failure()`, after a
   guard step that has already failed the job -- so the job's red status does not depend on this
   script succeeding, and cannot be rescued by it succeeding either. A notification failure is
   printed as a DISTINCT `::error::` annotation, so "the audit failed" and "we could not tell
   anyone the audit failed" are two separately visible events rather than one confusing one.

2. THE LABEL MAY NOT EXIST. Creating an issue with a label the repository does not have fails the
   whole create. `guard-failure` did not exist in this repository when this was written. So this
   script CREATES THE LABEL IF MISSING (GET, then POST on 404) and treats a failure to create it as
   a hard error with a clear message -- never as a reason to drop the alert or to open an
   unlabelled issue that the label's watcher would not see.

3. ISSUES ARE IDEMPOTENT, NOT NIGHTLY DUPLICATES. A fresh issue every night buries the label and
   trains people to ignore it. An open issue carrying both the label and the stable marker below is
   COMMENTED ON instead. A new issue is opened only when no such open issue exists.

4. IT SAYS WHAT FAILED. The body and each comment carry the run URL, the commit, the trigger, and
   the failing checks as the workflow reported them, so the issue is actionable without opening the
   run -- which matters most for the run whose logs have aged out.

Environment: GITHUB_TOKEN (or GH_TOKEN), GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_SERVER_URL,
GITHUB_SHA, GITHUB_REF_NAME, GITHUB_EVENT_NAME, GITHUB_WORKFLOW. GUARD_AUDIT_SUMMARY optionally
holds the human-readable list of what failed. GUARD_NOTIFY_FORCE_FAIL=1 forces the notification
path to fail, which is how the fail-closed behaviour is tested.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
LABEL = "guard-failure"
LABEL_COLOR = "b60205"
LABEL_DESC = "A repository guard failed. Watched by Lead-2-assistant; do not remove."

# Stable marker. The issue is matched on this, not on its title text, so the title can be reworded
# without the next run opening a second issue.
MARKER = "<!-- guard-audit-id: scheduled-whole-tree-guard-audit -->"
TITLE = "Scheduled guard audit is failing (whole-tree secret scan / full-history trailer check)"


class NotifyError(RuntimeError):
    """A failure of the notification path itself, as distinct from the guard failure it reports."""


def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not tok:
        raise NotifyError(
            "no GITHUB_TOKEN/GH_TOKEN in the environment. The workflow must pass "
            "`GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` and declare `issues: write`."
        )
    return tok


def api(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, raw
    except urllib.error.URLError as exc:
        raise NotifyError(f"{method} {url}: network failure: {exc.reason}") from exc


def ensure_label(repo: str) -> None:
    """Create `guard-failure` if it is missing. A missing label must never drop the alert."""
    status, payload = api("GET", f"/repos/{repo}/labels/{LABEL}")
    if status == 200:
        print(f"label {LABEL!r} already exists")
        return
    if status != 404:
        raise NotifyError(
            f"could not determine whether label {LABEL!r} exists (HTTP {status}): {payload!r}"
        )
    print(f"label {LABEL!r} is missing; creating it")
    status, payload = api(
        "POST",
        f"/repos/{repo}/labels",
        {"name": LABEL, "color": LABEL_COLOR, "description": LABEL_DESC},
    )
    # 422 covers the race where a concurrent run created it a moment ago.
    if status == 201:
        print(f"created label {LABEL!r}")
        return
    if status == 422:
        recheck, _ = api("GET", f"/repos/{repo}/labels/{LABEL}")
        if recheck == 200:
            print(f"label {LABEL!r} was created concurrently; fine")
            return
    raise NotifyError(
        f"could not create the {LABEL!r} label (HTTP {status}): {payload!r}. Without it the issue "
        f"cannot be labelled, and an unlabelled issue is not seen by whatever watches the label. "
        f"Create it by hand (Issues -> Labels) or grant `issues: write`."
    )


def find_open_issue(repo: str) -> dict | None:
    status, payload = api(
        "GET", f"/repos/{repo}/issues?state=open&labels={LABEL}&per_page=100"
    )
    if status != 200 or not isinstance(payload, list):
        raise NotifyError(f"could not list open {LABEL!r} issues (HTTP {status}): {payload!r}")
    for issue in payload:
        if "pull_request" in issue:
            continue
        if MARKER in (issue.get("body") or ""):
            return issue
    return None


def report() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "?")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else "(no run id)"
    summary = os.environ.get("GUARD_AUDIT_SUMMARY", "").strip() or "(no summary recorded)"
    return "\n".join(
        [
            f"- **Run:** {run_url}",
            f"- **Workflow:** `{os.environ.get('GITHUB_WORKFLOW', '?')}`",
            f"- **Trigger:** `{os.environ.get('GITHUB_EVENT_NAME', '?')}`",
            f"- **Ref:** `{os.environ.get('GITHUB_REF_NAME', '?')}`",
            f"- **Commit:** `{os.environ.get('GITHUB_SHA', '?')}`",
            "",
            "**What failed:**",
            "",
            "```",
            summary,
            "```",
        ]
    )


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise NotifyError("GITHUB_REPOSITORY is not set")

    # Test hook: forces this path to fail so that the workflow's fail-closed behaviour can be
    # verified deliberately rather than hoped for. Checked before any API call.
    if os.environ.get("GUARD_NOTIFY_FORCE_FAIL") == "1":
        raise NotifyError(
            "GUARD_NOTIFY_FORCE_FAIL=1 -- deliberately failing the notification path to verify "
            "that the job still goes red and that this failure is visible on its own."
        )

    ensure_label(repo)
    existing = find_open_issue(repo)

    if existing:
        number = existing["number"]
        print(f"refreshing existing open issue #{number} instead of opening a duplicate")
        status, payload = api(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            {"body": "The scheduled guard audit failed again.\n\n" + report()},
        )
        if status != 201:
            raise NotifyError(
                f"could not comment on issue #{number} (HTTP {status}): {payload!r}"
            )
        print(f"commented on {existing['html_url']}")
        return 0

    print("no open guard-failure issue with this marker; opening one")
    body = "\n\n".join(
        [
            "The scheduled guard audit failed.",
            report(),
            (
                "This issue is **refreshed with a comment** by later failing runs rather than "
                "duplicated, and it is labelled `guard-failure` because that label is watched. "
                "Close it once the audit is green; the next failure will open a fresh one."
            ),
            MARKER,
        ]
    )
    status, payload = api(
        "POST",
        f"/repos/{repo}/issues",
        {"title": TITLE, "body": body, "labels": [LABEL]},
    )
    if status != 201 or not isinstance(payload, dict):
        raise NotifyError(f"could not create the issue (HTTP {status}): {payload!r}")
    print(f"opened {payload['html_url']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotifyError as exc:
        # A DISTINCT error, deliberately worded so it is not mistaken for the guard failure it was
        # trying to report. The job is already red; this makes the second, quieter failure loud.
        print(
            f"::error title=guard-failure notification FAILED::{exc} "
            f"-- THE GUARD AUDIT FAILED *AND* THIS ALERT COULD NOT BE FILED. "
            f"Nothing is watching for this run. Fix the notification path as well as the guard.",
            file=sys.stderr,
        )
        sys.exit(1)
