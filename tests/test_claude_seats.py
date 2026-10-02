"""Which Claude seat the command line spends, and what happens when that seat stops being spendable.

Two premium seats on one team plan are not interchangeable. One is always there and one comes and goes, and
the operator's instruction of 2026-09-11 orders them: spend the one that comes and goes, hold the other in
reserve, and move between them without a human. These tests pin the order, the conditions that release it,
and the conditions that must NOT release it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from pravrudhi.agents import account
from pravrudhi.application import availability

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_pinned_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the seat REGISTRY, so the pin that bypasses it must be absent.

    The heartbeat service sets `PRAVRUDHI_CLAUDE_CONFIG_DIR` for every dispatch, and a build dispatch validates
    its worktree with the engine's own tests under that environment: on 2026-09-11 thirteen of these failed there
    and nowhere else, and the loop's first self-built change was rejected three times for it."""
    monkeypatch.delenv(account.HOME_ENV, raising=False)


def _seat_dir(path: Path, *, email: str, refresh: str = "r-default") -> Path:
    """A directory shaped like a logged-in Claude config dir."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": f"a-{refresh}", "refreshToken": refresh,
                                      "subscriptionType": "team"}})
    )
    (path / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": email}}))
    return path


def _registry(root: Path, seats: list[dict[str, str]]) -> Path:
    (root / "configs").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "seats.yaml").write_text(yaml.safe_dump({"version": 1, "seats": seats}))
    return root


def _two_seats(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _seat_dir(tmp_path / "primary", email="one@example.com", refresh="r-one")
    _seat_dir(tmp_path / "fallback", email="two@example.com", refresh="r-two")
    return _registry(root, [
        {"id": "primary", "email": "one@example.com", "config_dir": str(tmp_path / "primary")},
        {"id": "fallback", "email": "two@example.com", "config_dir": str(tmp_path / "fallback")},
    ])


def test_seats_are_read_in_the_order_the_registry_declares(tmp_path: Path) -> None:
    root = _two_seats(tmp_path)
    assert [s.id for s in account.seats(root)] == ["primary", "fallback"]


def test_the_first_provisioned_seat_is_chosen(tmp_path: Path) -> None:
    root = _two_seats(tmp_path)
    chosen = account.select_seat(root)
    assert chosen is not None and chosen.id == "primary"


def test_an_unprovisioned_seat_is_skipped_for_the_next_one(tmp_path: Path) -> None:
    root = _two_seats(tmp_path)
    (tmp_path / "primary" / ".credentials.json").unlink()
    chosen = account.select_seat(root)
    assert chosen is not None and chosen.id == "fallback"


def test_a_seat_inside_a_usage_limit_cooldown_is_skipped_for_the_next_one(tmp_path: Path) -> None:
    root = _two_seats(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    availability.mark_limited(root, "claude-code:primary", minutes=300, now=now)
    chosen = account.select_seat(root, now=now)
    assert chosen is not None and chosen.id == "fallback"


def test_the_reserve_seat_is_released_only_while_the_primary_cannot_serve(tmp_path: Path) -> None:
    """The instruction's hard half: never the reserve while the seat to spend is working."""
    root = _two_seats(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    availability.mark_limited(root, "claude-code:primary", minutes=60, now=now)
    assert account.select_seat(root, now=now).id == "fallback"
    availability.clear(root, "claude-code:primary")
    assert account.select_seat(root, now=now).id == "primary"


def test_no_seat_is_chosen_when_every_seat_is_spent(tmp_path: Path) -> None:
    root = _two_seats(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    for seat in ("primary", "fallback"):
        availability.mark_limited(root, f"claude-code:{seat}", minutes=60, now=now)
    assert account.select_seat(root, now=now) is None


def test_no_seat_available_refuses_naming_the_registry(tmp_path: Path) -> None:
    """`refuse_no_seat_available` (issue #82's split-out of `claude_env`'s old registry-refusal branch): the
    real agentic-dispatch error path, raised only after a caller's own `select_seat` loop already came up
    empty -- distinct from `claude_env`, which no longer touches the registry at all."""
    root = _two_seats(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    for seat in ("primary", "fallback"):
        availability.mark_limited(root, f"claude-code:{seat}", minutes=60, now=now)
    assert account.select_seat(root, now=now) is None
    with pytest.raises(account.PersonalAccountRefused):
        account.refuse_no_seat_available(root)


def test_an_explicit_pinned_directory_still_wins_over_the_registry(tmp_path: Path, monkeypatch) -> None:
    """`PRAVRUDHI_CLAUDE_CONFIG_DIR` is what keeps an automated unit off an interactive login's credential.

    It is an instruction from the unit that is running, so it outranks a file describing the machine's seats.
    This is `select_seat`'s own registry, not `claude_env` -- issue #82 split the two apart, and `claude_env`
    no longer reads `HOME_ENV`/the pin at all (see TestClaudeEnvIsTheScriptedSeatZero below)."""
    root = _two_seats(tmp_path)
    pinned = _seat_dir(tmp_path / "isolated", email="one@example.com", refresh="r-one")
    monkeypatch.setenv(account.HOME_ENV, str(pinned))
    assert account.select_seat(root).config_dir == pinned


def test_a_registry_that_is_absent_leaves_one_seat_at_the_default_location(tmp_path: Path) -> None:
    """No `configs/seats.yaml` must behave exactly as the module did before seats existed."""
    root = tmp_path / "repo"
    root.mkdir()
    declared = account.seats(root)
    assert [s.id for s in declared] == ["default"]
    assert declared[0].config_dir == account.PROJECT_CLAUDE_HOME.expanduser()


def test_a_directory_whose_cached_profile_names_another_account_is_reported(tmp_path: Path) -> None:
    """The failure the CLI cannot see: a live token beside a profile block naming a different seat."""
    root = _two_seats(tmp_path)
    _seat_dir(tmp_path / "primary", email="someone-else@example.com", refresh="r-one")
    problems = account.mismatches(root)
    assert any("someone-else@example.com" in p and "primary" in p for p in problems)


def test_matching_profiles_report_nothing(tmp_path: Path) -> None:
    assert account.mismatches(_two_seats(tmp_path)) == []


def test_two_seats_sharing_one_credential_are_reported_as_one_account(tmp_path: Path) -> None:
    """Two directories holding the same refresh token are not a failover pair, however they are declared."""
    root = _two_seats(tmp_path)
    _seat_dir(tmp_path / "fallback", email="two@example.com", refresh="r-one")
    problems = account.mismatches(root)
    assert any("same credential" in p for p in problems)


def test_account_status_reports_a_mismatch_without_calling_the_seat_unusable(tmp_path: Path) -> None:
    root = _two_seats(tmp_path)
    _seat_dir(tmp_path / "primary", email="someone-else@example.com", refresh="r-one")
    ok, detail = account.account_status(root)
    assert ok is True
    assert "someone-else@example.com" in detail


def _record_runs(monkeypatch, outcomes: dict[str, tuple[int, str]]) -> list[str]:
    """Stub the process launch, keyed by which seat directory the call was pointed at."""
    from pravrudhi.agents import cli_agents

    seen: list[str] = []

    def fake_run(cmd, workspace, timeout_s, env=None, *, stdin_text=None):  # noqa: ANN001, ARG001
        directory = (env or {}).get("CLAUDE_CONFIG_DIR", "")
        seen.append(directory)
        code, text = outcomes.get(Path(directory).name, (0, "ok"))
        return code, json.dumps({"result": text, "is_error": code != 0}), text, 0.1

    monkeypatch.setattr(cli_agents, "_run", fake_run)
    return seen


def test_a_usage_limit_on_the_primary_seat_is_retried_on_the_reserve(tmp_path: Path, monkeypatch) -> None:
    from pravrudhi.agents.cli_agents import ClaudeCodeAgent

    root = _two_seats(tmp_path)
    seen = _record_runs(monkeypatch, {"primary": (1, "Claude usage limit reached")})
    result = ClaudeCodeAgent(root).run("do the thing", tmp_path)

    assert [Path(d).name for d in seen] == ["primary", "fallback"]
    assert result.ok is True


def test_the_spent_seat_is_left_cooling_so_the_next_dispatch_skips_it(tmp_path: Path, monkeypatch) -> None:
    from pravrudhi.agents.cli_agents import ClaudeCodeAgent

    root = _two_seats(tmp_path)
    _record_runs(monkeypatch, {"primary": (1, "Claude usage limit reached")})
    ClaudeCodeAgent(root).run("do the thing", tmp_path)

    assert availability.is_cool(root, "claude-code:primary")
    assert not availability.is_cool(root, "claude-code:fallback")


def test_an_ordinary_failure_does_not_spend_the_reserve_seat(tmp_path: Path, monkeypatch) -> None:
    """A prompt the model botched will be botched by the reserve too; moving would invert the instruction."""
    from pravrudhi.agents.cli_agents import ClaudeCodeAgent

    root = _two_seats(tmp_path)
    seen = _record_runs(monkeypatch, {"primary": (1, "SyntaxError: unexpected token")})
    result = ClaudeCodeAgent(root).run("do the thing", tmp_path)

    assert [Path(d).name for d in seen] == ["primary"]
    assert result.ok is False
    assert not availability.is_cool(root, "claude-code:primary")


def test_when_every_seat_is_spent_the_limit_is_returned_for_the_router_to_act_on(
    tmp_path: Path, monkeypatch
) -> None:
    """The agent absorbs a limit it can route around. One it cannot must still reach the caller."""
    from pravrudhi.agents.cli_agents import ClaudeCodeAgent

    root = _two_seats(tmp_path)
    seen = _record_runs(monkeypatch, {
        "primary": (1, "Claude usage limit reached"),
        "fallback": (1, "Claude usage limit reached"),
    })
    result = ClaudeCodeAgent(root).run("do the thing", tmp_path)

    assert [Path(d).name for d in seen] == ["primary", "fallback"]
    assert result.ok is False
    assert "usage limit" in result.text.lower()


def test_the_seat_is_available_while_any_declared_seat_can_still_serve(tmp_path: Path, monkeypatch) -> None:
    """One spent seat must not report the whole vendor as gone; that is what the reserve is for."""
    from pravrudhi.agents import cli_agents

    root = _two_seats(tmp_path)
    monkeypatch.setattr(cli_agents.shutil, "which", lambda _: "/usr/bin/claude")
    availability.mark_limited(root, "claude-code:primary", minutes=60)
    assert cli_agents.ClaudeCodeAgent(root).available() is True

    availability.mark_limited(root, "claude-code:fallback", minutes=60)
    assert cli_agents.ClaudeCodeAgent(root).available() is False


def test_the_provisioning_instruction_names_a_command_that_exists(tmp_path: Path) -> None:
    """`claude login` is not a subcommand on CLI 2.x -- it is taken as a prompt and starts a session."""
    root = _two_seats(tmp_path)
    text = account.how_to_provision(account.seats(root)[1])
    assert "claude auth login" in text
    assert "claude login\n" not in text


def test_the_provisioning_instruction_names_the_seat_that_is_missing(tmp_path: Path) -> None:
    """One instruction for "a credential" is useless when there are two seats and one of them is fine."""
    root = _two_seats(tmp_path)
    fallback = account.seats(root)[1]
    text = account.how_to_provision(fallback)
    assert str(fallback.config_dir) in text
    assert fallback.email in text


def test_the_live_identity_of_a_seat_is_read_from_the_cli_not_the_profile_cache(
    tmp_path: Path, monkeypatch
) -> None:
    """The cache is the thing that lies; a check built on it cannot settle who a directory is logged in as."""
    root = _two_seats(tmp_path)
    seat = account.seats(root)[0]
    monkeypatch.setattr(account, "_auth_status", lambda _d: {"loggedIn": True, "email": "live@example.com"})
    assert account.live_identity(seat) == "live@example.com"


def test_a_mismatch_is_judged_against_the_live_identity_when_the_cli_can_be_asked(
    tmp_path: Path, monkeypatch
) -> None:
    """A stale cache naming the wrong account is NOT a mismatch when the live token is the declared seat."""
    root = _two_seats(tmp_path)
    _seat_dir(tmp_path / "primary", email="stale@example.com", refresh="r-one")
    monkeypatch.setattr(
        account, "_auth_status",
        lambda d: {"loggedIn": True, "email": "one@example.com" if d.name == "primary" else "two@example.com"},
    )
    assert account.mismatches(root, live=True) == []


def test_a_live_identity_that_contradicts_the_registry_is_reported(tmp_path: Path, monkeypatch) -> None:
    root = _two_seats(tmp_path)
    monkeypatch.setattr(
        account, "_auth_status",
        lambda d: {"loggedIn": True, "email": "someone@example.com" if d.name == "primary" else "two@example.com"},
    )
    problems = account.mismatches(root, live=True)
    assert any("someone@example.com" in p and "primary" in p for p in problems)


def test_the_cheap_check_does_not_spawn_a_subprocess(tmp_path: Path, monkeypatch) -> None:
    """`account_status` is polled by status surfaces; it must not shell out to the CLI to answer."""
    root = _two_seats(tmp_path)
    called: list[Path] = []
    monkeypatch.setattr(account, "_auth_status", lambda d: called.append(d) or None)

    account.mismatches(root)
    account.account_status(root)
    assert called == []

    account.mismatches(root, live=True)
    assert called != []


class TestClaudeEnvIsTheScriptedSeatZero:
    """Issue #82 (Tag/Lead-2, 2026-09-26 -- stopped Track-A's audit run over this): `claude_env` used to
    resolve through the registry `select_seat` walks (primary=sathish/seat 2), the right rotation for real
    agentic coding dispatch but the wrong account for a one-shot SCRIPTED `claude -p` call, which TEAM-
    RULES.md's own Claude usage cost rules require to bill seat 0 (sharath.ai.colab@gmail.com). `claude_env`
    now resolves independently of the registry entirely -- these tests use `SCRIPTED_CLAUDE_HOME_ENV` to
    redirect at a throwaway directory rather than asserting on the real, live, machine-specific seat-0
    directory (same discipline as the panel.py `_claude_cli_env` tests this mirrors)."""

    def test_defaults_to_the_seat_zero_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(account.SCRIPTED_CLAUDE_HOME_ENV, raising=False)
        assert account.scripted_claude_home() == account.SCRIPTED_CLAUDE_HOME_DEFAULT.expanduser()

    def test_env_var_redirects_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        redirected = tmp_path / "redirected-seat-0"
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(redirected))
        assert account.scripted_claude_home() == redirected

    def test_a_provisioned_correct_seat_is_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = _seat_dir(tmp_path / "seat0", email=account.SCRIPTED_CLAUDE_EMAIL, refresh="r-seat0")
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
        assert account.claude_env()["CLAUDE_CONFIG_DIR"] == str(home)

    def test_no_credential_raises_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(tmp_path / "never-provisioned"))
        with pytest.raises(account.PersonalAccountRefused):
            account.claude_env()

    def test_no_credential_with_require_false_still_sets_the_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "never-provisioned"
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(empty))
        assert account.claude_env(require=False)["CLAUDE_CONFIG_DIR"] == str(empty)

    def test_a_directory_logged_in_as_the_wrong_account_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #82's second requirement: verify the account, don't just check a credential file exists."""
        home = _seat_dir(tmp_path / "seat0", email="sharath.sathish@gmail.com", refresh="r-wrong")
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
        with pytest.raises(account.ScriptedSeatMismatch, match="sharath.sathish@gmail.com"):
            account.claude_env()

    def test_a_wrong_account_is_refused_even_with_require_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`require=False` excuses a MISSING credential (a status listing must not crash over one), never a
        credential for the WRONG account -- that is a live, silent-billing risk, not an absent-file question."""
        home = _seat_dir(tmp_path / "seat0", email="sharath.sathish@gmail.com", refresh="r-wrong")
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
        with pytest.raises(account.ScriptedSeatMismatch):
            account.claude_env(require=False)

    def test_no_cached_profile_yet_is_not_treated_as_a_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A live credential with no `.claude.json` yet (freshly logged in, no command has re-fetched the
        profile) must not be refused -- there is nothing to disagree with the expected account yet."""
        home = tmp_path / "seat0"
        home.mkdir()
        (home / ".credentials.json").write_text("{}")
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
        assert account.claude_env()["CLAUDE_CONFIG_DIR"] == str(home)

    def test_claude_env_never_reads_the_registry_or_the_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registry (`configs/seats.yaml`) and `PRAVRUDHI_CLAUDE_CONFIG_DIR` are `select_seat`'s own
        concerns -- issue #82 split `claude_env` away from both entirely, so neither can leak back in."""
        root = _two_seats(tmp_path)  # a fully provisioned, valid registry -- must be ignored
        pinned = _seat_dir(tmp_path / "isolated", email="one@example.com", refresh="r-pin")
        monkeypatch.setenv(account.HOME_ENV, str(pinned))
        home = _seat_dir(tmp_path / "seat0", email=account.SCRIPTED_CLAUDE_EMAIL, refresh="r-seat0")
        monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
        assert account.claude_env()["CLAUDE_CONFIG_DIR"] == str(home)
        del root  # registry exists only to prove it is ignored


def _seat_with_activation(root: Path, tmp_path: Path) -> Path:
    """A two-seat registry matching the real v2 shape: primary always eligible, fallback gated on
    `SeatActivation` (primary's own 5h reading and fallback's own weekly reading)."""
    _seat_dir(tmp_path / "primary", email="one@example.com", refresh="r-one")
    _seat_dir(tmp_path / "fallback", email="two@example.com", refresh="r-two")
    (root / "configs").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "seats.yaml").write_text(yaml.safe_dump({
        "version": 2,
        "seats": [
            {"id": "primary", "email": "one@example.com", "config_dir": str(tmp_path / "primary")},
            {
                "id": "fallback", "email": "two@example.com", "config_dir": str(tmp_path / "fallback"),
                "activation": {
                    "depends_on_seat": "primary",
                    "other_seat_5h_pct_at_least": 90,
                    "self_weekly_pct_below": 80,
                },
            },
        ],
    }))
    return root


def _write_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, usage: dict[str, Any]) -> None:
    path = tmp_path / "seat-usage.json"
    path.write_text(json.dumps(usage))
    monkeypatch.setenv(account.SEAT_USAGE_FILE_ENV, str(path))


class TestFallbackSeatActivation:
    """Lead-2, 2026-09-27: seat 2 is genuine overflow, never a default -- reachable only while seat 0 (here,
    the test's `primary`) is running hot AND seat 2 itself has weekly headroom left. Fails CLOSED on missing
    usage data: a watchdog outage must never look like permission to spend the reserve."""

    def _root(self, tmp_path: Path) -> Path:
        root = tmp_path / "repo"
        root.mkdir()
        return _seat_with_activation(root, tmp_path)

    def test_primary_is_chosen_while_it_can_serve_regardless_of_usage_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = self._root(tmp_path)
        monkeypatch.delenv(account.SEAT_USAGE_FILE_ENV, raising=False)
        chosen = account.select_seat(root)
        assert chosen is not None and chosen.id == "primary"

    def test_fallback_is_never_offered_with_no_usage_data_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail closed: primary unprovisioned (cannot serve) and no usage file at all -- a real watchdog
        outage -- must resolve to NO seat, never a silent fall back to seat 2."""
        root = self._root(tmp_path)
        (tmp_path / "primary" / ".credentials.json").unlink()
        monkeypatch.delenv(account.SEAT_USAGE_FILE_ENV, raising=False)
        assert account.select_seat(root) is None

    def test_fallback_is_offered_when_the_gate_is_met(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = self._root(tmp_path)
        (tmp_path / "primary" / ".credentials.json").unlink()  # primary itself cannot serve
        _write_usage(tmp_path, monkeypatch, {
            "primary": {"five_hour_pct": 91}, "fallback": {"weekly_pct": 40},
        })
        chosen = account.select_seat(root)
        assert chosen is not None and chosen.id == "fallback"

    def test_fallback_is_refused_when_primary_is_not_actually_hot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = self._root(tmp_path)
        (tmp_path / "primary" / ".credentials.json").unlink()
        _write_usage(tmp_path, monkeypatch, {
            "primary": {"five_hour_pct": 50}, "fallback": {"weekly_pct": 40},
        })
        assert account.select_seat(root) is None

    def test_fallback_is_refused_when_its_own_weekly_usage_is_too_high(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spending the reserve into its own limit would just move the problem, not solve it."""
        root = self._root(tmp_path)
        (tmp_path / "primary" / ".credentials.json").unlink()
        _write_usage(tmp_path, monkeypatch, {
            "primary": {"five_hour_pct": 95}, "fallback": {"weekly_pct": 85},
        })
        assert account.select_seat(root) is None

    def test_fallback_is_refused_when_only_half_the_usage_data_is_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = self._root(tmp_path)
        (tmp_path / "primary" / ".credentials.json").unlink()
        _write_usage(tmp_path, monkeypatch, {"primary": {"five_hour_pct": 95}})  # fallback's own reading missing
        assert account.select_seat(root) is None

    def test_fallback_still_needs_its_own_credential_even_when_the_gate_is_met(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = self._root(tmp_path)
        (tmp_path / "primary" / ".credentials.json").unlink()
        (tmp_path / "fallback" / ".credentials.json").unlink()
        _write_usage(tmp_path, monkeypatch, {
            "primary": {"five_hour_pct": 95}, "fallback": {"weekly_pct": 40},
        })
        assert account.select_seat(root) is None


class TestSelectSeatVerifiesTheAccountEmail:
    """Issue #82/#83's verification, reused rather than restated (Lead-2, 2026-09-27): a live token in a
    seat's directory is not enough -- it must belong to the account THAT SEAT declares."""

    def test_a_seat_whose_cached_profile_names_a_different_account_is_skipped(
        self, tmp_path: Path
    ) -> None:
        root = _two_seats(tmp_path)
        _seat_dir(tmp_path / "primary", email="someone-else@example.com", refresh="r-one")
        chosen = account.select_seat(root)
        assert chosen is not None and chosen.id == "fallback"

    def test_a_seat_with_no_cached_profile_yet_is_still_chosen(self, tmp_path: Path) -> None:
        """Same discipline as `claude_env`'s own check: no `.claude.json` yet is not a mismatch, since there
        is nothing recorded to disagree with the declared account."""
        root = _two_seats(tmp_path)
        (tmp_path / "primary" / ".claude.json").unlink()
        chosen = account.select_seat(root)
        assert chosen is not None and chosen.id == "primary"


class TestShippedSeatsRegistry:
    """The real `configs/seats.yaml` this project ships, pinned so a hand-edit cannot silently reintroduce
    seat 2 as a default or bring claude-admin back into automated dispatch (Lead-2, 2026-09-27)."""

    def test_primary_is_the_seat_zero_colab_account(self) -> None:
        declared = account.seats(REPO)
        assert declared[0].id == "primary"
        assert declared[0].email == "sharath.ai.colab@gmail.com"
        assert declared[0].activation is None

    def test_fallback_is_seat_two_gated_never_a_default(self) -> None:
        declared = account.seats(REPO)
        fallback = next(s for s in declared if s.id == "fallback")
        assert fallback.email == "sharath.sathish@gmail.com"
        assert fallback.activation == account.SeatActivation(
            depends_on_seat="primary", other_seat_5h_pct_at_least=90, self_weekly_pct_below=80,
        )

    def test_claude_admin_is_not_in_the_registry_at_all(self) -> None:
        assert all(s.email != "admin@axismeru.com" for s in account.seats(REPO))
