# ruff: noqa: E501
"""The five flag-off config readers (A1, A2, A3, A5, B3) all answer None (off) in every flag-OFF situation: the shipped config,
no file, a broken file, the flag false, and every value that is not the boolean true. One test over all five, so a new
reader has to be added here by name (prabhasa-nyaya#984: the release condition is OFF behaviour, not import lists)."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import agenda_config, chronology_config, draft_check_config, matter_spans_config, s138_checklist_config

REPO = Path(__file__).resolve().parent.parent

READERS: list[tuple[str, Callable[[Path], Any], Callable[[Any], Any], str]] = [
    ("A1", draft_check_config.load_draft_check_config, draft_check_config.parse_draft_check_config, draft_check_config.FLAG),
    ("A2", matter_spans_config.load_matter_spans_config, matter_spans_config.parse_matter_spans_config, matter_spans_config.FLAG),
    ("A3", chronology_config.load_chronology_config, chronology_config.parse_chronology_config, chronology_config.FLAG),
    ("A5", agenda_config.load_agenda_config, agenda_config.parse_agenda_config, agenda_config.FLAG),
    ("B3", s138_checklist_config.load_s138_checklist_config, s138_checklist_config.parse_s138_checklist_config, s138_checklist_config.FLAG),
]
IDS = [r[0] for r in READERS]


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
def test_the_shipped_config_has_every_flag_off(name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str) -> None:
    assert load(REPO) is None, name


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
def test_no_file_or_a_broken_file_is_off_and_never_raises(
    name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str, tmp_path: Path
) -> None:
    assert load(tmp_path / "nowhere") is None
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(f"{flag}: [unclosed", encoding="utf-8")
    assert load(tmp_path) is None


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "yes", "on", [], {}])
def test_every_value_that_is_not_the_boolean_true_is_off(
    name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str, value: Any
) -> None:
    assert parse({flag: value}) is None


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
@pytest.mark.parametrize("body", [None, {}, [], "text", 5])
def test_a_body_without_the_key_or_of_the_wrong_shape_is_off(
    name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str, body: Any
) -> None:
    assert parse(body) is None


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
def test_only_the_boolean_true_turns_a_reader_on(name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str) -> None:
    assert parse({flag: True}) is not None


def test_the_five_readers_are_the_five_flagged_modules() -> None:
    """Adding a config reader means adding it here (and to the declared flag-off import list)."""
    assert IDS == ["A1", "A2", "A3", "A5", "B3"]


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
@pytest.mark.parametrize("literal", ["false", "null", "1", '"true"', "[]"])
def test_a_file_with_the_key_set_to_anything_but_the_boolean_true_is_off(
    name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str, literal: str, tmp_path: Path
) -> None:
    """The loader itself (file -> yaml -> parse), not only the pure parser: the yaml spellings of false, null, 1, "true"."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(f"{flag}: {literal}\n", encoding="utf-8")
    assert load(tmp_path) is None
    (tmp_path / "configs" / "partner_api.yaml").write_text("some_other_key: true\n", encoding="utf-8")
    assert load(tmp_path) is None  # the key is missing


@pytest.mark.parametrize(("name", "load", "parse", "flag"), READERS, ids=IDS)
def test_the_loader_turns_a_reader_on_only_for_the_yaml_boolean_true(
    name: str, load: Callable[[Path], Any], parse: Callable[[Any], Any], flag: str, tmp_path: Path
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(f"{flag}: true\n", encoding="utf-8")
    assert load(tmp_path) is not None


MODULES = [
    draft_check_config,
    matter_spans_config,
    s138_checklist_config,
    chronology_config,
    agenda_config,
]


@pytest.mark.parametrize("module", MODULES, ids=[m.__name__.rsplit(".", 1)[1] for m in MODULES])
def test_no_module_level_call_but_the_logger_and_the_dataclass_decorator(module: Any) -> None:
    """Source scan: at import each of the five runs no call except logging.getLogger(__name__) and @dataclass(frozen=True); a
    function or class BODY is not executed at import, and a class body holds no call (only field defaults)."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    calls: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and ast.unparse(dec.func) == "dataclass") and not (
                    isinstance(dec, ast.Name) and dec.id == "dataclass"
                ):
                    calls.append(f"decorator {ast.unparse(dec)}")
            for inner in node.body:
                calls += [ast.unparse(c.func) for c in ast.walk(inner) if isinstance(c, ast.Call)]
            continue
        calls += [ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)]
    assert [c for c in calls if c != "logging.getLogger"] == [], module.__name__
