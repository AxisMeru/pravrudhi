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
    env = os.environ | {
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": committer[0],
        "GIT_COMMITTER_EMAIL": committer[1],
    }
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
    rc, _ = _run(
        "x\n",
        tmp_path,
        author=("Ext Person", "ext@example.org"),
        allowed="# Ext Person <ext@example.org>\nSharathSPhD <admin@axismeru.com>\n",
    )
    assert rc == 1


def test_missing_allowlist_fails_closed(tmp_path: Path) -> None:
    d = tmp_path / "hooks"
    d.mkdir()
    (d / "commit-msg").write_text(HOOK.read_text())
    f = tmp_path / "MSG"
    f.write_text("x\n")
    env = os.environ | {"GIT_AUTHOR_NAME": TEAM[0], "GIT_AUTHOR_EMAIL": TEAM[1]}
    assert subprocess.run(["bash", str(d / "commit-msg"), str(f)], env=env, capture_output=True).returncode == 1


def _push(ref: str) -> int:
    pre = HOOK.parent / "pre-push"
    return subprocess.run(["bash", str(pre)], input=f"refs/heads/x abc {ref} 0\n", text=True, capture_output=True).returncode


def test_pre_push_refuses_main_and_allows_branches() -> None:
    assert _push("refs/heads/main") == 1
    assert _push("refs/heads/feature") == 0


def test_shipped_allowlist_accepts_the_org_handle_spelling_of_the_team_identity(tmp_path: Path) -> None:
    rc, _ = _run("fix: z\n", tmp_path, author=("AxisMeru", "admin@axismeru.com"))
    assert rc == 0


def test_shipped_allowlist_still_requires_the_pair_for_the_org_handle(tmp_path: Path) -> None:
    rc, _ = _run("fix: z\n", tmp_path, author=("AxisMeru", "someone@else.example"))
    assert rc == 1
