import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"


def _run(msg: str, tmp_path: Path, *, name: str = "SharathSPhD", email: str = "admin@axismeru.com") -> tuple[int, str]:
    f = tmp_path / "MSG"
    f.write_text(msg)
    env = os.environ | {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email}
    p = subprocess.run(["bash", str(HOOK), str(f)], env=env, capture_output=True, text=True)
    return p.returncode, f.read_text()


def test_strips_co_authored_by_and_claude_session(tmp_path: Path) -> None:
    rc, out = _run("feat: x\n\nbody\n\nCo-Authored-By: Claude <noreply@anthropic.com>\nClaude-Session: abc\n", tmp_path)
    assert rc == 0
    assert "Co-Authored-By" not in out and "Claude-Session" not in out
    assert out.strip() == "feat: x\n\nbody".strip()


def test_rejects_wrong_author(tmp_path: Path) -> None:
    rc, _ = _run("feat: x\n", tmp_path, name="Someone Else", email="x@y.z")
    assert rc == 1


def test_accepts_clean_message(tmp_path: Path) -> None:
    rc, out = _run("L0: scaffold [gate:pass]\n", tmp_path)
    assert rc == 0 and out.startswith("L0: scaffold")
