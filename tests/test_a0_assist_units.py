"""The backstop's systemd units: sweeper role only, no secrets in the files, the module path exists, flags are real."""

from __future__ import annotations

import importlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UNITS = REPO / "deploy" / "systemd"


def test_the_unit_files_exist_and_carry_no_secret_value():
    for name in (
        "pravrudhi-assist-backstop.service",
        "pravrudhi-assist-backstop.timer",
        "pravrudhi-assist-canary.service",
        "pravrudhi-assist-canary.timer",
    ):
        text = (UNITS / name).read_text()
        assert "postgresql://" not in text and "sk-ant" not in text and "password" not in text.lower().replace("passwords", "")


def test_the_services_read_secrets_only_from_an_environment_file_outside_the_repo():
    for name in ("pravrudhi-assist-backstop.service", "pravrudhi-assist-canary.service"):
        text = (UNITS / name).read_text()
        assert "EnvironmentFile=%h/.config/pravrudhi/assist-sweeper.env" in text and "Type=oneshot" in text


def test_the_exec_lines_run_the_real_module_with_real_flags():
    mod = importlib.import_module("pravrudhi.application.a0_assist.backstop")
    for name, flags in (("pravrudhi-assist-backstop.service", ()), ("pravrudhi-assist-canary.service", ("--canary", "--prune"))):
        line = next(ln for ln in (UNITS / name).read_text().splitlines() if ln.startswith("ExecStart="))
        assert "pravrudhi.application.a0_assist.backstop" in line and all(f in line for f in flags)
    assert callable(mod.main)


def test_the_backstop_units_are_not_in_the_developer_channel_installer():
    assert "assist" not in (UNITS / "install.sh").read_text()


def test_the_units_name_the_workspace_variable_beside_the_other_two():
    text = (UNITS / "pravrudhi-assist-backstop.service").read_text()
    assert all(n in text for n in ("SUPABASE_ASSIST_DB_URL_SWEEPER", "ANTHROPIC_SWEEPER_KEY", "ANTHROPIC_WORKSPACE_ID"))
