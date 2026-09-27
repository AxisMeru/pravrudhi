"""Pins `tests/test_check_contract_classification.py`'s token-exhaustiveness mapping FROM OUTSIDE that file.

WHY A SEPARATE FILE. The behavioural token check in `test_check_contract_classification.py` is the last
check in its own chain: nothing else in that file notices if it is deleted, which is true of any such check
and is stated plainly in that file rather than implied away. This module is the cheap pin. It imports
`TOKEN_FIXTURES` and asserts it still covers every declared outcome token, so deleting the mapping, or
quietly narrowing it, turns `governance` red in a different file than the one being edited.

TWO THINGS ARE PINNED, because deleting either half would otherwise be invisible.

  1. THE MAPPING: `TOKEN_FIXTURES` still covers `OUTCOME_TOKENS` exactly. Asserted over the imported dict,
     never over source text -- the thing being pinned here is data, so a set comparison is the honest
     mechanism and there is nothing to AST-parse.

  2. THE DRIVER: the parametrised test that consumes the mapping is still there, still parametrised over
     the whole token set, and still COLLECTABLE. Asserted by asking pytest to collect that node id in a
     subprocess and comparing the ids it reports against `OUTCOME_TOKENS`. That is pytest's own collection,
     not a grep: a driver that was deleted, renamed, narrowed to fewer tokens, or made uncollectable
     reports the wrong ids and this goes red. It deliberately does not RUN the driver -- running it here
     would duplicate the assertions rather than pin them, and the duplicate would be the next unpinned
     link.

WHAT IS STILL NOT PINNED, said out loud so nobody reads more into a green tick. This file is itself the
last link: deleting THIS file is caught by nothing. That regress does not terminate in tests, so it
terminates in process -- `tests/test_check_*.py` is covered by the `workflow-change` guard, so the driver
cannot be edited without a label from an authorised account, and this file sits in `tests/governance`,
which `make governance` runs as its own CI step.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_check_contract_classification import OUTCOME_TOKENS, TOKEN_FIXTURES

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER_NODE_ID = (
    "tests/test_check_contract_classification.py::TestOutcomeTokens::"
    "test_each_declared_outcome_token_is_actually_raised_by_the_guard"
)


def test_every_declared_outcome_token_still_has_a_driving_fixture() -> None:
    declared, driven = set(OUTCOME_TOKENS), set(TOKEN_FIXTURES)
    assert declared == driven, (
        "tests/test_check_contract_classification.py::TOKEN_FIXTURES no longer covers the declared outcome "
        f"space. Declared with no fixture: {sorted(declared - driven)}; fixtures for undeclared tokens: "
        f"{sorted(driven - declared)}"
    )


def test_every_fixture_is_callable() -> None:
    """A mapping whose values are not builders would satisfy the set comparison above while driving nothing."""
    not_callable = sorted(token for token, builder in TOKEN_FIXTURES.items() if not callable(builder))
    assert not not_callable, f"TOKEN_FIXTURES entries that are not input builders: {not_callable}"


def test_the_behavioural_driver_is_still_collected_for_every_token() -> None:
    """The driver itself, pinned by pytest's own collection rather than by looking for text.

    `--collect-only` is used deliberately: it proves the node id resolves and reports one case per declared
    token, without executing anything here. Deleting the driver, renaming it, or narrowing its
    parametrisation to a subset of `OUTCOME_TOKENS` all change the ids pytest reports, and all turn this
    red."""
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", DRIVER_NODE_ID],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, (
        f"pytest could not collect {DRIVER_NODE_ID} -- the behavioural token driver is gone, renamed or "
        f"uncollectable.\nstdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    )
    collected = {
        line.rsplit("[", 1)[1].rstrip("]")
        for line in done.stdout.splitlines()
        if line.startswith(DRIVER_NODE_ID) and line.endswith("]")
    }
    assert collected == set(OUTCOME_TOKENS), (
        "the behavioural token driver no longer covers the declared outcome space. Tokens with no collected "
        f"case: {sorted(set(OUTCOME_TOKENS) - collected)}; collected cases for undeclared tokens: "
        f"{sorted(collected - set(OUTCOME_TOKENS))}"
    )
