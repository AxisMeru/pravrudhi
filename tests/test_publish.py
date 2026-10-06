"""Publishing must refuse rather than half-complete.

Each of these cases actually happened. A page shipped before the snapshot carried its data and rendered "this
recording predates the requests log" on the public site while working against a live engine. A build succeeded
and the commit went out unpushed, so the deployment that rebuilds on push never ran. The order of the steps and
the refusals are the lesson, so they are what is tested.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.publish import (
    CHECK_PAGES,
    build_interface,
    commit,
    export_snapshot,
    personal_data_guard,
    publish,
    push,
    push_branch,
    repo_slug,
    start_branch,
    sync_write_root,
    verify_pages,
)

_REAL_PRE_COMMIT_HOOK = Path(__file__).resolve().parents[1] / ".githooks" / "pre-commit"


def _real_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=check)


def _ok(cmd: list[str], out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def _fail(cmd: list[str], err: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=err)


def _is(cmd: list[str], sub: str) -> bool:
    """A git subcommand, ignoring the -c identity flags the house rules require before it."""
    return cmd[:1] == ["git"] and sub in cmd


def _workspace(tmp_path: Path, *, snapshot: dict[str, Any] | None = None, pages: dict[str, str] | None = None) -> Path:
    fe = tmp_path / "app" / "frontend"
    (fe / "public").mkdir(parents=True)
    (fe / "package.json").write_text('{"name": "x"}')
    (fe / "public" / "demo.json").write_text(json.dumps(snapshot if snapshot is not None else {"requests": {}}))
    out = fe / "out"
    out.mkdir()
    for page in CHECK_PAGES:
        name = "index.html" if page == "/" else f"{page.strip('/')}.html"
        (out / name).write_text((pages or {}).get(page, "<html>real content</html>"))
    return tmp_path


@pytest.fixture(autouse=True)
def _private_names_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The export step needs the private-name list's path and sha256 from the environment (a fake runner: any value will do)."""
    monkeypatch.setenv("PRAVRUDHI_DEMO_PRIVATE_NAMES", "/nonexistent/names.txt")
    monkeypatch.setenv("PRAVRUDHI_DEMO_PRIVATE_NAMES_SHA256", "0" * 64)


class TestTheStepsRunInOrder:
    def test_nothing_is_committed_when_the_export_fails(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        seen: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            seen.append(cmd)
            return _fail(cmd, "ledger unreadable") if "demo-export" in cmd else _ok(cmd)

        result = publish(root, runner=runner)
        assert not result.published
        assert "export failed" in result.reason
        assert not any(_is(c, "commit") for c in seen), "a stale snapshot must never be committed"

    def test_nothing_is_pushed_when_the_build_fails(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        seen: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            seen.append(cmd)
            return _fail(cmd, "Type error in page.tsx") if cmd[-1] == "build" else _ok(cmd)

        result = publish(root, runner=runner)
        assert not result.published and "build failed" in result.reason
        assert not any(_is(c, "push") for c in seen)

    def test_the_snapshot_is_exported_before_the_interface_is_built(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        order: list[str] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if "demo-export" in cmd:
                order.append("export")
            if cmd[-1] == "build":
                order.append("build")
            if _is(cmd, "commit"):
                order.append("commit")
            if _is(cmd, "push"):
                order.append("push")
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _ok(cmd, "app/frontend/public/demo.json\n")
            return _ok(cmd, "abc1234")

        publish(root, runner=runner)
        assert order == ["export", "build", "commit", "push"], order


class TestARenderedErrorStopsThePublish:
    def test_a_page_showing_a_stale_recording_notice_is_a_failure(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path, pages={"/requests": "<html>This recording predates the requests log.</html>"})
        seen: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            seen.append(cmd)
            return _ok(cmd)

        result = publish(root, runner=runner)
        assert not result.published
        assert "predates" in result.reason and "/requests" in result.reason
        assert not any(_is(c, "push") for c in seen)

    def test_a_page_that_cannot_reach_the_engine_is_a_failure(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path, pages={"/swarm": "<html>Could not reach the engine's swarm API.</html>"})
        step = verify_pages(root)
        assert not step.ok and "/swarm" in step.detail

    def test_a_page_that_was_never_built_is_a_failure(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        (root / "app" / "frontend" / "out" / "candidates.html").unlink()
        step = verify_pages(root)
        assert not step.ok and "candidates" in step.detail

    def test_pages_carrying_content_pass(self, tmp_path: Path) -> None:
        step = verify_pages(_workspace(tmp_path))
        assert step.ok and str(len(CHECK_PAGES)) in step.detail


class TestSteps:
    def test_the_export_reports_what_the_snapshot_contains(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path, snapshot={"requests": {}, "candidates": [], "inbox": []})
        step = export_snapshot(root, lambda cmd, cwd: _ok(cmd))
        assert step.ok and "candidates" in step.detail

    def test_an_unreadable_snapshot_fails_the_export(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        (root / "app" / "frontend" / "public" / "demo.json").write_text("{not json")
        step = export_snapshot(root, lambda cmd, cwd: _ok(cmd))
        assert not step.ok and "unreadable" in step.detail

    def test_the_local_bundle_is_built_without_a_base_path(self, tmp_path: Path) -> None:
        """A sub-path baked into the bundle the engine serves at its own root breaks every asset."""
        root = _workspace(tmp_path)
        seen: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            seen.append(cmd)
            return _ok(cmd)

        assert build_interface(root, runner).ok
        assert seen[0][0] == "npm", seen[0]

        seen.clear()
        assert build_interface(root, runner, base_path="/pravrudhi/app").ok
        assert "NEXT_PUBLIC_BASE_PATH=/pravrudhi/app" in seen[0]

    def test_publishing_can_stop_before_the_push(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)
        seen: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            seen.append(cmd)
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _ok(cmd, "app/frontend/public/demo.json\n")
            return _ok(cmd, "abc1234")

        result = publish(root, runner=runner, do_push=False)
        assert result.published and "not pushed" in result.reason
        assert not any(_is(c, "push") for c in seen)

    def test_an_unchanged_snapshot_is_not_an_error(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path)

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            return _ok(cmd, "")  # `git diff --cached` reports nothing staged

        result = publish(root, runner=runner)
        assert result.published, result.reason


class TestTheReadRootAndWriteRootCanDiffer:
    """ADR-0053 §2: the publisher's own clone commits and pushes; the loop root is only ever read from. The
    main checkout the lead merges assistant branches into must have exactly one writer."""

    def test_the_snapshot_and_build_land_in_write_root_not_read_root(self, tmp_path: Path) -> None:
        read_root = tmp_path / "loop"
        (read_root / "research").mkdir(parents=True)
        (read_root / "research" / "ledger.jsonl").write_text("")  # exists and empty: zero real events, valid
        write_root = _workspace(tmp_path / "publish-clone")

        git_cwds: list[Path] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if "demo-export" in cmd:
                # The real command writes to --dest; the fake stands in for that side effect so the export
                # step's own read of the file it just "wrote" still succeeds.
                dest = Path(cmd[cmd.index("--dest") + 1])
                dest.write_text('{"requests": {}}')
                assert cmd[cmd.index("--root") + 1] == str(read_root), "must read the ledger from read_root"
                assert cwd == read_root, "demo-export must run where the engine (and its ledger) actually is"
            if cmd[:1] == ["git"]:
                git_cwds.append(cwd)
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _ok(cmd, "app/frontend/public/demo.json\n")
            return _ok(cmd, "abc1234")

        result = publish(read_root, write_root=write_root, runner=runner)
        assert result.published, result.reason
        assert (write_root / "app" / "frontend" / "public" / "demo.json").exists()
        assert not (read_root / "app").exists(), "nothing should be written into the read root at all"
        assert git_cwds and all(c == write_root for c in git_cwds), "every git step must run in write_root"

    def test_a_read_root_with_no_ledger_refuses_rather_than_publishing_empty(self, tmp_path: Path) -> None:
        read_root = tmp_path / "not-a-real-engine-root"
        read_root.mkdir()
        write_root = _workspace(tmp_path / "publish-clone")

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            raise AssertionError("must refuse before running anything when read_root has no ledger")

        result = publish(read_root, write_root=write_root, runner=runner)
        assert not result.published
        assert "ledger" in result.reason
        assert result.steps == []

    def test_a_single_root_publish_with_no_ledger_is_unaffected(self, tmp_path: Path) -> None:
        """The no-ledger guard is scoped to the two-root case. A plain single-root publish (write_root unset)
        keeps demo_export.build_demo's own deliberate behaviour: no ledger is a legitimate empty bundle for a
        fresh single-machine install, not a refusal."""
        root = _workspace(tmp_path)  # _workspace never creates research/ledger.jsonl

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _ok(cmd, "app/frontend/public/demo.json\n")
            return _ok(cmd, "abc1234")

        result = publish(root, runner=runner)
        assert result.published, result.reason


class TestThePublisherIsNeverPointedAtAGuardedTree:
    """The publisher ran from the lead's own main checkout and was refused, 20 beats in a row, by
    `.githooks/pre-commit` -- a guard written to stop agents committing on `main` in a primary checkout, never
    considering the publisher was another writer there. ADR-0053 §2's fix is a clone of its own
    (`~/pravrudhi-publish`), checked out on a branch that is deliberately not named `main`. These tests use the
    real hook file, not a description of it: if anyone ever points `write_root` back at a `main`-checked-out
    primary tree, this must fail for the same reason production did, and if the branch-name workaround is ever
    undone, the second test catches that too."""

    @staticmethod
    def _hooked_repo(tmp_path: Path, name: str, *, branch: str) -> Path:
        """The seed commit lands before the hook is installed - installing it first would refuse the very
        commit that creates the repo's history, which is not what either test is checking."""
        repo = tmp_path / name
        _git(tmp_path, "init", "-q", "-b", branch, str(repo))
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "user.email", "t@t.example")
        (repo / "app" / "frontend" / "public").mkdir(parents=True)
        (repo / "app" / "frontend" / "public" / "demo.json").write_text("{}")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "seed")
        _git(repo, "config", "core.hooksPath", ".githooks")
        (repo / ".githooks").mkdir()
        hook = repo / ".githooks" / "pre-commit"
        hook.write_bytes(_REAL_PRE_COMMIT_HOOK.read_bytes())
        hook.chmod(0o755)
        return repo

    def test_a_write_root_checked_out_on_main_is_refused_by_the_real_hook(self, tmp_path: Path) -> None:
        repo = self._hooked_repo(tmp_path, "guarded", branch="main")
        (repo / "app" / "frontend" / "public" / "demo.json").write_text('{"v": 1}')

        step, sha = commit(repo, _real_runner, "refresh snapshot", ["app/frontend/public/demo.json"])

        assert not step.ok
        assert "refusing a commit on `main`" in step.detail
        assert sha is None

    def test_a_write_root_on_a_differently_named_branch_commits_and_pushes_to_origin_main(self, tmp_path: Path) -> None:
        # A bare origin, and the publish clone on branch `publish` -- never `main` -- tracking it.
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        seed = self._hooked_repo(tmp_path, "seed", branch="main")
        _git(seed, "remote", "add", "origin", str(origin))
        _git(seed, "push", "-q", "origin", "main")

        clone = tmp_path / "publish-clone"
        _git(tmp_path, "clone", "-q", str(origin), str(clone))
        _git(clone, "checkout", "-q", "-b", "publish")
        _git(clone, "config", "user.name", "t")
        _git(clone, "config", "user.email", "t@t.example")
        _git(clone, "config", "core.hooksPath", ".githooks")
        (clone / ".githooks").mkdir()
        hook = clone / ".githooks" / "pre-commit"
        hook.write_bytes(_REAL_PRE_COMMIT_HOOK.read_bytes())
        hook.chmod(0o755)
        (clone / "app" / "frontend" / "public" / "demo.json").write_text('{"v": 2}')

        step, sha = commit(clone, _real_runner, "refresh snapshot", ["app/frontend/public/demo.json"])
        assert step.ok, step.detail
        assert sha is not None

        push_step = push(clone, _real_runner)
        assert push_step.ok, push_step.detail

        remote_main = _git(clone, "ls-remote", "--heads", str(origin), "main").stdout
        assert sha in remote_main, "HEAD:main must land the commit on origin's main even though the local branch is not main"


class TestTheWriteRootSyncsBeforeItBuildsOnAStaleTip:
    """A `pravrudhi-publish` clone that last synced hours ago, while anything else merged to `origin/main` in
    the meantime, used to build its next snapshot commit on that stale tip and then have the push rejected --
    every 30 minutes, forever, because nothing ever moved the clone's tip forward again. This happened in
    production: one clone sat 13 commits ahead of a stale base and 10 behind the real tip, failing on every
    run. The fix is to always reset the write root to the remote's current tip before building anything on
    it, since a write root only ever carries auto-generated snapshot/paper commits -- never unique work worth
    preserving across a run.
    """

    @staticmethod
    def _hooked_repo(tmp_path: Path, name: str, *, branch: str) -> Path:
        repo = tmp_path / name
        _git(tmp_path, "init", "-q", "-b", branch, str(repo))
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "user.email", "t@t.example")
        (repo / "app" / "frontend" / "public").mkdir(parents=True)
        (repo / "app" / "frontend" / "public" / "demo.json").write_text("{}")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "seed")
        return repo

    def test_push_is_rejected_when_the_write_root_is_stale_and_sync_fixes_it(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        seed = self._hooked_repo(tmp_path, "seed", branch="main")
        _git(seed, "remote", "add", "origin", str(origin))
        _git(seed, "push", "-q", "origin", "main")

        clone = tmp_path / "publish-clone"
        _git(tmp_path, "clone", "-q", str(origin), str(clone))
        _git(clone, "checkout", "-q", "-b", "publish")
        _git(clone, "config", "user.name", "t")
        _git(clone, "config", "user.email", "t@t.example")

        # Someone else merges unrelated work to origin/main after the clone was made -- exactly what happens
        # between two publish cycles on a live project.
        (seed / "other.txt").write_text("someone else's work")
        _git(seed, "add", "other.txt")
        _git(seed, "commit", "-q", "-m", "unrelated merge")
        _git(seed, "push", "-q", "origin", "main")

        (clone / "app" / "frontend" / "public" / "demo.json").write_text('{"v": 2}')
        step, sha = commit(clone, _real_runner, "refresh snapshot", ["app/frontend/public/demo.json"])
        assert step.ok, step.detail

        push_step = push(clone, _real_runner)
        assert not push_step.ok, "this push must fail on an unsynced clone, or the test no longer reproduces the bug"

        # Reset the clone back to as it stood right after cloning (pre-existing local commit only, origin has
        # since moved past it) and confirm sync_write_root brings it to the real tip before anything is built.
        _git(clone, "reset", "--hard", "HEAD~1")
        sync_step = sync_write_root(clone, _real_runner)
        assert sync_step.ok, sync_step.detail

        log = _git(clone, "log", "--oneline", "-1").stdout
        assert "unrelated merge" in log, "the write root must be at the remote's current tip after syncing"

        (clone / "app" / "frontend" / "public" / "demo.json").write_text('{"v": 3}')
        step2, sha2 = commit(clone, _real_runner, "refresh snapshot", ["app/frontend/public/demo.json"])
        assert step2.ok, step2.detail
        push_step2 = push(clone, _real_runner)
        assert push_step2.ok, push_step2.detail

        remote_main = _git(clone, "ls-remote", "--heads", str(origin), "main").stdout
        assert sha2 in remote_main

    def test_publish_syncs_the_write_root_before_exporting_when_write_root_is_given(self, tmp_path: Path) -> None:
        """The end-to-end path: `publish()` itself must call the sync, not just leave it as a function nobody
        calls -- the production bug was `publish()` never syncing, not the absence of a sync primitive."""
        read_root = tmp_path / "loop"
        (read_root / "research").mkdir(parents=True)
        (read_root / "research" / "ledger.jsonl").write_text("")
        write_root = _workspace(tmp_path / "publish-clone")
        order: list[str] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if cmd[:2] == ["git", "fetch"]:
                order.append("sync-fetch")
            if cmd[:2] == ["git", "reset"]:
                order.append("sync-reset")
            if "demo-export" in cmd:
                order.append("export")
                dest = Path(cmd[cmd.index("--dest") + 1])
                dest.write_text('{"requests": {}}')
            if cmd[-1] == "build":
                order.append("build")
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _ok(cmd, "app/frontend/public/demo.json\n")
            return _ok(cmd, "abc1234")

        result = publish(read_root, write_root=write_root, runner=runner)
        assert result.published, result.reason
        assert order[:4] == ["sync-fetch", "sync-reset", "export", "build"], order

    def test_publish_does_not_sync_a_single_root_write(self, tmp_path: Path) -> None:
        """A single-root publish (`write_root` unset) writes into the shared primary checkout -- resetting it
        to a remote tip would discard whatever the lead or an assistant has in progress there, so it must
        never be touched by the sync."""
        root = _workspace(tmp_path)
        seen: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            seen.append(cmd)
            if cmd[:3] == ["git", "diff", "--cached"]:
                return _ok(cmd, "app/frontend/public/demo.json\n")
            return _ok(cmd, "abc1234")

        result = publish(root, runner=runner)
        assert result.published, result.reason
        assert not any(c[:2] == ["git", "reset"] for c in seen), "a single-root publish must never hard-reset root"


def test_the_export_step_refuses_without_the_private_name_list(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """No list path or no pinned sha256 in the environment: nothing is exported, the runner is never called."""
    from pravrudhi.application import publish

    calls: list[list[str]] = []

    def runner(cmd, cwd):  # noqa: ANN001
        calls.append(cmd)
        raise AssertionError("the exporter must not run without the private-name list")

    for missing in ("PRAVRUDHI_DEMO_PRIVATE_NAMES", "PRAVRUDHI_DEMO_PRIVATE_NAMES_SHA256"):
        monkeypatch.setenv("PRAVRUDHI_DEMO_PRIVATE_NAMES", "/x/names.txt")
        monkeypatch.setenv("PRAVRUDHI_DEMO_PRIVATE_NAMES_SHA256", "0" * 64)
        monkeypatch.delenv(missing)
        step = publish.export_snapshot(tmp_path, runner)
        assert step.ok is False and "private-name list" in step.detail
    assert calls == []


def test_the_export_step_passes_the_list_and_its_sha_to_the_exporter(tmp_path) -> None:
    from pravrudhi.application import publish

    seen: list[list[str]] = []

    def runner(cmd, cwd):  # noqa: ANN001
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "stop here")

    publish.export_snapshot(tmp_path, runner)
    assert seen and "--private-names" in seen[0] and "--private-names-sha256" in seen[0]
    assert seen[0][seen[0].index("--private-names") + 1] == "/nonexistent/names.txt"


class TestPrMode:
    """The publisher cannot push to the protected `main` (GH006): in pr mode it builds from a fresh origin/main tree on a NEW
    branch, scans the committed snapshot, pushes the branch and opens a PR, which it never merges."""

    def _run(self, tmp_path: Path, *, prs: str = "[]", nothing: bool = False, guard_fails: bool = False, push_ok: bool = True):
        write_root = _workspace(tmp_path / "w")
        read_root = tmp_path / "read"
        (read_root / "research").mkdir(parents=True)
        (read_root / "research" / "ledger.jsonl").write_text("")
        (write_root / "scripts").mkdir()
        (write_root / "scripts" / "check_no_personal_data.py").write_text("")
        calls: list[list[str]] = []

        def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if cmd[:3] == ["git", "remote", "get-url"]:
                return _ok(cmd, "git@github-x:AxisMeru/pravrudhi.git\n")
            if cmd[:3] == ["gh", "pr", "list"]:
                return _ok(cmd, prs)
            if cmd[:3] == ["gh", "pr", "create"]:
                return _ok(cmd, "https://github.com/AxisMeru/pravrudhi/pull/999\n")
            if _is(cmd, "diff") and "--cached" in cmd:
                return _ok(cmd, "" if nothing else "app/frontend/public/demo.json\n")
            if cmd[:2] == ["git", "push"] and not push_ok:
                return _fail(cmd, "remote: error: GH006: Protected branch update failed")
            if cmd[0] == "python3":
                return _fail(cmd, "app/frontend/public/demo.json:1: gmail address") if guard_fails else _ok(cmd)
            return _ok(cmd, "abc1234\n" if _is(cmd, "rev-parse") else "")

        res = publish(read_root, write_root=write_root, runner=runner, mode="pr", now=lambda: 1_700_000_000.0)
        return res, calls

    def test_branch_pushed_pr_opened_and_never_merged_or_pushed_to_main(self, tmp_path: Path) -> None:
        res, calls = self._run(tmp_path)
        assert res.published and "not merged" in res.reason and res.commit == "abc1234"
        names = [s.name for s in res.steps]
        assert names.index("sync") < names.index("snapshot-pr-check") < names.index("branch") < names.index("export")
        assert names.index("commit") < names.index("personal-data") < names.index("push") < names.index("pr")
        pushes = [c for c in calls if c[:2] == ["git", "push"]]
        assert len(pushes) == 1 and pushes[0][3].startswith("HEAD:refs/heads/publish/snapshot-")
        assert not any(c[-1] in ("HEAD:main", "main") for c in pushes) and "--force" not in " ".join(pushes[0])
        create = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
        assert "--repo" in create and create[create.index("--repo") + 1] == "AxisMeru/pravrudhi"
        assert create[create.index("--base") + 1] == "main"
        assert not any("merge" in c and c[:2] == ["gh", "pr"] for c in calls) and not any("--auto" in c for c in calls)
        assert any(c[:3] == ["git", "switch", "-C"] and c[-1] == "origin/main" for c in calls)  # fresh origin/main tree

    def test_an_open_snapshot_pr_blocks_a_second_one_before_anything_is_built(self, tmp_path: Path) -> None:
        res, calls = self._run(tmp_path, prs='[{"number": 7, "headRefName": "publish/snapshot-20261005-010101"}]')
        assert not res.published and "snapshot PR #7 is still open" in res.reason
        assert not any(c[:2] == ["git", "push"] for c in calls) and not any(c[:3] == ["gh", "pr", "create"] for c in calls)
        assert "export" not in [s.name for s in res.steps]
        other, _ = self._run(tmp_path / "o", prs='[{"number": 8, "headRefName": "trackc/something"}]')
        assert other.published  # an unrelated open PR does not block

    def test_the_personal_data_guard_failing_stops_before_the_push(self, tmp_path: Path) -> None:
        res, calls = self._run(tmp_path, guard_fails=True)
        assert not res.published and "personal-data guard failed" in res.reason and "gmail" in res.reason
        assert not any(c[:2] == ["git", "push"] for c in calls)

    def test_a_missing_guard_script_refuses_to_publish_unscanned(self, tmp_path: Path) -> None:
        write_root = _workspace(tmp_path)
        step = personal_data_guard(write_root, lambda cmd, cwd: _ok(cmd))
        assert not step.ok and "refusing to publish unscanned" in step.detail

    def test_nothing_new_pushes_no_branch_and_opens_no_pr(self, tmp_path: Path) -> None:
        res, calls = self._run(tmp_path, nothing=True)
        assert res.published and "nothing new" in res.reason
        assert not any(c[:2] == ["git", "push"] for c in calls) and not any(c[:3] == ["gh", "pr", "create"] for c in calls)

    def test_a_failed_branch_push_is_reported_and_no_pr_is_opened(self, tmp_path: Path) -> None:
        res, calls = self._run(tmp_path, push_ok=False)
        assert not res.published and "push failed" in res.reason and "GH006" in res.reason
        assert not any(c[:3] == ["gh", "pr", "create"] for c in calls)

    def test_protected_and_unprefixed_branch_names_are_refused(self, tmp_path: Path) -> None:
        for name in ("main", "master", "publish", "feature/x"):
            assert not start_branch(tmp_path, lambda cmd, cwd: _ok(cmd), name).ok
            assert not push_branch(tmp_path, lambda cmd, cwd: _ok(cmd), name).ok

    def test_pr_mode_needs_a_write_root_and_a_known_mode(self, tmp_path: Path) -> None:
        assert "needs a write root" in publish(tmp_path, runner=lambda cmd, cwd: _ok(cmd), mode="pr").reason
        assert "unknown mode" in publish(tmp_path, runner=lambda cmd, cwd: _ok(cmd), mode="yolo").reason

    def test_repo_slug_is_read_from_the_remote_url(self, tmp_path: Path) -> None:
        for url in ("git@github-axismeru:AxisMeru/pravrudhi.git", "https://github.com/AxisMeru/pravrudhi", "https://github.com/AxisMeru/pravrudhi.git"):
            assert repo_slug(tmp_path, lambda cmd, cwd, u=url: _ok(cmd, u + "\n")) == "AxisMeru/pravrudhi"
        assert repo_slug(tmp_path, lambda cmd, cwd: _fail(cmd, "no remote")) is None
