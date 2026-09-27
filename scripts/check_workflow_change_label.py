#!/usr/bin/env python3
"""A pull request that edits CI or guard machinery must carry the `workflow-change` label.

WHY THIS EXISTS
---------------
A `pull_request` run -- and a `workflow_dispatch` run on a branch -- reads its workflow definition
FROM THE BRANCH IT RUNS ON. So a pull request can edit the very file that gates it: add an `if:`
that never fires, add a `paths-ignore:`, delete a step, delete the whole job. That is inherent to
GitHub Actions; it is not specific to any one pull request in this repository's history, and
nothing inside a `pull_request` workflow can police it, because the thing doing the policing is
also branch-controlled.

`pull_request_target` is the only trigger that can. It runs the workflow definition FROM THE BASE
BRANCH, which a pull request cannot edit. That is why this guard lives in its own
`pull_request_target` workflow (`.github/workflows/workflow-change.yml`) and not as a step in
`ci.yml`.

`prabhasa-nyaya`'s `.github/workflows/data-change-label.yml` says, in its own header, never to
introduce `pull_request_target` there -- and that is right FOR THAT GUARD: reading labels on
`pull_request` is enough for it, and the danger of `pull_request_target` is that it pairs a
write-capable token with an untrusted head checkout. This guard is the one case where the trigger
is load-bearing, and it answers that danger directly rather than by assertion:

  * it NEVER checks out or executes pull-request code -- the workflow checks out the BASE sha and
    only the base sha, purely to get this file, and never fetches or references the head ref/sha;
  * the workflow declares `contents: read` and `pull-requests: read` and nothing else, so the
    token this runs with is not write-capable at all;
  * no pull-request-controlled string (title, branch name, label name, body) is ever interpolated
    into a `run:` block, where a `$(...)` inside it would execute.

WHAT IS ENFORCED, AND WHAT IS NOT
---------------------------------
ENFORCED (exit 1): the pull request touches at least one path matching PROTECTED_PATTERNS below,
and either the `workflow-change` label is absent, or it is present but the most recent `labeled`
event for it was performed by an account that is not listed in
`scripts/workflow_change_label_setters.txt` AS THAT FILE STANDS ON THE BASE BRANCH.

NOT ENFORCED, and not enforceable here:

  * WHICH PERSON acted. The actor check proves the ACCOUNT. Lead-2, Lead-2-assistant and every
    agent holding the token all act as `AxisMeru`, so a green tick from this guard means "the
    authorised account applied the label", never "a named human reviewed the workflow diff".
  * THAT ANYONE LOOKED. Nothing in the GitHub API distinguishes a label applied after reading a
    diff from one applied without looking.
  * THE BROADER HOLE. This guard makes an unreviewed edit to CI machinery go red. It does NOT and
    cannot stop a branch from editing its own workflow: `pull_request`/`workflow_dispatch` runs
    still read their definition from the branch. What it does is ensure that such an edit cannot
    land without the authorised account having said so. Until `workflow-change` is made a REQUIRED
    status context alongside `guards` in this repository's branch-protection settings -- an
    operator decision, not this guard's -- a pull request can still merge with this check red.

WHY DELETIONS COUNT HERE AND DO NOT IN #26
------------------------------------------
`prabhasa-nyaya`'s data guard deliberately ignores `removed`, because the rule it implements is
"adds or modifies" a data file. For THIS guard, deletion is the primary attack: deleting
`.github/workflows/ci.yml` deletes the `guards` job. So every status except `unchanged` counts --
see `TOUCHING_STATUSES` -- and a `renamed` entry is matched on BOTH its new `filename` and its
`previous_filename`, because renaming `scripts/check_no_private_data.py` to `scripts/npd.py`
removes a guard while the new path matches nothing.

FAILS CLOSED, EVERYWHERE
------------------------
Every input the verdict rests on is either proven or the run is a hard failure. In particular an
EMPTY changed-file list is never accepted on faith: GitHub's own `changed_files` count for the pull
request is compared against the number of entries actually paged in, and a mismatch is a failure
that says so. "We only read page one" must never render as "nothing to see" -- that is the bug
class this paragraph exists to prevent, and a genuinely empty diff is reported with its own
distinct wording so the two can never be confused in a log.

The hard-failure cases, each with its own message:

  * the repository, pull request number or token is missing from the environment;
  * the pull request object cannot be read, or lacks `base.ref` / `base.sha` / `changed_files`;
  * the changed-file list cannot be paged in completely (count mismatch, or page cap hit), or an
    entry has no filename or no status;
  * the label list cannot be read;
  * the label is present but the timeline cannot be read, or holds no `labeled` event for it, or
    such an event has no actor, or that actor login does not resolve to a real account;
  * the setter allowlist cannot be fetched from the base ref, or is empty, or holds an entry with
    no reason.

THERE IS NO DEGRADED MODE. When the label is present and the actor cannot be established -- API
error, rate limit, a timeline with no `labeled` event, an event with no actor, a login that does
not resolve -- this guard FAILS. It never falls back to "the label is there, good enough".
Presence-only is the fail-open pattern that has now shipped inside several guards across these two
repositories.

USAGE
-----
    check_workflow_change_label.py [--pr N] [--repo OWNER/NAME] [--allowlist-file FILE]

Exit status: 0 if the enforced rule holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# ==========================================================================================
# THE PATTERN TABLE. One place, one reason per line. This is the part a human amends.
#
# HOW A PATTERN IS MATCHED. `**/` is zero or more directory segments, a trailing `/**` is
# everything below that directory, `*` and `?` never cross a `/`, and the path is LOWERCASED before
# matching (see `matches`). Lowercasing is the safe direction: it can only make a pattern match
# MORE paths, so its worst case is a false failure a human clears, never a false pass. There is no
# path in this tree that differs from another only by case.
#
# The four paths in the ruling are marked RULING. Two further entries are marked WIDENING and are
# flagged for the reviewer in the pull request body rather than slipped in: they came out of the
# adversarial pass on this guard, and either can be deleted without touching anything else.
# ==========================================================================================

#: (glob, why this path is CI/guard machinery). A changed path matching any of these is protected.
PROTECTED_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        ".github/workflows/**",
        "RULING. Every workflow definition. A `pull_request` or `workflow_dispatch` run reads its "
        "own definition from the branch, so an edit here can disable the check that is meant to "
        "be judging the edit. Covers a file directly in the directory and one nested below it.",
    ),
    (
        "scripts/check_*.py",
        "RULING. The guard scripts `ci.yml`'s `guards` job invokes: check_no_private_data, "
        "check_no_secret_dockerfile_args, check_fail_open_defaults, check_no_secrets_in_diff, "
        "check_score_bin_gate_ran, check_sign_carryover -- and this file. Editing one of these "
        "weakens a gate without touching any workflow YAML.",
    ),
    (
        "scripts/uncollected_test_baseline.txt",
        "RULING. A waiver list for tests that are not collected. NO SUCH FILE EXISTS IN THIS "
        "REPOSITORY as of this guard's authoring (the comparable files here are "
        "scripts/fail_open_defaults_baseline.txt and scripts/secret_scan_baseline.txt). The path "
        "is guarded anyway, exactly as the ruling names it, so that the first one committed is "
        "covered on the commit that creates it rather than on a later amendment to this table.",
    ),
    (
        ".githooks/**",
        "RULING, on Tag's proposal. `.githooks/commit-msg` is what enforces the "
        "SharathSPhD/admin@axismeru.com identity and strips attribution trailers, and "
        "`.githooks/pre-commit` is what refuses a commit on `main` in the primary checkout. Both "
        "are guard machinery in every sense except that they run before CI. Easy to drop: delete "
        "this entry and nothing else changes.",
    ),
    (
        "scripts/**/check_*.py",
        "WIDENING (adversarial pass). `scripts/check_*.py` above does not cross a `/`, so a guard "
        "filed one directory deeper -- `scripts/guards/check_x.py` -- would match nothing. There "
        "is no such directory today; this is here so that the first one is not a bypass. It also "
        "subsumes the ruling's glob, which is kept above so that the ruling's own wording is what "
        "a log reports for the paths it covers.",
    ),
    (
        "scripts/workflow_change_label_setters.txt",
        "WIDENING (adversarial pass). The allowlist this guard reads. Editing it in a pull request "
        "cannot authorise that pull request -- the allowlist is fetched from the BASE ref, see "
        "`fetch_allowlist_at_base` -- but a change to who may clear CI edits is itself a change "
        "that should not land unremarked.",
    ),
)

#: File statuses that count as touching a path. Everything except `unchanged`, because DELETING a
#: guard is the attack this exists to catch -- see the module docstring. `renamed` is additionally
#: matched on `previous_filename`.
TOUCHING_STATUSES: frozenset[str] = frozenset(
    {"added", "modified", "removed", "renamed", "copied", "changed"}
)

#: The label that clears a CI/guard change.
WORKFLOW_CHANGE_LABEL = "workflow-change"

DEFAULT_ALLOWLIST = "scripts/workflow_change_label_setters.txt"

#: Pagination safety cap. `/pulls/N/files` itself stops at 3,000 files, so 30 pages of 100 is the
#: whole of what GitHub will ever hand over; hitting the cap is a hard failure rather than a silent
#: truncation of the list the verdict is computed from.
PER_PAGE = 100
MAX_PAGES = 30

API_ROOT = "https://api.github.com"


class GuardFailure(Exception):
    """Something the verdict depends on could not be determined. Always exit 1, never a pass."""


# ------------------------------------------------------------------------------------------
# Glob matching
# ------------------------------------------------------------------------------------------


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a path glob to an anchored regex with gitignore-ish `**` semantics.

    `**/` is zero or more directory segments, a trailing `/**` is everything below, `*` and `?`
    never cross a `/`. Everything else is literal. Written out rather than using `fnmatch`, whose
    `*` DOES cross `/`: under `fnmatch`, `scripts/check_*.py` would match
    `scripts/deep/check_x.py`, which is a silent scope error in a guard.

    Lifted from prabhasa-nyaya's `scripts/check_data_change_label.py` INCLUDING the end-of-string
    condition on the `/**` branch, which is the whole point of copying it rather than rewriting it:
    without that condition the branch fires on the `/` of `scripts/**/check_*.py` and translates it
    to `scripts/.*/check_[^/]*\\.py`, which quietly requires an intermediate directory. In #26 that
    defect made nine real data files invisible to the guard that was supposed to see them.
    """
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("/.*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_PROTECTED_RES = tuple((glob, glob_to_regex(glob), why) for glob, why in PROTECTED_PATTERNS)


def matches(path: str) -> tuple[str, str] | None:
    """(glob, reason) for the first PROTECTED_PATTERNS entry `path` matches, else None.

    Lowercases the path first. The regexes are anchored at both ends, so a path that merely
    CONTAINS a protected prefix somewhere in the middle -- `docs/.github/workflows/notes.yml`,
    `vendor/.githooks/x` -- does not match. That is deliberate: those are not the paths GitHub
    Actions or `core.hooksPath` read.
    """
    low = path.lower()
    for glob, rx, why in _PROTECTED_RES:
        if rx.match(low):
            return glob, why
    return None


# ------------------------------------------------------------------------------------------
# Setter allowlist
# ------------------------------------------------------------------------------------------


def parse_allowlist(text: str, source: str) -> dict[str, str]:
    """`<github-login>  <reason>` per line -> {login: reason}. Comments and blanks ignored.

    An entry with no reason text is a hard failure, not a silent permission: a waiver nobody can
    read is a waiver nobody re-reads. Same rule and same wording as
    prabhasa-nyaya's `scripts/data_change_label_setters.txt`. Logins are compared
    case-insensitively because GitHub logins are.
    """
    entries: dict[str, str] = {}
    bad: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        login = parts[0]
        reason = parts[1].strip() if len(parts) > 1 else ""
        if not reason:
            bad.append(login)
            continue
        entries[login.lower()] = reason

    if bad:
        raise GuardFailure(
            f"{source}: these entries have no reason and therefore authorise nobody: "
            + ", ".join(sorted(bad))
        )
    if not entries:
        raise GuardFailure(
            f"{source} holds no entries, so nobody is authorised to set "
            f"`{WORKFLOW_CHANGE_LABEL}`. This is the fail-closed direction on purpose: add a "
            "GitHub login with a reason before the label is used."
        )
    return entries


def fetch_allowlist_at_base(fetch, repo: str, base_ref: str, path: str) -> dict[str, str]:
    """The setter allowlist AS IT STANDS ON THE BASE BRANCH, via the contents API.

    Pinned to `base_ref` -- `main` -- and never to the pull request's head, so a pull request
    cannot add an account to the list in the same change it wants that account to authorise. That
    self-authorisation is the whole control undone in one line of a text file.

    `base_ref` rather than `base.sha`: it is the CURRENT tip of the base branch, so amending the
    allowlist on `main` takes effect on open pull requests without reopening them, and it is still
    a ref the pull request cannot write to.

    Any failure here is a hard failure. A missing or unreadable allowlist means nobody is
    authorised, and that must be loud.
    """
    url = f"{API_ROOT}/repos/{repo}/contents/{path}?ref={base_ref}"
    payload, _ = fetch(url)
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise GuardFailure(
            f"could not determine who may set `{WORKFLOW_CHANGE_LABEL}`: {path} at {base_ref} did "
            "not come back as a base64 file from the contents API."
        )
    try:
        text = base64.b64decode(payload.get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise GuardFailure(
            f"could not determine who may set `{WORKFLOW_CHANGE_LABEL}`: {path} at {base_ref} "
            f"could not be decoded: {exc}"
        ) from exc
    return parse_allowlist(text, f"{path}@{base_ref}")


# ------------------------------------------------------------------------------------------
# GitHub API
# ------------------------------------------------------------------------------------------


def make_fetcher(token: str):
    """Return `fetch(url) -> (payload, link_header)`. Injected so tests never touch the network."""

    def fetch(url: str) -> tuple[object, str]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pravrudhi-workflow-change-guard",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.headers.get("Link", "") or ""
        except urllib.error.HTTPError as exc:
            raise GuardFailure(f"GET {url} failed: HTTP {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise GuardFailure(f"GET {url} failed: {exc}") from exc

    return fetch


def _has_next(link_header: str) -> bool:
    return 'rel="next"' in link_header


def page_all(fetch, url: str, what: str) -> list[dict]:
    """Page a list endpoint to exhaustion. Hitting MAX_PAGES is a hard failure, not a truncation."""
    items: list[dict] = []
    sep = "&" if "?" in url else "?"
    for _page in range(1, MAX_PAGES + 1):
        payload, link = fetch(f"{url}{sep}per_page={PER_PAGE}&page={_page}")
        if not isinstance(payload, list):
            raise GuardFailure(f"could not determine {what}: expected a JSON list from {url}")
        items.extend(payload)
        if not _has_next(link):
            return items
    raise GuardFailure(
        f"could not determine {what}: more than {MAX_PAGES * PER_PAGE} entries; the list was not "
        "paged to the end, so no verdict can be computed from it."
    )


def fetch_pull_request(fetch, repo: str, number: int) -> dict:
    payload, _ = fetch(f"{API_ROOT}/repos/{repo}/pulls/{number}")
    if not isinstance(payload, dict):
        raise GuardFailure(f"could not read pull request {repo}#{number}")
    if not isinstance(payload.get("changed_files"), int):
        raise GuardFailure(
            "could not determine the pull request's `changed_files`; without it the changed-file "
            "list cannot be proven complete."
        )
    base = payload.get("base") or {}
    if not base.get("ref") or not base.get("sha"):
        raise GuardFailure(
            "could not resolve the pull request's base ref/sha; without a base there is nothing "
            "to diff this pull request against, and no trustworthy ref to read the allowlist from."
        )
    return payload


def fetch_touched_paths(fetch, repo: str, number: int, expected: int) -> list[tuple[str, str]]:
    """The pull request's touched (path, status) pairs, PROVEN complete.

    `expected` is GitHub's own `changed_files` count from the pull request object. If the number of
    ENTRIES paged in does not equal it, this raises: an incomplete list that happens to contain no
    protected path is indistinguishable from a complete one, and "we never looked" must never read
    as "nothing to see". This is the single most important line in the file.

    A `renamed` entry contributes BOTH paths -- the new `filename` and the `previous_filename` --
    because a rename removes the old path, and removing a guard script by renaming it is exactly
    the bypass a filename-only check would wave through. The reconciliation above counts ENTRIES,
    not paths, so the extra path from a rename cannot make the count disagree.
    """
    entries = page_all(
        fetch, f"{API_ROOT}/repos/{repo}/pulls/{number}/files", "the changed-file list"
    )
    if len(entries) != expected:
        raise GuardFailure(
            f"could not determine the changed-file list: GitHub reports {expected} changed files "
            f"for {repo}#{number} but the files endpoint returned {len(entries)}. (That endpoint "
            "caps at 3,000 files; a pull request larger than that cannot be checked here and must "
            "be split.) Refusing to compute a verdict from a partial list."
        )
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("filename") or not entry.get("status"):
            raise GuardFailure(
                "could not determine the changed-file list: an entry from the files endpoint has "
                "no filename or no status."
            )

    touched: list[tuple[str, str]] = []
    for entry in entries:
        status = entry["status"]
        if status not in TOUCHING_STATUSES:
            continue
        touched.append((entry["filename"], status))
        previous = entry.get("previous_filename")
        if isinstance(previous, str) and previous:
            touched.append((previous, f"{status} (was this path)"))
    return touched


def fetch_labels(fetch, repo: str, number: int) -> list[str]:
    """The pull request's labels, read LIVE rather than from the event payload.

    A re-run of an older workflow run replays that run's original event payload, so
    `event.pull_request.labels` can be arbitrarily stale -- which is exactly the situation this
    guard creates, because it asks someone to add a label to a run that already went red. Reading
    the label list from the API at check time means "re-run jobs" gives the current answer.

    The paged `/issues/{n}/labels` endpoint rather than the `labels` array on the pull request
    object: that array is capped and carries no `Link` header, so it cannot be PROVEN complete.
    """
    entries = page_all(fetch, f"{API_ROOT}/repos/{repo}/issues/{number}/labels", "the label list")
    names = [e.get("name") if isinstance(e, dict) else None for e in entries]
    if any(not isinstance(n, str) or not n for n in names):
        raise GuardFailure("could not determine the label list: an entry has no name")
    return [n for n in names if isinstance(n, str)]


def fetch_last_labeller(fetch, repo: str, number: int, label: str) -> tuple[str, str]:
    """(actor login, when) of the MOST RECENT `labeled` event for `label`.

    Most recent, not first: a label can be added, removed and added again by a different account,
    and the one that counts is the one that put it there now. The timeline is chronological, so the
    last match wins. No matching event at all is a hard failure -- the label is visibly present, so
    a timeline that does not explain it means this guard cannot establish provenance, and an
    unexplained label must not clear a CI change.
    """
    entries = page_all(
        fetch, f"{API_ROOT}/repos/{repo}/issues/{number}/timeline", "the pull request timeline"
    )
    found: tuple[str, str] | None = None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("event") != "labeled":
            continue
        if (entry.get("label") or {}).get("name") != label:
            continue
        login = (entry.get("actor") or {}).get("login")
        if not isinstance(login, str) or not login:
            raise GuardFailure(
                f"could not determine who applied `{label}`: a `labeled` event in the timeline "
                "has no actor."
            )
        found = (login, entry.get("created_at") or "an unrecorded time")
    if found is None:
        raise GuardFailure(
            f"the `{label}` label is present on {repo}#{number} but the timeline holds no "
            "`labeled` event for it, so this guard cannot establish who applied it. Refusing to "
            "treat an unexplained label as authorisation."
        )
    return found


def resolve_actor(fetch, login: str) -> str:
    """The canonical login for `login`, proven to exist. Any failure to resolve is a hard failure.

    The timeline hands back whatever string it holds. This turns that string into a confirmed
    account through `GET /users/{login}`, so an actor that cannot be resolved -- a deleted account,
    a rate-limited or 403'd read, a payload with no `login` -- fails the job instead of being
    compared against the allowlist on trust. A bracketed app login (`claude[bot]`) is not a
    `/users/` resource and 404s here, which is the intended direction: no agent account is
    allowlisted, so an app-applied label must not clear a CI change.
    """
    payload, _ = fetch(f"{API_ROOT}/users/{urllib.parse.quote(login, safe='')}")
    if not isinstance(payload, dict):
        raise GuardFailure(f"could not resolve the account @{login}: /users/{login} is not an object")
    resolved = payload.get("login")
    if not isinstance(resolved, str) or not resolved:
        raise GuardFailure(f"could not resolve the account @{login}: /users/{login} carries no login")
    if resolved.lower() != login.lower():
        raise GuardFailure(
            f"could not resolve the account @{login}: /users/{login} answered for @{resolved} "
            "instead. Refusing to authorise against an account this guard cannot pin down."
        )
    return resolved


# ------------------------------------------------------------------------------------------
# Verdict
# ------------------------------------------------------------------------------------------


def check(
    fetch,
    repo: str,
    number: int,
    out=None,
    allowlist_file: str | None = None,
) -> int:
    """0 if the enforced rule holds, 1 if it does not. GuardFailure propagates to `main`."""
    out = sys.stdout if out is None else out
    pull = fetch_pull_request(fetch, repo, number)
    expected = pull["changed_files"]
    touched = fetch_touched_paths(fetch, repo, number, expected)

    hits: list[tuple[str, str, str]] = []
    for path, status in touched:
        hit = matches(path)
        if hit is not None:
            hits.append((path, status, hit[0]))

    if expected == 0:
        # An EMPTY DIFF, proven empty (the files endpoint agreed), which is a different fact from
        # "files changed, none of them protected". Said in its own words so a log can never be read
        # as the guard having examined a list it never fetched.
        print(
            f"{repo}#{number}: GitHub reports 0 changed files and the files endpoint returned 0 "
            "entries -- an empty diff, not an unread list. Nothing to protect.",
            file=out,
        )
        return 0

    print(
        f"{repo}#{number}: {expected} changed file(s) confirmed complete, {len(touched)} path(s) "
        f"touched (renames count both names), {len(hits)} matching a protected pattern",
        file=out,
    )

    if not hits:
        print(
            "no CI or guard machinery touched; the `workflow-change` label is not required",
            file=out,
        )
        return 0

    print(f"protected paths in this pull request ({len(hits)}):", file=out)
    for path, status, glob in hits:
        print(f"  {status:24s} {path}    [{glob}]", file=out)

    labels = fetch_labels(fetch, repo, number)
    if WORKFLOW_CHANGE_LABEL not in labels:
        print(
            f"::error title=CI change without the `{WORKFLOW_CHANGE_LABEL}` label::This pull "
            f"request touches {len(hits)} protected path(s) and does not carry the "
            f"`{WORKFLOW_CHANGE_LABEL}` label. Ask the authorised account to review the diff and "
            "apply it (this guard can confirm only that the authorised ACCOUNT applied the label, "
            "never which person did); this job re-runs on `labeled`.",
            file=out,
        )
        print(f"labels present: {', '.join(sorted(labels)) or '(none)'}", file=out)
        return 1

    # The allowlist comes from the BASE branch, not from this checkout -- otherwise a pull request
    # could list an account and then have that account authorise it. `allowlist_file` is the
    # offline/test override only; the workflow never passes it.
    if allowlist_file is not None:
        with open(allowlist_file, encoding="utf-8") as fh:
            setters = parse_allowlist(fh.read(), allowlist_file)
        source = allowlist_file
    else:
        base_ref = pull["base"]["ref"]
        setters = fetch_allowlist_at_base(fetch, repo, base_ref, DEFAULT_ALLOWLIST)
        source = f"{DEFAULT_ALLOWLIST}@{base_ref}"

    # Only now, with the label confirmed still present above, is the timeline consulted. Any
    # failure in here raises GuardFailure and fails the job -- there is no presence-only fallback.
    login, when = fetch_last_labeller(fetch, repo, number, WORKFLOW_CHANGE_LABEL)
    login = resolve_actor(fetch, login)
    if login.lower() not in setters:
        print(
            f"::error title=`{WORKFLOW_CHANGE_LABEL}` set by an account that is not authorised::"
            f"`{WORKFLOW_CHANGE_LABEL}` was applied by @{login} at {when}, who is not in "
            f"{source}. Authorised: "
            + ", ".join(sorted(setters))
            + ". Remove the label and ask an authorised account to apply it after reading the "
            "diff, or add this account to the allowlist with a reason.",
            file=out,
        )
        return 1

    print(
        f"`{WORKFLOW_CHANGE_LABEL}` applied by @{login} at {when} -- authorised "
        f"({setters[login.lower()]}). This proves the ACCOUNT, not which person acted as it.",
        file=out,
    )
    return 0


def resolve_pr_number(explicit: int | None) -> int:
    """PR number from --pr, else the event payload, else the ref. Missing is a hard failure."""
    if explicit is not None:
        return explicit
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        try:
            with open(event_path, encoding="utf-8") as fh:
                event = json.load(fh)
            number = (event.get("pull_request") or {}).get("number")
            if isinstance(number, int):
                return number
        except (OSError, ValueError) as exc:
            raise GuardFailure(f"could not read {event_path}: {exc}") from exc
    ref = os.environ.get("GITHUB_REF", "")
    match = re.match(r"^refs/pull/(\d+)/", ref)
    if match:
        return int(match.group(1))
    raise GuardFailure(
        "could not determine which pull request to check: no --pr, no pull_request in "
        "GITHUB_EVENT_PATH, and GITHUB_REF is not a pull ref."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--allowlist-file",
        default=None,
        help=(
            "read the setter allowlist from this LOCAL file instead of from the base ref. For "
            "offline runs and this repository's own tests only -- CI must not pass it, because a "
            "local read is a read of whatever tree the job happens to have checked out."
        ),
    )
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--repo", default=None, help="OWNER/NAME; default $GITHUB_REPOSITORY")
    args = parser.parse_args(argv)

    try:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
        if not repo:
            raise GuardFailure("could not determine the repository: set --repo or GITHUB_REPOSITORY")
        token = os.environ.get("GITHUB_TOKEN") or ""
        if not token:
            raise GuardFailure(
                "could not read labels or the timeline: GITHUB_TOKEN is not set. The workflow "
                "must pass it in; an unauthenticated read of a private repository is a 404, and a "
                "404 must not look like 'no label'."
            )
        number = resolve_pr_number(args.pr)
        return check(
            make_fetcher(token),
            repo,
            number,
            allowlist_file=args.allowlist_file,
        )
    except GuardFailure as exc:
        print(f"::error title=workflow-change guard could not run::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
