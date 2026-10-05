"""scripts/check_openapi_contract.py: clean when the contract matches, loud (with the fix command) when not."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from pravrudhi.api.partner_openapi import CONTRACT_PATH, render

_spec = importlib.util.spec_from_file_location(
    "check_openapi_contract", Path(__file__).resolve().parent.parent / "scripts" / "check_openapi_contract.py"
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _root(tmp_path: Path, text: str | None) -> Path:
    if text is not None:
        (tmp_path / CONTRACT_PATH).parent.mkdir(parents=True)
        (tmp_path / CONTRACT_PATH).write_text(text)
    return tmp_path


def test_matching_contract_is_clean(tmp_path: Path) -> None:
    assert guard.check(_root(tmp_path, render()), render()) == []


def test_stale_contract_fails_with_diff_and_fix_command(tmp_path: Path) -> None:
    stale = render().replace('"v1"', '"v0"', 1)
    problems = guard.check(_root(tmp_path, stale), render())
    assert problems and guard.FIX in problems[0]
    assert any(line.startswith("-") or line.startswith("+") for line in problems[1:])


def test_missing_contract_fails(tmp_path: Path) -> None:
    problems = guard.check(_root(tmp_path, None), render())
    assert len(problems) == 1 and "missing" in problems[0]


def test_diff_output_is_bounded(tmp_path: Path) -> None:
    problems = guard.check(_root(tmp_path, "{}\n"), render())
    assert len(problems) <= guard.MAX_DIFF_LINES + 2


def test_repo_contract_is_current() -> None:
    root = Path(__file__).resolve().parent.parent
    assert guard.check(root, render()) == []
