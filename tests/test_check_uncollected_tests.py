"""Tests for `scripts/check_uncollected_tests.py` -- the uncollected-test-file CI guard.

Every test builds a real throwaway git repository in `tmp_path` and runs the guard over it, so
`git ls-files` (what CI actually exercises) is used rather than stubbed. The fixtures are written at
runtime rather than committed, for a reason specific to this guard: a committed fixture named `test_*.py`
sitting outside `tests/` would be a real violation of the rule this guard enforces, and the guard would
correctly fail on its own pull request. There is nowhere in this repository to keep one.

THE TESTS THAT ARE THE POINT OF THIS FILE test the guard's failure modes, not its happy path:

* `TestFailsClosed` -- git failing, an empty file list, a vacuous scan, a missing testpath, an unreadable
  file and a `collect_ignore` in a conftest are each a VIOLATION whose message says the run proved nothing.
  Four guards on this project shipped or nearly shipped with the opposite behaviour, so there is a negative
  fixture per case rather than a single "it fails closed" assertion.
* `TestRootsComeFromConfiguration` -- the same tree passes or fails purely according to what `testpaths`
  says, including with the two testpaths this repo really has. A hardcoded `tests` would pass both ways and
  these are the tests that would catch it.
* `TestCannotEscapeByNamingOrLocation` -- `tests_extra/` must not read as inside `tests` (a string prefix
  says it does), a directory pytest prunes inside a collected root must not read as collected, a path
  differing from a root only in case must not read as collected, and a symlinked test file must not read as
  reliably collected.
* `TestNegativeFixtureIsNotVacuous` -- every assertion that a file produced NO violation is paired with an
  assertion that the guard actually examined that file. `scripts/check_fail_open_defaults.py`'s first draft
  skipped unparseable files and one of its own negative fixtures passed without ever being read.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_uncollected_tests import (  # noqa: E402 -- path set just above, repo idiom (test_check_fail_open_defaults.py:26)
    ConfigError,
    check,
    content_key,
    load_allowlist,
    main,
    read_config,
)

#: The real repository's own configuration, asserted on rather than restated, so this file cannot drift
#: from pyproject.toml and quietly stop testing the multi-root case.
REAL_TESTPATHS = ("tests", "pravrudhi_kernel/tests")

A_TEST_FILE = "def test_something():\n    assert True\n"


def _repo(
    tmp_path: Path,
    files: dict[str, str],
    *,
    config: str | None = None,
    config_name: str = "pyproject.toml",
    add: bool = True,
    init: bool = True,
    symlinks: dict[str, str] | None = None,
) -> Path:
    """A throwaway git repository. `config=None` writes this repo's real two-testpath configuration."""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    if config is None:
        config = '[tool.pytest.ini_options]\ntestpaths = ["tests", "pravrudhi_kernel/tests"]\n'
        # Both declared roots are made to exist: a testpath that is not in the tree is its own violation
        # (`testpath-missing`), and it has its own test -- it must not leak into every other case.
        for root_dir in REAL_TESTPATHS:
            (root / root_dir).mkdir(parents=True, exist_ok=True)
            (root / root_dir / ".gitkeep").write_text("")
    if config != "":
        (root / config_name).write_text(config)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for link, target in (symlinks or {}).items():
        p = root / link
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(target)
    if init:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    if init and add:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def _rules(report) -> set[str]:
    return {v.rule for v in report.violations}


def _find(report, rule: str) -> list[str]:
    return [v.rel for v in report.violations if v.rule == rule]


class TestTheHappyPath:
    def test_a_test_file_inside_a_collected_root_passes(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE}))
        assert report.ok, [v.render() for v in report.violations]
        # Non-vacuity: the file was genuinely examined and counted as inside a root.
        assert "tests/test_a.py" in report.examined
        assert "tests/test_a.py" in report.inside_roots

    def test_a_test_file_in_the_second_testpath_passes(self, tmp_path: Path) -> None:
        """The multi-root case. `pytest tests` and a bare `pytest` cover different sets in this repo, and a
        guard that knew only about `tests` would call the kernel's whole suite uncollected."""
        report = check(_repo(tmp_path, {"pravrudhi_kernel/tests/test_k.py": A_TEST_FILE}))
        assert report.ok, [v.render() for v in report.violations]
        assert "pravrudhi_kernel/tests/test_k.py" in report.inside_roots

    def test_a_non_test_file_outside_the_roots_is_not_examined(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "src/helpers.py": "x = 1\n"}))
        assert report.ok, [v.render() for v in report.violations]
        assert "src/helpers.py" not in report.examined

    def test_the_underscore_test_suffix_convention_is_matched_too(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "src/thing_test.py": A_TEST_FILE}))
        assert "src/thing_test.py" in _find(report, "uncollected-test")


class TestAnOutsiderFails:
    def test_a_test_file_outside_every_collected_root_fails(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": A_TEST_FILE}))
        assert not report.ok
        assert _find(report, "uncollected-test") == ["research/test_b.py"]
        assert "research/test_b.py" in report.examined

    def test_the_failure_message_names_the_configured_roots_and_their_source(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/gates/test_b.py": A_TEST_FILE}))
        [finding] = [v for v in report.violations if v.rule == "uncollected-test"]
        assert "pravrudhi_kernel/tests" in finding.message
        assert "pyproject.toml" in finding.message
        # The message must be actionable: it prints the exact allowlist line to paste.
        assert "research/gates/test_b.py::" in finding.message

    def test_main_returns_one_and_prints_the_path(self, tmp_path: Path, capsys, monkeypatch) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": A_TEST_FILE})
        monkeypatch.setattr(sys, "argv", ["check_uncollected_tests.py", "--root", str(root)])
        assert main() == 1
        assert "research/test_b.py" in capsys.readouterr().out

    def test_main_returns_zero_on_a_clean_tree(self, tmp_path: Path, capsys, monkeypatch) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE})
        monkeypatch.setattr(sys, "argv", ["check_uncollected_tests.py", "--root", str(root)])
        assert main() == 0
        assert "OK:" in capsys.readouterr().out


class TestTheAllowlist:
    def test_an_outsider_with_a_reasoned_waiver_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": A_TEST_FILE})
        key = content_key("research/test_b.py", A_TEST_FILE.encode())
        allow = tmp_path / "allow.txt"
        allow.write_text(f"{key}  frozen 2026 prototype, kept for provenance, never meant to run in CI\n")
        report = check(root, allow)
        assert report.ok, [v.render() for v in report.violations]
        # Non-vacuity, twice over: the file was examined, and it was reported as waived rather than ignored.
        assert "research/test_b.py" in report.examined
        assert [w.rel for w in report.waived] == ["research/test_b.py"]
        assert "frozen 2026 prototype" in report.waived[0].message

    def test_a_key_with_no_reason_waives_nothing_and_is_itself_an_error(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": A_TEST_FILE})
        key = content_key("research/test_b.py", A_TEST_FILE.encode())
        allow = tmp_path / "allow.txt"
        allow.write_text(f"{key}\n")
        report = check(root, allow)
        assert "allowlist-no-reason" in _rules(report)
        # And the file it would have waived is still reported: a bare key waives NOTHING.
        assert "research/test_b.py" in _find(report, "uncollected-test")

    def test_a_malformed_key_is_an_error(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE})
        allow = tmp_path / "allow.txt"
        allow.write_text("research/test_b.py  no digest at all\nnotapath::zzzz  bad digest\n")
        report = check(root, allow)
        assert _rules(report) == {"allowlist-malformed"}
        assert len(_find(report, "allowlist-malformed")) == 2

    def test_a_duplicate_key_is_an_error(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": A_TEST_FILE})
        key = content_key("research/test_b.py", A_TEST_FILE.encode())
        allow = tmp_path / "allow.txt"
        allow.write_text(f"{key}  reason one\n{key}  reason two\n")
        report = check(root, allow)
        assert "allowlist-duplicate" in _rules(report)

    def test_a_waiver_for_a_vanished_path_is_reported_stale_and_does_not_fail(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE})
        key = content_key("research/gone.py", A_TEST_FILE.encode())
        allow = tmp_path / "allow.txt"
        allow.write_text(f"{key}  a file that has since been deleted\n")
        report = check(root, allow)
        assert report.ok, [v.render() for v in report.violations]
        assert len(report.stale_allowlist) == 1
        assert "no such tracked file any more" in report.stale_allowlist[0]
        assert "remove this line" in report.stale_allowlist[0]

    def test_a_waiver_for_a_path_that_is_now_collected_is_reported_stale(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE})
        key = content_key("tests/test_a.py", A_TEST_FILE.encode())
        allow = tmp_path / "allow.txt"
        allow.write_text(f"{key}  waived before it was moved into tests/\n")
        report = check(root, allow)
        assert report.ok, [v.render() for v in report.violations]
        assert len(report.stale_allowlist) == 1
        assert "now collected" in report.stale_allowlist[0]

    def test_a_waiver_does_not_follow_the_file_into_becoming_something_else(self, tmp_path: Path) -> None:
        """Rule 4. The waiver is keyed on path PLUS contents, so editing a waived file breaks the waiver."""
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": "def test_new(): assert 1\n"})
        stale_key = content_key("research/test_b.py", A_TEST_FILE.encode())
        allow = tmp_path / "allow.txt"
        allow.write_text(f"{stale_key}  waived at its original contents\n")
        report = check(root, allow)
        assert "research/test_b.py" in _find(report, "waiver-digest-moved")
        assert not report.waived

    def test_this_repos_own_allowlist_parses_with_no_problems(self) -> None:
        """The shipped file must itself be well-formed -- an unparseable waiver file is a red build."""
        keys, problems = load_allowlist(REPO_ROOT / "scripts" / "uncollected_tests_allowlist.txt")
        assert problems == [], [p.render() for p in problems]
        # It ships with no entries because the real scan of main found no offenders; see the file's header.
        assert keys == {}


class TestRootsComeFromConfiguration:
    def test_this_repos_real_configuration_is_read_and_has_more_than_one_testpath(self) -> None:
        config = read_config(REPO_ROOT)
        assert config.source == "pyproject.toml"
        # A subset assertion, not equality: adding a third testpath is a legitimate change that must not
        # turn this test red, but losing one of these two would mean the guard is measuring the wrong tree.
        assert set(REAL_TESTPATHS) <= set(config.testpaths)
        assert len(config.testpaths) > 1

    def test_the_same_tree_passes_or_fails_according_to_testpaths_alone(self, tmp_path: Path) -> None:
        """A hardcoded `tests` would pass this tree both ways; only reading the config distinguishes them."""
        files = {"suite/test_a.py": A_TEST_FILE}
        wide = check(_repo(tmp_path / "wide", files, config='[tool.pytest.ini_options]\ntestpaths = ["suite"]\n'))
        assert wide.ok, [v.render() for v in wide.violations]
        assert "suite/test_a.py" in wide.inside_roots
        narrow = check(_repo(tmp_path / "narrow", files, config='[tool.pytest.ini_options]\ntestpaths = ["other", "suite"]\n'))
        assert "other" in _find(narrow, "testpath-missing")

    def test_an_ini_configuration_is_read_too(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path, {"suite/test_a.py": A_TEST_FILE, "elsewhere/test_b.py": A_TEST_FILE},
            config="[pytest]\ntestpaths = suite\naddopts = -p no:cacheprovider\n", config_name="pytest.ini",
        )
        report = check(root)
        assert _find(report, "uncollected-test") == ["elsewhere/test_b.py"]

    def test_an_ini_addopts_containing_a_percent_sign_does_not_break_parsing(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path, {"suite/test_a.py": A_TEST_FILE},
            config="[pytest]\ntestpaths = suite\naddopts = --cov-fail-under=80%\n", config_name="pytest.ini",
        )
        report = check(root)
        assert report.ok, [v.render() for v in report.violations]
        assert "suite/test_a.py" in report.inside_roots

    def test_a_configured_python_files_pattern_is_honoured(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path, {"suite/check_a.py": A_TEST_FILE, "elsewhere/check_b.py": A_TEST_FILE},
            config='[tool.pytest.ini_options]\ntestpaths = ["suite"]\npython_files = ["check_*.py"]\n',
        )
        report = check(root)
        assert _find(report, "uncollected-test") == ["elsewhere/check_b.py"]
        assert read_config(root).python_files_defaulted is False

    def test_two_config_files_both_declaring_testpaths_is_reported_not_silently_resolved(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path, {"suite/test_a.py": A_TEST_FILE},
            config='[tool.pytest.ini_options]\ntestpaths = ["suite"]\n',
        )
        (root / "pytest.ini").write_text("[pytest]\ntestpaths = somewhere-else\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        report = check(root)
        assert "config-ambiguous" in _rules(report)


class TestConfigurationFailuresExitNonZero:
    def test_a_missing_configuration_is_a_hard_failure(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE}, config=""))
        assert not report.ok
        assert "config-unreadable" in _rules(report)

    def test_an_unparseable_configuration_is_a_hard_failure(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE}, config="[tool.pytest.ini_options\nthis is not toml"))
        assert "config-unreadable" in _rules(report)

    def test_a_configuration_with_no_testpaths_is_a_hard_failure(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE},
                             config='[tool.pytest.ini_options]\naddopts = "-q"\n'))
        assert "config-unreadable" in _rules(report)
        assert "names no `testpaths`" in report.violations[0].message

    def test_an_empty_testpaths_list_is_a_hard_failure(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE},
                             config="[tool.pytest.ini_options]\ntestpaths = []\n"))
        assert "config-unreadable" in _rules(report)

    def test_a_non_list_testpaths_is_a_hard_failure(self, tmp_path: Path) -> None:
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE},
                             config="[tool.pytest.ini_options]\ntestpaths = 3\n"))
        assert "config-unreadable" in _rules(report)

    def test_read_config_raises_rather_than_returning_a_guessed_default(self, tmp_path: Path) -> None:
        root = tmp_path / "bare"
        root.mkdir()
        with pytest.raises(ConfigError):
            read_config(root)

    def test_main_exits_non_zero_on_a_configuration_failure(self, tmp_path: Path, capsys, monkeypatch) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE}, config="")
        monkeypatch.setattr(sys, "argv", ["check_uncollected_tests.py", "--root", str(root)])
        assert main() == 1
        assert "will not assume a default test root" in capsys.readouterr().out


class TestFailsClosed:
    def test_git_failing_is_a_violation_not_a_pass(self, tmp_path: Path) -> None:
        """Not a git repository at all, so `git ls-files` fails. A guard that treats that as "nothing to
        report" reports clean for a tree it never read."""
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE}, init=False)
        report = check(root)
        assert "scan-failed" in _rules(report)
        assert "examined NOTHING" in report.violations[0].message

    def test_an_empty_file_list_is_a_violation_not_a_pass(self, tmp_path: Path) -> None:
        """A git repository with nothing staged: git succeeds and returns no files."""
        root = _repo(tmp_path, {}, config="", init=True, add=False)
        (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        report = check(root)
        assert "scan-empty" in _rules(report)
        assert report.tracked_count == 0

    def test_a_scan_that_found_no_test_file_inside_any_root_is_a_violation(self, tmp_path: Path) -> None:
        """The vacuity case the other guards got wrong: files exist, none of them is a collected test, so
        the run measured nothing and must not report a pass."""
        root = _repo(tmp_path, {"src/a.py": "x = 1\n", "README.md": "hi\n"})
        report = check(root)
        assert "scan-vacuous" in _rules(report)
        assert not report.inside_roots

    def test_a_tree_whose_only_test_file_is_outside_the_roots_reports_both_problems(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"research/test_b.py": A_TEST_FILE})
        report = check(root)
        assert {"uncollected-test", "scan-vacuous"} <= _rules(report)

    def test_a_configured_testpath_missing_from_the_tree_is_a_violation(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE},
                     config='[tool.pytest.ini_options]\ntestpaths = ["tests", "pravrudhi_kernel/tests"]\n')
        report = check(root)
        assert _find(report, "testpath-missing") == ["pravrudhi_kernel/tests"]

    def test_an_unreadable_outsider_is_reported_rather_than_skipped(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "research/test_b.py": A_TEST_FILE})
        (root / "research" / "test_b.py").unlink()  # tracked in the index, gone from the working tree
        report = check(root)
        assert "research/test_b.py" in _find(report, "unreadable")
        assert "research/test_b.py" in report.examined

    def test_a_conftest_that_removes_paths_from_collection_is_refused(self, tmp_path: Path) -> None:
        """A `collect_ignore` can de-collect a file from INSIDE a collected root. The guard cannot evaluate
        it, so it says so rather than certifying roots it can no longer vouch for."""
        root = _repo(tmp_path, {
            "tests/test_a.py": A_TEST_FILE,
            "tests/conftest.py": 'collect_ignore = ["test_a.py"]\n',
        })
        report = check(root)
        assert _find(report, "collect-ignore-present") == ["tests/conftest.py"]

    def test_an_absolute_testpath_is_a_hard_failure(self, tmp_path: Path) -> None:
        """A `/home/ss`-shaped hardcode. `root / "/abs"` is `/abs` in pathlib, so the existence check alone
        would have waved this through while nothing in the checkout ever matched it."""
        report = check(_repo(tmp_path, {"tests/test_a.py": A_TEST_FILE},
                             config='[tool.pytest.ini_options]\ntestpaths = ["/home/ss/tests"]\n'))
        assert "config-unreadable" in _rules(report)
        assert "absolute" in report.violations[0].message

    def test_a_conftest_merely_mentioning_collect_ignore_in_a_comment_is_not_flagged(self, tmp_path: Path) -> None:
        """The false-red this check must not produce: a name node, not a substring."""
        root = _repo(tmp_path, {
            "tests/test_a.py": A_TEST_FILE,
            "tests/conftest.py": '"""We deliberately do not use collect_ignore here."""\n# collect_ignore = [...]\n',
        })
        report = check(root)
        assert report.ok, [v.render() for v in report.violations]
        assert "tests/test_a.py" in report.inside_roots

    def test_a_conftest_appending_to_collect_ignore_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {
            "tests/test_a.py": A_TEST_FILE,
            "tests/conftest.py": 'collect_ignore = []\nif True:\n    collect_ignore.append("test_a.py")\n',
        })
        assert "collect-ignore-present" in _rules(check(root))

    def test_a_conftest_that_does_not_parse_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "tests/conftest.py": "def broken(:\n"})
        assert "conftest-unparseable" in _rules(check(root))

    def test_collect_ignore_glob_is_refused_too(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {
            "tests/test_a.py": A_TEST_FILE,
            "tests/conftest.py": 'collect_ignore_glob = ["*_slow.py"]\n',
        })
        report = check(root)
        assert "collect-ignore-present" in _rules(report)

    def test_this_repos_own_conftests_are_clean_of_collect_ignore(self) -> None:
        """Non-vacuity for the two tests above: they would also pass if the marker string never matched."""
        report = check(REPO_ROOT)
        assert "collect-ignore-present" not in _rules(report)


class TestCannotEscapeByNamingOrLocation:
    def test_a_sibling_directory_sharing_a_prefix_is_not_inside_the_root(self, tmp_path: Path) -> None:
        """`'tests_extra/test_x.py'.startswith('tests')` is True. A string-prefix check lets this through."""
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "tests_extra/test_x.py": A_TEST_FILE})
        report = check(root)
        assert _find(report, "uncollected-test") == ["tests_extra/test_x.py"]

    def test_a_deeper_path_starting_with_a_root_component_is_not_inside_it(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"pravrudhi_kernel/tests/test_k.py": A_TEST_FILE,
                                "pravrudhi_kernel/tests_old/test_x.py": A_TEST_FILE})
        report = check(root)
        assert _find(report, "uncollected-test") == ["pravrudhi_kernel/tests_old/test_x.py"]

    def test_a_pruned_directory_inside_a_collected_root_is_not_collected(self, tmp_path: Path) -> None:
        """Escape by LOCATION: inside `tests`, so a containment check says fine, but pytest's own
        norecursedirs prunes `node_modules` and never walks into it."""
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE,
                                "tests/node_modules/test_x.py": A_TEST_FILE})
        report = check(root)
        assert _find(report, "uncollected-test") == ["tests/node_modules/test_x.py"]
        [f] = [v for v in report.violations if v.rule == "uncollected-test"]
        assert "norecursedirs" in f.message

    def test_a_dot_directory_inside_a_collected_root_is_not_collected(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "tests/.scratch/test_x.py": A_TEST_FILE})
        report = check(root)
        assert "tests/.scratch/test_x.py" in _find(report, "uncollected-test")

    def test_a_path_differing_from_a_root_only_in_case_is_not_collected(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "Tests/test_x.py": A_TEST_FILE})
        report = check(root)
        assert "Tests/test_x.py" in _find(report, "uncollected-test")
        [f] = [v for v in report.violations if v.rel == "Tests/test_x.py"]
        assert "ONLY IN CASE" in f.message

    def test_a_symlinked_test_file_inside_a_root_is_not_reliably_collected(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"tests/test_a.py": A_TEST_FILE, "elsewhere/real.py": A_TEST_FILE},
                     symlinks={"tests/test_linked.py": "../elsewhere/real.py"})
        report = check(root)
        assert "tests/test_linked.py" in _find(report, "symlinked-test")

    def test_a_testpath_pointing_at_a_single_file_still_collects_that_file(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"suite/test_a.py": A_TEST_FILE},
                     config='[tool.pytest.ini_options]\ntestpaths = ["suite/test_a.py"]\n')
        report = check(root)
        assert report.ok, [v.render() for v in report.violations]
        assert "suite/test_a.py" in report.inside_roots


class TestNegativeFixtureIsNotVacuous:
    def test_the_real_repository_passes_its_own_guard(self) -> None:
        """The integration case, and the one that would have been vacuous without the counts below: this
        must pass because 276 test files were examined and all of them were inside a root, not because the
        guard looked at nothing."""
        report = check(REPO_ROOT)
        assert report.ok, [v.render() for v in report.violations]
        assert report.tracked_count > 500
        assert len(report.examined) > 200
        assert report.examined == report.inside_roots
        assert report.stale_allowlist == []

    def test_every_negative_case_in_this_file_asserts_the_file_was_examined(self) -> None:
        """A guard test that asserts "no violations" without asserting the file was read is the exact bug
        `scripts/check_fail_open_defaults.py`'s first draft shipped. This keeps that promise mechanical:
        every `report.ok` assertion in this file is in a test that also touches `examined` or `inside_roots`
        (or is itself a configuration-failure test, where nothing is examined by design)."""
        source = Path(__file__).read_text()
        blocks = source.split("\n    def test_")
        checked = 0
        offenders = []
        for block in blocks[1:]:
            name = block.split("(")[0]
            if not re.search(r"assert \w+\.ok", block):
                continue
            checked += 1
            if any(handle in block for handle in ("examined", "inside_roots", "stale_allowlist")):
                continue
            offenders.append(name)
        assert offenders == [], offenders
        # Non-vacuity for this test itself: it must actually have found the pass-asserting tests.
        assert checked >= 7, checked
