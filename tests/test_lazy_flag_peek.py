# ruff: noqa: E501
"""#986: the router reads the optional slices' flags ONCE (`partner._peek_flags`) and imports a slice's config reader only for a flag
that reads boolean true (YAML true/yes/on), or, for A2 only, when an existing A2 store folder exists (so stored data stays deletable
with the flag off). A flag-off process with no store folder imports exactly what the released tree imports. Real files and a fresh
interpreter for the off arm and for the store-folder arms; real YAML rows pin how each way of writing a flag is read; recording
loaders for the per-flag arms."""

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


# ---- real YAML (not a patched peek): what the peek and every loader do with each way of writing a flag -----------------------------------


_REAL_YAML_FLAGS = (
    ("wf_a1_draft_citation_check", dcc, "load_draft_check_config"),
    ("wf_a2_span_index", msc, "load_matter_spans_config"),
    ("wf_a3_chronology", cc, "load_chronology_config"),
    ("wf_a5_ingredient_agenda", ac, "load_agenda_config"),
    ("wf_b3_s138_checklist", scc, "load_s138_checklist_config"),
)


@pytest.mark.parametrize(
    ("written", "on"),
    [
        ("true", True),
        ("True", True),
        ("yes", True),  # unquoted yes/on are YAML booleans: pinned as the CURRENT behaviour of the peek AND of every loader
        ("on", True),
        ("false", False),
        ("no", False),
        ('"true"', False),  # a quoted string is a string, not a boolean
        ('"yes"', False),
        ("1", False),
        ("null", False),
        ("[true]", False),
    ],
)
@pytest.mark.parametrize(("key", "module", "fn"), _REAL_YAML_FLAGS)
def test_real_yaml_the_peek_and_the_loader_agree_for_every_way_of_writing_a_flag(
    tmp_path: Path, key: str, module: Any, fn: str, written: str, on: bool
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(f"{key}: {written}\n", encoding="utf-8")
    assert (partner._peek_flags(tmp_path).get(key) is True) is on
    assert (getattr(module, fn)(tmp_path) is not None) is on


def test_real_yaml_a_missing_key_is_off_in_the_peek_and_in_every_loader(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text("some_other_key: true\n", encoding="utf-8")
    for key, module, fn in _REAL_YAML_FLAGS:
        assert partner._peek_flags(tmp_path).get(key) is None
        assert getattr(module, fn)(tmp_path) is None


# ---- the A2 store-folder arm in a FRESH interpreter (the ONE intended flag-off import) ------------------------------------------------


_STORE_ARM_CODE = (
    "import sys\n"
    "sys.path.insert(0, {src!r})\n"
    "from pathlib import Path\n"
    "from pravrudhi.api.partner import PartnerApiConfig, build_partner_router\n"
    "r = build_partner_router(Path({root!r}), agent_factory=lambda _r: None,"
    " config=PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False))\n"
    "mods = {mods!r}\n"
    "print(','.join(m for m in mods if m in sys.modules))\n"
    "print(any(getattr(x, 'path', '').endswith('/matter-spans/delete') for x in r.routes))\n"
)
_A2_MODULES = ("pravrudhi.application.matter_spans_config", "pravrudhi.application.matter_spans")
_OTHER_MODULES = tuple(m for m in READER_MODULES + SLICE_MODULES if m not in _A2_MODULES)


def _store_arm(root: Path, env: dict[str, str]) -> tuple[list[str], list[str], bool]:
    import os

    code = _STORE_ARM_CODE.format(src=str(REPO / "src"), root=str(root), mods=list(_A2_MODULES + _OTHER_MODULES))
    clean = {k: v for k, v in os.environ.items() if k != msc.DIR_ENV}
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=REPO, check=True, env={**clean, **env}
    ).stdout.splitlines()
    loaded = [m for m in out[0].split(",") if m]
    return [m for m in loaded if m in _A2_MODULES], [m for m in loaded if m not in _A2_MODULES], out[1] == "True"


@pytest.mark.parametrize("flag", [None, "false"])
@pytest.mark.parametrize("how", ["default", "yaml", "env"])
def test_fresh_interpreter_an_existing_a2_store_folder_imports_only_a2_and_keeps_the_delete_route(
    tmp_path: Path, how: str, flag: str | None
) -> None:
    root = tmp_path / "root"
    (root / "configs").mkdir(parents=True)
    store = root / "matter_spans" if how == "default" else tmp_path / "elsewhere"
    store.mkdir()
    yaml_lines = [] if flag is None else [f"wf_a2_span_index: {flag}"]
    env: dict[str, str] = {}
    if how == "yaml":
        yaml_lines.append(f"matter_spans_dir: {store}")
    if how == "env":
        env[msc.DIR_ENV] = str(store)
    (root / "configs" / "partner_api.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    a2, others, delete_route = _store_arm(root, env)
    assert sorted(a2) == sorted(_A2_MODULES) and others == [] and delete_route


@pytest.mark.parametrize("flag", [None, "false"])
def test_fresh_interpreter_with_no_store_folder_neither_a2_module_is_imported_and_there_is_no_delete_route(
    tmp_path: Path, flag: str | None
) -> None:
    root = tmp_path / "root"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "partner_api.yaml").write_text("" if flag is None else f"wf_a2_span_index: {flag}\n", encoding="utf-8")
    a2, others, delete_route = _store_arm(root, {})
    assert a2 == [] and others == [] and not delete_route
