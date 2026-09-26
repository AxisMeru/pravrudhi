"""Tests for `scripts/check_no_secrets_in_diff.py` -- the secret-scanning CI guard.

Every test runs the guard over a real throwaway git checkout, so `git ls-files` and `git diff` (what CI
actually exercises) are used rather than stubbed. The fixtures in `tests/secret_scan_fixtures/` are copied
into that checkout under `scripts/`, a path the guard scans; in this repo they sit in a directory the guard
skips by name, so the deliberately token-shaped values in them can never fail the real build.

FOUR OF THESE TESTS ARE THE POINT OF THE FILE, and they test the guard's safety properties rather than its
coverage:

* `TestNeverPrintsTheSecret` -- a planted fixture value must not appear anywhere in the guard's output. A
  scanner that echoes findings into a CI log publishes them to everyone who can read the run.
* `TestBaselineHoldsNoSecrets` -- the waiver file must key on a digest, so it cannot itself become the leak.
* `TestFailsClosed` -- a file that cannot be read is REPORTED, and a file that cannot be decoded is scanned
  anyway. Silently skipping either turns "clean" into "never looked".
* `TestNegativeFixtureIsNotVacuous` -- every assertion that a file produced NO findings is paired with an
  assertion that the file was actually scanned. `scripts/check_fail_open_defaults.py`'s first draft skipped
  unparseable files, and one of its own negative fixtures passed without ever being read.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "secret_scan_fixtures"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_no_secrets_in_diff import (  # noqa: E402 -- path set just above, repo idiom (test_check_fail_open_defaults.py:26)
    Finding,
    check,
    fingerprint,
    load_baseline,
    looks_like_placeholder,
    main,
    resolve_base,
    scan_line,
    shannon_entropy,
)

#: Fixture file -> the rule each of its token-shaped lines must trip.
STRUCTURAL_CASES = [
    ("PLANTED_GITHUB_PAT", "github-pat-classic"),
    ("PLANTED_GITHUB_FINE_GRAINED", "github-pat-fine-grained"),
    ("PLANTED_TELEGRAM", "telegram-bot-token"),
    ("PLANTED_AWS_KEY_ID", "aws-access-key-id"),
    ("PLANTED_PEM_HEADER", "private-key-pem"),
]


def _planted(fixture: str, name: str) -> str:
    """The literal value bound to `name` in a fixture module.

    Read out of the fixture rather than written here, so no token-shaped string lives in this test file and
    the planted values have exactly one definition.
    """
    tree = ast.parse((FIXTURES / fixture).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return str(ast.literal_eval(node.value))
    raise AssertionError(f"{fixture} has no assignment to {name}")


def _repo_with(tmp_path: Path, *fixture_names: str, commit: bool = False) -> Path:
    """A throwaway git repo holding the named fixtures at `scripts/<name>`, tracked."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in fixture_names:
        shutil.copy(FIXTURES / name, root / "scripts" / name)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    if commit:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixtures"],
            cwd=root, check=True,
        )
    return root


def _rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


def _rendered(report) -> str:  # noqa: ANN001 -- Report, imported lazily above
    return "\n".join(
        [*(f.render() for f in report.violations), *(f.render() for f in report.waived), *report.notices]
    )


# -- coverage: the shapes the operator asked for ------------------------------------------------------------


class TestStructuralRules:
    @pytest.mark.parametrize(("name", "rule"), STRUCTURAL_CASES)
    def test_each_shape_is_caught(self, tmp_path: Path, name: str, rule: str) -> None:
        report = check(_repo_with(tmp_path, "trips_structural.py"), all_tracked=True)
        assert rule in _rules(report.violations), f"{name} was not caught by {rule}"

    def test_bearer_value_is_caught_including_a_jwt(self, tmp_path: Path) -> None:
        """A JWT is `<base64>.<base64>.<base64>`, which reads exactly like a dotted module path. An earlier
        draft suppressed dotted values as "looks like an import path" and so could not see the commonest
        bearer token there is."""
        report = check(_repo_with(tmp_path, "trips_structural.py"), all_tracked=True)
        assert "bearer-token" in _rules(report.violations)

    def test_high_entropy_assignment_to_a_credential_named_binding_is_caught(self, tmp_path: Path) -> None:
        report = check(_repo_with(tmp_path, "trips_entropy.py"), all_tracked=True)
        assert "secret-shaped-assignment" in _rules(report.violations)
        assert len(report.violations) == 4

    def test_a_token_embedded_in_an_escaped_string_is_caught(self, tmp_path: Path) -> None:
        """REGRESSION, and the reason none of the patterns carries a leading `\\b`.

        pravrudhi's real 2026-09-11 Telegram token sits in `app/frontend/public/demo.json` at `85ad860`
        inside an escaped JSON string, so the character before it is the `n` of a `\\n` escape -- a word
        character, so there is no word boundary and a `\\b`-anchored pattern does not match it. Measured:
        the `\\b` version of this guard reported 0 findings over that whole commit's tree.
        """
        root = _repo_with(tmp_path)
        shutil.copy(FIXTURES / "embedded_in_escaped_json.json", root / "scripts" / "embedded.json")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        report = check(root, all_tracked=True)
        assert "telegram-bot-token" in _rules(report.violations)


# -- safety property 1: the output never contains a secret --------------------------------------------------


class TestNeverPrintsTheSecret:
    def test_no_planted_value_appears_in_any_rendered_finding(self, tmp_path: Path) -> None:
        report = check(_repo_with(tmp_path, "trips_structural.py", "trips_entropy.py"), all_tracked=True)
        assert report.violations, "nothing was found, so this assertion would pass vacuously"
        text = _rendered(report)
        for name, _rule in STRUCTURAL_CASES:
            value = _planted("trips_structural.py", name)
            assert value not in text, f"{name} was printed in the guard's output"
        assert _planted("trips_entropy.py", "PLANTED_API_KEY_VALUE") not in text

    def test_no_planted_value_reaches_stdout_when_run_as_ci_runs_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The end-to-end version: `main()`, the whole printed log, exactly what lands in the CI run."""
        root = _repo_with(tmp_path, "trips_structural.py", "trips_entropy.py")
        monkeypatch.setattr(sys, "argv", ["check_no_secrets_in_diff.py", "--root", str(root), "--all-tracked"])
        assert main() == 1
        out = capsys.readouterr().out
        assert "FAIL" in out, "the run passed, so this assertion would pass vacuously"
        for name, _rule in STRUCTURAL_CASES:
            assert _planted("trips_structural.py", name) not in out

    def test_the_fingerprint_is_short_and_bounded(self) -> None:
        value = _planted("trips_structural.py", "PLANTED_GITHUB_PAT")
        prefix, length, key = fingerprint("scripts/x.py", value)
        assert len(prefix) == 4
        assert length == len(value)
        assert len(key) == 16
        assert key not in value and value[4:] not in key


# -- safety property 2: the baseline holds digests, never values --------------------------------------------


class TestBaselineHoldsNoSecrets:
    def test_a_baseline_entry_does_not_contain_the_value_and_still_waives_it(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path, "trips_structural.py")
        before = check(root, all_tracked=True)
        assert before.violations
        baseline = tmp_path / "baseline.txt"
        lines = [f"{f.key}  fixture value, constructed, never a real credential" for f in before.violations]
        baseline.write_text("# waivers\n" + "\n".join(lines) + "\n")

        text = baseline.read_text()
        for name, _rule in STRUCTURAL_CASES:
            assert _planted("trips_structural.py", name) not in text, "the waiver file became the leak"

        after = check(root, all_tracked=True, baseline_path=baseline)
        assert after.violations == []
        assert len(after.waived) == len(before.violations)
        assert all("constructed" in w.waived_reason for w in after.waived)

    def test_a_baseline_key_with_no_reason_waives_nothing(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path, "trips_structural.py")
        before = check(root, all_tracked=True)
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("\n".join(f.key for f in before.violations) + "\n")
        after = check(root, all_tracked=True, baseline_path=baseline)
        assert len(after.violations) == len(before.violations)
        assert any("has no reason" in n for n in after.notices)

    def test_the_repo_baseline_contains_no_token_shaped_string(self) -> None:
        """The shipped baseline, not a synthetic one: it must be keys and prose, nothing else."""
        path = REPO_ROOT / "scripts" / "secret_scan_baseline.txt"
        assert path.exists()
        keys, problems = load_baseline(path)
        assert problems == [], problems
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            assert scan_line(str(path), lineno, line) == [], f"baseline line {lineno} is token-shaped"

    def test_a_stale_baseline_key_is_reported_so_the_file_shrinks(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path, "correct_patterns.py")
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("0123456789abcdef  waived for a value that is no longer here\n")
        report = check(root, all_tracked=True, baseline_path=baseline)
        assert report.stale_baseline == ["0123456789abcdef"]


# -- safety property 3: fail closed -------------------------------------------------------------------------


class TestFailsClosed:
    def test_a_file_that_cannot_be_read_is_a_violation_not_a_skip(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path, "correct_patterns.py")
        (root / "scripts" / "gone.py").symlink_to(root / "scripts" / "does-not-exist.py")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        report = check(root, all_tracked=True)
        unreadable = [v for v in report.violations if v.rule == "unreadable"]
        assert len(unreadable) == 1
        assert "NOTHING in it was scanned" in unreadable[0].message
        assert not report.ok

    def test_a_file_that_cannot_be_decoded_is_scanned_anyway(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path)
        shutil.copy(FIXTURES / "undecodable.bin", root / "scripts" / "undecodable.bin")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        report = check(root, all_tracked=True)
        assert "scripts/undecodable.bin" in report.scanned, "the file was skipped, not scanned"
        assert any("decoded latin-1" in n for n in report.notices)
        assert "github-pat-classic" in _rules(report.violations), (
            "the token inside a non-UTF-8 file was not found, which is the exact vacuous pass this "
            "property exists to prevent"
        )

    def test_no_resolvable_base_audits_the_whole_tree_rather_than_passing_vacuously(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A single-commit checkout with no `origin/main` and no `HEAD~1` has no diff to scan. The answer is
        a whole-tree audit, never "0 files changed, OK"."""
        root = _repo_with(tmp_path, "trips_structural.py", commit=True)
        monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
        monkeypatch.delenv("SECRET_SCAN_BEFORE", raising=False)
        assert resolve_base(root, None) is None
        monkeypatch.setattr(sys, "argv", ["check_no_secrets_in_diff.py", "--root", str(root)])
        assert main() == 1
        out = capsys.readouterr().out
        assert "auditing the whole tracked tree" in out


# -- safety property 4: a negative fixture that was never read proves nothing --------------------------------


class TestNegativeFixtureIsNotVacuous:
    def test_the_correct_patterns_produce_no_findings_and_the_file_was_really_scanned(
        self, tmp_path: Path
    ) -> None:
        root = _repo_with(tmp_path, "correct_patterns.py", "trips_entropy.py")
        report = check(root, all_tracked=True)
        # Non-vacuity, three ways: the file is in the scanned list, lines were read from it, and the
        # positive control in the same run did fire -- so the scan was live, not a no-op.
        assert "scripts/correct_patterns.py" in report.scanned
        assert report.lines_scanned > 40
        assert "secret-shaped-assignment" in _rules(report.violations), "positive control did not fire"
        assert [v for v in report.violations if v.path == "scripts/correct_patterns.py"] == []

    @pytest.mark.parametrize("fixture", ["correct_patterns.py"])
    def test_every_negative_fixture_is_scanned(self, tmp_path: Path, fixture: str) -> None:
        root = _repo_with(tmp_path, fixture)
        report = check(root, all_tracked=True)
        assert f"scripts/{fixture}" in report.scanned
        assert report.lines_scanned > 0

    def test_the_guards_own_source_and_fixtures_are_the_only_exempt_paths(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path)
        shutil.copy(REPO_ROOT / "scripts" / "check_no_secrets_in_diff.py", root / "scripts")
        shutil.copy(FIXTURES / "trips_structural.py", root / "scripts")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        report = check(root, all_tracked=True)
        assert "scripts/check_no_secrets_in_diff.py" not in report.scanned
        assert "scripts/trips_structural.py" in report.scanned


# -- waivers carry a reason ---------------------------------------------------------------------------------


class TestWaiversNeedAReason:
    def test_a_waiver_with_a_reason_waives_and_stays_visible_in_the_output(self, tmp_path: Path) -> None:
        report = check(_repo_with(tmp_path, "waived_inline.py"), all_tracked=True)
        waived = [w for w in report.waived if w.lineno == 5]
        assert len(waived) == 1
        assert "constructed fixture value" in waived[0].waived_reason
        assert "WAIVED" in waived[0].render()
        assert "constructed fixture value" in _rendered(report)

    def test_a_bare_marker_with_no_reason_waives_nothing(self, tmp_path: Path) -> None:
        report = check(_repo_with(tmp_path, "waived_inline.py"), all_tracked=True)
        bare = [v for v in report.violations if v.lineno == 8]
        assert len(bare) == 1
        assert "NO REASON" in bare[0].message
        assert not report.ok


# -- diff mode ----------------------------------------------------------------------------------------------


class TestDiffMode:
    def test_only_added_lines_are_scanned(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path, "correct_patterns.py", commit=True)
        shutil.copy(FIXTURES / "trips_entropy.py", root / "scripts")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add secret"],
            cwd=root, check=True,
        )
        report = check(root, base="HEAD~1", head="HEAD")
        assert "secret-shaped-assignment" in _rules(report.violations)
        assert {v.path for v in report.violations} == {"scripts/trips_entropy.py"}

    def test_removing_a_secret_line_is_not_a_violation(self, tmp_path: Path) -> None:
        """A PR that DELETES an old hit must go green. Flagging removed lines would make cleaning up the
        one thing the author is being asked to do impossible."""
        root = _repo_with(tmp_path, "trips_entropy.py", commit=True)
        (root / "scripts" / "trips_entropy.py").write_text('"""cleaned"""\n')
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "remove secret"],
            cwd=root, check=True,
        )
        report = check(root, base="HEAD~1", head="HEAD")
        assert report.violations == []
        assert report.ok

    def test_a_binary_file_in_the_diff_is_scanned_whole(self, tmp_path: Path) -> None:
        """git gives no `+` lines for a binary file, so the diff alone would say nothing about it. The blob
        is read at the head commit and scanned as bytes instead of being passed over."""
        root = _repo_with(tmp_path, "correct_patterns.py", commit=True)
        shutil.copy(FIXTURES / "undecodable.bin", root / "scripts" / "blob.bin")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add blob"],
            cwd=root, check=True,
        )
        report = check(root, base="HEAD~1", head="HEAD")
        assert "scripts/blob.bin" in report.scanned
        assert "github-pat-classic" in _rules(report.violations)


# -- helpers ------------------------------------------------------------------------------------------------


class TestHelpers:
    def test_entropy_orders_prose_below_credentials(self) -> None:
        assert shannon_entropy("aaaaaaaaaaaaaaaaaaaa") < 1.0
        assert shannon_entropy(_planted("trips_entropy.py", "PLANTED_API_KEY_VALUE")) > 3.5

    @pytest.mark.parametrize("value", [
        "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "your-api-key-goes-here-replace-me",
        "abcdefghijklmnopqrstuvwxyz",
        "REPLACE_WITH_YOUR_TOKEN_VALUE",
    ])
    def test_illustrative_values_are_recognised_as_placeholders(self, value: str) -> None:
        assert looks_like_placeholder(value)

    def test_a_constructed_credential_is_not_recognised_as_a_placeholder(self) -> None:
        for name, _rule in STRUCTURAL_CASES:
            if name == "PLANTED_PEM_HEADER":
                continue
            assert not looks_like_placeholder(_planted("trips_structural.py", name))


class TestRealTreeStaysGreen:
    def test_this_repos_own_current_tree_has_no_unwaived_hit(self) -> None:
        """The gate must not land red for the owners. Runs the whole-tree audit -- strictly more than the
        diff the CI job scans -- against this checkout, with the shipped baseline."""
        report = check(REPO_ROOT, all_tracked=True)
        assert report.violations == [], [v.render() for v in report.violations]
