"""Tests for `scripts/check_testpaths_run_by_ci.py` (#96): every configured testpaths root must be run by CI."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_testpaths_run_by_ci import check, invocations_in  # noqa: E402 -- path set just above (repo idiom)


def _repo(tmp_path: Path, testpaths: list[str], runs: list[str], nested: dict[str, list[str]] | None = None) -> Path:
    tp = ", ".join(f'"{t}"' for t in testpaths)
    (tmp_path / "pyproject.toml").write_text(f"[tool.pytest.ini_options]\ntestpaths = [{tp}]\n")
    steps = "\n".join(f"      - run: {r}" for r in runs)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(f"jobs:\n  j:\n    runs-on: x\n    steps:\n{steps}\n")
    for d, paths in (nested or {}).items():
        (tmp_path / d).mkdir(parents=True)
        p = ", ".join(f'"{x}"' for x in paths)
        (tmp_path / d / "pyproject.toml").write_text(f"[tool.pytest.ini_options]\ntestpaths = [{p}]\n")
    return tmp_path


def _rules(tmp_path: Path) -> list[str]:
    return [v.rule for v in check(tmp_path)]


def test_every_root_run_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path, ["tests", "kernel/tests"], ["uv run pytest tests -q", "uv run pytest kernel/tests -q"])
    assert check(root) == []


def test_a_root_no_job_runs_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, ["tests", "kernel/tests"], ["uv run pytest tests -q"])
    assert [(v.rule, "kernel/tests" in v.message) for v in check(root)] == [("root-not-run", True)]


def test_a_subtree_job_does_not_run_the_parent_root(tmp_path: Path) -> None:
    """`pytest tests/governance` contains the substring `tests` but runs only part of that root."""
    root = _repo(tmp_path, ["tests"], ["uv run pytest tests/governance -q"])
    assert _rules(root) == ["root-not-run"]


def test_a_sibling_with_a_shared_prefix_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, ["tests"], ["uv run pytest tests_extra -q"])
    assert _rules(root) == ["root-not-run"]


@pytest.mark.parametrize("spelling", ["./tests", "tests/", "tests::test_x", "."])
def test_equivalent_spellings_count(tmp_path: Path, spelling: str) -> None:
    assert check(_repo(tmp_path, ["tests"], [f"uv run pytest {spelling} -q"])) == []


def test_no_path_means_the_configured_testpaths(tmp_path: Path) -> None:
    assert check(_repo(tmp_path, ["tests", "kernel/tests"], ["uv run pytest -q"])) == []


@pytest.mark.parametrize("flag", ["-k slow", "-m 'not slow'", "--ignore=tests/a", "--ignore tests/a", "--deselect x"])
def test_a_filtered_invocation_does_not_count(tmp_path: Path, flag: str) -> None:
    assert _rules(_repo(tmp_path, ["tests"], [f"uv run pytest tests {flag}"])) == ["root-not-run"]


def test_value_flags_do_not_leak_their_value_as_a_path(tmp_path: Path) -> None:
    root = _repo(tmp_path, ["tests"], ["uv run pytest -p no:cov --cov pkg tests -q"])
    assert check(root) == []
    assert invocations_in("pytest --cov pkg tests", "w")[0].paths == ("tests",)


def test_chained_and_continued_commands_are_seen() -> None:
    inv = invocations_in("uv sync && uv run pytest a \\\n  b -q; python -m pytest c", "w")
    assert [i.paths for i in inv] == [("a", "b"), ("c",)]


def test_an_indirect_invocation_is_not_recognised(tmp_path: Path) -> None:
    assert "no-pytest" in _rules(_repo(tmp_path, ["tests"], ["make test"]))


@pytest.mark.parametrize("breakage", ["no_workflows", "bad_yaml", "no_config"])
def test_it_fails_closed(tmp_path: Path, breakage: str) -> None:
    root = _repo(tmp_path, ["tests"], ["uv run pytest tests"])
    if breakage == "no_workflows":
        (root / ".github" / "workflows" / "ci.yml").unlink()
    elif breakage == "bad_yaml":
        (root / ".github" / "workflows" / "ci.yml").write_text("jobs: [unclosed\n")
    else:
        (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert check(root), breakage


def test_a_nested_testpaths_the_root_config_does_not_name_is_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path, ["tests", "kernel/tests"], ["uv run pytest tests", "uv run pytest kernel/tests"],
                 nested={"kernel": ["tests", "extra"]})
    vs = check(root)
    assert [v.rule for v in vs] == ["testpaths-drift"] and "kernel/extra" in vs[0].message


def test_a_narrower_nested_scope_is_fine(tmp_path: Path) -> None:
    root = _repo(tmp_path, ["tests", "kernel/tests"], ["uv run pytest tests", "uv run pytest kernel/tests"],
                 nested={"kernel": ["tests"]})
    assert check(root) == []


def test_the_real_repo_is_clean() -> None:
    assert check(REPO_ROOT) == []
