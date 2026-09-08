"""HTTP surface for the swarm itself: agent availability, the routing table's live per-tier choice, the last
runs of the objective and self-build swarms, and which agent processes are actually alive on this machine.
Before this, none of that was visible through the API at all."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.application import selfbuild, subagents
from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project

H = {"host": "127.0.0.1:8008"}

ROUTING_ROW_FIELDS = {"tier", "route", "agent", "model", "relative_cost", "reason", "records", "error"}
ROUTING_RECORD_FIELDS = {"route_id", "tier", "trials", "successes", "rate", "lo", "hi", "mean_wall_s", "relative_cost"}
RUN_FIELDS = {"task_id", "route", "accepted", "wall_s", "files", "reasons", "at"}
LIVE_FIELDS = {"pid", "elapsed_s", "kind", "worktree"}


def _client(tmp_path: Path) -> TestClient:
    init_project(tmp_path)
    return TestClient(build_app(tmp_path), headers=H)


class _FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_swarm_reports_agents_routing_and_empty_runs_on_a_fresh_workspace(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = c.get("/api/swarm").json()

    assert set(body) == {"agents", "routing", "subagent_runs", "selfbuild_runs"}
    assert body["agents"], "the registry always surveys at least the built-in cli agents"
    for entry in body["agents"]:
        assert set(entry) == {"name", "available", "reason"}
        assert isinstance(entry["available"], bool)

    assert body["routing"], "every declared tier gets a row, resolved or not"
    for row in body["routing"]:
        assert set(row) == ROUTING_ROW_FIELDS
        for record in row["records"]:
            assert set(record) == ROUTING_RECORD_FIELDS

    assert body["subagent_runs"] == []
    assert body["selfbuild_runs"] == []


def test_swarm_run_lists_are_capped_at_100_and_ordered_newest_first(tmp_path: Path) -> None:
    root = tmp_path
    init_project(root)
    for i in range(105):
        subagents.record_run(
            root,
            subagents.SubagentRun(
                objective="obj", step=f"s{i}", task_id=f"obj:s{i}", route="claude-code", accepted=True, wall_s=1.0,
            ),
        )
        selfbuild.record_run(
            root,
            selfbuild.BuildRun(task_id=f"build:{i}", route="claude-code", accepted=True, wall_s=1.0),
        )

    c = TestClient(build_app(root), headers=H)
    body = c.get("/api/swarm").json()

    assert len(body["subagent_runs"]) == 100
    assert body["subagent_runs"][0]["step"] == "s104", "the most recently appended run comes first"
    assert body["subagent_runs"][-1]["step"] == "s5"
    for run in body["subagent_runs"]:
        assert set(run) == {"objective", "step"} | RUN_FIELDS

    assert len(body["selfbuild_runs"]) == 100
    assert body["selfbuild_runs"][0]["task_id"] == "build:104"
    assert body["selfbuild_runs"][-1]["task_id"] == "build:5"
    for run in body["selfbuild_runs"]:
        assert set(run) == RUN_FIELDS


def test_swarm_endpoint_never_carries_a_stored_provider_key(tmp_path: Path, monkeypatch) -> None:
    import pravrudhi.application.credentials as credentials
    from pravrudhi.api.localguard import TOKEN_HEADER, app_token

    monkeypatch.setattr(credentials, "validate", lambda *a, **kw: (True, "ok"))
    c = _client(tmp_path)
    secret_key = "sk-thisisaverysecretlookingapikey1234567890"
    r = c.post(
        "/api/providers/openai/key",
        json={"key": secret_key},
        headers={TOKEN_HEADER: app_token(tmp_path)},
    )
    assert r.status_code == 200, r.text

    body = c.get("/api/swarm")
    assert secret_key not in body.text


def test_swarm_live_parses_matching_processes_and_never_echoes_the_raw_command_line(
    tmp_path: Path, monkeypatch
) -> None:
    from pravrudhi.api import server as server_module

    fake_ps_output = (
        "  PID ELAPSED COMMAND\n"
        "  111     42 claude -p do-something --token sk-shouldnotleak1234567890\n"
        "  222    900 codex exec --model gpt --api-key sk-anothersecretvalue0000000\n"
        "  333      7 agent_code --worktree /x/.worktrees/agent-swarm-api\n"
        "  444     10 unrelated-process --flag\n"
    )
    init_project(tmp_path)
    monkeypatch.setattr(server_module.subprocess, "run", lambda *a, **kw: _FakeCompleted(fake_ps_output))

    c = TestClient(build_app(tmp_path), headers=H)
    r = c.get("/api/swarm/live")
    assert r.status_code == 200
    body = r.json()

    by_pid = {row["pid"]: row for row in body}
    assert set(by_pid) == {111, 222, 333}, "only the three declared launch patterns are matched"
    assert by_pid[111]["kind"] == "claude"
    assert by_pid[111]["elapsed_s"] == 42
    assert by_pid[222]["kind"] == "codex"
    assert by_pid[222]["elapsed_s"] == 900
    assert by_pid[333]["kind"] == "agent_code"

    for row in body:
        assert set(row) == LIVE_FIELDS
        assert isinstance(row["pid"], int)
        assert isinstance(row["elapsed_s"], int)
        assert "sk-" not in json.dumps(row), "the raw command line must never reach the response"


def test_swarm_live_survives_a_broken_ps_call(tmp_path: Path, monkeypatch) -> None:
    from pravrudhi.api import server as server_module

    def _boom(*a, **kw):
        raise OSError("ps not found")

    init_project(tmp_path)
    monkeypatch.setattr(server_module.subprocess, "run", _boom)
    c = TestClient(build_app(tmp_path), headers=H)
    r = c.get("/api/swarm/live")
    assert r.status_code == 200
    assert r.json() == []


def test_update_endpoint_serves_the_settings_page(tmp_path):
    """The settings page reads current.version from here; /api/status never carried it."""
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    client = TestClient(create_app(tmp_path))
    r = client.get("/api/update", headers={"host": "127.0.0.1:8008"})
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["version"] and body["current"]["kernel_version"]
    # `checked` says whether the check reached GitHub. Without it `update_available: false` answered both
    # "nothing newer" and "could not look", so a rate-limited machine reported itself up to date and an
    # install in flight was declared finished.
    assert set(body) == {"current", "latest", "update_available", "checked", "how"}


def test_heartbeat_endpoint_is_empty_before_the_first_beat(tmp_path):
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    client = TestClient(create_app(tmp_path))
    r = client.get("/api/heartbeat?n=5", headers={"host": "127.0.0.1:8008"})
    assert r.status_code == 200 and r.json() == {"beats": []}
