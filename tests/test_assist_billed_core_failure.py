"""When the assisted session was run and closed (its cost spent) and the ordinary core result then raises, the run does not end as
an unexplained 500 a client would retry: it raises `CoreFailedAfterSpend`, and the routes answer the coded 502
`assistant_session_billed` with the `assistant` block. Invented text only."""

import json
from pathlib import Path

import pytest

from pravrudhi.application.a0_assist import assist as AS
from pravrudhi.application.a0_assist import intake
from tests import test_a0_assist_request as RQ
from tests.test_intake_routes import ASSIST, TEXT, _assist_client, _env  # noqa: F401  (the autouse fixture: key + tier reset)

BAD_SESSION = {"id": "x", "type": "session.error"}


def _failing_core_env(tmp_path: Path) -> RQ.Env:
    def bad(tr):  # noqa: ANN001, ANN202
        yield BAD_SESSION

    e = RQ.Env(tmp_path, bad)

    def core() -> dict:
        e.core_calls += 1
        raise RuntimeError("secret detail /home/someone/x")

    e.deps = AS.Deps(**{**e.deps.__dict__, "core": core}) if hasattr(e.deps, "__dict__") else e.deps
    return e


def test_a_core_that_raises_after_a_billed_session_raises_core_failed_after_spend(tmp_path: Path) -> None:
    e = _failing_core_env(tmp_path)
    with pytest.raises(AS.CoreFailedAfterSpend) as info:
        e.run()
    blk = info.value.block
    assert blk["used"] is False and blk["outcome"] == "refused" and blk["reason"] == "core_error"
    assert blk["cost"] is not None and blk["session_id"] == "client-1"
    assert "secret" not in json.dumps(blk)
    assert e.core_calls == 1 and e.tr.calls.count("create_session") == 1  # one session, started once, then closed
    assert "delete_session" in e.tr.calls


def test_a_core_that_raises_before_any_spend_is_not_wrapped(tmp_path: Path) -> None:
    """The early fallbacks (no reservation made) are unchanged: nothing was billed, so the exception is the core's own."""
    e = _failing_core_env(tmp_path)
    anonymous = AS.Caller(False, None, None, "1.1.1.1")
    with pytest.raises(RuntimeError):
        e.run(anonymous)


def test_the_intake_assist_route_answers_the_coded_502_and_leaves_no_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad(tr):  # noqa: ANN001, ANN202
        yield BAD_SESSION

    c = _assist_client(tmp_path, program=bad)

    def boom(*_a: object, **_k: object) -> dict:
        raise RuntimeError("secret detail /home/someone/x")

    monkeypatch.setattr(intake, "build_bundle", boom)
    r = c.post(ASSIST, json={"text": TEXT, "consent": True})
    body = r.json()
    assert r.status_code == 502 and body["error"] == "assistant_session_billed"
    assert "do not retry" in body["detail"] and body["assistant"]["reason"] == "core_error"
    assert "secret" not in r.text and "/home" not in r.text
    assert "retry-after" not in {k.lower() for k in r.headers}
    created = [x for x in c.rt.transport.calls if x == "create_session"]  # type: ignore[attr-defined]
    assert len(created) == 1  # the billed session happened once; the 502 is not an invitation to start another


def test_the_ordinary_assisted_failure_still_answers_the_core_result(tmp_path: Path) -> None:
    """A failing SESSION with a working core is unchanged: the deterministic result plus an `assistant` block (200)."""

    def bad(tr):  # noqa: ANN001, ANN202
        yield BAD_SESSION

    c = _assist_client(tmp_path, program=bad)
    r = c.post(ASSIST, json={"text": TEXT, "consent": True})
    assert r.status_code == 200 and r.json()["assistant"]["reason"] == "error" and r.json()["bundle"]["facts"]


def test_the_analyse_facts_assist_route_the_app_calls_answers_the_same_stable_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route the app uses (Screening). Web maps HTTP 502 + the code `assistant_session_billed` only: fields pinned."""
    from pravrudhi.application import nyaya_agent as agent_mod
    from tests.test_a0_assist_route import BODY, URL, client

    c = client(tmp_path)

    def bad(tr):  # noqa: ANN001, ANN202
        yield BAD_SESSION

    c.rt.transport = RQ.FakeTransport(bad, RQ.Clock())  # type: ignore[attr-defined]

    def boom(self: object, *_a: object, **_k: object) -> object:
        raise RuntimeError("secret detail /home/someone/x")

    monkeypatch.setattr(agent_mod.NyayaAgent, "run", boom)
    r = c.post(URL, json=BODY)
    body = r.json()
    assert r.status_code == 502
    assert set(body) == {"error", "detail", "assistant"}  # stable field names
    assert body["error"] == "assistant_session_billed"
    assert set(body["assistant"]) >= {"used", "outcome", "reason", "cost", "session_id"}
    assert body["assistant"]["outcome"] == "refused" and body["assistant"]["reason"] == "core_error"
    assert "secret" not in r.text and "/home" not in r.text and "retry-after" not in {k.lower() for k in r.headers}
    assert len([x for x in c.rt.transport.calls if x == "create_session"]) == 1  # type: ignore[attr-defined]
