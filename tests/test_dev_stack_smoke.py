"""check_response in scripts/dev_stack_smoke.py is the smoke's only logic; pin its pass/fail rules."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("dev_stack_smoke", Path(__file__).parent.parent / "scripts" / "dev_stack_smoke.py")
smoke = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(smoke)  # type: ignore[union-attr]


def _resp(std, elements):
    return {"standard": std, "results": [{"elements": elements}]}


OK_STD = {"requested": "proved", "applied": None, "source": "default", "proceeding_posture": None, "in_judge_prompt": False}


def test_clean_response_passes() -> None:
    el = [{"element": "a", "status": "established", "fact_id": "F1", "quote": "q"},
          {"element": "b", "status": "not_established", "fact_id": None, "quote": None}]
    assert smoke.check_response(_resp(OK_STD, el), None) == []


def test_missing_or_wrong_standard_fails() -> None:
    assert smoke.check_response({"results": []}, None) == ["standard missing"]
    bad = {"requested": "prima_facie_disclosed", "applied": None, "source": "proceeding_posture",
           "proceeding_posture": "quash", "in_judge_prompt": False}
    assert any("proved/default" in f for f in smoke.check_response(_resp(bad, []), None))
    assert any("!= sent" in f for f in smoke.check_response(_resp(bad, []), "trial"))


def test_fact_id_fail_closed_rules() -> None:
    est_no_id = [{"element": "a", "status": "established", "fact_id": None, "quote": "q"}]
    assert any("without fact_id" in f for f in smoke.check_response(_resp(OK_STD, est_no_id), None))
    ne_with_id = [{"element": "a", "status": "not_established", "fact_id": "F1", "quote": None}]
    assert any("carries fact_id" in f for f in smoke.check_response(_resp(OK_STD, ne_with_id), None))


def test_in_judge_prompt_must_be_a_bool() -> None:
    bad = {**OK_STD, "in_judge_prompt": "no"}
    assert any("in_judge_prompt" in f for f in smoke.check_response(_resp(bad, []), None))
    assert smoke.check_response(_resp({**OK_STD, "applied": "proved", "in_judge_prompt": True}, []), None) == []


def test_applied_is_null_unless_the_judge_prompt_stated_the_standard() -> None:
    lied = {**OK_STD, "applied": "proved"}  # in_judge_prompt false, yet applied is set
    assert any("applied" in f for f in smoke.check_response(_resp(lied, []), None))
    missing = {**OK_STD, "in_judge_prompt": True}  # in_judge_prompt true, applied null
    assert any("applied" in f for f in smoke.check_response(_resp(missing, []), None))
    assert smoke.check_response(_resp({**OK_STD, "applied": "proved", "in_judge_prompt": True}, []), None) == []


def test_only_a_loopback_or_named_dev_host_is_labelled_dev_stack_at_zero_cost() -> None:
    import pytest

    assert smoke.label_for("http://127.0.0.1:8301", set(), None) == ("dev stack (local)", 0)
    assert smoke.label_for("http://spark-dev:8301", {"spark-dev"}, None) == ("dev stack (local)", 0)
    with pytest.raises(SystemExit, match="REFUSING"):
        smoke.label_for("https://engine.example.com", set(), None)
    assert smoke.label_for("https://engine.example.com", set(), "production engine") == ("production engine", None)


def test_the_judge_pin_is_asserted_against_the_served_models(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "served_models", lambda base: ["/models/Qwen2.5-32B-Instruct", "judge32b"])
    seen, fails = smoke.check_judges(["judge32b=http://x/v1"])
    assert fails == [] and seen[0]["listed"][0] == "/models/Qwen2.5-32B-Instruct"
    _, fails = smoke.check_judges(["nyaya-judge-4b=http://x/v1"])
    assert fails and "nyaya-judge-4b" in fails[0]


def test_main_refuses_without_a_pinned_judge_and_reports_http_errors_with_their_status(monkeypatch, capsys) -> None:
    import io
    import urllib.error

    monkeypatch.setattr(smoke.sys, "argv", ["x", "--url", "http://127.0.0.1:1"])
    assert smoke.main() == 2 and "pin the judge" in capsys.readouterr().err
    monkeypatch.setattr(smoke.sys, "argv", ["x", "--url", "http://127.0.0.1:1", "--judge", "m=http://j/v1"])
    monkeypatch.setattr(smoke, "served_models", lambda base: ["m"])

    def forbidden(*a, **k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(smoke, "post", forbidden)
    assert smoke.main() == 1  # a real API error is a failed check, not an outage
    assert '"http_status": 403' in capsys.readouterr().out

    def unavailable(*a, **k):
        raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(smoke, "post", unavailable)
    assert smoke.main() == 2
