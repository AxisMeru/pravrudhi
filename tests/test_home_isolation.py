"""The real gap PR #15 (2026-09-25) exposed: a test passed locally and failed in CI because a credential-file
fallback (`panel.Vendor.key()`) silently read a REAL file under this operator's own `~/.config/llm/` -- a
provider-id mismatch the test itself should have caught was masked by whatever happened to exist on the
machine running it. `conftest._isolated_home` redirects `~` for the whole session; this file proves that
redirection actually holds, and that the exact incident it was built to prevent can no longer happen.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

#: The real operator's home, read a way `HOME`/`XDG_CONFIG_HOME` patching cannot affect -- `pwd` asks the
#: OS's own user database directly, never an environment variable -- so this is real ground truth to compare
#: the (patched) `Path.home()` against, not something the isolation fixture could accidentally launder.
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_home_no_longer_resolves_to_the_real_operators_directory() -> None:
    assert Path.home() != _REAL_HOME
    assert not str(Path.home()).startswith(str(_REAL_HOME) + "/")


def test_the_isolated_config_directory_has_neither_pravrudhi_nor_llm_subdirectories() -> None:
    # Structural, not behavioral: the isolated `~/.config` is a directory this session created empty and
    # nothing under test has any way to have populated from outside it. If `~/.config/pravrudhi` or
    # `~/.config/llm` genuinely don't exist here, no code resolving through `~` can possibly read a real
    # file from either -- not "we checked one code path", every path that goes through `Path.home()` at all.
    config = Path.home() / ".config"
    assert not (config / "pravrudhi").exists()
    assert not (config / "llm").exists()


def test_the_dashscope_plan_credential_file_fallback_is_unreachable_even_if_the_real_one_exists() -> None:
    """The exact shape of PR #15's bug, reproduced directly: `Vendor.key()`'s credential_file fallback reads
    `~/.config/llm/dashscope-plan.env` by name. Whether or not that file exists on the real host running
    this suite, `~` here resolves to the isolated, empty directory, so the fallback must find nothing."""
    from pravrudhi.application.panel import VENDORS

    vendor = VENDORS["qwen-dashscope"]
    assert vendor.credential_file == "~/.config/llm/dashscope-plan.env"
    resolved = Path(vendor.credential_file).expanduser()
    assert not resolved.exists(), (
        f"{resolved} exists under the isolated HOME -- the isolation fixture is not actually active, or "
        "something under test created it"
    )
