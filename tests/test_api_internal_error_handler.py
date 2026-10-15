"""Any exception no route handled answers the coded 500 {"error": "internal_error", "detail": ...}; handled errors (HTTPException,
validation) are unchanged; nothing of the exception reaches the body. Invented text only."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pravrudhi.api.errors import MESSAGES, coded_500
from pravrudhi.api.server import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path)

    def boom() -> dict:
        raise RuntimeError("secret detail /home/someone/.pravrudhi/objectives/x.yaml")

    def missing() -> dict:
        raise HTTPException(404, "no such thing")

    def stop() -> dict:
        raise StopIteration  # what a bare next() raised before 0.6.17

    app.add_api_route("/api/_t/boom", boom)
    app.add_api_route("/api/_t/missing", missing)
    app.add_api_route("/api/_t/stop", stop)
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def test_an_unhandled_exception_answers_the_coded_500_without_its_text(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/api/_t/boom")
    assert r.status_code == 500
    assert r.json() == {"error": "internal_error", "detail": MESSAGES["internal_error"]}
    assert "secret" not in r.text and "/home" not in r.text


def test_a_stop_iteration_is_the_same_coded_500(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/api/_t/stop")
    assert r.status_code == 500 and r.json()["error"] == "internal_error"


def test_handled_errors_are_unchanged(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/_t/missing")
    assert r.status_code == 404 and r.json() == {"detail": "no such thing"}
    assert c.get("/api/_t/does-not-exist").status_code == 404


def test_the_handler_logs_the_type_and_route_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR"):
        _client(tmp_path).get("/api/_t/boom")
    msgs = [m for m in caplog.messages if "unhandled" in m]
    assert msgs == ["unhandled RuntimeError on GET /api/_t/boom"]


def test_the_coded_500_shape_and_an_unknown_code_is_refused() -> None:
    assert coded_500().status_code == 500
    with pytest.raises(KeyError):
        coded_500("not_a_code")
