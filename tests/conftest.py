"""Shared fixtures for the engine's tests.

The tests must say the same thing in a shell and under the heartbeat unit, because a build dispatch validates its
worktree with this suite under the unit's environment. That environment carries the operator's live
configuration: `~/.config/pravrudhi/telegram.env` (a real bot token and chat id) and the pinned Claude seat. On
2026-09-11 thirteen seat-registry tests and then two telegram tests failed only there, and the loop's first
self-built change was rejected four times for it. The fixture below removes that configuration for every test;
a test that wants it sets it itself.

The `HOME`/`XDG_CONFIG_HOME` redirect right below the imports is a second, broader net for the same class of
problem, found a different way: PR #15 (2026-09-25) passed locally but failed in CI because `panel.Vendor.
key()`'s credential-file fallback silently read a REAL file, `~/.config/llm/dashscope-plan.env`, that happens
to exist on this operator's own box -- a test asserting "no stored key means unavailable" instead asserted "no
stored key means unavailable, unless the machine running it happens to have this specific file", and nothing
caught the gap until a clean CI runner exposed it.

This MUST run as plain module-level code, not inside a fixture (even a session-scoped one): several modules
compute a `Path.home()`-derived constant once, at their own import time (`models.hosted.DEFAULT_CLIENT_PATH`,
`application.workspaces.DEFAULT_WORKSPACES`, `application.recipes.DEFAULT_SKILL_DIRS`, `application.
demo_export.DEFAULT_PRODUCT_ROOT`) -- by the time ANY fixture runs, pytest has already collected (imported)
every test module, which is what pulls those application modules in and bakes in `Path.home()`'s value for
the rest of the process. A session-scoped fixture is too late for that; the redirect has to land before
`conftest.py` finishes importing, since `conftest.py` is what pytest loads first, before collecting anything
else in this directory. (Confirmed the hard way: a first version of this used a session-scoped fixture and
two tests -- `test_hosted_models.py::test_the_client_path_is_outside_the_package`, `test_workspaces.py::
test_workspaces_root_defaults_under_home` -- failed because their module's constant had already been computed
against the real home before the fixture ever ran.)

No teardown/undo: this redirects the whole pytest process for its entire run, which is exactly the point --
nothing else runs in this process afterward that would need the real `HOME` back.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_ISOLATED_HOME = Path(tempfile.mkdtemp(prefix="pravrudhi-test-home-"))
(_ISOLATED_HOME / ".config").mkdir()
os.environ["HOME"] = str(_ISOLATED_HOME)
os.environ["XDG_CONFIG_HOME"] = str(_ISOLATED_HOME / ".config")

OPERATOR_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PRAVRUDHI_CLAUDE_CONFIG_DIR")


@pytest.fixture(autouse=True)
def _without_the_operators_live_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OPERATOR_ENV:
        monkeypatch.delenv(name, raising=False)
