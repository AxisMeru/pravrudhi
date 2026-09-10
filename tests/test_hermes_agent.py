"""Nous Research's hermes-agent as a measured seat.

Added on the operator's instruction of 2026-09-10. A survey reported it disqualifying on two grounds and both
were wrong, which is why the adapter exists: the protocol needs only `run()`, because `GitWorktreeMixin`
already supplies `create_workspace` and `collect_changes` for every CLI seat; and hermes DOES report usage,
through `-z --usage-file`, whose own help says the report "is written even when the run fails, so pipelines
can always account for spend".

So this seat arrives measured. That matters on the day `alibaba_agent` was found reporting its spend as zero:
adding an unmeasured seat straight afterwards would have widened the hole that was just closed.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.agents.hermes_agent import usage_from_report


class TestUsageFromReport:
    def test_a_report_becomes_tokens_cache_and_cost(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({
            "total_tokens": 12345, "input_tokens": 10000, "output_tokens": 2345,
            "cache_read_tokens": 8000, "cache_write_tokens": 500,
            "estimated_cost_usd": 0.0421, "model": "qwen3-coder-plus", "failed": False,
        }))
        usage = usage_from_report(path)
        assert usage.tokens == 12345
        assert usage.cache_read == 8000 and usage.cache_write == 500
        assert usage.cost_usd == 0.0421

    def test_a_missing_report_is_unmeasured_not_free(self, tmp_path: Path) -> None:
        """The distinction this whole file exists to protect. A run whose report never landed did not cost
        nothing; nobody knows what it cost."""
        usage = usage_from_report(tmp_path / "absent.json")
        assert usage.tokens is None and usage.cost_usd is None

    def test_a_report_without_a_total_is_unmeasured(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"model": "x", "failed": True}))
        assert usage_from_report(path).tokens is None

    def test_a_total_of_zero_survives_as_zero(self, tmp_path: Path) -> None:
        """A genuinely free turn is a measured zero. Collapsing it into None would hide a free seat exactly as
        collapsing None into zero hides a paid one."""
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"total_tokens": 0}))
        assert usage_from_report(path).tokens == 0

    def test_a_total_is_summed_when_absent_but_the_parts_are_present(self, tmp_path: Path) -> None:
        """`total_tokens` is what hermes normally writes; the parts are the fallback, and a seat that reports
        its input and output but no total is measured, not unmeasured."""
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"input_tokens": 700, "output_tokens": 300, "cache_read_tokens": 200}))
        assert usage_from_report(path).tokens == 1200

    def test_unparseable_json_is_unmeasured_and_does_not_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        path.write_text("{not json")
        assert usage_from_report(path).tokens is None

    def test_the_failure_reason_travels_when_the_run_failed(self, tmp_path: Path) -> None:
        """Hermes writes the report even on failure, which is the property that makes a failed dispatch
        accountable rather than invisible."""
        path = tmp_path / "usage.json"
        path.write_text(json.dumps({"total_tokens": 42, "failed": True, "failure": "rate limited"}))
        usage = usage_from_report(path)
        assert usage.tokens == 42 and usage.failed and "rate limited" in (usage.failure or "")


class TestStatusIsHonestAboutConfiguration:
    def test_an_installed_but_unconfigured_hermes_is_not_ready(self, tmp_path: Path, monkeypatch) -> None:
        """A fresh `uv tool install` leaves the CLI on PATH and no provider anywhere. Reporting that as ready
        lets the router pick a seat guaranteed to fail, and the failure is then recorded against the seat's
        success rate as though it were evidence about its quality."""
        from pravrudhi.agents.hermes_agent import HermesAgent

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/hermes")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        ok, why = HermesAgent(tmp_path).status()
        assert not ok and "no provider configured" in why

    def test_a_configured_hermes_is_ready(self, tmp_path: Path, monkeypatch) -> None:
        from pravrudhi.agents.hermes_agent import HermesAgent

        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text("model:\n  provider: auto\n")
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/hermes")
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert HermesAgent(tmp_path).status()[0]
