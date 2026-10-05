"""Tests for `scripts/check_workflow_change_label.py` -- the `workflow-change` label guard.

WHAT THESE TESTS ARE FOR, in the order that matters.

`TestFailsClosed` is the point of the file. The failure mode a guard like this actually dies of is
not a missed pattern -- it is a green tick produced without looking. So there is one negative
fixture per way this guard could pass without knowing the answer:

  * the changed-file list came back short of GitHub's own `changed_files` count (an incomplete list
    that happens to hold no protected path is indistinguishable from a complete one);
  * the list was longer than the page cap, so it was never paged to the end;
  * `changed_files` is missing, so completeness cannot be proven at all;
  * the base ref/sha cannot be resolved;
  * an entry from the files endpoint has no filename or no status;
  * the label list cannot be read, or an entry in it has no name;
  * the label is present but the timeline read fails, or holds no `labeled` event for it, or the
    event has no actor, or the actor login does not resolve to a real account -- NONE of which may
    degrade to "the label is there, good enough";
  * the repository's default branch cannot be determined, so there is no ref to read an
    allowlist from;
  * the setter allowlist cannot be fetched, came back with no blob sha, is empty, or holds an
    entry with no reason;
  * the guard's OWN SCRIPT is absent, unreadable or unparseable at the checked-out ref -- which
    cannot be detected from inside the script, so `TestThePreflightNamesEveryCause` extracts the
    workflow's preflight step and runs it.

`TestEmptyDiffIsNotTheSameAsNoProtectedFiles` pins requirement 11's last case. Both outcomes are a
pass, and that is correct, but they are DIFFERENT FACTS and the log must say which one happened --
otherwise "we never fetched the list" and "we fetched it and it was clean" read identically to the
next person debugging a suspiciously green run.

`TestAllowlistComesFromTheBaseRef` pins the one property that makes the actor check worth anything:
the allowlist is read from `main` through the contents API, not from any checkout.

`TestGlobSemantics` and `TestPatternTable` carry a fixture for every pattern, including the two the
brief names as traps: `scripts/checkfoo.py`, which must NOT match `scripts/check_*.py`, and a path
that merely CONTAINS `.github/workflows` further along the string, which must not match either.
Glob translation is where #26's own guard broke -- its `/**` branch had no end-of-string condition
and made nine real data files invisible -- so `test_the_slash_before_a_doubleslash_star_is_not_
swallowed` is a regression test for a defect in the code this translator was copied from.

`TestBypasses` is the adversarial pass, written as tests rather than as a paragraph: a guard script
removed rather than edited, a guard script RENAMED so its new path matches nothing, a `labeled`
event pushed onto page two of the timeline, a label added by an authorised account then re-added by
someone else, and an upper-cased path.

`TestWorkflowWiring` reads the workflow YAML as text. `pull_request_target` with
`types: [... labeled, unlabeled]`, read-only permissions, a default-branch checkout and no `${{ }}` in
any `run:` block are the four properties that make this guard both effective and safe, and a
reviewer who does not know that will delete one as noise. A test is a comment that fights back.
"""

from __future__ import annotations

import base64
import inspect
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_workflow_change_label import (  # noqa: E402 -- path set just above, repo idiom (test_check_no_secrets_in_diff.py:33)
    DEFAULT_ALLOWLIST,
    MAX_PAGES,
    PER_PAGE,
    PROTECTED_PATTERNS,
    TOUCHING_STATUSES,
    VERDICT_ACTOR_UNRESOLVED,
    VERDICT_CLEARED,
    VERDICT_COULD_NOT_RUN,
    VERDICT_EMPTY_DIFF,
    VERDICT_LABEL_MISSING,
    VERDICT_NO_PROTECTED_PATHS,
    VERDICT_NOT_AUTHORISED,
    VERDICT_PREFIX,
    WORKFLOW_CHANGE_LABEL,
    GuardFailure,
    build_parser,
    check,
    fetch_allowlist_at_ref,
    fetch_default_branch,
    glob_to_regex,
    main,
    matches,
    parse_allowlist,
    resolve_pr_number,
)

REPO = "AxisMeru/pravrudhi"
PR = 78
ALLOWLIST_TEXT = "# comment\n\nAxisMeru  the account the lead acts as\n"
#: The blob sha the contents API hands back for the allowlist. The guard quotes it in every
#: message that names the allowlist, so that "`@main`" -- a moving target -- is not the whole of
#: what a reader is given. `fetch_allowlist_at_ref` fails closed if it is absent.
ALLOWLIST_BLOB = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "workflow-change.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")

#: The workflow with its comment lines removed. Every "this string must NEVER appear" assertion
#: below runs against THIS and not against the raw text, because the file's own header quotes the
#: dangerous constructs in order to forbid them -- `head.sha` appears there as a warning, and a
#: test that could not tell a warning from a use would force the warning to be deleted.
WORKFLOW_CODE = "".join(
    line for line in WORKFLOW_TEXT.splitlines(keepends=True) if not line.lstrip().startswith("#")
)

#: The guard's own script, as the workflow's preflight step names it. One constant so that the
#: tests and the assertion "every literal path in the change names a path that exists" cannot
#: drift apart.
GUARD_SCRIPT = "scripts/check_workflow_change_label.py"

#: This very file, as `PROTECTED_PATTERNS` names it. Protected on Lead-2's ruling of 2026-09-27:
#: the guard's safety properties are asserted here and nowhere else, so weakening an assertion
#: removes a gate just as editing `ci.yml` does.
GUARD_TESTS = "tests/test_check_workflow_change_label.py"

#: The PROTECTED_PATTERNS entry that covers it, as adopted on Lead-2's ruling of 2026-09-27: a
#: glob mirroring `scripts/check_*.py`, so it covers the other guard scripts' tests too and
#: survives a rename of any one of them. Because it carries a wildcard it is NOT covered by
#: `test_every_literal_pattern_matches_its_own_path`, which is why both directions are asserted
#: explicitly in `test_the_guard_tests_glob_matches_guard_tests_and_nothing_else`.
GUARD_TESTS_GLOB = "tests/test_check_*.py"

#: The three reason tokens the preflight step distinguishes. Each is a DIFFERENT repository state,
#: and #103's rule is that a proof asserts on which token appeared, never on redness alone -- so
#: two failure paths that mean different things must not be provable by one assertion.
PREFLIGHT_REASONS = (
    "guard-file-absent",
    "guard-file-unreadable",
    "guard-script-unparseable",
)


def _workflow_steps() -> list[dict]:
    """The parsed steps of the `workflow-change` job. Parsed, not grepped, on purpose.

    A text assertion is satisfied by a commented-out line or by a second occurrence somewhere
    else in the file; the parsed step list is what GitHub Actions will actually run.
    """
    doc = yaml.safe_load(WORKFLOW_TEXT)
    steps = doc["jobs"]["workflow-change"]["steps"]
    assert isinstance(steps, list) and steps
    return steps


def _preflight_body() -> str:
    """The preflight step's `run:` script, lifted out of the real workflow file.

    THE TESTS BELOW RUN THIS, not a copy of it pasted into the test file. A copy would pass
    forever after someone edited the workflow, which is the failure mode these tests exist to
    prevent: the artifact under test is the YAML that CI executes.
    """
    named = [s for s in _workflow_steps() if "Preflight" in (s.get("name") or "")]
    assert len(named) == 1, f"expected exactly one preflight step, found {len(named)}"
    body = named[0]["run"]
    assert isinstance(body, str)
    # Non-vacuity: a step that had been gutted to `true` would otherwise pass every test below.
    assert len(body.splitlines()) >= 15, "the preflight step looks gutted"
    for reason in PREFLIGHT_REASONS:
        assert reason in body, f"the preflight step no longer names {reason}"
    assert GUARD_SCRIPT in body
    return body


class FakeAPI:
    """A recording stand-in for the GitHub REST API.

    Every test drives the guard through this rather than the network, so the tests are fast,
    hermetic, and able to express "this endpoint 403s" -- which is most of what needs testing.
    `calls` is asserted on directly where the point of a test is WHICH endpoint was read (live
    labels rather than the event payload; the allowlist at the base ref rather than at head).
    """

    def __init__(
        self,
        *,
        pull: dict | None = None,
        files: list[list[dict]] | None = None,
        labels: list[list[dict]] | None = None,
        timeline: list[list[dict]] | None = None,
        users: dict | None = None,
        allowlist: str | None = ALLOWLIST_TEXT,
        allowlist_blob: str | None = ALLOWLIST_BLOB,
        default_branch: str | None = "main",
        errors: tuple[str, ...] = (),
    ) -> None:
        self.pull = pull
        self.files = files if files is not None else [[]]
        self.labels = labels if labels is not None else [[]]
        self.timeline = timeline if timeline is not None else [[]]
        self.users = users
        self.allowlist = allowlist
        self.allowlist_blob = allowlist_blob
        self.default_branch = default_branch
        self.errors = errors
        self.calls: list[str] = []

    def __call__(self, url: str) -> tuple[object, str]:
        self.calls.append(url)
        for fragment in self.errors:
            if fragment in url:
                raise GuardFailure(f"GET {url} failed: HTTP 403 Forbidden")
        path, _, query = url.partition("?")
        # The repository object, for `default_branch`. Matched before the endpoints below because
        # its URL is a prefix of theirs.
        if path == f"https://api.github.com/repos/{REPO}":
            return ({} if self.default_branch is None else {"default_branch": self.default_branch}), ""
        page = 1
        for part in query.split("&"):
            if part.startswith("page="):
                page = int(part[5:])
        if path.endswith(f"/pulls/{PR}"):
            return self.pull, ""
        if path.endswith(f"/pulls/{PR}/files"):
            return self._page(self.files, page)
        if path.endswith(f"/issues/{PR}/labels"):
            return self._page(self.labels, page)
        if path.endswith(f"/issues/{PR}/timeline"):
            return self._page(self.timeline, page)
        if "/users/" in path:
            if self.users == "404":
                raise GuardFailure(f"GET {url} failed: HTTP 404 Not Found")
            if self.users is None:
                import urllib.parse as _up

                return {"login": _up.unquote(path.rsplit("/", 1)[1])}, ""
            return self.users, ""
        if "/contents/" in path:
            if self.allowlist is None:
                raise GuardFailure(f"GET {url} failed: HTTP 404 Not Found")
            payload: dict[str, object] = {
                "encoding": "base64",
                "content": base64.b64encode(self.allowlist.encode()).decode(),
            }
            if self.allowlist_blob is not None:
                payload["sha"] = self.allowlist_blob
            return payload, ""
        raise AssertionError(f"unexpected URL in test: {url}")

    @staticmethod
    def _page(pages: list[list[dict]], page: int) -> tuple[object, str]:
        body = pages[page - 1] if page - 1 < len(pages) else []
        link = '<next>; rel="next"' if page < len(pages) else ""
        return body, link


def pull_obj(*, changed: int, base: str = "main") -> dict:
    return {
        "changed_files": changed,
        "base": {"ref": base, "sha": "b" * 40},
        "head": {"sha": "h" * 40},
    }


def files(*paths: str, status: str = "modified") -> list[list[dict]]:
    return [[{"filename": p, "status": status} for p in paths]]


def labelled_by(login: str, when: str = "2026-09-27T12:00:00Z") -> dict:
    return {
        "event": "labeled",
        "label": {"name": WORKFLOW_CHANGE_LABEL},
        "actor": {"login": login},
        "created_at": when,
    }


def run(api: FakeAPI) -> tuple[int, str]:
    out = io.StringIO()
    code = check(api, REPO, PR, out=out)
    return code, out.getvalue()


def cleared(*paths: str, login: str = "AxisMeru", status: str = "modified") -> FakeAPI:
    """A pull request touching `paths`, labelled by `login`. The happy path, parameterised."""
    return FakeAPI(
        pull=pull_obj(changed=len(paths)),
        files=files(*paths, status=status),
        labels=[[{"name": WORKFLOW_CHANGE_LABEL}]],
        timeline=[[labelled_by(login)]],
    )


# ==========================================================================================
# Glob translation. The brief names this as a known defect source; there is a fixture per pattern.
# ==========================================================================================


class TestGlobSemantics:
    def test_star_does_not_cross_a_slash(self):
        # fnmatch's `*` does cross `/`; this is why the module translates globs itself.
        assert glob_to_regex("scripts/check_*.py").match("scripts/check_foo.py")
        assert not glob_to_regex("scripts/check_*.py").match("scripts/deep/check_foo.py")
        # THE FIXTURE THAT ACTUALLY PINS `[^/]*` RATHER THAN `.*`. The line above survives a
        # mutation of `*` to `.*`, because `scripts/deep/...` does not start with `scripts/check_`
        # either way. This one does not: under `.*` the segment `check_a/b` would be swallowed and
        # a file in a directory merely NAMED `check_something` would be treated as a guard script.
        assert not glob_to_regex("scripts/check_*.py").match("scripts/check_a/b.py")

    def test_trailing_doubleslash_star_means_everything_below(self):
        rx = glob_to_regex(".github/workflows/**")
        assert rx.match(".github/workflows/ci.yml")
        assert rx.match(".github/workflows/a/b/c.yml")
        # The bare directory name is not a file and must not match.
        assert not rx.match(".github/workflows")

    def test_doubleslash_star_slash_means_zero_or_more_directories(self):
        rx = glob_to_regex("scripts/**/check_*.py")
        assert rx.match("scripts/check_foo.py")
        assert rx.match("scripts/guards/check_foo.py")
        assert rx.match("scripts/a/b/c/check_foo.py")

    def test_the_slash_before_a_doubleslash_star_is_not_swallowed(self):
        # THE #26 DEFECT, as a regression test. Without the end-of-string condition on the `/**`
        # branch, `scripts/**/check_*.py` translated to `scripts/.*/check_[^/]*\.py`, which
        # quietly requires an intermediate directory -- so every guard script actually in
        # `scripts/` became invisible. Nine real data files went missing that way in #26.
        assert glob_to_regex("scripts/**/check_*.py").match("scripts/check_no_private_data.py")

    def test_anchored_at_both_ends(self):
        assert not glob_to_regex(".github/workflows/**").match("docs/.github/workflows/ci.yml")
        assert not glob_to_regex("scripts/check_*.py").match("scripts/check_foo.py.bak")


class TestPatternTable:
    @pytest.mark.parametrize(
        ("path", "why"),
        [
            (".github/workflows/ci.yml", "a workflow directly in the directory"),
            (".github/workflows/workflow-change.yml", "this guard's own workflow"),
            (".github/workflows/nested/deeper/ci.yml", "a workflow nested below it"),
            ("scripts/check_foo.py", "the ruling's guard-script glob"),
            ("scripts/check_no_private_data.py", "a guard script that really exists here"),
            ("scripts/check_workflow_change_label.py", "this guard's own script"),
            ("scripts/guards/check_foo.py", "a guard script filed one directory deeper"),
            ("scripts/uncollected_test_baseline.txt", "the baseline the ruling names"),
            ("scripts/workflow_change_label_setters.txt", "this guard's own allowlist"),
            (
                "tests/test_check_workflow_change_label.py",
                "this guard's own test file -- the only place the guard's safety properties are "
                "asserted, so weakening it removes a gate (Lead-2, 2026-09-27)",
            ),
            (
                "tests/test_check_no_secrets_in_diff.py",
                "another guard script's test file: the glob mirrors `scripts/check_*.py`, so it "
                "covers the other guards' tests on the same reasoning",
            ),
            (
                "tests/test_check_fail_open_defaults.py",
                "likewise, and `check_fail_open_defaults` runs in ci.yml's `guards` job",
            ),
            (
                "tests/test_check_uncollected_tests.py",
                "likewise, and `check_uncollected_tests` runs in ci.yml's `guards` job",
            ),
            (
                "tests/test_check_renamed_later.py",
                "a name that does not exist today: the glob survives a rename, which is the "
                "property a bare filename does not have",
            ),
            (".githooks/commit-msg", "Tag's proposal: the identity hook"),
            (".githooks/pre-commit", "Tag's proposal: the primary-checkout hook"),
            (".githooks/deep/thing", "a hook filed one directory deeper"),
            ("pyproject.toml", "ruling follow-up: testpaths, ruff select, mypy config"),
            ("uv.lock", "ruling follow-up: which dependency versions CI resolves"),
            ("Makefile", "ruling follow-up: `make init` sets core.hooksPath and the identity"),
            ("MAKEFILE", "the path is lowercased before matching, so case is not a bypass"),
            (
                "pravrudhi_kernel/pyproject.toml",
                "beyond the ruling: the workspace member carries its own testpaths, and the "
                "`kernel` and `windows-import-smoke` jobs run against it",
            ),
        ],
    )
    def test_protected(self, path: str, why: str):
        assert matches(path) is not None, f"{path} must be protected ({why})"

    @pytest.mark.parametrize(
        ("path", "why"),
        [
            ("scripts/checkfoo.py", "no underscore: `check_*.py` must not match `checkfoo.py`"),
            ("scripts/checked.py", "`checked.py` is not `check_<something>.py` either"),
            ("scripts/check_foo.txt", "the ruling's glob is scoped to .py"),
            (
                "scripts/check_a/b.py",
                "a file inside a directory merely NAMED check_* is not a guard script; `*` must "
                "not cross a `/`",
            ),
            ("README.md", "a path that matches nothing at all"),
            ("src/pravrudhi/cli.py", "ordinary engine code"),
            (
                "docs/.github/workflows/notes.yml",
                "contains `.github/workflows` further along the string, but is not the directory "
                "GitHub Actions reads",
            ),
            (
                "docs/notes-about-.github/workflows-and-.githooks.md",
                "the protected prefixes appear as substrings in the middle of the path",
            ),
            ("vendor/.githooks/commit-msg", "not the hooks directory `core.hooksPath` points at"),
            (
                "paper/Makefile",
                "the ruling's `Makefile` is anchored to the root; the paper build's own makefile "
                "is not the contributor on-ramp and no required job runs it",
            ),
            ("docs/pyproject.toml", "anchored: only the root project file is the ruling's path"),
            ("uv.lock.bak", "anchored at the end too"),
            ("my-pyproject.toml", "anchored at the start too"),
            # `tests/test_check_workflow_change_label.py` USED TO SIT HERE, as a deliberate
            # non-entry: #103 argued that deleting a test does not weaken the RUNNING guard and
            # listed it as a residual in its pull request body. Lead-2 reversed that on
            # 2026-09-27, and adopted `tests/test_check_*.py` rather than the one path, so that
            # file and its siblings moved to the positive list above. The reversal is right: the
            # guard's safety properties are asserted in those files and nowhere else, so gutting
            # an assertion there removes a gate -- just the slowest-acting way to do it, since
            # nothing goes red on the commit that does it.
            ("tests/test_workflow_change_label.py", "a near-miss name: no `test_check_` prefix"),
            ("tests/test_nyaya_agent.py", "a real non-guard test file must not be dragged in"),
            (
                "tests/governance/test_check_something.py",
                "`*` never crosses a `/`, so the glob is `tests/` only",
            ),
        ],
    )
    def test_not_protected(self, path: str, why: str):
        assert matches(path) is None, f"{path} must not be protected ({why})"

    def test_every_literal_pattern_matches_its_own_path(self):
        """A pattern that matches nothing at all reads as coverage and is worse than none.

        THIS IS A REGRESSION TEST FOR A REAL DEFECT IN THIS FILE'S OWN GUARD. `matches()`
        lowercases the path before testing it, so `Makefile` -- added on the 2026-09-27 follow-up
        ruling -- compiled to a regex with a capital `M` and matched NOTHING. Every wildcard-free
        pattern must match the very path it names.
        """
        unmatched = [
            glob
            for glob, _ in PROTECTED_PATTERNS
            if not any(ch in glob for ch in "*?") and matches(glob) is None
        ]
        assert unmatched == [], f"patterns that match nothing: {unmatched}"

    def test_a_pattern_with_an_upper_case_letter_still_matches(self):
        # The narrow case, stated separately from the structural test above so a reader sees it.
        assert matches("Makefile") is not None
        assert matches("makefile") is not None

    def test_every_pattern_has_a_reason(self):
        for glob, why in PROTECTED_PATTERNS:
            assert why.strip(), f"{glob} carries no reason"

    def test_the_four_ruling_paths_are_all_present(self):
        globs = {glob for glob, _ in PROTECTED_PATTERNS}
        assert {
            ".github/workflows/**",
            "scripts/check_*.py",
            "scripts/uncollected_test_baseline.txt",
            ".githooks/**",
        } <= globs

    def test_the_three_follow_up_ruling_paths_are_all_present(self):
        globs = {glob for glob, _ in PROTECTED_PATTERNS}
        assert {"pyproject.toml", "uv.lock", "Makefile"} <= globs

    def test_the_ruling_glob_is_what_a_log_reports_for_a_script_in_scripts(self):
        # Both `scripts/check_*.py` and the wider `scripts/**/check_*.py` match this path. The
        # narrower ruling glob is listed first so that is the one reported.
        hit = matches("scripts/check_no_private_data.py")
        assert hit is not None and hit[0] == "scripts/check_*.py"


# ==========================================================================================
# The verdict
# ==========================================================================================


class TestClears:
    def test_a_pull_request_touching_nothing_protected_passes_without_reading_labels(self):
        api = FakeAPI(pull=pull_obj(changed=2), files=files("README.md", "src/x.py"))
        code, text = run(api)
        assert code == 0
        assert "no CI or guard machinery touched" in text
        assert not any("/labels" in c or "/timeline" in c for c in api.calls)

    def test_a_labelled_ci_change_by_the_authorised_account_passes(self):
        code, text = run(cleared(".github/workflows/ci.yml"))
        assert code == 0
        assert "applied by @AxisMeru" in text

    def test_the_pass_message_says_the_account_not_the_person(self):
        _, text = run(cleared(".github/workflows/ci.yml"))
        assert "ACCOUNT" in text and "not which person" in text


class TestFails:
    def test_an_unlabelled_ci_change_fails(self):
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=files(".github/workflows/ci.yml"),
            labels=[[]],
        )
        code, text = run(api)
        assert code == 1
        assert f"does not carry the `{WORKFLOW_CHANGE_LABEL}` label" in text

    def test_the_failure_message_says_the_actor_check_proves_the_account_only(self):
        api = FakeAPI(pull=pull_obj(changed=1), files=files(".githooks/commit-msg"), labels=[[]])
        _, text = run(api)
        assert "ACCOUNT applied the label" in text and "never which person" in text

    def test_a_label_set_by_a_non_allowed_actor_does_not_waive(self):
        code, text = run(cleared(".github/workflows/ci.yml", login="SomeoneElse"))
        assert code == 1
        assert "not authorised" in text
        assert "@SomeoneElse" in text

    def test_a_label_set_by_an_agent_account_does_not_waive(self):
        # `claude[bot]` is deliberately absent from the allowlist. Here the /users/ lookup is made
        # to resolve so the ALLOWLIST is what rejects it, which is the check being pinned.
        api = cleared(".github/workflows/ci.yml", login="claude-bot")
        code, text = run(api)
        assert code == 1
        assert "not authorised" in text


class TestEmptyDiffIsNotTheSameAsNoProtectedFiles:
    """Requirement 11's last case. Both pass; the log must say WHICH fact it is reporting."""

    def test_a_proven_empty_diff_says_so_in_its_own_words(self):
        api = FakeAPI(pull=pull_obj(changed=0), files=[[]])
        code, text = run(api)
        assert code == 0
        assert "an empty diff, not an unread list" in text

    def test_a_non_empty_clean_diff_uses_the_other_wording(self):
        _, text = run(FakeAPI(pull=pull_obj(changed=1), files=files("README.md")))
        assert "no CI or guard machinery touched" in text
        assert "empty diff" not in text

    def test_a_claimed_empty_diff_that_the_files_endpoint_contradicts_is_a_hard_failure(self):
        # The dangerous direction: `changed_files: 0` while the endpoint really holds a workflow
        # edit. Reconciliation must catch it BEFORE the empty-diff shortcut is taken.
        api = FakeAPI(pull=pull_obj(changed=0), files=files(".github/workflows/ci.yml"))
        with pytest.raises(GuardFailure, match="reports 0 changed files"):
            run(api)


class TestFailsClosed:
    """One negative fixture per way this guard could pass without knowing the answer."""

    def test_a_truncated_changed_file_list_is_a_hard_failure(self):
        # GitHub says 2, the endpoint hands back 1. The one it handed back is innocuous, so a
        # guard that trusted page one would go GREEN here. This is the bug class.
        api = FakeAPI(pull=pull_obj(changed=2), files=files("README.md"))
        with pytest.raises(GuardFailure, match="Refusing to compute a verdict from a partial list"):
            run(api)

    def test_a_list_longer_than_the_page_cap_is_a_hard_failure(self):
        pages = [[{"filename": f"f{i}-{p}.txt", "status": "modified"} for i in range(PER_PAGE)]
                 for p in range(MAX_PAGES + 1)]
        api = FakeAPI(pull=pull_obj(changed=PER_PAGE * (MAX_PAGES + 1)), files=pages)
        with pytest.raises(GuardFailure, match="was not paged to the end"):
            run(api)

    def test_a_missing_changed_files_count_is_a_hard_failure(self):
        api = FakeAPI(pull={"base": {"ref": "main", "sha": "b" * 40}}, files=files("README.md"))
        with pytest.raises(GuardFailure, match="`changed_files`"):
            run(api)

    def test_a_non_integer_changed_files_count_is_a_hard_failure(self):
        api = FakeAPI(pull={"changed_files": "2", "base": {"ref": "main", "sha": "b" * 40}})
        with pytest.raises(GuardFailure, match="`changed_files`"):
            run(api)

    def test_an_unresolvable_base_is_a_hard_failure(self):
        api = FakeAPI(pull={"changed_files": 1, "base": {}})
        with pytest.raises(GuardFailure, match="could not resolve the pull request's base"):
            run(api)

    def test_an_unreadable_pull_request_is_a_hard_failure(self):
        # A 403 on the pull request object itself, before anything else is read. Written as its own
        # fetch rather than through FakeAPI's substring matcher, because `/pulls/78` is a prefix of
        # `/pulls/78/files` and this test is about the first of the two.
        def fetch(url: str) -> tuple[object, str]:
            raise GuardFailure(f"GET {url} failed: HTTP 403 Forbidden")

        with pytest.raises(GuardFailure, match="HTTP 403"):
            check(fetch, REPO, PR, out=io.StringIO())

    def test_a_files_endpoint_error_is_a_hard_failure(self):
        api = FakeAPI(pull=pull_obj(changed=1), errors=(f"/pulls/{PR}/files",))
        with pytest.raises(GuardFailure, match="HTTP 403"):
            run(api)

    def test_a_files_entry_with_no_filename_is_a_hard_failure(self):
        api = FakeAPI(pull=pull_obj(changed=1), files=[[{"status": "modified"}]])
        with pytest.raises(GuardFailure, match="no filename or no status"):
            run(api)

    def test_a_files_entry_with_no_status_is_a_hard_failure(self):
        api = FakeAPI(pull=pull_obj(changed=1), files=[[{"filename": "README.md"}]])
        with pytest.raises(GuardFailure, match="no filename or no status"):
            run(api)

    def test_a_files_endpoint_that_does_not_return_a_list_is_a_hard_failure(self):
        # An error object where a list belongs -- what a 200-with-a-body-of-`{"message": ...}`
        # looks like. Iterating it would silently see zero files.
        def fetch(url: str) -> tuple[object, str]:
            if url.split("?")[0].endswith(f"/pulls/{PR}"):
                return pull_obj(changed=1), ""
            return {"message": "Not Found"}, ""

        with pytest.raises(GuardFailure, match="expected a JSON list"):
            check(fetch, REPO, PR, out=io.StringIO())

    def test_an_unreadable_label_list_is_a_hard_failure(self):
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=files(".github/workflows/ci.yml"),
            errors=(f"/issues/{PR}/labels",),
        )
        with pytest.raises(GuardFailure, match="HTTP 403"):
            run(api)

    def test_a_label_entry_with_no_name_is_a_hard_failure(self):
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=files(".github/workflows/ci.yml"),
            labels=[[{"colour": "red"}]],
        )
        with pytest.raises(GuardFailure, match="an entry has no name"):
            run(api)

    def test_a_failed_timeline_read_does_not_degrade_to_presence_only(self):
        api = cleared(".github/workflows/ci.yml")
        api.errors = (f"/issues/{PR}/timeline",)
        with pytest.raises(GuardFailure, match="HTTP 403"):
            run(api)

    def test_a_timeline_with_no_labeled_event_is_a_hard_failure(self):
        api = cleared(".github/workflows/ci.yml")
        api.timeline = [[{"event": "commented", "actor": {"login": "AxisMeru"}}]]
        with pytest.raises(GuardFailure, match="holds no `labeled` event"):
            run(api)

    def test_a_labeled_event_for_a_different_label_does_not_count(self):
        api = cleared(".github/workflows/ci.yml")
        api.timeline = [[{
            "event": "labeled",
            "label": {"name": "data-change"},
            "actor": {"login": "AxisMeru"},
        }]]
        with pytest.raises(GuardFailure, match="holds no `labeled` event"):
            run(api)

    def test_a_labeled_event_with_no_actor_is_a_hard_failure(self):
        api = cleared(".github/workflows/ci.yml")
        api.timeline = [[{"event": "labeled", "label": {"name": WORKFLOW_CHANGE_LABEL}}]]
        with pytest.raises(GuardFailure, match="has no actor"):
            run(api)

    def test_an_actor_lookup_that_fails_is_a_hard_failure(self):
        api = cleared(".github/workflows/ci.yml")
        api.errors = ("/users/",)
        with pytest.raises(GuardFailure, match="HTTP 403"):
            run(api)

    def test_an_actor_lookup_that_carries_no_login_is_a_hard_failure(self):
        api = cleared(".github/workflows/ci.yml")
        api.users = {"id": 1}
        with pytest.raises(GuardFailure, match="carries no login"):
            run(api)

    def test_an_actor_lookup_that_answers_for_a_different_account_is_a_hard_failure(self):
        # A redirect or a renamed account. Comparing the allowlist against a login the API did not
        # confirm is exactly the trust this guard is not allowed to extend.
        api = cleared(".github/workflows/ci.yml")
        api.users = {"login": "SomebodyElse"}
        with pytest.raises(GuardFailure, match="answered for @SomebodyElse"):
            run(api)

    def test_a_missing_allowlist_is_a_hard_failure(self):
        api = cleared(".github/workflows/ci.yml")
        api.allowlist = None
        with pytest.raises(GuardFailure, match="HTTP 404"):
            run(api)

    def test_an_empty_allowlist_authorises_nobody(self):
        api = cleared(".github/workflows/ci.yml")
        api.allowlist = "# nothing but comments\n"
        with pytest.raises(GuardFailure, match="authorised to set"):
            run(api)

    def test_an_allowlist_entry_with_no_reason_authorises_nobody(self):
        api = cleared(".github/workflows/ci.yml")
        api.allowlist = "AxisMeru\n"
        with pytest.raises(GuardFailure, match="no reason and therefore authorise nobody"):
            run(api)

    def test_a_contents_response_that_is_not_a_base64_file_is_a_hard_failure(self):
        def fetch(url: str) -> tuple[object, str]:
            return {"encoding": "none", "content": "x", "sha": ALLOWLIST_BLOB}, ""

        with pytest.raises(GuardFailure, match="did not come back as a base64 file"):
            fetch_allowlist_at_ref(fetch, REPO, "main", DEFAULT_ALLOWLIST)

    def test_an_undecodable_allowlist_is_a_hard_failure(self):
        def fetch(url: str) -> tuple[object, str]:
            return {"encoding": "base64", "content": "!!!not base64!!!", "sha": ALLOWLIST_BLOB}, ""

        with pytest.raises(GuardFailure, match="could not be decoded"):
            fetch_allowlist_at_ref(fetch, REPO, "main", DEFAULT_ALLOWLIST)

    def test_an_allowlist_with_no_blob_sha_is_a_hard_failure(self):
        """A message that cannot cite which bytes it read is not a message this guard will send.

        The whole point of quoting the blob sha is that `@main` names a moving target. A contents
        payload with no `sha` would leave the failure message saying "not in ...@main" with
        nothing checkable in it, so it fails closed instead.
        """

        def fetch(url: str) -> tuple[object, str]:
            return {
                "encoding": "base64",
                "content": base64.b64encode(ALLOWLIST_TEXT.encode()).decode(),
            }, ""

        with pytest.raises(GuardFailure, match="no blob sha"):
            fetch_allowlist_at_ref(fetch, REPO, "main", DEFAULT_ALLOWLIST)

    def test_a_repository_object_without_a_default_branch_is_a_hard_failure(self):
        """No guessing `main`. A silent fallback to a branch name is the pattern this file bans."""

        def fetch(url: str) -> tuple[object, str]:
            return {}, ""

        with pytest.raises(GuardFailure, match="default branch"):
            fetch_default_branch(fetch, REPO)

    def test_a_default_branch_that_is_not_a_string_is_a_hard_failure(self):
        def fetch(url: str) -> tuple[object, str]:
            return {"default_branch": 7}, ""

        with pytest.raises(GuardFailure, match="default branch"):
            fetch_default_branch(fetch, REPO)

    def test_an_undeterminable_default_branch_fails_the_run_rather_than_clearing_it(self):
        """The end-to-end shape of it: a would-be `cleared` run that cannot name the ref."""
        api = cleared(".github/workflows/ci.yml")
        api.default_branch = None
        with pytest.raises(GuardFailure, match="default branch"):
            run(api)

    def test_a_missing_pull_request_number_is_a_hard_failure(self, monkeypatch):
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
        with pytest.raises(GuardFailure, match="could not determine which pull request"):
            resolve_pr_number(None)


class TestAllowlistComesFromTheDefaultBranch:
    """Which REF the allowlist is read at. Changed by this pull request; see the class below."""

    def test_the_allowlist_is_read_at_the_default_branch_and_never_at_head(self):
        api = cleared(".github/workflows/ci.yml")
        code, _ = run(api)
        assert code == 0
        contents = [c for c in api.calls if "/contents/" in c]
        assert contents, "the allowlist was never fetched"
        assert all("?ref=main" in c for c in contents)
        assert not any("h" * 40 in c for c in api.calls)

    def test_the_allowlist_is_never_read_at_a_commit_sha(self):
        """A REF, not a sha: that is what makes it the CURRENT tip rather than a snapshot.

        `pull_obj` sets `base.sha` to `"b" * 40`. If any allowlist read carried it, amending the
        allowlist on the default branch would stop taking effect on already-open pull requests --
        which is the property the docstring claims.
        """
        api = cleared(".github/workflows/ci.yml")
        assert run(api)[0] == 0
        contents = [c for c in api.calls if "/contents/" in c]
        assert contents
        for call in contents:
            assert "b" * 40 not in call, f"the allowlist was read at a commit sha: {call}"

    def test_the_allowlist_path_is_the_one_the_workflow_does_not_override(self):
        assert DEFAULT_ALLOWLIST == "scripts/workflow_change_label_setters.txt"
        # The flag is NAMED in the workflow's comments, which say why it is not passed. What must
        # not exist is a `run:` line that actually passes it.
        assert "--allowlist-file" not in WORKFLOW_CODE

    def test_the_repositorys_own_allowlist_parses_and_authorises_the_owner(self):
        text = (REPO_ROOT / DEFAULT_ALLOWLIST).read_text(encoding="utf-8")
        setters = parse_allowlist(text, DEFAULT_ALLOWLIST)
        assert "axismeru" in setters
        assert setters["axismeru"].strip()

    def test_the_repositorys_own_allowlist_lists_no_agent_account(self):
        setters = parse_allowlist(
            (REPO_ROOT / DEFAULT_ALLOWLIST).read_text(encoding="utf-8"), DEFAULT_ALLOWLIST
        )
        assert not any("[bot]" in login or login == "bot" for login in setters)


class TestBypasses:
    """The adversarial pass, as tests."""

    def test_deleting_a_guard_script_is_caught(self):
        # #26 deliberately ignores `removed`. Here it is the primary attack: no ci.yml, no
        # `guards` job.
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=files(".github/workflows/ci.yml", status="removed"),
            labels=[[]],
        )
        code, text = run(api)
        assert code == 1
        assert "removed" in text

    def test_removed_is_in_the_touching_statuses(self):
        assert "removed" in TOUCHING_STATUSES
        assert "unchanged" not in TOUCHING_STATUSES

    def test_renaming_a_guard_script_away_is_caught_by_its_previous_name(self):
        # The new path matches nothing; the old one is the guard. A filename-only check passes
        # this and the guard is gone.
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=[[{
                "filename": "scripts/npd.py",
                "previous_filename": "scripts/check_no_private_data.py",
                "status": "renamed",
            }]],
            labels=[[]],
        )
        code, text = run(api)
        assert code == 1
        assert "scripts/check_no_private_data.py" in text
        assert "was this path" in text

    def test_a_rename_does_not_break_the_completeness_reconciliation(self):
        # The rename contributes two PATHS from one ENTRY; reconciliation counts entries.
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=[[{
                "filename": "docs/a.md",
                "previous_filename": "docs/b.md",
                "status": "renamed",
            }]],
        )
        code, text = run(api)
        assert code == 0
        assert "2 path(s) touched" in text

    def test_an_upper_cased_protected_path_is_caught(self):
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=files(".GitHub/Workflows/CI.yml"),
            labels=[[]],
        )
        assert run(api)[0] == 1

    def test_a_labeled_event_on_page_two_of_the_timeline_is_found(self):
        api = cleared(".github/workflows/ci.yml")
        api.timeline = [
            [{"event": "commented", "actor": {"login": "AxisMeru"}}],
            [labelled_by("AxisMeru")],
        ]
        assert run(api)[0] == 0

    def test_the_most_recent_labeller_is_the_one_that_counts(self):
        # Authorised account labels, it comes off, an unauthorised account re-adds it. The label is
        # present either way; only the LAST `labeled` event may decide the verdict.
        api = cleared(".github/workflows/ci.yml")
        api.timeline = [[
            labelled_by("AxisMeru", "2026-09-27T10:00:00Z"),
            {"event": "unlabeled", "label": {"name": WORKFLOW_CHANGE_LABEL},
             "actor": {"login": "AxisMeru"}},
            labelled_by("SomeoneElse", "2026-09-27T11:00:00Z"),
        ]]
        code, text = run(api)
        assert code == 1
        assert "@SomeoneElse" in text

    def test_labels_are_read_live_not_from_the_event_payload(self):
        api = cleared(".github/workflows/ci.yml")
        run(api)
        assert any(f"/issues/{PR}/labels" in c for c in api.calls)

    def test_a_protected_path_among_many_innocuous_ones_is_still_found(self):
        paths = [f"docs/note{i}.md" for i in range(120)] + [".githooks/commit-msg"]
        api = FakeAPI(pull=pull_obj(changed=len(paths)), files=files(*paths), labels=[[]])
        code, text = run(api)
        assert code == 1
        assert ".githooks/commit-msg" in text

    def test_a_protected_path_on_page_two_of_the_files_list_is_still_found(self):
        page1 = [{"filename": f"docs/n{i}.md", "status": "modified"} for i in range(PER_PAGE)]
        page2 = [{"filename": ".github/workflows/ci.yml", "status": "modified"}]
        api = FakeAPI(
            pull=pull_obj(changed=PER_PAGE + 1), files=[page1, page2], labels=[[]]
        )
        code, text = run(api)
        assert code == 1
        assert ".github/workflows/ci.yml" in text


class TestWorkflowWiring:
    """The workflow YAML read as text. Four properties, each one a thing a reviewer might delete."""

    def test_the_trigger_is_pull_request_target(self):
        # The whole mechanism: a `pull_request` run reads its definition from the branch under
        # test, so it cannot police edits to itself.
        #
        # Asserted against WORKFLOW_CODE, not the raw text. A first version of this test read the
        # raw text and SURVIVED the mutant that swaps the trigger for `pull_request:` -- because
        # the header comment "A bare `pull_request_target:` fires only on opened..." contains the
        # string it was looking for. The mutation pass caught that; the fix is here.
        assert re.search(r"^on:\s*$", WORKFLOW_CODE, re.MULTILINE)
        assert re.search(r"^  pull_request_target:\s*$", WORKFLOW_CODE, re.MULTILINE)
        assert not re.search(r"^  pull_request:\s*$", WORKFLOW_CODE, re.MULTILINE)

    def test_the_types_list_includes_labeled_and_unlabeled(self):
        # Without these the remedy ("add the label") never re-runs the job and the pull request is
        # red forever; without `unlabeled`, "label it then take it off" is a bypass.
        match = re.search(r"types:\s*\[([^\]]*)\]", WORKFLOW_TEXT)
        assert match is not None
        types = {t.strip() for t in match.group(1).split(",")}
        assert types == {"opened", "synchronize", "reopened", "labeled", "unlabeled"}

    def test_permissions_are_declared_read_only_and_nothing_wider(self):
        block = re.search(r"^permissions:\n((?:  .*\n)+)", WORKFLOW_TEXT, re.MULTILINE)
        assert block is not None, "permissions must be declared at the workflow level"
        scopes = dict(
            re.findall(r"^\s+([a-z-]+):\s*(\S+)\s*$", block.group(1), re.MULTILINE)
        )
        # `issues: read` added on Lead-2's ruling of 2026-09-27: the label and timeline endpoints
        # live in the issues namespace even for a pull request, so the guard needs it. All three
        # are `read`; the next test pins that no `write` appears anywhere in the file.
        assert scopes == {"contents": "read", "pull-requests": "read", "issues": "read"}

    def test_no_write_permission_anywhere_in_the_file(self):
        assert not re.search(r"^\s*[a-z-]+:\s*write\s*$", WORKFLOW_TEXT, re.MULTILINE)

    def test_the_only_checkout_is_pinned_to_the_default_branch(self):
        """ONE checkout, of the repository's default branch, and never of `base.sha`.

        WHY THERE IS NO TEST THAT SIMULATES TWO VERSIONS OF THE TABLE, which is the question a
        reviewer asks first when told the defect was a STALE table producing a false green.

        The stale-table false green needs two things to be true at once: the checker reads a
        `PROTECTED_PATTERNS` table, and the table it reads can be older than the default
        branch's. This pin and `test_the_base_sha_is_never_used_as_a_checkout_ref` together
        remove the SECOND condition -- they force the checkout, and therefore the table the
        checker reads, to be the default branch's current tip on every run. The false green is
        then impossible BY CONSTRUCTION, not merely unobserved.

        A simulation test would have to hand the checker an old table and a new one and watch the
        verdict differ. All that proves is that the checker reads whichever file it is handed,
        which nobody doubts and which would still be true if this workflow went back to
        `base.sha` tomorrow. It would be a test of `matches()`, dressed as a test of the fix. The
        property that actually matters is a property of the WIRING, so it is asserted on the
        wiring.

        THE PROPERTY HOLDS ONLY WHILE BOTH PINS HOLD.
        `test_the_base_sha_is_never_used_as_a_checkout_ref` is the other half: this test says the
        ref IS the default branch, that one says it is NEVER `base.sha`. Delete either and the
        construction argument above stops being true -- so if you are removing one, remove this
        docstring's claim with it rather than leaving documentation that promises coverage the
        suite no longer provides. That specific failure -- a comment still asserting a property
        after the test behind it went away -- is one this repository has hit repeatedly.

        Asserted against the PARSED YAML and not only against the text, so that a second `ref:`
        elsewhere, a commented-out line, or a differently-quoted spelling cannot satisfy it. The
        negative half runs against WORKFLOW_CODE, because the file's comments must remain free to
        NAME `base.sha` as the thing not to do -- they explain at length why it was wrong.
        """
        checkouts = re.findall(r"uses:\s*actions/checkout@", WORKFLOW_TEXT)
        assert len(checkouts) == 1, "exactly one checkout, of the default branch"

        steps = _workflow_steps()
        assert steps, "no steps parsed -- this test would be vacuous"
        checkout_steps = [s for s in steps if "actions/checkout@" in (s.get("uses") or "")]
        assert len(checkout_steps) == 1
        ref = (checkout_steps[0].get("with") or {}).get("ref")
        assert ref == "${{ github.event.repository.default_branch }}", (
            f"the checkout ref must be the repository default branch, not {ref!r}"
        )
        assert (checkout_steps[0].get("with") or {}).get("persist-credentials") is False

    def test_the_base_sha_is_never_used_as_a_checkout_ref(self):
        """The regression this locks. `base.sha` does not advance as the default branch moves.

        A pull request opened before a path was added to `PROTECTED_PATTERNS` would run the OLD
        table, match nothing, and report `no-protected-paths` -- green -- while editing a path
        this repository protects. Observed in the weaker form on #104, whose base `e40ee4a`
        predates the guard entirely, so `python3` died with `can't open file`.

        WHY THAT FALSE GREEN IS NOT PROVED BY A SIMULATION TEST. Together with
        `test_the_only_checkout_is_pinned_to_the_default_branch`, this pin forces the table the
        checker reads to be the default branch's current tip on every run, so there is never an
        old table for the checker to read and the false green cannot arise. A test that fed the
        checker an old table and a new one would only show that it reports whatever table it is
        given -- true before this change, true after it, and true again if someone reverts the
        `ref:`. It would prove nothing about the fix. The guarantee lives in the wiring, so the
        assertion does too.

        THE PROPERTY HOLDS ONLY WHILE BOTH PINS HOLD.
        `test_the_only_checkout_is_pinned_to_the_default_branch` is the other half: it says the
        ref IS the default branch, this one says it is NEVER `base.sha`. A `ref:` that is neither
        -- a literal sha, a hard-coded branch name, some other event field -- fails that test and
        passes this one, which is exactly why one is not enough. If either is deleted, this
        docstring's claim goes with it; leaving it behind would be the guard's own documentation
        reading as coverage it no longer provides.
        """
        assert "base.sha" not in WORKFLOW_CODE, "base.sha must never be USED here"
        # ...and the comments must still be allowed to explain why it was wrong.
        assert "base.sha" in WORKFLOW_TEXT
        assert "default_branch" in WORKFLOW_CODE

    def test_the_head_ref_and_head_sha_are_never_referenced(self):
        # Checking out or fetching the head is what turns `pull_request_target` into a
        # code-execution path. Prose in the comments explains this; the code must not do it.
        for forbidden in (
            "pull_request.head.sha",
            "pull_request.head.ref",
            "refs/pull/",
            "github.head_ref",
        ):
            assert forbidden not in WORKFLOW_CODE, f"{forbidden} must never be USED here"
            # ...and the header must still be allowed to name it as the thing not to do.
        assert "pull_request.head.sha" in WORKFLOW_TEXT

    def test_no_interpolation_reaches_any_run_block(self):
        # A pull request title, branch name or label name pasted into a `run:` by `${{ }}` is
        # shell-injected before the shell ever starts. Every `run:` here must be literal.
        runs = re.findall(r"^\s*run:\s*(?:\|[^\n]*\n((?:\s{8,}.*\n)+)|(.*)$)", WORKFLOW_TEXT, re.MULTILINE)
        bodies = [a or b for a, b in runs]
        assert bodies, "no run: block found -- this test would be vacuous"
        for body in bodies:
            assert "${{" not in body, f"interpolation in a run block: {body!r}"

    def test_the_status_context_does_not_collide_with_guards(self):
        assert re.search(r"^name:\s*workflow-change\s*$", WORKFLOW_TEXT, re.MULTILINE)
        assert re.search(r"^  workflow-change:\s*$", WORKFLOW_TEXT, re.MULTILINE)
        assert not re.search(r"^\s+guards:\s*$", WORKFLOW_TEXT, re.MULTILINE)

    def test_ci_yml_is_untouched_by_this_change(self):
        # Requirement, and the rule scheduled-guard-audit.yml states for itself in the sibling
        # repository: nothing in a new workflow may change the timing or permissions of ci.yml's
        # jobs. ci.yml's own trigger must still be a bare `pull_request:`.
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        # Comment lines stripped, for the same reason WORKFLOW_CODE exists: ci.yml's own comments
        # NAME `pull_request_target` in order to forbid it there, and a test that could not tell a
        # prohibition from a use would either fail on merge or force those comments to be deleted.
        ci_code = "".join(
            line for line in ci.splitlines(keepends=True) if not line.lstrip().startswith("#")
        )
        assert re.search(r"^  pull_request:\s*$", ci_code, re.MULTILINE)
        assert "pull_request_target" not in ci_code


class TestTheTwoRedsAreDistinguishable:
    """`not authorised` and `could not resolve` are both red and prove DIFFERENT things.

    Only the first demonstrates that the allowlist check ran and rejected an account. A proof run
    that asserts on redness alone cannot tell them apart, so an allowlist bug that accepted any
    actor would be indistinguishable from a resolution failure. Every terminal path therefore
    prints a stable `workflow-change: VERDICT=<token>` line, and these tests pin the tokens.
    """

    def test_an_unauthorised_actor_reports_not_authorised_not_unresolved(self):
        code, text = run(cleared(".github/workflows/ci.yml", login="SomeoneElse"))
        assert code == 1
        assert f"{VERDICT_PREFIX}{VERDICT_NOT_AUTHORISED}" in text
        assert VERDICT_ACTOR_UNRESOLVED not in text
        assert "is not authorised" in text or "not authorised" in text

    def test_an_allowlisted_actor_that_cannot_be_resolved_reports_unresolved(self):
        # Resolution now runs only for a login that IS on the allowlist -- the one path where
        # this code extends trust.
        api = cleared(".github/workflows/ci.yml", login="AxisMeru")
        api.users = "404"
        with pytest.raises(GuardFailure) as exc:
            run(api)
        assert exc.value.verdict == VERDICT_ACTOR_UNRESOLVED
        assert "could not resolve the account @AxisMeru" in str(exc.value)

    def test_main_prints_the_actor_unresolved_token_on_that_failure(self, capsys, monkeypatch):
        import check_workflow_change_label as guard

        api = cleared(".github/workflows/ci.yml", login="AxisMeru")
        api.users = "404"
        monkeypatch.setenv("GITHUB_TOKEN", "x")
        monkeypatch.setattr(guard, "make_fetcher", lambda token: api)
        assert guard.main(["--repo", REPO, "--pr", str(PR)]) == 1
        out = capsys.readouterr().out
        assert f"{VERDICT_PREFIX}{VERDICT_ACTOR_UNRESOLVED}" in out
        assert VERDICT_NOT_AUTHORISED not in out

    @pytest.mark.parametrize(
        ("api_factory", "token"),
        [
            (lambda: FakeAPI(pull=pull_obj(changed=0), files=[[]]), VERDICT_EMPTY_DIFF),
            (
                lambda: FakeAPI(pull=pull_obj(changed=1), files=files("README.md")),
                VERDICT_NO_PROTECTED_PATHS,
            ),
            (
                lambda: FakeAPI(
                    pull=pull_obj(changed=1),
                    files=files(".github/workflows/ci.yml"),
                    labels=[[]],
                ),
                VERDICT_LABEL_MISSING,
            ),
            (lambda: cleared(".github/workflows/ci.yml"), VERDICT_CLEARED),
            (
                lambda: cleared(".github/workflows/ci.yml", login="SomeoneElse"),
                VERDICT_NOT_AUTHORISED,
            ),
        ],
    )
    def test_every_terminal_path_prints_exactly_one_verdict_token(self, api_factory, token: str):
        _, text = run(api_factory())
        tokens = [ln for ln in text.splitlines() if ln.startswith(VERDICT_PREFIX)]
        assert tokens == [f"{VERDICT_PREFIX}{token}"]


class TestABotLoginIsARealCase:
    """`claude[bot]` is the login this session itself acts as -- not a hypothetical."""

    def test_a_bracketed_bot_login_lands_in_not_authorised_not_unresolved(self):
        # THE POINT OF THE ORDERING. `claude[bot]` is not a `/users/` resource and 404s there. If
        # resolution ran first, this would report `actor-unresolved` -- red, but the WRONG red: it
        # would not demonstrate that the allowlist rejected the account, so a post-merge proof run
        # asserting only on redness would pass even with a broken allowlist.
        api = cleared(".github/workflows/ci.yml", login="claude[bot]")
        api.users = "404"
        code, text = run(api)
        assert code == 1
        assert f"{VERDICT_PREFIX}{VERDICT_NOT_AUTHORISED}" in text
        assert VERDICT_ACTOR_UNRESOLVED not in text
        assert "@claude[bot]" in text

    def test_the_bot_login_never_reaches_the_users_endpoint_at_all(self):
        api = cleared(".github/workflows/ci.yml", login="claude[bot]")
        api.users = "404"
        run(api)
        assert not any("/users/" in c for c in api.calls)

    def test_a_bracketed_login_is_url_encoded_when_it_IS_on_the_allowlist(self):
        # If a bracketed login were ever added to the allowlist, the lookup must be a well-formed
        # request. An unencoded `[` would fail for a URL-syntax reason, which reads in a log like
        # a security refusal while being a bug.
        from check_workflow_change_label import resolve_actor

        seen: list[str] = []

        def fetch(url: str) -> tuple[object, str]:
            seen.append(url)
            return {"login": "claude[bot]"}, ""

        assert resolve_actor(fetch, "claude[bot]") == "claude[bot]"
        assert seen == ["https://api.github.com/users/claude%5Bbot%5D"]
        assert "[" not in seen[0] and "]" not in seen[0]

    def test_the_allowlist_still_refuses_a_bot_even_if_users_would_resolve_it(self):
        api = cleared(".github/workflows/ci.yml", login="claude[bot]")
        code, text = run(api)  # users resolves fine here
        assert code == 1
        assert f"{VERDICT_PREFIX}{VERDICT_NOT_AUTHORISED}" in text


class TestTheFrictionThisAdds:
    """The follow-up ruling's cost, written down so it is weighed rather than discovered."""

    def test_an_ordinary_dependency_bump_now_needs_the_label(self):
        # `uv.lock` + `pyproject.toml` is what a routine bump touches. It now goes red without
        # the label. This test exists so that cost is visible in the suite, not only in a comment.
        api = FakeAPI(
            pull=pull_obj(changed=2),
            files=files("pyproject.toml", "uv.lock"),
            labels=[[]],
        )
        code, text = run(api)
        assert code == 1
        assert "pyproject.toml" in text and "uv.lock" in text

    def test_the_same_bump_clears_with_the_label(self):
        assert run(cleared("pyproject.toml", "uv.lock"))[0] == 0


class TestTheGuardGuardsItself:
    def test_this_guards_own_workflow_script_allowlist_and_tests_are_all_protected(self):
        for path in (
            ".github/workflows/workflow-change.yml",
            "scripts/check_workflow_change_label.py",
            DEFAULT_ALLOWLIST,
            GUARD_TESTS,
        ):
            assert matches(path) is not None, f"{path} must be protected"

    def test_all_four_files_exist_where_the_guard_expects_them(self):
        assert WORKFLOW.is_file()
        assert (REPO_ROOT / "scripts" / "check_workflow_change_label.py").is_file()
        assert (REPO_ROOT / DEFAULT_ALLOWLIST).is_file()
        assert (REPO_ROOT / GUARD_TESTS).is_file()

    def test_the_guard_tests_glob_matches_guard_tests_and_nothing_else(self):
        """Lead-2's ruling of 2026-09-27, as adopted: the glob `tests/test_check_*.py`.

        BOTH DIRECTIONS ARE ASSERTED, AND THE EXISTING STRUCTURAL SWEEP DOES NOT COVER THIS
        ENTRY. `test_every_literal_pattern_matches_its_own_path` walks only the WILDCARD-FREE
        entries -- it explicitly skips any glob containing `*` or `?` -- so this entry, which has
        a wildcard, is outside it. Nothing else in the suite would notice if this pattern
        silently matched nothing, or if it over-matched.

        The over-matching direction matters as much as the under-matching one: a pattern that
        catches unrelated files quietly puts them behind a human label, which is friction nobody
        signed up for and which will get the whole table switched off rather than corrected.

        THE SECOND POSITIVE CASE IS WHAT EARNS THE GLOB. A single positive assertion on this
        guard's own test file would pass just as well against the exact-path entry this replaced,
        so it would not distinguish the pattern from its predecessor at all.
        """
        # 1. this guard's own test file -- the file this entry was originally asked for.
        assert Path(__file__).relative_to(REPO_ROOT).as_posix() == GUARD_TESTS, (
            "GUARD_TESTS must name THIS file"
        )
        hit = matches(GUARD_TESTS)
        assert hit is not None, f"{GUARD_TESTS} must be protected"
        glob, why = hit
        assert glob == GUARD_TESTS_GLOB, (
            f"expected {GUARD_TESTS_GLOB!r} to be the entry doing the work, not {glob!r}"
        )
        assert why.strip()

        # 2. ANOTHER guard script's test file. This is the assertion the exact-path entry could
        #    not have satisfied, so it is what proves the glob is in place.
        other = "tests/test_check_no_secrets_in_diff.py"
        assert (REPO_ROOT / other).is_file(), f"{other} must exist or this case is vacuous"
        other_hit = matches(other)
        assert other_hit is not None, f"{other} must be protected by the glob"
        assert other_hit[0] == GUARD_TESTS_GLOB

        # 3. a non-guard test file, which must NOT be dragged behind the label.
        unrelated = "tests/test_nyaya_agent.py"
        assert (REPO_ROOT / unrelated).is_file(), (
            f"{unrelated} must exist or the negative case is vacuous"
        )
        assert matches(unrelated) is None, f"{unrelated} must NOT be protected"

    def test_the_glob_does_not_cross_a_directory_separator(self):
        """`*` never crosses a `/`, so this entry is `tests/` only and says so."""
        assert matches("tests/governance/test_check_something.py") is None
        assert matches("tests/test_identity_header.py") is None


class TestTheExhaustivenessPinIsProtected:
    """Lead-2, 2026-09-27: the outcome-token pin is in the table, by its exact path.

    WHY TWO CASES AND NOT ONE. The entry is WILDCARD-FREE, so
    `test_every_literal_pattern_matches_its_own_path` already walks it and would notice a pattern
    that matched nothing at all. What that structural sweep cannot notice is the OTHER direction:
    an entry widened to `tests/governance/**` during some later tidy-up would still satisfy the
    sweep while quietly putting every governance test behind a human label. So the negative case
    is what pins the entry's NARROWNESS, and it is the half that earns its keep.
    """

    #: The pin itself. `tests/test_check_*.py` does not reach it -- `*` never crosses a `/`.
    PIN = "tests/governance/test_outcome_token_fixtures_pinned.py"
    #: A name nothing in this tree claims; see the negative test's docstring for why that matters.
    UNRELATED = "tests/governance/test_unrelated_governance_thing.py"

    def test_the_pin_is_protected_by_its_exact_path(self):
        """Deleting the pin must itself need the label, or the chain ends in an unguarded file.

        The pin is the LAST link: it asserts from outside `test_check_contract_classification.py`
        that `TOKEN_FIXTURES` still covers `OUTCOME_TOKENS` and that the driver is still collected
        per token, and nothing else in the suite goes red if the pin is simply removed. The
        `tests/test_check_*.py` entry cannot cover it, because that glob is `tests/` only (see
        `glob_to_regex`, and `test_the_glob_does_not_cross_a_directory_separator` above). This
        asserts the entry that closes that gap is in the table AND that it is the entry doing the
        work -- not some other pattern matching by accident, which would make the table's log
        report the wrong reason for the wrong file.
        """
        assert (REPO_ROOT / self.PIN).is_file(), (
            f"{self.PIN} must exist or this case is vacuous"
        )
        hit = matches(self.PIN)
        assert hit is not None, (
            f"{self.PIN} must be protected: it is the last link in the outcome-token "
            f"exhaustiveness chain and nothing else notices its deletion"
        )
        glob, why = hit
        assert glob == self.PIN, (
            f"expected the exact-path entry {self.PIN!r} to be the entry doing the work, "
            f"not {glob!r}"
        )
        assert why.strip(), "every entry carries a written reason for the next reader"

    def test_an_unrelated_governance_test_is_not_protected(self):
        """One path, not a directory glob, so the rest of `tests/governance/` stays label-free.

        DELIBERATELY A PATH THAT DOES NOT EXIST IN THE TREE. A real sibling could legitimately be
        added to the table one day on its own merits, and this case would then be asserting
        something nobody meant -- it would go red for a correct change, which is how a test gets
        deleted instead of read. A name nothing will ever claim keeps this a test of the entry's
        WIDTH and of nothing else.

        Over-matching is not a harmless excess. A table that drags unrelated files behind a human
        label is friction nobody signed up for, and that is what gets a whole table switched off
        rather than corrected.
        """
        assert not (REPO_ROOT / self.UNRELATED).exists(), (
            f"{self.UNRELATED} was chosen precisely because nothing claims it; pick another name"
        )
        assert matches(self.UNRELATED) is None, (
            f"{self.UNRELATED} must NOT be protected -- the pin is guarded by an exact path, and "
            f"widening that to `tests/governance/**` would put every governance test, present and "
            f"future, behind the `{WORKFLOW_CHANGE_LABEL}` label"
        )


# ==========================================================================================
# CHANGE (a): the guard must never fail with a bare interpreter error.
#
# Observed live on #104, check run 108552280328 (head 043ff3f3, conclusion `failure`,
# 2026-09-27T04:42:10Z): the checked-out ref did not contain the guard script, `python3` died with
# `can't open file ... [Errno 2]`, and the job printed NO `workflow-change: VERDICT=` line at all.
#
# Why that is a defect and not merely untidy: #103's own rule is that a proof run must assert on
# WHICH token the guard printed, never on redness alone, because `label-missing`,
# `label-setter-not-authorised` and `actor-unresolved` are all red and prove different things. A
# bare interpreter error is indistinguishable from a runner outage or a GitHub incident, so it
# defeats that rule from inside the guard.
#
# These tests run the REAL preflight script, extracted from the workflow YAML.
# ==========================================================================================


class TestThePreflightNamesEveryCause:
    """One fixture per distinct cause, each asserted on BY ITS OWN TOKEN.

    `guard-file-absent` and `guard-file-unreadable` are both `could-not-run` and both exit 1. If
    the only assertion were "it failed", a preflight that reported every cause as `absent` would
    pass this class -- and a reader of a red log would be told the wrong thing about their
    repository. So every test here pins its own token AND asserts the other two are not claimed.
    """

    @staticmethod
    def _run(setup) -> subprocess.CompletedProcess[str]:
        tmp = Path(tempfile.mkdtemp())
        try:
            env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"), "GIT_CONFIG_SYSTEM": str(tmp / "gitconfig")}
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True, env=env)
            (tmp / "scripts").mkdir()
            (tmp / "seed").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, env=env)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "-c", "core.hooksPath=", "commit", "-qm", "seed"],
                cwd=tmp, check=True, env=env,
            )
            setup(tmp)
            script = tmp / "preflight.sh"
            script.write_text(_preflight_body(), encoding="utf-8")
            return subprocess.run(
                ["bash", str(script)], cwd=tmp, capture_output=True, text=True, timeout=120, env=env
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _assert_could_not_run(result, reason: str) -> None:
        out = result.stdout + result.stderr
        assert result.returncode != 0, f"a {reason} state must NOT exit 0:\n{out}"
        assert f"{VERDICT_PREFIX}{VERDICT_COULD_NOT_RUN}" in out, (
            f"no explicit could-not-run verdict was printed:\n{out}"
        )
        assert f"REASON={reason}" in out, f"expected REASON={reason}, got:\n{out}"
        for other in PREFLIGHT_REASONS:
            if other != reason:
                assert f"REASON={other}" not in out, f"{reason} was also reported as {other}"
        assert GUARD_SCRIPT in out, "the message must name WHICH file"

    def test_an_absent_guard_script_is_could_not_run_and_not_a_pass(self):
        """#104's exact state: the checked-out ref simply does not contain the script."""
        result = self._run(lambda tmp: None)
        self._assert_could_not_run(result, "guard-file-absent")

    def test_an_unreadable_guard_script_is_could_not_run_and_not_a_pass(self):
        """A path that exists but whose bytes cannot be read.

        A directory at the path rather than `chmod 000`, and that choice is load-bearing: this
        suite runs as root in some environments, and root defeats every DAC permission bit, so a
        `chmod 000` fixture would silently PASS THE FILE as readable and the test would prove
        nothing. `EISDIR` is refused for everyone. The preflight tests readability by actually
        reading a byte (`head -c 1`) rather than by consulting `-r`, which is what makes both the
        permission case and this one land on the same token.
        """
        result = self._run(lambda tmp: (tmp / GUARD_SCRIPT).mkdir())
        self._assert_could_not_run(result, "guard-file-unreadable")

    def test_an_unparseable_guard_script_is_could_not_run_and_not_a_pass(self):
        """It reads, but `python3` would die with a SyntaxError and print no verdict either."""
        result = self._run(
            lambda tmp: (tmp / GUARD_SCRIPT).write_text("def broken(:\n", encoding="utf-8")
        )
        self._assert_could_not_run(result, "guard-script-unparseable")

    def test_a_present_readable_parseable_script_passes_the_preflight(self):
        """The non-vacuity half: if this failed too, the three tests above would prove nothing."""
        result = self._run(
            lambda tmp: (tmp / GUARD_SCRIPT).write_text("x = 1\n", encoding="utf-8")
        )
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "PREFLIGHT=ok" in out
        assert VERDICT_COULD_NOT_RUN not in out
        for reason in PREFLIGHT_REASONS:
            assert f"REASON={reason}" not in out

    def test_the_real_guard_script_passes_the_preflight_in_this_tree(self):
        """The literal path in the workflow must name the file that is actually here.

        A preflight pointed at a path that does not exist would report `guard-file-absent` on
        every run, which is a guard that cries wolf; a preflight pointed at a path that exists but
        is not this guard reads as coverage.
        """
        result = self._run(
            lambda tmp: shutil.copyfile(REPO_ROOT / GUARD_SCRIPT, tmp / GUARD_SCRIPT)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PREFLIGHT=ok" in result.stdout

    def test_the_failure_message_names_the_ref_and_the_sha_it_actually_read(self):
        """Not the expression that was meant to produce them -- the values git landed on.

        `ref=` and `sha=` are read back with `git rev-parse` inside the step, so the message is
        verifiable after the fact rather than aspirational.
        """
        result = self._run(lambda tmp: None)
        out = result.stdout + result.stderr
        assert "ref=main" in out, out
        sha = re.search(r"sha=([0-9a-f]{40})\b", out)
        assert sha is not None, f"no 40-hex sha in the message:\n{out}"
        assert sha.group(1) not in ("0" * 40,)

    def test_the_three_reasons_are_pairwise_distinct(self):
        assert len(set(PREFLIGHT_REASONS)) == len(PREFLIGHT_REASONS)

    def test_the_preflight_runs_before_the_guard(self):
        """Order matters: a preflight after the failing step would never be reached."""
        names = [(s.get("name") or s.get("uses") or "") for s in _workflow_steps()]
        pre = [i for i, n in enumerate(names) if "Preflight" in n]
        guard = [i for i, n in enumerate(names) if "workflow-change` label" in n]
        assert len(pre) == 1 and len(guard) == 1, names
        assert pre[0] < guard[0], names

    def test_the_preflight_run_block_has_no_interpolation(self):
        """Same rule as every other `run:` here, restated where the new block is.

        `test_no_interpolation_reaches_any_run_block` covers the file; this names the new step, so
        a reader of this class does not have to go looking for the guarantee.
        """
        assert "${{" not in _preflight_body()

    def test_every_literal_repo_path_in_the_preflight_exists(self):
        """A literal path or glob that names nothing reads as coverage.

        Mirrors `test_every_literal_pattern_matches_its_own_path` for the paths introduced by this
        change: every `scripts/...`-shaped literal in the preflight must name a real file here.
        """
        body = _preflight_body()
        found = re.findall(r"(?:scripts|\.github|\.githooks)/[A-Za-z0-9_./-]+", body)
        assert found, "no repository path found in the preflight -- this test would be vacuous"
        missing = sorted({f for f in set(found) if not (REPO_ROOT / f).exists()})
        assert missing == [], f"the preflight names paths that do not exist: {missing}"


class TestCouldNotRunIsNeverAPass:
    """`could-not-run` must be a FAILURE on EVERY path that can emit it.

    A `could-not-run` path that exited 0 would be a fail-open bug in the merged guard. There are
    exactly two emitters: `main`'s `except GuardFailure` handler in the script, and the workflow's
    preflight step. Both are checked here, and the script's is checked structurally as well as
    behaviourally, so a future path that prints the token cannot quietly return 0.
    """

    def test_main_returns_non_zero_when_the_guard_cannot_run(self, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        code = main(["--pr", str(PR)])
        out = capsys.readouterr().out
        assert code != 0, "a guard that could not run must never exit 0"
        assert f"{VERDICT_PREFIX}{VERDICT_COULD_NOT_RUN}" in out

    def test_main_returns_non_zero_when_the_repository_is_unknown(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "x")
        code = main(["--pr", str(PR)])
        out = capsys.readouterr().out
        assert code != 0
        assert f"{VERDICT_PREFIX}{VERDICT_COULD_NOT_RUN}" in out

    def test_the_actor_unresolved_red_is_also_non_zero_and_its_own_token(self, monkeypatch, capsys):
        """The other GuardFailure verdict, so this class is not only about one token."""
        assert VERDICT_ACTOR_UNRESOLVED != VERDICT_COULD_NOT_RUN

    def test_the_only_emitter_of_the_token_in_the_script_returns_one(self):
        """Structural, so a NEW path that prints the token cannot be added returning 0.

        The script prints `could-not-run` in exactly one place: `main`'s `except GuardFailure`
        handler, which ends `return 1`. If a second print site appears, this test fails and whoever
        added it has to say what its exit status is.
        """
        source = (REPO_ROOT / GUARD_SCRIPT).read_text(encoding="utf-8")
        body = "".join(
            line for line in source.splitlines(keepends=True)
            if not line.lstrip().startswith("#")
        )
        printers = [
            line.strip()
            for line in body.splitlines()
            if "print(" in line and "VERDICT_COULD_NOT_RUN" in line
        ]
        assert printers == [], (
            "could-not-run is printed via `exc.verdict`, not by name; a new by-name print site "
            f"needs its exit status stated: {printers}"
        )
        handler = body[body.index("except GuardFailure as exc:") :]
        assert "exc.verdict" in handler
        assert "return 1" in handler.split("if __name__")[0]

    def test_no_terminal_path_in_the_script_returns_zero_after_a_guard_failure(self):
        """Every GuardFailure reaches `main`'s handler, which is the only `return` after it."""
        api = cleared(".github/workflows/ci.yml")
        api.default_branch = None
        with pytest.raises(GuardFailure):
            run(api)


class TestTheAllowlistRefIsTheDefaultBranchAndNotTheBase:
    """Change (b), the half that is NOT about the checkout.

    The merged guard read the allowlist at `pull.base.ref` -- the pull request's own base branch.
    For a pull request into `main` that is the same string and the same answer, which is why the
    defect was invisible. For a pull request into any OTHER branch it was a self-authorisation
    hole: the allowlist came from the branch being merged into, so anyone who could push to that
    branch could add an account there and have it clear their own CI change.

    NOTE, because it was the premise of this pull request and it turned out not to hold: the
    merged guard did NOT read the allowlist out of the checked-out tree. It fetched it from the
    contents API at `base.ref`, a REF, which GitHub resolves to that branch's current tip -- so
    REVOCATION ALREADY WORKED for pull requests into `main`. Removing an account took effect on
    the next run of every open pull request, whatever commit it was based on. The staleness in the
    merged code was the guard SCRIPT, checked out at `base.sha`; the allowlist was never stale.
    """

    def test_a_pull_request_into_a_non_default_branch_still_reads_the_default_branchs_allowlist(
        self,
    ):
        api = cleared(".github/workflows/ci.yml")
        api.pull = {
            "changed_files": 1,
            # A branch the author may well be able to push to.
            "base": {"ref": "release/0.5.x", "sha": "b" * 40},
            "head": {"sha": "h" * 40},
        }
        api.default_branch = "main"
        code, _ = run(api)
        assert code == 0
        contents = [c for c in api.calls if "/contents/" in c]
        assert contents, "the allowlist was never fetched"
        for call in contents:
            assert "?ref=main" in call, f"the allowlist was read at the PR's base: {call}"
            assert "release/0.5.x" not in call
            assert "release%2F0.5.x" not in call

    def test_the_default_branch_is_asked_for_explicitly(self):
        api = cleared(".github/workflows/ci.yml")
        assert run(api)[0] == 0
        assert f"https://api.github.com/repos/{REPO}" in api.calls, (
            "the repository object was never read, so the default branch was assumed"
        )

    def test_the_default_branch_is_not_hard_coded_to_main(self):
        """A repository whose default branch is not `main` must still be handled."""
        api = cleared(".github/workflows/ci.yml")
        api.default_branch = "trunk"
        code, _ = run(api)
        assert code == 0
        contents = [c for c in api.calls if "/contents/" in c]
        assert contents
        assert all("?ref=trunk" in c for c in contents)

    def test_the_source_named_in_a_failure_cites_the_ref_and_the_blob_sha(self):
        """"`@main`" alone names a moving target. The blob sha is what makes it checkable.

        Asserted on the `label-setter-not-authorised` message, which is the one a human is sent to
        act on: they are told an account is not on the list, so they must be able to read the exact
        list that was consulted -- `git cat-file -p <sha>`.
        """
        api = FakeAPI(
            pull=pull_obj(changed=1),
            files=files(".github/workflows/ci.yml"),
            labels=[[{"name": WORKFLOW_CHANGE_LABEL}]],
            timeline=[[labelled_by("someone-else")]],
        )
        code, text = run(api)
        assert code == 1
        assert f"{VERDICT_PREFIX}{VERDICT_NOT_AUTHORISED}" in text
        assert f"{DEFAULT_ALLOWLIST}@main" in text
        assert ALLOWLIST_BLOB in text, f"the blob sha is not cited:\n{text}"

    def test_the_cleared_message_also_cites_a_checkable_allowlist(self):
        api = cleared(".github/workflows/ci.yml")
        code, text = run(api)
        assert code == 0
        assert f"{VERDICT_PREFIX}{VERDICT_CLEARED}" in text

    def test_the_guard_script_no_longer_reads_the_allowlist_at_the_pull_requests_base(self):
        """Structural, so the line cannot quietly go back.

        The one remaining read of `pull["base"]` is `fetch_pull_request`'s completeness check,
        which is about having a base at all, not about which ref to trust.
        """
        source = (REPO_ROOT / GUARD_SCRIPT).read_text(encoding="utf-8")
        body = "".join(
            line for line in source.splitlines(keepends=True)
            if not line.lstrip().startswith("#")
        )
        check_body = body[body.index("def check("): body.index("def resolve_pr_number(")]
        assert "fetch_default_branch" in check_body
        assert 'pull["base"]' not in check_body, (
            "check() must not take the allowlist ref from the pull request's base"
        )


# ==========================================================================================
# THE ADVERSARIAL PASS ON THIS CHANGE FOUND THREE SURVIVING MUTANTS. They are fixed here rather
# than counted as coverage, and each class below names the mutant it exists to kill.
# ==========================================================================================


class TestThePreflightedScriptIsTheScriptThatRuns:
    """SURVIVOR: the guard step was pointed at a different file than the preflight checks.

    The mutant changed `run: python3 scripts/check_workflow_change_label.py` to
    `..._v2.py` and the whole suite still passed. The preflight would then have gone green on a
    file nothing executes while `python3` died with `can't open file` on the file nothing checked
    -- reinstating the exact defect this change removes, with a preflight standing next to it
    saying everything was fine.

    Both paths are read out of the parsed YAML, so they cannot drift apart again.
    """

    @staticmethod
    def _guard_step() -> dict:
        steps = _workflow_steps()
        running = [
            s for s in steps
            if isinstance(s.get("run"), str)
            and "check_workflow" in s["run"]
            and "Preflight" not in (s.get("name") or "")
        ]
        assert len(running) == 1, f"expected exactly one step that runs the guard, got {len(running)}"
        return running[0]

    def test_the_guard_step_invokes_the_guard_script(self):
        run_line = self._guard_step()["run"].strip()
        assert run_line == f"python3 {GUARD_SCRIPT}", (
            f"the guard step must invoke {GUARD_SCRIPT} and nothing else, got {run_line!r}"
        )

    def test_the_preflighted_path_and_the_executed_path_are_the_same_file(self):
        body = _preflight_body()
        assigned = re.findall(r"^\s*script=(\S+)\s*$", body, re.MULTILINE)
        assert len(assigned) == 1, f"the preflight must name exactly one script, got {assigned}"
        executed = re.findall(r"python3\s+(\S+\.py)", self._guard_step()["run"])
        assert len(executed) == 1, f"the guard step must run exactly one script, got {executed}"
        assert assigned[0] == executed[0], (
            f"the preflight checks {assigned[0]} but the job runs {executed[0]}"
        )
        assert (REPO_ROOT / executed[0]).is_file(), f"{executed[0]} does not exist in this tree"

    def test_the_guard_step_is_still_the_only_interpolation_free_run_of_the_guard(self):
        # Non-vacuity: if `_guard_step` ever matched nothing, every assertion above would be
        # unreachable rather than false, so the count is asserted inside `_guard_step`.
        assert "${{" not in self._guard_step()["run"]


class TestTheLocalAllowlistReadIsNeverTheDefault:
    """SURVIVOR: `--allowlist-file` was given a default, moving the CI read into the checkout.

    Two mutants survived the whole suite: `--allowlist-file default=DEFAULT_ALLOWLIST` in the
    parser, and `allowlist_file: str | None = DEFAULT_ALLOWLIST` on `check` itself. Either one
    makes CI read the allowlist out of whatever tree the job checked out, with NO change to the
    workflow -- so the existing
    `test_the_allowlist_path_is_the_one_the_workflow_does_not_override`, which asserts only that
    the workflow does not PASS the flag, passed unchanged.

    THIS IS A LATENT FAIL-OPEN IN THE MERGED GUARD, not something this change introduced: the
    merged code had both defaults right and neither was asserted. It matters even now that the
    checkout is the default branch, because the guarantee the file states everywhere is that the
    allowlist comes from a REF the pull request cannot write to -- never from a tree, which is a
    thing one line of a later edit can re-point.
    """

    def test_the_parser_defaults_to_no_local_allowlist(self):
        args = build_parser().parse_args([])
        assert args.allowlist_file is None, (
            "--allowlist-file must default to None, or CI reads the allowlist from the checkout"
        )

    def test_the_parser_still_accepts_the_flag_for_offline_runs(self):
        # Non-vacuity: the test above would also pass if the flag had simply been deleted.
        args = build_parser().parse_args(["--allowlist-file", "x.txt"])
        assert args.allowlist_file == "x.txt"

    def test_checks_own_parameter_defaults_to_no_local_allowlist(self):
        default = inspect.signature(check).parameters["allowlist_file"].default
        assert default is None, f"check()'s allowlist_file must default to None, not {default!r}"

    def test_the_default_path_really_does_reach_the_contents_api(self):
        """The behavioural half: with the defaults above, the read is an API read."""
        api = cleared(".github/workflows/ci.yml")
        assert run(api)[0] == 0
        assert [c for c in api.calls if "/contents/" in c], "no API read of the allowlist happened"
