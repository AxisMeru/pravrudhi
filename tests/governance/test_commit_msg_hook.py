import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"
TEAM = ("SharathSPhD", "admin@axismeru.com")


def _run(msg: str, tmp_path: Path, *, author=TEAM, committer=None, allowed: str | None = None) -> tuple[int, str]:
    f = tmp_path / "MSG"
    f.write_text(msg)
    hook = HOOK
    if allowed is not None:
        d = tmp_path / "hooks"
        d.mkdir(exist_ok=True)
        (d / "commit-msg").write_text(HOOK.read_text())
        (d / "allowed-identities").write_text(allowed)
        hook = d / "commit-msg"
    committer = committer or author
    env = os.environ | {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
                        "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1]}
    p = subprocess.run(["bash", str(hook), str(f)], env=env, capture_output=True, text=True)
    return p.returncode, f.read_text()


def test_strips_co_authored_by_and_claude_session(tmp_path: Path) -> None:
    rc, out = _run("feat: x\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\nClaude-Session: abc\n", tmp_path)
    assert rc == 0
    assert "Co-Authored-By" not in out and "Claude-Session" not in out
    assert out.strip() == "feat: x\n\nbody".strip()


def test_rejects_unlisted_author(tmp_path: Path) -> None:
    rc, _ = _run("feat: x\n", tmp_path, author=("Someone Else", "x@y.z"))
    assert rc == 1


def test_rejects_unlisted_committer_even_when_author_is_listed(tmp_path: Path) -> None:
    rc, _ = _run("feat: x\n", tmp_path, committer=("Someone Else", "x@y.z"))
    assert rc == 1


def test_accepts_clean_message(tmp_path: Path) -> None:
    rc, out = _run("L0: scaffold [gate:pass]\n", tmp_path)
    assert rc == 0 and out.startswith("L0: scaffold")


def test_listed_external_contributor_is_accepted_and_trailers_still_stripped(tmp_path: Path) -> None:
    ext = ("Ext Person", "ext@example.org")
    allowed = "# comment\nSharathSPhD <admin@axismeru.com>\nExt Person <ext@example.org>\n"
    rc, out = _run("fix: y\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", tmp_path, author=ext, allowed=allowed)
    assert rc == 0 and "Co-Authored-By" not in out


def test_name_and_email_must_match_as_a_pair(tmp_path: Path) -> None:
    allowed = "Ext Person <ext@example.org>\nSharathSPhD <admin@axismeru.com>\n"
    rc, _ = _run("x\n", tmp_path, author=("Ext Person", "admin@axismeru.com"), allowed=allowed)
    assert rc == 1


def test_commented_identity_is_not_allowed(tmp_path: Path) -> None:
    rc, _ = _run("x\n", tmp_path, author=("Ext Person", "ext@example.org"),
                 allowed="# Ext Person <ext@example.org>\nSharathSPhD <admin@axismeru.com>\n")
    assert rc == 1


def test_missing_allowlist_fails_closed(tmp_path: Path) -> None:
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "commit-msg").write_text(HOOK.read_text())
    f = tmp_path / "MSG"
    f.write_text("x\n")
    env = os.environ | {"GIT_AUTHOR_NAME": TEAM[0], "GIT_AUTHOR_EMAIL": TEAM[1]}
    assert subprocess.run(["bash", str(d / "commit-msg"), str(f)], env=env, capture_output=True).returncode == 1


def _push(ref: str, tmp_path: Path) -> int:
    pre = HOOK.parent / "pre-push"
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD").strip()
    return subprocess.run(["bash", str(pre)], cwd=repo, input=f"refs/heads/x {sha} {ref} 0\n", text=True, capture_output=True).returncode


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=os.environ | (env or {}), capture_output=True, text=True, check=True).stdout


def _ident(who: tuple[str, str]) -> dict:
    return {"GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1], "GIT_COMMITTER_NAME": who[0], "GIT_COMMITTER_EMAIL": who[1]}


def _repo(tmp_path: Path) -> Path:
    """origin (bare) + clone with one pushed team commit; returns the clone."""
    repo = tmp_path / "clone"
    if repo.exists():
        return repo
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    (repo / "f").write_text("0")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "base", env=_ident(TEAM))
    _git(repo, "push", "-q", "origin", "main", "--no-verify")
    _git(repo, "fetch", "-q", "origin")
    return repo


def _commit(repo: Path, who: tuple[str, str], committer: tuple[str, str] | None = None) -> str:
    (repo / "f").write_text(str(len(list(repo.glob("*"))) + hash(who) % 997))
    _git(repo, "commit", "-qam", "c", env=_ident(who) | ({"GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1]} if committer else {}))
    return _git(repo, "rev-parse", "HEAD").strip()


def _hook(repo: Path, sha: str, ref: str = "refs/heads/feature", extra_files: dict | None = None) -> subprocess.CompletedProcess:
    hooks = repo.parent / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-push", "allowed-identities"):
        (hooks / name).write_text((HOOK.parent / name).read_text())
    for name, body in (extra_files or {}).items():
        (hooks / name).write_text(body)
    return subprocess.run(["bash", str(hooks / "pre-push")], cwd=repo, input=f"refs/heads/feature {sha} {ref} {'0' * 40}\n", text=True, capture_output=True)


def test_pre_push_refuses_main_and_allows_branches(tmp_path: Path) -> None:
    assert _push("refs/heads/main", tmp_path) == 1
    assert _push("refs/heads/feature", tmp_path) == 0


def test_push_accepts_new_commits_by_listed_identity(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert _hook(repo, _commit(repo, TEAM)).returncode == 0


def test_push_refuses_unlisted_author(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    r = _hook(repo, _commit(repo, ("Someone Else", "x@y.z")))
    assert r.returncode == 1 and "Someone Else" in r.stderr


def test_push_refuses_cherry_pick_replay_with_foreign_committer(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    r = _hook(repo, _commit(repo, TEAM, committer=("Rebaser", "r@y.z")))
    assert r.returncode == 1 and "committer 'Rebaser <r@y.z>'" in r.stderr


def test_push_checks_every_new_commit_not_only_the_tip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, ("Old Personal", "p@y.z"))
    assert _hook(repo, _commit(repo, TEAM)).returncode == 1


def test_push_ignores_commits_already_on_a_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    bad = _commit(repo, ("Old Personal", "p@y.z"))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/legacy", "--no-verify")
    _git(repo, "fetch", "-q", "origin")
    assert _hook(repo, _commit(repo, TEAM)).returncode == 0 and bad


def test_push_sha_allowlist_exempts_a_listed_legacy_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    bad = _commit(repo, ("Old Personal", "p@y.z"))
    tip = _commit(repo, TEAM)
    assert _hook(repo, tip).returncode == 1
    assert _hook(repo, tip, extra_files={"identity-sha-allowlist": f"# reason\n{bad}\n"}).returncode == 0


def test_push_checks_non_main_branches_and_skips_deletes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    r = subprocess.run(["bash", str(_hook_dir(repo) / "pre-push")], cwd=repo, input=f"(delete) {'0' * 40} refs/heads/gone {'a' * 40}\n", text=True, capture_output=True)
    assert r.returncode == 0


def _hook_dir(repo: Path) -> Path:
    _hook(repo, _git(repo, "rev-parse", "HEAD").strip())
    return repo.parent / "hooks"


def test_push_fails_closed_on_unreadable_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    r = _hook(repo, "f" * 40)
    assert r.returncode == 1 and "cannot read" in r.stderr
