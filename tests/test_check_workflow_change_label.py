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
  * the setter allowlist cannot be fetched from the base ref, is empty, or holds an entry with no
    reason.

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
`types: [... labeled, unlabeled]`, read-only permissions, a base-sha checkout and no `${{ }}` in
any `run:` block are the four properties that make this guard both effective and safe, and a
reviewer who does not know that will delete one as noise. A test is a comment that fights back.
"""

from __future__ import annotations

import base64
import io
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_workflow_change_label import (  # noqa: E402 -- path set just above, repo idiom (test_check_no_secrets_in_diff.py:33)
    DEFAULT_ALLOWLIST,
    MAX_PAGES,
    PER_PAGE,
    PROTECTED_PATTERNS,
    TOUCHING_STATUSES,
    WORKFLOW_CHANGE_LABEL,
    GuardFailure,
    check,
    fetch_allowlist_at_base,
    glob_to_regex,
    matches,
    parse_allowlist,
    resolve_pr_number,
)

REPO = "AxisMeru/pravrudhi"
PR = 78
ALLOWLIST_TEXT = "# comment\n\nAxisMeru  the account the lead acts as\n"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "workflow-change.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")

#: The workflow with its comment lines removed. Every "this string must NEVER appear" assertion
#: below runs against THIS and not against the raw text, because the file's own header quotes the
#: dangerous constructs in order to forbid them -- `head.sha` appears there as a warning, and a
#: test that could not tell a warning from a use would force the warning to be deleted.
WORKFLOW_CODE = "".join(
    line for line in WORKFLOW_TEXT.splitlines(keepends=True) if not line.lstrip().startswith("#")
)


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
        errors: tuple[str, ...] = (),
    ) -> None:
        self.pull = pull
        self.files = files if files is not None else [[]]
        self.labels = labels if labels is not None else [[]]
        self.timeline = timeline if timeline is not None else [[]]
        self.users = users
        self.allowlist = allowlist
        self.errors = errors
        self.calls: list[str] = []

    def __call__(self, url: str) -> tuple[object, str]:
        self.calls.append(url)
        for fragment in self.errors:
            if fragment in url:
                raise GuardFailure(f"GET {url} failed: HTTP 403 Forbidden")
        path, _, query = url.partition("?")
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
            if self.users is None:
                return {"login": path.rsplit("/", 1)[1]}, ""
            return self.users, ""
        if "/contents/" in path:
            if self.allowlist is None:
                raise GuardFailure(f"GET {url} failed: HTTP 404 Not Found")
            return {
                "encoding": "base64",
                "content": base64.b64encode(self.allowlist.encode()).decode(),
            }, ""
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
            (".githooks/commit-msg", "Tag's proposal: the identity hook"),
            (".githooks/pre-commit", "Tag's proposal: the primary-checkout hook"),
            (".githooks/deep/thing", "a hook filed one directory deeper"),
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
                "tests/test_check_workflow_change_label.py",
                "this test file is deliberately NOT protected -- see the pull request body's "
                "residuals; deleting a test does not weaken the running guard",
            ),
        ],
    )
    def test_not_protected(self, path: str, why: str):
        assert matches(path) is None, f"{path} must not be protected ({why})"

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
            return {"encoding": "none", "content": "x"}, ""

        with pytest.raises(GuardFailure, match="did not come back as a base64 file"):
            fetch_allowlist_at_base(fetch, REPO, "main", DEFAULT_ALLOWLIST)

    def test_an_undecodable_allowlist_is_a_hard_failure(self):
        def fetch(url: str) -> tuple[object, str]:
            return {"encoding": "base64", "content": "!!!not base64!!!"}, ""

        with pytest.raises(GuardFailure, match="could not be decoded"):
            fetch_allowlist_at_base(fetch, REPO, "main", DEFAULT_ALLOWLIST)

    def test_a_missing_pull_request_number_is_a_hard_failure(self, monkeypatch):
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
        with pytest.raises(GuardFailure, match="could not determine which pull request"):
            resolve_pr_number(None)


class TestAllowlistComesFromTheBaseRef:
    def test_the_allowlist_is_read_at_the_base_ref_and_never_at_head(self):
        api = cleared(".github/workflows/ci.yml")
        code, _ = run(api)
        assert code == 0
        contents = [c for c in api.calls if "/contents/" in c]
        assert contents, "the allowlist was never fetched"
        assert all("?ref=main" in c for c in contents)
        assert not any("h" * 40 in c for c in api.calls)

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
        assert scopes == {"contents": "read", "pull-requests": "read"}

    def test_no_write_permission_anywhere_in_the_file(self):
        assert not re.search(r"^\s*[a-z-]+:\s*write\s*$", WORKFLOW_TEXT, re.MULTILINE)

    def test_the_only_checkout_is_pinned_to_the_base_sha(self):
        checkouts = re.findall(r"uses:\s*actions/checkout@", WORKFLOW_TEXT)
        assert len(checkouts) == 1, "exactly one checkout, of the base"
        assert "ref: ${{ github.event.pull_request.base.sha }}" in WORKFLOW_TEXT

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


class TestTheGuardGuardsItself:
    def test_this_guards_own_workflow_script_and_allowlist_are_all_protected(self):
        for path in (
            ".github/workflows/workflow-change.yml",
            "scripts/check_workflow_change_label.py",
            DEFAULT_ALLOWLIST,
        ):
            assert matches(path) is not None, f"{path} must be protected"

    def test_all_three_files_exist_where_the_guard_expects_them(self):
        assert WORKFLOW.is_file()
        assert (REPO_ROOT / "scripts" / "check_workflow_change_label.py").is_file()
        assert (REPO_ROOT / DEFAULT_ALLOWLIST).is_file()
