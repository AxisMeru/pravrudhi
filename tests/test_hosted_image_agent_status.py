"""#276: in the hosted image an unavailable agent says what is true ("agents run on the host"), not a missing install."""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.agents import registry
from pravrudhi.agents.registry import HOSTED_AGENT_REASON, AgentStatus, hosted_image, survey


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (registry.HOSTED_IMAGE_ENV, "PRAVRUDHI_DISABLE_LOCAL_GUARD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(registry.Path, "exists", lambda self: False if str(self) == "/.dockerenv" else Path.exists(self))


@pytest.fixture
def raw(monkeypatch: pytest.MonkeyPatch) -> list[AgentStatus]:
    statuses = [
        AgentStatus("codex", False, "codex CLI not installed"),
        AgentStatus("orca:claude", False, "orca-ide runtime not reachable (needs a display server: xvfb)"),
        AgentStatus("opencode:alibaba", False, "opencode CLI not installed"),
        AgentStatus("hermes", False, "hermes CLI not installed (uv tool install hermes-agent)"),
        AgentStatus("claude-code", False, "claude CLI not installed"),
        AgentStatus("ready-one", True, "ready"),
    ]
    monkeypatch.setattr(registry, "_survey", lambda root, include_orca=True: list(statuses))
    return statuses


def test_a_local_install_keeps_the_specific_reasons(tmp_path: Path, raw: list[AgentStatus]) -> None:
    assert survey(tmp_path) == raw


def test_the_hosted_image_says_agents_run_on_the_host(
    tmp_path: Path, raw: list[AgentStatus], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(registry.HOSTED_IMAGE_ENV, "1")
    out = survey(tmp_path)
    assert [a.name for a in out] == [a.name for a in raw]
    for a in out:
        if a.name == "ready-one":
            assert a.available and a.reason == "ready"  # an agent that does run is left alone
        else:
            assert not a.available and a.reason == HOSTED_AGENT_REASON
    text = " ".join(a.reason for a in out)
    assert "not installed" not in text and "xvfb" not in text


def test_the_marker_is_exactly_one_and_an_older_image_is_recognised_by_its_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(registry.HOSTED_IMAGE_ENV, "0")
    assert not hosted_image()
    monkeypatch.setenv(registry.HOSTED_IMAGE_ENV, "1")
    assert hosted_image()
    monkeypatch.delenv(registry.HOSTED_IMAGE_ENV)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    assert not hosted_image()  # not in a container: a local install that disabled the guard is not the hosted image
    monkeypatch.setattr(registry.Path, "exists", lambda self: str(self) == "/.dockerenv")
    assert hosted_image()  # in a container with the hosted image's guard default


def test_the_dockerfile_sets_the_marker() -> None:
    text = (Path(__file__).parent.parent / "deploy" / "docker" / "Dockerfile").read_text()
    assert "PRAVRUDHI_HOSTED_IMAGE=1" in text


def test_the_settings_route_reports_the_hosted_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    monkeypatch.setenv(registry.HOSTED_IMAGE_ENV, "1")
    one = [AgentStatus("codex", False, "codex CLI not installed")]
    monkeypatch.setattr(registry, "_survey", lambda root, include_orca=True: one)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    client = TestClient(create_app(tmp_path), base_url="http://localhost")
    agents = client.get("/api/agents").json()
    assert agents and all(a["reason"] == HOSTED_AGENT_REASON for a in agents if not a["available"])
