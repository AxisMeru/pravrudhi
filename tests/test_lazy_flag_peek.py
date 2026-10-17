# ruff: noqa: E501
"""#986: the router reads the optional slices' flags ONCE (`partner._peek_flags`) and imports a slice's config reader only for a flag
that reads true, so a flag-off process imports exactly what the released tree imports. Real files and a fresh interpreter for the
off arm; recording loaders for the per-flag arms."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.api import identity, partner, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import agenda_config as ac
from pravrudhi.application import chronology_config as cc
from pravrudhi.application import draft_check_config as dcc
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application import s138_checklist_config as scc
from tests.flag_peek_util import ALL_FLAG_KEYS
from tests.test_api_partner import _agent, _proof_script

REPO = Path(__file__).resolve().parent.parent
READER_MODULES = (
    "pravrudhi.application.draft_check_config",
    "pravrudhi.application.matter_spans_config",
    "pravrudhi.application.s138_checklist_config",
    "pravrudhi.application.chronology_config",
    "pravrudhi.application.agenda_config",
)
SLICE_MODULES = (
    "pravrudhi.application.draft_check",
    "pravrudhi.application.matter_spans",
    "pravrudhi.application.s138_checklist",
    "pravrudhi.application.chronology",
    "pravrudhi.application.chronology_matter",
    "pravrudhi.application.agenda",
    "pravrudhi.application.agenda_matter",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building a router with a slice on rebinds the gate lists; restore them so no later test sees them."""
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


# ---- the peek ------------------------------------------------------------------------------------------------------------------------


def test_the_peek_is_one_mapping_read_and_never_raises(tmp_path: Path) -> None:
    assert partner._peek_flags(tmp_path / "nowhere") == {}
    (tmp_path / "configs").mkdir()
    f = tmp_path / "configs" / "partner_api.yaml"
    f.write_text("wf_a3_chronology: true\nother: 3\n", encoding="utf-8")
    assert partner._peek_flags(tmp_path) == {"wf_a3_chronology": True, "other": 3}
    f.write_text("- a\n- b\n", encoding="utf-8")
    assert partner._peek_flags(tmp_path) == {}
    f.write_text("wf_a3_chronology: [unclosed", encoding="utf-8")
    assert partner._peek_flags(tmp_path) == {}


def test_the_flag_keys_the_router_peeks_are_the_modules_own_flag_constants() -> None:
    assert set(ALL_FLAG_KEYS) == {dcc.FLAG, msc.FLAG, cc.FLAG, ac.FLAG, scc.FLAG}


def test_the_mirrored_store_dir_equals_the_modules_store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "configs").mkdir()
    f = tmp_path / "configs" / "partner_api.yaml"
    monkeypatch.delenv(msc.DIR_ENV, raising=False)
    f.write_text("wf_a2_span_index: false\n", encoding="utf-8")
    assert (
        partner._peek_matter_store_dir(tmp_path, partner._peek_flags(tmp_path))
        == msc.store_dir(tmp_path)
        == tmp_path / "matter_spans"
    )
    f.write_text(f"matter_spans_dir: {tmp_path / 'elsewhere'}\n", encoding="utf-8")
    assert (
        partner._peek_matter_store_dir(tmp_path, partner._peek_flags(tmp_path))
        == msc.store_dir(tmp_path)
        == tmp_path / "elsewhere"
    )
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "from-env"))
    assert (
        partner._peek_matter_store_dir(tmp_path, partner._peek_flags(tmp_path))
        == msc.store_dir(tmp_path)
        == tmp_path / "from-env"
    )
    monkeypatch.setenv(msc.DIR_ENV, "   ")
    assert (
        partner._peek_matter_store_dir(tmp_path, partner._peek_flags(tmp_path))
        == msc.store_dir(tmp_path)
        == tmp_path / "elsewhere"
    )


# ---- flag OFF: nothing of the slices is imported (fresh interpreter, the real shipped config) -----------------------------------------


def test_with_every_flag_off_no_reader_and_no_slice_module_is_imported(tmp_path: Path) -> None:
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        f"os.environ['PRAVRUDHI_MATTER_SPANS_DIR'] = {str(tmp_path / 'no-such-store')!r}\n"
        "from pravrudhi.api.server import create_app\n"
        f"create_app({str(REPO)!r})\n"
        f"mods = {list(READER_MODULES + SLICE_MODULES)!r}\n"
        "print(','.join(m for m in mods if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=REPO, check=True)
    assert out.stdout.strip() == "", out.stdout  # the names of any slice module found loaded would be printed


# ---- one flag on: only that slice's reader is imported/called -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "module", "fn"),
    [
        ("wf_a1_draft_citation_check", dcc, "load_draft_check_config"),
        ("wf_a2_span_index", msc, "load_matter_spans_config"),
        ("wf_a3_chronology", cc, "load_chronology_config"),
        ("wf_a5_ingredient_agenda", ac, "load_agenda_config"),
        ("wf_b3_s138_checklist", scc, "load_s138_checklist_config"),
    ],
)
def test_a_flag_that_reads_true_calls_only_its_own_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, module: Any, fn: str
) -> None:
    calls: list[str] = []
    for mod, name in (
        (dcc, "load_draft_check_config"),
        (msc, "load_matter_spans_config"),
        (cc, "load_chronology_config"),
        (ac, "load_agenda_config"),
        (scc, "load_s138_checklist_config"),
    ):
        monkeypatch.setattr(mod, name, lambda _root, _n=name: (calls.append(_n), None)[1])
    monkeypatch.setattr(partner, "_peek_flags", lambda _root: {key: True})
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "no-such-store"))
    agent = _agent(tmp_path, _proof_script())
    build_partner_router(
        tmp_path,
        agent_factory=lambda _r: agent,
        config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False),
    )
    assert calls == [fn]


def test_a_flag_of_false_or_a_look_alike_calls_no_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for mod, name in ((dcc, "load_draft_check_config"), (msc, "load_matter_spans_config"), (cc, "load_chronology_config")):
        monkeypatch.setattr(mod, name, lambda _root, _n=name: (calls.append(_n), None)[1])
    monkeypatch.setattr(
        partner, "_peek_flags", lambda _root: dict(zip(ALL_FLAG_KEYS, (False, "true", 1, None, "yes"), strict=True))
    )
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "no-such-store"))
    agent = _agent(tmp_path, _proof_script())
    build_partner_router(
        tmp_path,
        agent_factory=lambda _r: agent,
        config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False),
    )
    assert calls == []


def test_an_a2_store_folder_keeps_the_reader_imported_so_data_stays_deletable_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: (calls.append("a2"), None)[1])
    monkeypatch.setattr(partner, "_peek_flags", lambda _root: {})
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv(msc.DIR_ENV, str(store))
    agent = _agent(tmp_path, _proof_script())
    router = build_partner_router(
        tmp_path,
        agent_factory=lambda _r: agent,
        config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False),
    )
    assert calls == ["a2"]
    assert any(getattr(r, "path", "").endswith("/matter-spans/delete") for r in router.routes)


def test_the_ownership_line_readers_are_called_only_when_both_slices_are_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ac, "load_agenda_config", lambda _root: ac.AgendaConfig())
    monkeypatch.setattr(cc, "load_chronology_config", lambda _root: (calls.append("a3"), None)[1])
    monkeypatch.setattr(scc, "load_s138_checklist_config", lambda _root: (calls.append("b3"), None)[1])
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: None)
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "no-such-store"))
    agent = _agent(tmp_path, _proof_script())
    # A5 alone: no reader of A3/B3 is called; A5 + A3: only A3's OWN block calls its reader (no ownership call without B3)
    for flags, expect in (
        ({"wf_a5_ingredient_agenda": True}, []),
        ({"wf_a5_ingredient_agenda": True, "wf_a3_chronology": True}, ["a3"]),
    ):
        calls.clear()
        monkeypatch.setattr(partner, "_peek_flags", lambda _root, _f=flags: _f)
        build_partner_router(
            tmp_path,
            agent_factory=lambda _r: agent,
            config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False),
        )
        assert calls == expect
    calls.clear()
    monkeypatch.setattr(
        partner,
        "_peek_flags",
        lambda _root: {"wf_a5_ingredient_agenda": True, "wf_a3_chronology": True, "wf_b3_s138_checklist": True},
    )
    build_partner_router(
        tmp_path,
        agent_factory=lambda _r: agent,
        config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False),
    )
    # A3 own block + the ownership line (A3 answers None, so B3 is not asked a second time): a3, a3, then B3's own block once
    assert sorted(calls) == ["a3", "a3", "b3"]
