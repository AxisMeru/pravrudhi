"""`pravrudhi requests` triage subcommands: so nobody has to hand-edit `.pravrudhi/requests.json`.

An operator's hand-flip of eleven criteria one morning had no tool behind it: no audit note saying who changed
what or why, and no lock against the heartbeat's own write landing on the same file at the same moment. These
tests exercise the CLI surface that replaces the hand-edit, not just the application-layer functions underneath
it (covered separately in `test_requests.py`): that `show`, `set-mode`, `mark-met` and `decline` are wired to the
right Typer group, refuse a blank reason at the CLI boundary, and report their result the way an operator reading
a terminal, not a return value, needs to see it.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pravrudhi.application import requests as reqs
from pravrudhi.cli.app import app

runner = CliRunner()


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _ask(root: Path, text: str = "make the widget spin") -> str:
    req = reqs.capture(root, text)
    reqs.add_criteria(root, req.id, [reqs.Criterion(text=text, source="operator")])
    return req.id


class TestBareRequestsListsTheBacklog:
    """The old `pravrudhi requests` (a plain command) became the group's no-subcommand behaviour; the output
    it prints must not have changed underneath the migration."""

    def test_an_empty_store_says_so(self, tmp_path: Path) -> None:
        result = _invoke("requests", "--root", str(tmp_path))
        assert result.exit_code == 0
        assert "no requests captured yet" in result.stdout

    def test_a_captured_ask_is_listed(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = _invoke("requests", "--root", str(tmp_path))
        assert result.exit_code == 0
        assert rid in result.stdout

    def test_json_mode_is_still_honoured(self, tmp_path: Path) -> None:
        _ask(tmp_path)
        result = _invoke("requests", "--json", "--root", str(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total"] == 1


class TestRequestsShow:
    def test_shows_the_ask_its_criteria_and_its_notes(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        reqs.set_mode(tmp_path, rid, 0, "build", why="it names a file")
        result = _invoke("requests", "show", rid, "--root", str(tmp_path))
        assert result.exit_code == 0
        assert "build" in result.stdout
        assert "it names a file" in result.stdout

    def test_an_unknown_id_is_refused(self, tmp_path: Path) -> None:
        result = _invoke("requests", "show", "r-nope", "--root", str(tmp_path))
        assert result.exit_code == 1

    def test_the_old_flat_command_still_works_identically(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        grouped = _invoke("requests", "show", rid, "--root", str(tmp_path))
        flat = _invoke("requests-show", rid, "--root", str(tmp_path))
        assert grouped.exit_code == flat.exit_code == 0
        assert grouped.stdout == flat.stdout


class TestRequestsSetMode:
    def test_flips_the_mode_and_reports_it(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = _invoke(
            "requests", "set-mode", rid, "0", "build", "--why", "drafted before the detector existed",
            "--root", str(tmp_path),
        )
        assert result.exit_code == 0
        assert "build" in result.stdout
        req = reqs.get(tmp_path, rid)
        assert req is not None and req.criteria[0].mode == "build"

    def test_an_unknown_mode_is_refused_at_the_cli_boundary(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = _invoke(
            "requests", "set-mode", rid, "0", "sideways", "--why", "because", "--root", str(tmp_path),
        )
        assert result.exit_code == 2

    def test_a_missing_reason_is_refused(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = runner.invoke(app, ["requests", "set-mode", rid, "0", "build", "--root", str(tmp_path)])
        assert result.exit_code != 0


class TestRequestsMarkMet:
    def test_marks_met_with_evidence_and_a_reason(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = _invoke(
            "requests", "mark-met", rid, "0", "--evidence", "abc1234",
            "--why", "watched it spin in the browser", "--root", str(tmp_path),
        )
        assert result.exit_code == 0
        req = reqs.get(tmp_path, rid)
        assert req is not None and req.criteria[0].met is True
        assert req.criteria[0].evidence[0].ref == "abc1234"
        assert req.criteria[0].evidence[0].kind == "commit", "commit is the default kind"
        assert any("watched it spin in the browser" in n["note"] for n in req.notes)

    def test_a_non_default_kind_is_honoured(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = _invoke(
            "requests", "mark-met", rid, "0", "--evidence", "README.md", "--kind", "file",
            "--why", "it is there", "--root", str(tmp_path),
        )
        assert result.exit_code == 0
        req = reqs.get(tmp_path, rid)
        assert req is not None and req.criteria[0].evidence[0].kind == "file"

    def test_a_missing_reason_is_refused_before_touching_the_store(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = runner.invoke(
            app, ["requests", "mark-met", rid, "0", "--evidence", "abc1234", "--why", "", "--root", str(tmp_path)],
        )
        assert result.exit_code == 2
        req = reqs.get(tmp_path, rid)
        assert req is not None and req.criteria[0].met is False


class TestRequestsDecline:
    def test_declines_one_criterion_and_leaves_the_rest(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        reqs.add_criteria(tmp_path, rid, [reqs.Criterion(text="a second criterion", source="operator")])
        result = _invoke("requests", "decline", rid, "0", "--why", "out of scope", "--root", str(tmp_path))
        assert result.exit_code == 0
        req = reqs.get(tmp_path, rid)
        assert req is not None
        assert req.criteria[0].declined is True
        assert req.criteria[1].declined is False

    def test_declines_the_whole_request_when_no_index_is_given(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = _invoke("requests", "decline", rid, "--why", "not going to happen", "--root", str(tmp_path))
        assert result.exit_code == 0
        req = reqs.get(tmp_path, rid)
        assert req is not None and req.state == "declined"
        assert any("not going to happen" in n["note"] for n in req.notes)

    def test_a_missing_reason_is_refused(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        result = runner.invoke(app, ["requests", "decline", rid, "0", "--root", str(tmp_path)])
        assert result.exit_code != 0

    def test_an_already_met_criterion_cannot_be_declined_through_the_cli(self, tmp_path: Path) -> None:
        rid = _ask(tmp_path)
        reqs.meet(tmp_path, rid, 0, [reqs.Evidence("commit", "abc1234")])
        result = _invoke("requests", "decline", rid, "0", "--why", "changed my mind", "--root", str(tmp_path))
        assert result.exit_code == 1
