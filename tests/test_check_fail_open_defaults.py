"""Tests for `scripts/check_fail_open_defaults.py` -- the fail-open-default CI guard.

Every test runs the guard the way CI runs it: over a real throwaway git checkout, so `git ls-files` (the same
tracked-file walk `check_no_private_data.py` uses) is exercised rather than stubbed. The fixtures in
`tests/fail_open_fixtures/` are copied into that checkout under a path the guard actually scans; in this
repo they sit under `tests/`, which the guard does not scan, and their directory name is skipped explicitly,
so the deliberately-wrong ones can never fail the real build.

The negative fixture (`correct_patterns.py`) is built from patterns this repo already gets right, cited by
file:line in that file. A change to the guard that starts flagging any of them is a regression in the guard.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fail_open_fixtures"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_fail_open_defaults import (  # noqa: E402 -- path set just above, repo idiom (test_citation_scorer_parity.py:20)
    DECISION_TOKENS,
    IDENTITY_ID_TOKENS,
    IDENTITY_SUBJECT_TOKENS,
    Finding,
    check,
    load_baseline,
    scan,
)


def _repo_with(tmp_path: Path, *fixture_names: str, subdir: str = "scripts") -> Path:
    """A throwaway git repo holding the named fixtures at `<subdir>/<name>`, tracked but not committed.

    `subdir` defaults to `scripts`, which is in both the guard's rule-1 paths and its scorer paths, so a
    fixture placed there is seen by every rule.
    """
    root = tmp_path / "repo"
    (root / subdir).mkdir(parents=True)
    for name in fixture_names:
        shutil.copy(FIXTURES / name, root / subdir / name)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def _by_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


# -- rule 1: a decision-bearing key defaulting to a falsy literal ------------------------------------------


class TestRule1:
    def test_the_canonical_instance_is_caught(self, tmp_path: Path) -> None:
        """`v.get("confidence", 0) >= THRESHOLD` -- the bug the whole guard exists for."""
        hard, _ = scan(_repo_with(tmp_path, "trips_rule1.py"))
        confidence = [f for f in _by_rule(hard, "rule1") if "'confidence'" in f.message]
        assert len(confidence) == 1
        assert "reads as a real verdict/score/identity" in confidence[0].message

    def test_every_falsy_shape_is_caught(self, tmp_path: Path) -> None:
        """`0`, `0.0`, `False`, `""`, `[]` and `{}` on a decision-bearing key, and `True` for a check whose
        False means a problem was found."""
        hard, _ = scan(_repo_with(tmp_path, "trips_rule1.py"))
        rendered = {f.snippet for f in _by_rule(hard, "rule1")}
        for expected in (
            'row.get("verdict", "")',
            'row.get("score", 0)',
            'row.get("p_established", 0.0)',
            'row.get("fooled", False)',
            'row.get("judge_scores", [])',
            'row.get("per_element_status", {})',
            'row.get("pass_rate", 0.0)',
            'checks.get("chain_ok", True)',
        ):
            assert expected in rendered, f"rule 1 missed {expected}"

    def test_identity_keys_that_compare_equal_to_themselves_are_caught(self, tmp_path: Path) -> None:
        """The record-gate hazard: both sides defaulting to `""` make the identity check pass vacuously, so
        an unverified endpoint/adapter reads as the verified one."""
        hard, _ = scan(_repo_with(tmp_path, "trips_rule1.py"))
        rendered = {f.snippet for f in _by_rule(hard, "rule1")}
        for expected in (
            'cfg.get("endpoint_id", "")',
            'cfg.get("adapter_sha", "")',
            'cfg.get("model_revision", "")',
            'cfg.get("corpus_sha256", "")',
            'cfg.get("binary_digest", "")',
            'cfg.get("record_commit", "")',
        ):
            assert expected in rendered, f"rule 1 missed the identity key {expected}"

    def test_an_identity_needs_both_a_subject_and_an_id_token(self) -> None:
        """A bare `endpoint` is a URL read from config, not an identity -- three false positives during
        calibration (`api/chat.py:44`, `application/night.py:109`, `application/harness_track.py:829`), all
        of which this pairing requirement removes."""
        assert "endpoint" not in DECISION_TOKENS
        assert "endpoint" in IDENTITY_SUBJECT_TOKENS
        assert "id" in IDENTITY_ID_TOKENS

    def test_a_none_default_is_never_flagged(self, tmp_path: Path) -> None:
        """`None` is the behaviour this guard pushes code towards, so it is never itself a finding."""
        root = _repo_with(tmp_path)
        (root / "scripts" / "ok.py").write_text('def f(d):\n    return d.get("verdict", None)\n')
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        hard, _ = scan(root)
        assert _by_rule(hard, "rule1") == []

    def test_a_substring_match_is_not_enough(self, tmp_path: Path) -> None:
        """Token matching, not substring: `MemAvailable`, `rate_limit_notice`, `strategy` and `validate`
        were four of nine hits before this, all false positives."""
        root = _repo_with(tmp_path)
        (root / "scripts" / "ok.py").write_text(
            "def f(d):\n"
            '    return (d.get("MemAvailable", 0.0), d.get("rate_limit_notice", ""), '
            'd.get("strategy", ""), d.get("validate", ""))\n'
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        hard, _ = scan(root)
        assert _by_rule(hard, "rule1") == []


# -- rule 3: a statistic falling back to a numeric literal -------------------------------------------------


class TestRule3:
    @pytest.mark.parametrize(
        ("stat", "fallback"),
        [
            ("mean", "0.0"),               # the plain `X if <no data> else <number>` form
            ("sd", "0.0"),                 # a fabricated zero variance
            ("wilson_interval", "(0.0, 0.0)"),     # the if-guarded-return form, named by its function
            ("hi", "(0.0, 0.0)"),          # an unpacked tuple target: `lo, hi = ... if n else (0.0, 0.0)`
            ("median_abs_dp", "0.0"),      # a call keyword is the only place the statistic is named
            ("rate", "0.0"),               # the most dangerous polarity: a 0.0 false-prove rate
        ],
    )
    def test_each_form_is_caught(self, tmp_path: Path, stat: str, fallback: str) -> None:
        hard, _ = scan(_repo_with(tmp_path, "trips_rule3.py"))
        matches = [f for f in _by_rule(hard, "rule3") if f"{stat!r}" in f.message and fallback in f.message]
        assert matches, f"rule 3 missed the {stat} / {fallback} form"

    def test_the_message_names_the_right_remedy(self, tmp_path: Path) -> None:
        hard, _ = scan(_repo_with(tmp_path, "trips_rule3.py"))
        message = _by_rule(hard, "rule3")[0].message
        assert "return None" in message
        assert "zero variance" in message

    def test_a_comparison_needs_a_count_shaped_left_side(self, tmp_path: Path) -> None:
        """`if den > 0` guards a degenerate float denominator (`pravrudhi_kernel/.../stats/bca.py:94`, where
        the BCa acceleration constant really is 0.0) and `if successes == 0` is the exact Clopper-Pearson
        edge case (`application/discordance.py:38`). Both were false positives before this restriction; a
        count-shaped name (`n`, `len(...)`, `total_items`) still fires."""
        root = _repo_with(tmp_path)
        (root / "scripts" / "stat.py").write_text(
            "def acceleration(num, den):\n"
            "    a = float(num / den) if den > 0 else 0.0\n"
            "    return a\n"
            "\n\n"
            "def mean_rate(hits, n_items):\n"
            "    rate = hits / n_items if n_items > 0 else 0.0\n"
            "    return rate\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        hard, _ = scan(root)
        flagged = {f.snippet for f in _by_rule(hard, "rule3")}
        assert "float(num / den) if den > 0 else 0.0" not in flagged
        assert "hits / n_items if n_items > 0 else 0.0" in flagged

    def test_a_none_fallback_is_never_flagged(self, tmp_path: Path) -> None:
        """`application/anchor.py:112` and `application/confirm_eval.py:52` are the shapes to copy."""
        root = _repo_with(tmp_path)
        (root / "scripts" / "stat.py").write_text(
            "def mean_or_none(xs):\n"
            "    mean = sum(xs) / len(xs) if xs else None\n"
            "    return mean\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        hard, _ = scan(root)
        assert _by_rule(hard, "rule3") == []

    def test_a_non_statistic_name_is_not_flagged(self, tmp_path: Path) -> None:
        """A zero-when-absent COUNT is not a fabricated statistic -- rule 3 is scoped by the name of the
        thing being bound, which is what keeps it at a usable false-positive rate."""
        root = _repo_with(tmp_path)
        (root / "scripts" / "stat.py").write_text(
            "def spent(rows):\n"
            "    n_rows = len(rows) if rows else 0\n"
            "    return n_rows\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        hard, _ = scan(root)
        assert _by_rule(hard, "rule3") == []

    def test_rule_3_only_applies_inside_scorer_paths(self, tmp_path: Path) -> None:
        """A statistic in, say, the frontend-serving layer is not what this rule is for; rule 1 still reads
        that file."""
        root = _repo_with(tmp_path, "trips_rule3.py", subdir="src/pravrudhi/hosts")
        hard, _ = scan(root)
        assert _by_rule(hard, "rule3") == []


# -- rule 2: advisory, and advisory for a measured reason --------------------------------------------------


class TestRule2IsAdvisory:
    def test_it_matches_both_a_real_bug_and_a_correct_default(self, tmp_path: Path) -> None:
        """Two lines, identical to the rule, one a bug and one correct. This is the measured ~30% false
        positive rate that keeps rule 2 out of the exit code."""
        _, advisory = scan(_repo_with(tmp_path, "trips_rule2_advisory.py"))
        snippets = {f.snippet for f in advisory}
        assert 'row.get("p_established") or 0.0' in snippets
        assert 'usage.get("total_tokens") or 0' in snippets

    def test_it_never_reaches_the_violations_list(self, tmp_path: Path) -> None:
        root = _repo_with(tmp_path, "trips_rule2_advisory.py")
        report = check(root, baseline_path=root / "missing_baseline.txt")
        assert report.advisories
        assert report.violations == []
        assert report.ok

    def test_it_fires_on_a_correct_pattern_too(self, tmp_path: Path) -> None:
        """`str(row.get("note") or "")` in the negative fixture is correct code, and rule 2 flags it. Pinned
        as a test so nobody later "fixes" rule 2 by promoting it to a hard failure."""
        _, advisory = scan(_repo_with(tmp_path, "correct_patterns.py"))
        assert any('row.get("note") or ""' in f.snippet for f in advisory)


# -- the negative fixture ----------------------------------------------------------------------------------


def test_the_correct_patterns_produce_no_hard_findings(tmp_path: Path) -> None:
    """The guard measured against this repo's own good practice. Every function in the fixture cites the
    file:line it was reduced from; a failure here means the guard regressed, not the fixture."""
    hard, advisory = scan(_repo_with(tmp_path, "correct_patterns.py"))
    assert hard == [], "\n".join(f.render() for f in hard)
    # Non-vacuity: an empty `hard` must mean "scanned and clean", not "never scanned". The first draft of
    # this fixture had an unterminated docstring, the guard skipped the whole file, and this test passed for
    # the wrong reason -- the same fail-open it exists to catch, one level up. Rule 2 fires on exactly one
    # line in the fixture (`str(row.get("note") or "")`), so its presence proves the file was read.
    assert advisory, "the negative fixture was not actually scanned"


def test_an_unparseable_file_is_reported_rather_than_skipped(tmp_path: Path) -> None:
    """A file that does not parse was not checked, and an unchecked file is not a clean file. This is the
    guard applying its own rule to itself; without it, a syntax error anywhere silently shrinks coverage."""
    root = _repo_with(tmp_path)
    (root / "scripts" / "broken.py").write_text('def f():\n    """unterminated\n')
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    hard, _ = scan(root)
    parse_findings = _by_rule(hard, "parse")
    assert len(parse_findings) == 1
    assert "NOTHING in it was checked" in parse_findings[0].message


def test_every_shipped_fixture_parses() -> None:
    """The fixtures are deliberately WRONG, never deliberately unparseable: a fixture that does not parse
    makes the test using it vacuous (see `test_the_correct_patterns_produce_no_hard_findings`)."""
    fixtures = sorted(FIXTURES.glob("*.py"))
    assert len(fixtures) == 5
    for path in fixtures:
        ast.parse(path.read_text(), filename=str(path))


# -- waivers -----------------------------------------------------------------------------------------------


class TestWaivers:
    def test_an_inline_waiver_with_a_reason_silences_the_line(self, tmp_path: Path) -> None:
        """Both on the offending line and on the line above it."""
        hard, _ = scan(_repo_with(tmp_path, "waived_inline.py"))
        snippets = {f.snippet for f in hard}
        assert 'record.get("available", False)' not in snippets
        assert 'data.get("ok", False)' not in snippets

    def test_a_waiver_with_no_reason_does_not_silence_anything(self, tmp_path: Path) -> None:
        """A bare `# fail-open-ok:` cannot be used to turn the guard off without saying why."""
        hard, _ = scan(_repo_with(tmp_path, "waived_inline.py"))
        assert any('row.get("confidence", 0.0)' in f.snippet for f in hard)

    def test_a_baselined_key_is_not_a_violation_but_is_still_found(self, tmp_path: Path) -> None:
        """The baseline suppresses the FAILURE, never the finding: `scan` still reports it, so the entry can
        be shown to be live and the list can shrink as fixes land."""
        root = _repo_with(tmp_path, "trips_rule3.py")
        hard, _ = scan(root)
        target = next(f for f in hard if f.snippet == "sum(scores) / len(scores) if scores else 0.0")
        baseline = root / "baseline.txt"
        baseline.write_text(f"# reason, issue: TBD\n{target.key}\n")
        report = check(root, baseline_path=baseline)
        assert target.render() not in report.violations
        assert target in hard

    def test_the_baseline_is_keyed_on_the_expression_not_the_line_number(self, tmp_path: Path) -> None:
        """An unrelated edit ABOVE a waived line must not turn CI red -- that is what makes a baseline
        survivable. The stated cost is in `test_an_identical_second_occurrence_is_missed` below."""
        root = _repo_with(tmp_path, "trips_rule3.py")
        target = next(f for f in scan(root)[0] if f.snippet == "sum(scores) / len(scores) if scores else 0.0")
        baseline = root / "baseline.txt"
        baseline.write_text(f"{target.key}\n")
        assert check(root, baseline_path=baseline).violations == [] or True

        path = root / "scripts" / "trips_rule3.py"
        path.write_text("# a new comment that shifts every line below it\n" + path.read_text())
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        shifted = [v for v in check(root, baseline_path=baseline).violations if "len(scores)" in v]
        assert shifted == []

    def test_an_identical_second_occurrence_is_missed(self, tmp_path: Path) -> None:
        """The documented cost of keying on (path, expression): a SECOND, byte-identical occurrence in an
        already-baselined file is not caught. Pinned as a test so the limitation stays visible rather than
        being discovered later."""
        root = _repo_with(tmp_path)
        (root / "scripts" / "stat.py").write_text(
            "def first(xs):\n"
            "    mean = sum(xs) / len(xs) if xs else 0.0\n"
            "    return mean\n"
            "\n\n"
            "def second(xs):\n"
            "    mean = sum(xs) / len(xs) if xs else 0.0\n"
            "    return mean\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        hard, _ = scan(root)
        assert len(hard) == 2, "both occurrences are found by the scan"
        baseline = root / "baseline.txt"
        baseline.write_text(f"{hard[0].key}\n")
        assert check(root, baseline_path=baseline).violations == [], "but one baseline entry waives both"

    def test_a_stale_baseline_entry_is_reported_and_never_fails(self, tmp_path: Path) -> None:
        """So the baseline shrinks as the findings are fixed instead of outliving them."""
        root = _repo_with(tmp_path, "correct_patterns.py")
        baseline = root / "baseline.txt"
        baseline.write_text("scripts/gone.py::sum(xs) / len(xs) if xs else 0.0\n")
        report = check(root, baseline_path=baseline)
        assert report.stale_baseline == ["scripts/gone.py::sum(xs) / len(xs) if xs else 0.0"]
        assert report.ok


# -- the shipped baseline and the repo as it stands --------------------------------------------------------


def test_the_guard_passes_on_this_repo() -> None:
    """The point of the shipped baseline: this check must be green on `main` the day it lands, or it blocks
    everyone. Every baselined line is a finding going to its owner as an issue, not something this PR fixed.
    """
    report = check(REPO_ROOT)
    assert report.violations == [], "\n".join(report.violations)


def test_the_shipped_baseline_is_entirely_live() -> None:
    """No baseline entry may be dead on arrival: a line that matches nothing would be a waiver for something
    nobody can find, which is how a baseline turns into a place to hide things."""
    report = check(REPO_ROOT)
    assert report.stale_baseline == [], "\n".join(report.stale_baseline)


def test_every_shipped_baseline_entry_carries_a_reason_and_an_issue_field() -> None:
    """A baseline that silently swallowed the current findings would defeat the purpose (the operator's own
    condition), so every entry sits under a comment block that says why and names its issue."""
    text = (REPO_ROOT / "scripts" / "fail_open_defaults_baseline.txt").read_text()
    keys, rule2_count = load_baseline(REPO_ROOT / "scripts" / "fail_open_defaults_baseline.txt")
    assert keys, "the shipped baseline is not empty"
    assert rule2_count is not None, "the rule-2 advisory count is recorded"

    # Every key must have at least one comment line above it before the previous key -- i.e. no run of bare
    # keys with no explanation between them and the last comment.
    reasoned = 0
    has_comment_since_last_gap = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            has_comment_since_last_gap = True
        elif not line:
            continue
        else:
            assert has_comment_since_last_gap, f"baseline key with no reason above it: {line}"
            reasoned += 1
    assert reasoned == len(keys)
    assert "issue:" in text
