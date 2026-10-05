"""`unvalidated_contracts_documented:` is read by CI and by nothing else (Lead-2, 2026-09-27).

This is the regression guard on the one real hazard in adding the key at all. `validated_contracts:` is an
ALLOWLIST precisely because the `unvalidated_contracts` deny-list it replaced (issue #36) was fail-OPEN: an
id missing from a deny-list is validated by default, which is how a pin bump could ship a contract as proved
without anyone deciding it should be. The release rule now bars that form outright.

Turning the historical deny-list comment into a REAL yaml key re-creates the raw material for that mistake: a
later change that reads it in `load_agent_config` -- "skip the second judge for anything on the documented
list", "REFER only ids named here" -- would put the deny-list back into the runtime while looking like a
small refactor. Prose in the yaml saying "CI only" does not hold that shut; this test does.

What is asserted, and why each part is needed:
  * no file under `src/` mentions the key at all (not "does not read it" -- MENTIONS it, because a mention is
    the first step and is cheap to forbid);
  * the scan actually examined a non-trivial number of Python files, so a broken path or a renamed package
    cannot read as a clean pass over nothing;
  * the key really exists in the shipped config and the CI guard that reads it really names it -- otherwise
    this test would pass forever over a key nobody added and a guard that checks nothing.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
KEY = "unvalidated_contracts_documented"

#: The scan below is only evidence if it read the real tree. `src/pravrudhi` held far more than this when the
#: test was written; the floor is deliberately low so an ordinary refactor never trips it, and any value
#: above zero closes the vacuity hole (a mistyped path yields nothing and would otherwise pass).
MIN_PYTHON_FILES_SCANNED = 50


def test_no_code_under_src_mentions_the_documented_key() -> None:
    offenders = []
    scanned = 0
    for path in sorted(SRC.rglob("*.py")):
        scanned += 1
        text = path.read_text(encoding="utf-8")
        if KEY in text:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if KEY in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert scanned >= MIN_PYTHON_FILES_SCANNED, (
        f"scanned only {scanned} python files under {SRC} -- the scan found (almost) nothing, so a clean "
        "result here would be evidence of a broken path, not of a clean tree"
    )
    assert not offenders, (
        f"`{KEY}` is referenced from src/, i.e. from the engine:\n  " + "\n  ".join(offenders) + "\n\n"
        "That key is CI-only by design. Reading it at runtime rebuilds the fail-OPEN deny-list the "
        "allowlist replaced (issue #36) -- an id missing from a deny-list is validated by default, which "
        "the release rule bars. The runtime gate is `validated_contracts:`: unlisted means REFER. If a new "
        "requirement genuinely needs this data in the engine, that is a Lead-2 decision, not a refactor."
    )


def test_the_key_exists_and_the_ci_guard_is_the_thing_that_reads_it() -> None:
    """Non-vacuity in the other direction: a test that forbids mentions of a key nobody added, checked by a
    guard that does not name it, would pass forever while enforcing nothing."""
    config = (REPO_ROOT / "configs" / "nyaya_agent.yaml").read_text(encoding="utf-8")
    assert f"{KEY}:" in config, f"configs/nyaya_agent.yaml no longer declares `{KEY}:`"
    assert "read by CI only" in config.lower() or "READ BY CI ONLY" in config, (
        "the yaml comment stating the key is CI-only is gone -- it is what a reader sees before this test "
        "ever runs"
    )

    guard = REPO_ROOT / "scripts" / "check_contract_classification.py"
    assert guard.is_file(), "the CI guard that reads this key is missing"
    assert KEY in guard.read_text(encoding="utf-8"), (
        "scripts/check_contract_classification.py no longer names the key, so nothing reads it at all and "
        "the classification decision is unchecked again"
    )


def test_the_guard_runs_inside_the_required_guards_job() -> None:
    """A static script nobody invokes is decoration. Asserted against the workflow text, and specifically
    against the `guards` job's own block -- `engine` is not a required check today, so landing the step
    there would not gate a merge."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = "uv run python scripts/check_contract_classification.py"
    assert step in workflow, f"ci.yml no longer runs the classification guard ({step!r})"

    assert "\n  guards:\n" in workflow, "ci.yml no longer has a `guards` job -- update this assertion"
    after_guards = workflow.split("\n  guards:\n", 1)[1]
    guards_block = after_guards.split("\n  engine:\n", 1)[0]
    assert step in guards_block, (
        "the classification guard is in ci.yml but not in the `guards` job. `guards` is the required "
        "static-checks context; a step in `engine` does not gate a merge today (see the morning list's "
        "branch-protection item)."
    )
