"""The operator must be able to talk to this engine when the session driving it has gone.

The chat surface pointed at one OpenAI-compatible endpoint read from an environment variable, defaulting to a
local server. On a machine where that server is not running every turn answered 503 — on the one surface the
operator would need if the session doing this work hit its limit. Meanwhile the engine already knew about
several working hosted models, kept a measured record of each, and knew which were spent and when they returned.

So the conversation draws on the same routing table the swarm does: permitted routes at a tier, in order,
skipping any that is cooling, moving on when one reports a usage limit rather than showing an error to the
person typing.

Only an HTTP endpoint can answer a completion. `claude-code` and `codex` drive their own loops and cannot, so
they are skipped — which is the point, because this exists for when those are exactly what is unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.network_chat import (
    ENDPOINT_AGENTS,
    NoModelAvailable,
    network_complete,
    usable_routes,
)


def _fake_endpoints(monkeypatch: pytest.MonkeyPatch, behaviour: dict[str, Any]) -> list[str]:
    """Drive `network_complete` against fake endpoints and record which models were tried, in order."""
    tried: list[str] = []

    class Fake:
        def __init__(self, base_url: str, model: str = "", **kw: Any) -> None:
            self.model = model

        def chat(self, messages: Any, **kw: Any) -> Any:
            tried.append(self.model)
            outcome = behaviour.get(self.model, '{"reply": "ok"}')
            if isinstance(outcome, Exception):
                raise outcome
            return type("R", (), {"text": outcome})()

    monkeypatch.setattr("pravrudhi.models.openai_compat.ChatClient", Fake)
    monkeypatch.setattr(
        "pravrudhi.application.network_chat._endpoint_for", lambda agent: ("https://x/v1", "k")
    )
    return tried


class TestWhichRoutesCanAnswer:
    def test_only_endpoint_routes_are_offered(self, tmp_path: Path) -> None:
        for route in usable_routes(tmp_path):
            assert route.agent in ENDPOINT_AGENTS

    def test_a_command_line_agent_is_never_offered(self, tmp_path: Path) -> None:
        """The reason this module exists is that those are the ones that have run out."""
        agents = {route.agent for route in usable_routes(tmp_path)}
        assert not agents & {"claude-code", "codex", "orca:local"}

    def test_a_cooling_route_is_left_out(self, tmp_path: Path) -> None:
        from pravrudhi.application.availability import mark_limited

        assert "qwen-lite-max" in {r.id for r in usable_routes(tmp_path)}
        mark_limited(tmp_path, "opencode:alibaba-plan", until=datetime.now(UTC) + timedelta(hours=1))
        assert "qwen-lite-max" not in {r.id for r in usable_routes(tmp_path)}

    def test_the_tables_own_order_is_kept(self, tmp_path: Path) -> None:
        """Cheapest verified route first, sentinel last, exactly as the swarm would reach them."""
        ids = [r.id for r in usable_routes(tmp_path)]
        assert ids.index("qwen-lite-max") < ids.index("qwen-loop") < ids.index("qwen-coder")


class TestFallingThrough:
    def test_a_spent_route_hands_on_to_the_next(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tried = _fake_endpoints(monkeypatch, {"qwen3.8-max": RuntimeError("Requests rate limit exceeded")})
        answer = network_complete(tmp_path)([{"role": "user", "content": "hello"}], [])
        assert answer["content"] == "ok"
        assert tried[0] == "qwen3.8-max" and len(tried) > 1, "the spent route must not be the last word"

    def test_a_spent_route_is_cooled_so_the_next_turn_skips_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application.availability import cooling

        _fake_endpoints(monkeypatch, {"qwen3.8-max": RuntimeError("Requests rate limit exceeded")})
        network_complete(tmp_path)([{"role": "user", "content": "hello"}], [])
        assert "opencode:alibaba-plan" in cooling(tmp_path)

    def test_an_ordinary_failure_does_not_cool_the_route(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A route that broke is not a route that is spent, and recording it as spent would hide a real fault."""
        from pravrudhi.application.availability import cooling

        _fake_endpoints(monkeypatch, {"qwen3.8-max": RuntimeError("connection reset by peer")})
        network_complete(tmp_path)([{"role": "user", "content": "hello"}], [])
        assert "opencode:alibaba-plan" not in cooling(tmp_path)

    def test_when_nothing_can_answer_it_says_so(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("pravrudhi.application.network_chat._endpoint_for", lambda agent: None)
        with pytest.raises(NoModelAvailable):
            network_complete(tmp_path)([{"role": "user", "content": "hello"}], [])


class TestReadingTheAnswer:
    def test_the_expected_shape_is_understood(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_endpoints(
            monkeypatch, {"qwen3.8-max": '{"reply": "hello", "tool_calls": [{"tool": "objectives"}]}'}
        )
        answer = network_complete(tmp_path)([{"role": "user", "content": "x"}], [])
        assert answer["content"] == "hello"
        assert answer["tool_calls"] == [{"tool": "objectives"}]

    def test_prose_is_kept_rather_than_discarded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hosted endpoint cannot have the shape compiled into its sampler, so it is asked for in words and
        sometimes ignored. What the model said is still what it said."""
        _fake_endpoints(monkeypatch, {"qwen3.8-max": "just some prose"})
        assert network_complete(tmp_path)([{"role": "user", "content": "x"}], [])["content"] == "just some prose"

    def test_an_object_naming_its_field_differently_is_still_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_endpoints(monkeypatch, {"qwen3.8-max": '{"answer": "said it anyway"}'})
        assert network_complete(tmp_path)([{"role": "user", "content": "x"}], [])["content"] == "said it anyway"
