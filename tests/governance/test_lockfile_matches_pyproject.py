import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_uv_lock_agrees_with_pyproject_toml() -> None:
    """2026-09-14: v0.5.20 shipped with `pyproject.toml` bumped but `uv.lock` never regenerated in the same
    commit. CI's own `uv sync --all-groups --frozen` never caught it - `--frozen` trusts the lockfile as-is
    and never checks it against `pyproject.toml` - so the drift reached every clone: it dirtied the studio
    loop's rebase-before-beat (which parked on a wall no rebase could ever clear) and the primary checkout
    alike. `uv lock --check` fails without writing anything when the two disagree; pinned here so the CI
    step doing the same thing (`.github/workflows/ci.yml`, governance job) is never the only thing checking
    this, and a contributor without network/CI access still sees the failure locally."""
    uv = shutil.which("uv")
    assert uv is not None, "uv must be on PATH to run this check"
    result = subprocess.run(
        [uv, "lock", "--check"], cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        "uv.lock is out of date with pyproject.toml - run `uv lock` and commit the result.\n"
        f"{result.stdout}\n{result.stderr}"
    )
