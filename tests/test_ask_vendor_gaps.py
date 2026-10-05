"""`panel.ask_vendor` gaps closed before the #306 head-to-head (pravrudhi #206).

Envelopes in the first classes are CONSTRUCTED (labelled as such). The codex model id lives in the rollout file,
not in the --json stream (observed 2026-10-02), so `TestCodexEnvelopeArmD` keeps its stream-key cases as constructed
forward-compat and `TestCodexModelIdFromRollout` covers the real source. `TestRecordedEnvelopes` replays RECORDED
envelopes from tests/fixtures/ask_vendor_envelopes (trivial prompts, no eval items). Nothing here calls a live model;
`cli_agents._run` and `subprocess.run` are stubbed. This module opts out of the conftest stub of `_claude_auth_email`
so the real seat check is exercised.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from pravrudhi.agents import cli_agents
from pravrudhi.application import panel


def _env(result="ANSWER: A", models=None, cost=0.0123, is_error=False):
    models = models if models is not None else {"claude-sonnet-5": {"outputTokens": 4}}
    d = {
        "result": result,
        "is_error": is_error,
        "modelUsage": models,
        "usage": {"input_tokens": 2, "output_tokens": 4, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 20},
    }
    if cost is not None:
        d["total_cost_usd"] = cost
    return json.dumps(d)


def _claude_vendor(**params):
    base = panel.VENDORS["claude-cli"]
    return replace(base, params={**base.params, **params})


def _codex_vendor(**params):
    base = panel.VENDORS["codex-cli"]
    return replace(base, params={**base.params, "codex_model": "gpt-x-1", **params})


@pytest.fixture
def seat(tmp_path, monkeypatch):
    home = tmp_path / "loop"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv("PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR", str(home))
    monkeypatch.setattr(panel, "_claude_auth_email", lambda env: panel.CLAUDE_CLI_EXPECTED_EMAIL)
    return home


class _Run:
    def __init__(self, out, code=0, err=""):
        self.out, self.code, self.err, self.calls = out, code, err, []

    def __call__(self, cmd, cwd, timeout_s, env=None, *, stdin_text=None):
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "stdin": stdin_text})
        return self.code, self.out, self.err, 0.2


def _ask_claude(monkeypatch, out, vendor=None, **kw):
    run = _Run(out, **kw)
    monkeypatch.setattr(cli_agents, "_run", run)
    return panel.ask_vendor(vendor or panel.VENDORS["claude-cli"], "q"), run


class TestModelPinnedAndVerified:
    def test_resolved_model_is_the_pinned_family_key_not_the_first_key(self, seat, monkeypatch):
        models = {"claude-haiku-4-5-20251001": {"outputTokens": 1}, "claude-sonnet-5": {"outputTokens": 4}}
        ans, _ = _ask_claude(monkeypatch, _env(models=models))
        assert ans.resolved_model == "claude-sonnet-5"
        assert ans.billed_models == ("claude-haiku-4-5-20251001", "claude-sonnet-5")

    def test_pinned_model_not_billed_is_an_error(self, seat, monkeypatch):
        with pytest.raises(RuntimeError, match="model mismatch"):
            _ask_claude(monkeypatch, _env(models={"claude-haiku-4-5-20251001": {"outputTokens": 4}}))

    def test_a_non_haiku_extra_model_is_an_error(self, seat, monkeypatch):
        models = {"claude-sonnet-5": {}, "claude-opus-5-5": {}}
        with pytest.raises(RuntimeError, match="unexpected models"):
            _ask_claude(monkeypatch, _env(models=models))

    def test_envelope_without_modelusage_is_an_error_not_a_pass(self, seat, monkeypatch):
        raw = json.loads(_env())
        del raw["modelUsage"]
        with pytest.raises(RuntimeError, match="unverifiable"):
            _ask_claude(monkeypatch, json.dumps(raw))

    def test_exact_id_pin_must_match_exactly(self, seat, monkeypatch):
        v = _claude_vendor(model="claude-sonnet-5")
        ans, _ = _ask_claude(monkeypatch, _env(models={"claude-sonnet-5": {}}), v)
        assert ans.resolved_model == "claude-sonnet-5"
        with pytest.raises(RuntimeError, match="model mismatch"):
            _ask_claude(monkeypatch, _env(models={"claude-sonnet-4-6": {}}), v)


class TestModelCap:
    @pytest.mark.parametrize("model", ["opus", "claude-opus-5-5"])
    def test_opus_without_effort_low_is_refused_before_any_call(self, seat, monkeypatch, model):
        run = _Run(_env())
        monkeypatch.setattr(cli_agents, "_run", run)
        with pytest.raises(ValueError, match="opus is allowed only"):
            panel.ask_vendor(_claude_vendor(model=model), "q")
        assert run.calls == []

    @pytest.mark.parametrize("model", ["claude-fable-5-1", "default", "sonnet-evil", "gpt-5", " sonnet"])
    def test_anything_outside_sonnet_haiku_is_refused(self, seat, monkeypatch, model):
        monkeypatch.setattr(cli_agents, "_run", _Run(_env()))
        with pytest.raises(ValueError, match="not a sonnet/haiku"):
            panel.ask_vendor(_claude_vendor(model=model), "q")

    def test_opus_at_effort_low_with_a_budgeted_arm_is_allowed_and_passes_the_flag(self, seat, monkeypatch):
        from pravrudhi.application import usage_gate

        claimed = []
        monkeypatch.setattr(
            usage_gate, "claim_opus_call",
            lambda root, arm, gate: claimed.append(arm) or {"arm": arm, "call_number": 1, "call_cap": 10},
        )
        v = _claude_vendor(model="opus", effort="low", m4_arm="m4-phase1-config-a")
        ans, run = _ask_claude(monkeypatch, _env(models={"claude-opus-5-5": {"outputTokens": 4}}), v)
        cmd = run.calls[0]["cmd"]
        assert cmd[cmd.index("--model") + 1] == "opus"
        assert cmd[cmd.index("--effort") + 1] == "low"
        assert ans.resolved_model == "claude-opus-5-5"
        assert claimed == ["m4-phase1-config-a"] and ans.usage_gate["opus_m4"]["call_number"] == 1

    def test_opus_at_effort_low_without_an_m4_arm_makes_no_call(self, seat, monkeypatch):
        from pravrudhi.application import usage_gate

        run = _Run(_env())
        monkeypatch.setattr(cli_agents, "_run", run)
        with pytest.raises(usage_gate.UsageGateRefused, match="m4_arm"):
            panel.ask_vendor(_claude_vendor(model="opus", effort="low"), "q")
        assert run.calls == []

    def test_a_spent_opus_cap_makes_no_call(self, seat, monkeypatch):
        from pravrudhi.application import usage_gate

        def spent(root, arm, gate):
            raise usage_gate.UsageGateRefused("refusing: opus call cap for arm 'x' is spent (1 of 1)")

        monkeypatch.setattr(usage_gate, "claim_opus_call", spent)
        run = _Run(_env())
        monkeypatch.setattr(cli_agents, "_run", run)
        with pytest.raises(usage_gate.UsageGateRefused, match="spent"):
            panel.ask_vendor(_claude_vendor(model="opus", effort="low", m4_arm="x"), "q")
        assert run.calls == []

    def test_sonnet_is_never_counted_against_the_opus_budget(self, seat, monkeypatch):
        from pravrudhi.application import usage_gate

        monkeypatch.setattr(usage_gate, "claim_opus_call", lambda *a: pytest.fail("sonnet must not claim opus budget"))
        ans, _ = _ask_claude(monkeypatch, _env())
        assert "opus_m4" not in (ans.usage_gate or {})

    def test_effort_is_validated_and_omitted_when_unset(self, seat, monkeypatch):
        _, run = _ask_claude(monkeypatch, _env())
        assert "--effort" not in run.calls[0]["cmd"]
        monkeypatch.setattr(cli_agents, "_run", _Run(_env()))
        with pytest.raises(ValueError, match="effort"):
            panel.ask_vendor(_claude_vendor(effort="turbo"), "q")

    def test_manifest_catches_a_bad_model_and_records_the_invocation(self):
        with pytest.raises(ValueError):
            panel.panel_manifest([{"id": "1", "prompt": "p"}], [_claude_vendor(model="opus")])
        m = panel.panel_manifest([{"id": "1", "prompt": "p"}], [_claude_vendor(model="opus", effort="low")])
        (v,) = m["vendors"]
        assert v["pinned_model"] == "opus" and v["claude_effort"] == "low"
        assert v["claude_slim_flags"] == list(panel.CLAUDE_CLI_SLIM_FLAGS)
        assert v["claude_expected_seat_email"] == "sharath.sathish@gmail.com"


class TestSlimFlagsEnforced:
    def test_every_slim_flag_is_present_in_order_and_params_cannot_remove_them(self, seat, monkeypatch):
        v = _claude_vendor(tools="Bash", extra_args=["--allowedTools", "Bash"], slim=False)
        _, run = _ask_claude(monkeypatch, _env(), v)
        cmd = run.calls[0]["cmd"]
        assert cmd == ["claude", "-p", "--output-format", "json", *panel.CLAUDE_CLI_SLIM_FLAGS, "--model", "sonnet"]
        assert "Bash" not in cmd

    def test_slim_flag_set_is_exactly_the_house_rule(self):
        assert panel.CLAUDE_CLI_SLIM_FLAGS == (
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--tools",
            "",
        )


#: Every real limit/quota notice text we have recorded (tests/test_claude_session_limit.py, test_limit_reset.py,
#: test_sentinel_fallback.py, test_heartbeat.py, limits.yaml phrases, this file), plus curly-apostrophe variants.
REAL_NOTICES = [
    "You've hit your session limit · resets 3:20am",
    "You\u2019ve hit your session limit \u00b7 resets 3:20am",
    "You've hit your weekly limit · resets Sep 29, 5pm",
    "You\u2019ve hit your weekly limit · resets 5pm",
    "You've hit your usage limit · resets 11am",
    "You\u2019ve hit your usage limit",
    "You've hit your session limit. Resets 9pm.",
    "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro) or try again at 3:51 PM.",
    "Claude usage limit reached. Your limit will reset at 3pm",
    "Claude usage limit reached",
    "5-hour limit reached \u2219 resets 3pm",
    "Rate limit exceeded, try again later",
    "Rate limit reached, try again later",
    "agent exited non-zero: rate limited (429), try again later",
    "You're out of extra usage",
    "You're out of usage \u00b7 resets 4pm",
    "You exceeded your current quota, please check your plan and billing details.",
    'API Error: "type":"rate_limit_error","message":"This request would exceed your rate limit"',
    "Error code: insufficient_quota",
    "overloaded_error: Overloaded",
    "429 Too Many Requests",
]
SHORT_LEGIT = [
    "The quota for sugar imports is 500 tonnes.",
    "Quota",
    "The limitation period resets in 2027.",
    "ESTABLISHED. The allowance resets at midnight under s. 4.",
    "Please try again later with more facts.",
    "The server was overloaded with facts, so the answer is partial.",
    "NOT_ESTABLISHED: the rate limit in clause 4 is not an element.",
    "Your weekly limit on withdrawals is 500 GBP.",
    "Set the session limit to 30 minutes in the config.",
    "The usage of the word limit is ambiguous.",
]


class TestQuotaRegexIsAnchored:
    @pytest.mark.parametrize("notice", REAL_NOTICES)
    def test_every_recorded_real_notice_is_still_a_quota_error(self, notice):
        assert panel._looks_like_quota(notice)

    @pytest.mark.parametrize("answer", SHORT_LEGIT)
    def test_a_short_legitimate_answer_is_not_misread(self, answer):
        assert not panel._looks_like_quota(answer)

    @pytest.mark.parametrize(
        "unseen",
        [
            "Capacity is exhausted for this plan until Friday.",
            "Your allowance for this period is used up. Come back after the reset.",
            "Service busy. Retry in a few minutes.",
        ],
    )
    def test_an_unseen_notice_the_regex_misses_still_raises_with_no_model_usage(self, seat, monkeypatch, unseen):
        """Second line of defence: a notice has no `modelUsage`, so `_check_claude_models` refuses it even though
        the narrowed regex does not recognise the wording."""
        assert not panel._looks_like_quota(unseen)
        raw = json.loads(_env(result=unseen))
        del raw["modelUsage"]
        with pytest.raises(RuntimeError, match="model unverifiable"):
            _ask_claude(monkeypatch, json.dumps(raw))


class TestQuotaTextIsAnError:
    @pytest.mark.parametrize(
        "notice",
        [
            "You've hit your session limit. Resets 9pm.",
            "Claude usage limit reached",
            "Rate limit exceeded, try again later",
            "You're out of extra usage",
        ],
    )
    def test_quota_notice_in_a_zero_exit_result_is_an_error(self, seat, monkeypatch, notice):
        with pytest.raises(RuntimeError, match="quota/limit notice"):
            _ask_claude(monkeypatch, _env(result=notice))

    def test_a_long_real_answer_that_mentions_a_limit_is_not_a_notice(self, seat, monkeypatch):
        text = "The statute sets a limitation period. " + "The rate limit on appeals is discussed. " * 20
        ans, _ = _ask_claude(monkeypatch, _env(result=text))
        assert ans.text.startswith("The statute")

    def test_a_json_answer_mentioning_quota_is_not_a_notice(self, seat, monkeypatch):
        ans, _ = _ask_claude(monkeypatch, _env(result='{"verdict": "NOT_ESTABLISHED", "why": "quota"}'))
        assert "NOT_ESTABLISHED" in ans.text

    def test_run_panel_records_a_quota_notice_as_a_gap_never_an_answer(self, seat, monkeypatch, tmp_path):
        monkeypatch.setattr(cli_agents, "_run", _Run(_env(result="You've hit your session limit")))
        (row,) = panel.run_panel(tmp_path, [{"id": "p", "prompt": "q"}], [panel.VENDORS["claude-cli"]])
        assert row.text == "" and "quota/limit notice" in (row.error or "")


class TestCostCapture:
    def test_total_cost_usd_is_carried_through_ask_and_run_panel(self, seat, monkeypatch, tmp_path):
        monkeypatch.setattr(cli_agents, "_run", _Run(_env(cost=0.0421)))
        (row,) = panel.run_panel(tmp_path, [{"id": "p", "prompt": "q"}], [panel.VENDORS["claude-cli"]])
        assert row.cost_usd == pytest.approx(0.0421)
        assert row.billed_models == ("claude-sonnet-5",)
        written = json.loads((tmp_path / "panel" / "answers.jsonl").read_text())
        assert written["cost_usd"] == pytest.approx(0.0421) and written["billed_models"] == ["claude-sonnet-5"]

    def test_absent_cost_is_none_unobserved_not_zero(self, seat, monkeypatch):
        ans, _ = _ask_claude(monkeypatch, _env(cost=None))
        assert ans.cost_usd is None


class TestSeatAssert:
    def test_default_dir_is_seat2_claude_loop(self):
        assert Path("~/.config/pravrudhi/claude-loop") == panel.CLAUDE_CLI_CONFIG_DIR_DEFAULT
        assert panel.CLAUDE_CLI_EXPECTED_EMAIL == "sharath.sathish@gmail.com"

    @pytest.mark.parametrize("email", ["sharath.ai.colab@gmail.com", "admin@axismeru.com", None])
    def test_wrong_or_unreadable_seat_refuses_before_the_call(self, seat, monkeypatch, email):
        monkeypatch.setattr(panel, "_claude_auth_email", lambda env: email)
        run = _Run(_env())
        monkeypatch.setattr(cli_agents, "_run", run)
        with pytest.raises(panel.ClaudeCliNotProvisioned, match="refusing"):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "q")
        assert run.calls == []

    def test_email_is_read_from_claude_auth_status_json_with_the_seat_dir(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw["env"]
            return subprocess.CompletedProcess(cmd, 0, stdout='{"email": "sharath.sathish@gmail.com"}', stderr="")

        monkeypatch.setattr(panel.subprocess, "run", fake_run)
        assert panel._claude_auth_email({"CLAUDE_CONFIG_DIR": "/x/loop"}) == "sharath.sathish@gmail.com"
        assert seen["cmd"] == ["claude", "auth", "status", "--json"]
        assert seen["env"]["CLAUDE_CONFIG_DIR"] == "/x/loop"

    def test_unparseable_auth_status_is_none_and_so_refused(self, monkeypatch):
        monkeypatch.setattr(
            panel.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="not json", stderr="")
        )
        assert panel._claude_auth_email({}) is None


#: CONSTRUCTED. `agent_message`/`turn.completed` follow the recorded stream; where the model id sits is a guess.
def _stream(*, text="ANSWER: A", model=None, model_in="thread.started", extra=()):
    ev = [
        {"type": "thread.started", "thread_id": "th_1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": 15296, "cached_input_tokens": 12160, "output_tokens": 5}},
    ]
    if model:
        ev[0 if model_in == "thread.started" else 2]["model"] = model
    return "\n".join(json.dumps(e) for e in [*ev, *extra])


def _ask_codex(monkeypatch, out, vendor=None, **kw):
    run = _Run(out, **kw)
    monkeypatch.setattr(cli_agents, "_run", run)
    return panel.ask_vendor(vendor or _codex_vendor(), "q"), run


class TestCodexEnvelopeArmD:
    def test_json_stream_gives_text_usage_and_the_model_id_wherever_it_sits(self, monkeypatch):
        for where in ("thread.started", "turn.completed"):
            ans, run = _ask_codex(monkeypatch, _stream(model="gpt-x-1", model_in=where))
            assert (ans.text, ans.resolved_model, ans.billed_models) == ("ANSWER: A", "gpt-x-1", ("gpt-x-1",))
            assert ans.tokens == 15296 + 5 and ans.cache_read_tokens == 12160
            assert "--json" in run.calls[0]["cmd"] and run.calls[0]["cmd"][run.calls[0]["cmd"].index("-m") + 1] == "gpt-x-1"
            assert ans.cost_usd is None

    def test_an_unpinned_codex_call_is_refused_before_any_call(self, monkeypatch):
        run = _Run(_stream(model="gpt-x-1"))
        monkeypatch.setattr(cli_agents, "_run", run)
        for pin in (None, "", "  "):
            with pytest.raises(RuntimeError, match="model unverifiable"):
                panel.ask_vendor(_codex_vendor(codex_model=pin), "q")
        with pytest.raises(RuntimeError, match="model unverifiable"):
            panel.ask_vendor(panel.VENDORS["codex-cli"], "q")
        assert run.calls == []

    @pytest.mark.parametrize(
        "unseen",
        [
            "Capacity is exhausted for this plan until Friday.",
            "Your allowance for this period is used up. Come back after the reset.",
        ],
    )
    def test_an_unseen_limit_notice_with_no_resolved_model_is_an_error_not_an_answer(self, monkeypatch, unseen):
        """R2 (#221): a notice the quota regex does not recognise, in an agent message with no model id anywhere
        in the stream or rollout, used to come back as an ANSWER with resolved=None."""
        assert not panel._looks_like_quota(unseen)
        with pytest.raises(RuntimeError, match="model unverifiable"):
            _ask_codex(monkeypatch, _stream(text=unseen))

    def test_a_pinned_call_with_no_resolved_model_is_an_error(self, monkeypatch):
        with pytest.raises(RuntimeError, match="model unverifiable"):
            _ask_codex(monkeypatch, _stream())

    def test_pin_passes_dash_m_and_must_be_confirmed_by_the_stream(self, monkeypatch):
        v = _codex_vendor(codex_model="gpt-x-1")
        ans, run = _ask_codex(monkeypatch, _stream(model="gpt-x-1"), v)
        cmd = run.calls[0]["cmd"]
        assert cmd[cmd.index("-m") + 1] == "gpt-x-1" and ans.resolved_model == "gpt-x-1"

    def test_pinned_but_different_or_absent_id_is_an_error(self, monkeypatch):
        v = _codex_vendor(codex_model="gpt-x-1")
        with pytest.raises(RuntimeError, match="model mismatch"):
            _ask_codex(monkeypatch, _stream(model="gpt-y-2"), v)
        with pytest.raises(RuntimeError, match="model unverifiable"):
            _ask_codex(monkeypatch, _stream(), v)

    def test_a_stream_naming_two_models_is_an_error(self, monkeypatch):
        out = _stream(model="gpt-x-1", extra=[{"type": "turn.completed", "model": "gpt-y-2", "usage": {}}])
        with pytest.raises(RuntimeError, match="several models"):
            _ask_codex(monkeypatch, out)

    @pytest.mark.parametrize("notice", ["You've hit your usage limit.", "Rate limit reached, try again later"])
    def test_quota_notice_is_an_error(self, monkeypatch, notice):
        with pytest.raises(RuntimeError, match="quota/limit notice"):
            _ask_codex(monkeypatch, _stream(text=notice))

    @pytest.mark.parametrize("event", [{"type": "error", "message": "usage limit"}, {"type": "turn.failed", "error": {}}])
    def test_error_events_are_errors(self, monkeypatch, event):
        with pytest.raises(RuntimeError, match="codex reported an error"):
            _ask_codex(monkeypatch, _stream(extra=[event]))

    def test_prose_stdout_and_empty_answer_are_errors_not_answers(self, monkeypatch):
        with pytest.raises(RuntimeError, match="no JSON event stream"):
            _ask_codex(monkeypatch, "ANSWER: A")
        with pytest.raises(RuntimeError, match="no completed agent message"):
            _ask_codex(monkeypatch, json.dumps({"type": "turn.completed", "usage": {}}))

    def test_nonzero_exit_is_an_error(self, monkeypatch):
        with pytest.raises(RuntimeError, match="boom"):
            _ask_codex(monkeypatch, "", code=2, err="boom")

    def test_manifest_records_the_codex_pin(self):
        m = panel.panel_manifest([{"id": "1", "prompt": "p"}], [_codex_vendor(codex_model="gpt-x-1")])
        assert m["vendors"][0]["codex_pinned_model"] == "gpt-x-1"


FIXTURES = Path(__file__).parent / "fixtures" / "ask_vendor_envelopes"


def _recorded(name):
    return json.loads((FIXTURES / name).read_text())


def _rollout(home, thread_id, model, *, day="2026/10/02"):
    d = home / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "session_meta", "payload": {"id": thread_id}}, {"type": "turn_context", "payload": {"model": model}}]
    (d / f"rollout-2026-10-02T13-57-06-{thread_id}.jsonl").write_text("\n".join(json.dumps(x) for x in lines))


TID = "00000000-0000-4000-8000-000000000010"


class TestCodexModelIdFromRollout:
    """The codex --json stream carries no model id (observed 2026-10-02); the id is the rollout's turn_context."""

    def _stream_tid(self, **kw):
        return _stream(**kw).replace("th_1", TID)

    def test_resolved_model_is_read_from_the_rollout_file(self, monkeypatch, tmp_path):
        _rollout(tmp_path, TID, "gpt-6-astra")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        ans, _ = _ask_codex(monkeypatch, self._stream_tid(), _codex_vendor(codex_model="gpt-6-astra"))
        assert ans.resolved_model == "gpt-6-astra" and ans.billed_models == ("gpt-6-astra",)

    def test_pin_matching_the_rollout_passes_and_a_different_one_is_an_error(self, monkeypatch, tmp_path):
        _rollout(tmp_path, TID, "gpt-6-astra")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        ans, _ = _ask_codex(monkeypatch, self._stream_tid(), _codex_vendor(codex_model="gpt-6-astra"))
        assert ans.resolved_model == "gpt-6-astra"
        with pytest.raises(RuntimeError, match="model mismatch"):
            _ask_codex(monkeypatch, self._stream_tid(), _codex_vendor(codex_model="gpt-x-1"))

    def test_pin_with_no_rollout_found_is_an_error_not_a_pass(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="model unverifiable"):
            _ask_codex(monkeypatch, self._stream_tid(), _codex_vendor(codex_model="gpt-6-astra"))

    def test_stream_model_disagreeing_with_rollout_is_an_error(self, monkeypatch, tmp_path):
        _rollout(tmp_path, TID, "gpt-6-astra")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="several models"):
            _ask_codex(monkeypatch, self._stream_tid(model="gpt-y-2"))

    def test_hostile_thread_id_cannot_widen_the_glob(self, monkeypatch, tmp_path):
        _rollout(tmp_path, TID, "gpt-6-astra")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert panel._codex_rollout_models("*") == set()
        assert panel._codex_rollout_models("../../x") == set()


class TestRecordedEnvelopes:
    """RECORDED (not constructed) 2026-10-02 from real trivial-prompt calls; pins the observed shapes."""

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_real_claude_envelope_parses(self, seat, monkeypatch, n):
        rec = _recorded(f"claude_{n}.json")
        ans, _ = _ask_claude(monkeypatch, rec["stdout"], _claude_vendor(model="claude-sonnet-5"))
        assert ans.resolved_model == "claude-sonnet-5" and ans.billed_models == ("claude-sonnet-5",)
        assert ans.cost_usd is not None and ans.cost_usd > 0 and ans.text

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_real_codex_stream_has_no_model_and_rollout_supplies_it(self, monkeypatch, tmp_path, n):
        rec = _recorded(f"codex_{n}.json")
        kinds = [json.loads(line)["type"] for line in rec["stdout"].splitlines() if line.strip()]
        assert kinds == ["thread.started", "turn.started", "item.completed", "turn.completed"]
        assert all('"model"' not in line for line in rec["stdout"].splitlines())
        tid = json.loads(rec["stdout"].splitlines()[0])["thread_id"]
        _rollout(tmp_path, tid, rec["rollout_turn_context_trimmed"]["payload"]["model"])
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        ans, _ = _ask_codex(monkeypatch, rec["stdout"], _codex_vendor(codex_model="gpt-6-astra"))
        assert ans.resolved_model == "gpt-6-astra" and ans.cost_usd is None and ans.text
