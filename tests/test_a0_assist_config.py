"""A0 assist config: fail closed on every key, one test per rule arm."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pravrudhi.application.a0_assist import config as C

REPO = Path(__file__).resolve().parent.parent


#: The shipped file turns the route on (0.6.5); every rule test below starts from the DARK form of it.
DARK = {"assist_route_enabled": False, "agent_id": None, "agent_version": None, "environment_id": None}


def base(**over):
    cfg = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    cfg.update(DARK)
    cfg.update(over)
    return cfg


def enabled(**over):
    cfg = base(
        assist_route_enabled=True, agent_id="agent_x", agent_version=2, environment_id="env_x", store_ca_file="/app/certs/ca.crt"
    )
    cfg.update(over)
    return cfg


def test_the_shipped_config_parses_enabled_with_the_product_agent_ids():
    # Lead-2's go, 14 Oct: ids from MANIFEST_PRODUCT_agent_env.json (sha256 5fb55e20ceb3...), ids only, no secrets
    shipped = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    c = C.parse(shipped)
    assert c.route_enabled is True
    assert (c.agent_id, c.agent_version, c.environment_id) == (
        "agent_012zKjNLSVQoivwTqEPBkVUZ", 1, "env_01MAG6qEGC999A8r8MogP5bt"
    )
    assert c.store_ca_file == "/opt/pravrudhi/certs/supabase-prod-ca.crt" and c.delete_after_run is True
    assert not any("sk-" in str(v) for v in shipped.values())  # no key material in the file


def test_the_dark_form_of_the_shipped_config_parses_dark():
    c = C.parse(base())
    assert (
        c.route_enabled is False
        and c.session_budget_cents == 25
        and c.daily_ceiling_cents == 400
        and c.max_pending_deletes == 5
        and c.retention_max_hours == 2
    )
    assert c.agent_id is None and c.workspace_id_env == "ANTHROPIC_WORKSPACE_ID" and c.store_dsn_env == "SUPABASE_ASSIST_DB_URL"
    assert (c.runs_per_user_per_day, c.runs_per_org_per_day, c.per_ip_per_minute) == (20, 100, 5)


def test_an_enabled_config_with_ids_parses():
    c = C.parse(enabled())
    assert c.route_enabled and (c.agent_id, c.agent_version, c.environment_id) == ("agent_x", 2, "env_x")


TIER_ONLY = {
    "enabled",
    "force_unavailable",
    "budget_floor_usd",
    "budget_file",
    "failure_ttl_s",
}  # read by assistant_tier (#388), not by the A0 config


@pytest.mark.parametrize("key", sorted(set(base()) - TIER_ONLY))
def test_every_key_is_required(key):
    cfg = base()
    del cfg[key]
    with pytest.raises(C.AssistConfigError):
        C.parse(cfg)


@pytest.mark.parametrize(
    "raw,cents",
    [
        (0.25, 25),
        (4.00, 400),
        (4, 400),
        (0.01, 1),
        (1, 100),
        ("x", None),
        (0.255, None),
        (0, None),
        (-1, None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
        (None, None),
        (101, None),
    ],
)
def test_usd_to_cents_exact_or_refused(raw, cents):
    if cents is None:
        with pytest.raises(C.AssistConfigError):
            C.usd_to_cents(raw, "k", 10_000)
    else:
        assert C.usd_to_cents(raw, "k", 10_000) == cents


def test_session_budget_is_capped_at_one_usd_and_ceiling_at_100():
    with pytest.raises(C.AssistConfigError):
        C.parse(base(session_budget_usd=1.01))
    with pytest.raises(C.AssistConfigError):
        C.parse(base(daily_ceiling_usd=100.01))
    assert C.parse(base(session_budget_usd=1.0, daily_ceiling_usd=100.0)).daily_ceiling_cents == 10_000


def test_session_budget_above_the_daily_ceiling_is_refused():
    with pytest.raises(C.AssistConfigError):
        C.parse(base(session_budget_usd=0.5, daily_ceiling_usd=0.25))
    assert C.parse(base(session_budget_usd=0.25, daily_ceiling_usd=0.25)).session_budget_cents == 25  # equal is fine


@pytest.mark.parametrize(
    "key,bad",
    [
        ("call_timeout_s", 0),
        ("call_timeout_s", 601),
        ("call_timeout_s", "60"),
        ("call_timeout_s", True),
        ("call_timeout_s", float("nan")),
        ("store_timeout_s", 0),
        ("store_timeout_s", 31),
        ("abandon_after_min", 0),
        ("abandon_after_min", 1.5),
        ("abandon_after_min", True),
        ("max_concurrent_sessions", 0),
        ("max_concurrent_sessions", 51),
        ("assist_runs_per_user_per_day", 0),
        ("assist_runs_per_org_per_day", 0),
        ("assist_per_ip_per_minute", 0),
        ("sweep_interval_min", 0),
        ("max_pending_deletes", 0),
        ("retention_max_hours", 0),
        ("retention_max_hours", -1),
        ("delete_after_run", "yes"),
        ("assist_route_enabled", 1),
    ],
)
def test_bad_values_are_refused(key, bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(**{key: bad}))


@pytest.mark.parametrize("bad", [[], None, "ANTHROPIC_API_KEY", [1], ["not valid name"]])
def test_key_env_names_must_be_a_list_of_names(bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(key_env_names=bad))


@pytest.mark.parametrize("key", ["workspace_id_env", "store_dsn_env", "sweeper_dsn_env"])
@pytest.mark.parametrize("bad", ["lowercase", "has space", "", None, 5, "postgres://user:pw@host/db"])
def test_env_names_must_be_names_never_values(key, bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(**{key: bad}))


@pytest.mark.parametrize("bad", [0, -1, "2", True, 1.5])
def test_agent_version_must_be_a_positive_integer_or_null(bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(agent_version=bad))


@pytest.mark.parametrize("key", ["agent_id", "environment_id"])
@pytest.mark.parametrize("bad", ["", "  ", 5, [], True])
def test_agent_and_environment_ids_must_be_strings_or_null(key, bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(**{key: bad}))


@pytest.mark.parametrize("missing", ["agent_id", "agent_version", "environment_id"])
def test_enabled_route_needs_every_product_id(missing):
    cfg = enabled()
    cfg[missing] = None
    with pytest.raises(C.AssistConfigError):
        C.parse(cfg)


def test_enabled_route_refuses_delete_after_run_false_but_a_dark_config_may_carry_it():
    with pytest.raises(C.AssistConfigError):
        C.parse(enabled(delete_after_run=False))
    assert C.parse(base(delete_after_run=False)).route_enabled is False


def test_enabled_route_abandon_must_cover_the_call_timeout():
    with pytest.raises(C.AssistConfigError):
        C.parse(enabled(abandon_after_min=1, call_timeout_s=61))
    assert C.parse(enabled(abandon_after_min=1, call_timeout_s=60)).abandon_after_min == 1  # equal is fine


def test_not_a_mapping():
    for raw in (None, [], "x", 5):
        with pytest.raises(C.AssistConfigError):
            C.parse(raw)


def write(tmp_path: Path, raw: str | bytes) -> Path:
    (tmp_path / "configs").mkdir(exist_ok=True)
    p = tmp_path / "configs" / "assistant_tier.yaml"
    p.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    return tmp_path


def test_load_reads_the_file_and_load_or_none_never_raises(tmp_path):
    root = write(tmp_path, yaml.safe_dump(base()))
    assert C.load(root).route_enabled is False
    cfg, why = C.load_or_none(root)
    assert cfg is not None and why is None
    for raw in (b"\xff\xfe not utf8", "x: " + "[" * 100000, "a: [unclosed", "- a\n- b\n"):
        root2 = write(tmp_path, raw)
        cfg, why = C.load_or_none(root2)
        assert cfg is None and why


def test_load_with_no_config_file_is_none(tmp_path):
    cfg, why = C.load_or_none(tmp_path)
    assert cfg is None and "cannot be read" in why


def test_the_module_has_no_network_or_driver_imports():
    src = Path(C.__file__).read_text()
    for bad in ("httpx", "pg8000", "psycopg", "urllib", "socket", "anthropic"):
        assert bad not in src


def test_store_ca_file_is_null_or_a_path_string():
    assert C.parse(base(store_ca_file=None)).store_ca_file is None
    assert C.parse(base(store_ca_file="/app/certs/supabase-prod-ca.crt")).store_ca_file == "/app/certs/supabase-prod-ca.crt"


@pytest.mark.parametrize("bad", ["", "  ", 5, True, ["x"]])
def test_store_ca_file_refuses_a_non_string(bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(store_ca_file=bad))


def test_canary_defaults_are_hourly_and_three_hours():
    c = C.parse(base())
    assert (c.canary_interval_min, c.canary_stale_min) == (60, 180)


@pytest.mark.parametrize("key", ["canary_interval_min", "canary_stale_min"])
@pytest.mark.parametrize("bad", [0, -1, "60", True, 1.5, None])
def test_canary_timings_refuse_non_integers(key, bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(**{key: bad}))


def test_a_stale_threshold_not_above_the_interval_is_refused():
    with pytest.raises(C.AssistConfigError):
        C.parse(base(canary_interval_min=60, canary_stale_min=60))


def _serve(monkeypatch, tmp_path, cfg):
    f = tmp_path / "assistant_tier.yaml"
    f.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(C, "config_file", lambda root, name: f)


@pytest.mark.parametrize(
    "key", sorted(set(base()) - {"enabled", "force_unavailable", "failure_ttl_s", "budget_floor_usd", "budget_file"})
)
def test_a_missing_key_keeps_the_route_off_never_a_default_in_code(key, monkeypatch, tmp_path):
    cfg = base(assist_route_enabled=True, agent_id="a", agent_version=1, environment_id="e")
    del cfg[key]
    _serve(monkeypatch, tmp_path, cfg)
    got, why = C.load_or_none(tmp_path)
    assert got is None and why


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("assist_route_enabled", "yes"),
        ("daily_ceiling_usd", "four"),
        ("session_budget_usd", 0.255),
        ("max_pending_deletes", "5"),
        ("canary_stale_min", None),
        ("store_ca_file", 7),
    ],
)
def test_a_malformed_value_keeps_the_route_off(key, bad, monkeypatch, tmp_path):
    cfg = base(assist_route_enabled=True, agent_id="a", agent_version=1, environment_id="e")
    cfg[key] = bad
    _serve(monkeypatch, tmp_path, cfg)
    got, why = C.load_or_none(tmp_path)
    assert got is None and why


def test_an_unreadable_or_non_mapping_config_keeps_the_route_off(monkeypatch, tmp_path):
    f = tmp_path / "assistant_tier.yaml"
    f.write_bytes(b"\xff\xfe not yaml")
    monkeypatch.setattr(C, "config_file", lambda root, name: f)
    assert C.load_or_none(tmp_path)[0] is None
    f.write_text("- just\n- a list\n")
    assert C.load_or_none(tmp_path)[0] is None


def test_an_enabled_route_refuses_to_parse_without_the_pinned_ca_path():
    with pytest.raises(C.AssistConfigError):
        C.parse(enabled(store_ca_file=None))
    assert C.parse(enabled()).store_ca_file == "/app/certs/ca.crt"
    assert C.parse(base(store_ca_file=None)).route_enabled is False  # dark with a null CA is fine


def test_the_shipped_ca_path_the_dockerfile_copy_and_the_pinned_sha_agree():
    import hashlib
    import re

    shipped = C.parse(base()).store_ca_file
    docker = (REPO / "deploy" / "docker" / "Dockerfile").read_text()
    cert = REPO / "deploy" / "docker" / "certs" / "supabase-prod-ca.crt"
    assert shipped == "/opt/pravrudhi/certs/supabase-prod-ca.crt"
    assert f"COPY certs/supabase-prod-ca.crt {shipped}" in docker
    pinned = re.search(r"ARG SUPABASE_CA_SHA256=([0-9a-f]{64})", docker).group(1)
    assert hashlib.sha256(cert.read_bytes()).hexdigest() == pinned
    assert cert.read_text().count("BEGIN CERTIFICATE") == 1
    assert 'pip install --no-cache-dir --quiet "$kernel" "pravrudhi[assist] @ $engine"' in docker  # the image installs the extra


# ---- per-workflow agents (workflow hook) -------------------------------------------------------------------------------------
HP = {"agent_id": "agent_hp", "agent_version": 3, "environment_id": "env_hp"}
IN = {"agent_id": "agent_in", "agent_version": 1, "environment_id": "env_in"}


def test_workflows_is_a_required_key_and_the_shipped_file_names_the_two_pinned_agents():
    cfg = base()
    del cfg["workflows"]
    with pytest.raises(C.AssistConfigError, match="workflows"):
        C.parse(cfg)
    shipped = C.parse(base()).workflows
    assert shipped == {
        "hearing_prep": C.WorkflowAgent("agent_01JvEU8nHU2JpQtUGEzrmmkX", 1, "env_018P77Nk1DHCMD1tatmY4smL"),
        "intake": C.WorkflowAgent("agent_019uQC3eQvCfo6viBX6xpErE", 1, "env_01NqsSVpfUrUMX6PiS7fGG9W"),
    }
    # one environment per workflow, none shared with screening or each other
    envs = [a.environment_id for a in shipped.values()] + [C.parse(base()).environment_id]
    assert len(set(envs)) == len(envs)


def test_a_configured_workflow_has_its_own_agent_and_screening_keeps_the_top_level_keys():
    c = C.parse(enabled(workflows={"hearing_prep": HP, "intake": IN}))
    assert c.agent_for("hearing_prep") == C.WorkflowAgent("agent_hp", 3, "env_hp")
    assert c.agent_for("intake") == C.WorkflowAgent("agent_in", 1, "env_in")
    assert c.agent_for("screening") == C.WorkflowAgent("agent_x", 2, "env_x")
    assert c.all_agent_ids() == ("agent_x", "agent_hp", "agent_in")


def test_a_workflow_without_an_entry_has_no_agent_and_stays_dark():
    c = C.parse(enabled(workflows={"hearing_prep": HP}))
    assert c.agent_for("intake") is None and c.agent_for("unknown") is None
    assert C.parse(base()).agent_for("screening") is None  # dark config: no product agent either


def test_all_agent_ids_drops_duplicates_and_keeps_screening_first():
    same = {**HP, "agent_id": "agent_x"}
    assert C.parse(enabled(workflows={"hearing_prep": same})).all_agent_ids() == ("agent_x",)


@pytest.mark.parametrize("bad", [
    {"screening": HP},  # screening is the top-level keys
    {"nonsense": HP},
    {"hearing_prep": {"agent_id": "a", "agent_version": 1}},  # missing key
    {"hearing_prep": {**HP, "extra": 1}},
    {"hearing_prep": {**HP, "agent_version": 0}},
    {"hearing_prep": {**HP, "agent_version": True}},
    {"hearing_prep": {**HP, "agent_version": "2"}},
    {"hearing_prep": {**HP, "agent_id": ""}},
    {"hearing_prep": {**HP, "environment_id": "  "}},
    {"hearing_prep": {**HP, "agent_id": 5}},
    {"hearing_prep": "agent"},
    ["hearing_prep"],
    "x",
    5,
])
def test_a_malformed_workflows_block_is_refused(bad):
    with pytest.raises(C.AssistConfigError):
        C.parse(base(workflows=bad))
    with pytest.raises(C.AssistConfigError):
        C.parse(enabled(workflows=bad))


def test_workflows_null_means_none():
    assert C.parse(base(workflows=None)).workflows == {}


def test_the_shipped_file_serves_both_workflows_and_dropping_the_entries_makes_them_dark_again():
    raw = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    shipped = C.parse(raw)
    assert shipped.agent_for("intake") is not None and shipped.agent_for("hearing_prep") is not None
    assert shipped.agent_for("screening") == C.WorkflowAgent("agent_012zKjNLSVQoivwTqEPBkVUZ", 1, "env_01MAG6qEGC999A8r8MogP5bt")
    assert len(shipped.all_agent_ids()) == 3  # the orphan scan covers all three agents
    assert len(set(shipped.all_agent_ids())) == 3  # three different agents, none pasted twice
    dark = C.parse({**raw, "workflows": {}})
    assert dark.agent_for("intake") is None and dark.agent_for("hearing_prep") is None and len(dark.all_agent_ids()) == 1


def test_the_shipped_wall_limit_and_event_cap_cover_a_five_contract_run_with_margin():
    """Dev measurement (14 Oct): five contracts = about 57 s of agent turns and 232 stream events. 60 s / 400 left no margin."""
    raw = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    c = C.parse(raw)
    assert (c.call_timeout_s, c.max_events) == (150.0, 1000)
    assert c.call_timeout_s >= 2 * 57 and c.max_events >= 2 * 232
    # the sweeper never abandons a run still allowed to run: wall limit + interrupt wait + a minute for the delete
    assert c.abandon_after_min * 60 >= c.call_timeout_s + c.interrupt_wait_s + 60
    assert c.call_timeout_s < 320  # inside the app's own request timeout
