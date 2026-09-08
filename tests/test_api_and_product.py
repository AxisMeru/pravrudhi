import json
from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application.export import export_adapter
from pravrudhi.application.init import init_project
from pravrudhi.application.status import status
from pravrudhi_kernel.ledger import LedgerWriter, verify

BUCKET = {"task_family": "t", "target_model": "m", "corpus": "c"}


def test_init_is_idempotent_and_private(tmp_path: Path) -> None:
    a = init_project(tmp_path, model="Qwen/Qwen3-4B")
    b = init_project(tmp_path)
    assert a["isolation"] in ("process", "container") and (tmp_path / "research" / "ledger.jsonl").exists()
    assert b["created"] == []  # second run creates nothing
    assert (tmp_path / ".pravrudhi" / "kernel" / "secret").stat().st_mode & 0o777 == 0o600
    assert ".pravrudhi/" in (tmp_path / ".gitignore").read_text()
    s = status(tmp_path)
    assert s["initialised"] and s["chain_ok"] and s["events"] == 1


def _promoted_ledger(tmp_path: Path) -> Path:
    init_project(tmp_path)
    w = LedgerWriter.open(tmp_path / "research" / "ledger.jsonl", "0.1.0")
    w.append(
        "propose",
        "proposer",
        {"op": "adapter", "strategy": "sft_rejection", "edit_family": "optimiser"},
        epoch=0,
        night=1,
        cycle=1,
        candidate_id="c-0001",
        surface="W3.adapter",
        bucket=BUCKET,
        provenance="agama",
    )
    w.append(
        "observe",
        "kernel",
        {"observed": {"delta_in": 0.05, "n_items": 100}, "hashes": {"model": "a" * 64}, "stats": {"boundary": "confirm"}},
        epoch=0,
        night=1,
        cycle=1,
        candidate_id="c-0001",
        surface="W3.adapter",
        bucket=BUCKET,
        provenance="pratyaksha",
    )
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_model.safetensors").write_bytes(b"x")
    (adapter / "adapter_config.json").write_text("{}")
    pack = tmp_path / "research" / "inbox" / "night1" / "c-0001"
    pack.mkdir(parents=True)
    (pack / "README.md").write_text("# pack\n")
    w.append(
        "promote",
        "broker",
        {"tier": "T2", "from_worktree": str(adapter), "merge_commit": "b" * 64, "inbox_pack": str(pack), "tau_after": 0.6},
        epoch=0,
        night=1,
        cycle=1,
        candidate_id="c-0001",
        surface="W3.adapter",
    )
    return tmp_path


def test_export_copies_green_adapter_with_manifest(tmp_path: Path) -> None:
    root = _promoted_ledger(tmp_path)
    m = export_adapter(root, tmp_path / "out")
    assert m["candidate_id"] == "c-0001" and (tmp_path / "out" / "adapter_model.safetensors").exists()
    assert json.loads((tmp_path / "out" / "pravrudhi_export.json").read_text())["badge"] == "green"


def test_export_refuses_when_not_green(tmp_path: Path) -> None:
    root = _promoted_ledger(tmp_path)
    w = LedgerWriter.open(root / "research" / "ledger.jsonl", "0.1.0")
    w.append(
        "prune",
        "auditor",
        {"hetvabhasa": "badhita", "reason": "canary", "status": "pruned"},
        epoch=0,
        night=1,
        candidate_id="c-0001",
        surface="W3.adapter",
    )
    try:
        export_adapter(root, tmp_path / "out2")
    except PermissionError as e:
        assert "not green" in str(e)
    else:
        raise AssertionError("expected refusal")


def test_api_reads_ledger_and_sign_is_a_human_act(tmp_path: Path) -> None:
    root = _promoted_ledger(tmp_path)
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    assert c.get("/api/health").json()["ok"]
    assert c.get("/api/status").json()["badges"]["green"] == 1
    assert c.get("/api/candidates/c-0001").json()["badge"] == "green"
    assert c.get("/api/candidates/c-9999").status_code == 404
    assert len(c.get("/api/observations").json()) == 1
    inbox = c.get("/api/inbox").json()
    assert len(inbox) == 1 and inbox[0]["signed"] is False
    pack = inbox[0]["pack"]
    tok = {TOKEN_HEADER: app_token(root)}
    # without the engine's local token a state change never reaches the endpoint's own rules
    assert c.post("/api/inbox/sign", json={"pack": pack, "decision": "approve"}).status_code == 401
    assert c.post("/api/inbox/sign", json={"pack": pack, "decision": "approve"}, headers=tok).status_code == 403
    assert (
        c.post(
            "/api/inbox/sign",
            json={"pack": pack, "decision": "approve"},
            headers={"X-Pravrudhi-Operator": "claude", **tok},
        ).status_code
        == 403
    )
    r = c.post(
        "/api/inbox/sign",
        json={"pack": pack, "decision": "approve", "note": "read"},
        headers={"X-Pravrudhi-Operator": "Sharath", **tok},
    )
    assert r.status_code == 200 and r.json()["by"] == "Sharath"
    assert c.get("/api/inbox").json()[0]["signed"] is True
    assert verify(root / "research" / "ledger.jsonl").ok


def test_evidence_endpoint_refuses_traversal_and_serves_only_evidence_files(tmp_path: Path) -> None:
    root = _promoted_ledger(tmp_path)
    (root / "docs" / "evidence").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "evidence" / "L3_noise_floor.md").write_text("# ok\n")
    (root / "secret.md").write_text("no\n")
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    assert c.get("/api/evidence/L3_noise_floor").json()["markdown"] == "# ok\n"
    for bad in ("..%2Fsecret", "../secret", "%2e%2e/secret", "L3_noise_floor/../../secret", "a" * 65, "x.y"):
        assert c.get(f"/evidence/{bad}").status_code == 404, bad


def test_a_note_can_be_written_corrected_and_deleted_over_the_api(tmp_path: Path) -> None:
    """The editor in the memory page had a Save button and no endpoint behind it. These four calls are the round
    trip that page makes, so the button cannot go dead again without a red test.

    Every one of them is state-changing, so each carries the local token the engine's guard requires.
    """
    root = tmp_path
    init_project(root)
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    tok = {TOKEN_HEADER: app_token(root)}

    created = c.post("/api/memory/notes", json={"text": "the base model is Qwen3-8B", "source": "user"}, headers=tok)
    assert created.status_code == 200, created.text
    note = created.json()
    assert note["revised"] == ""

    edited = c.patch(
        f"/api/memory/notes/{note['id']}", json={"text": "the base model is Qwen3-14B", "source": "user"}, headers=tok
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["id"] == note["id"]
    assert edited.json()["text"] == "the base model is Qwen3-14B"
    assert edited.json()["created"] == note["created"] and edited.json()["revised"]

    listed = c.get("/api/memory", headers=tok).json()["notes"]
    assert [n["text"] for n in listed] == ["the base model is Qwen3-14B"], "the edit did not reach the store"

    assert c.delete(f"/api/memory/notes/{note['id']}", headers=tok).status_code == 200
    assert c.get("/api/memory", headers=tok).json()["notes"] == []


def test_editing_a_note_into_a_ledger_number_is_refused_over_the_api(tmp_path: Path) -> None:
    """Evidence comes only from the kernel, and an edit reaches the same store a creation does."""
    root = tmp_path
    init_project(root)
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    tok = {TOKEN_HEADER: app_token(root)}

    note = c.post("/api/memory/notes", json={"text": "the legal objective is running"}, headers=tok).json()
    refused = c.patch(f"/api/memory/notes/{note['id']}", json={"text": "it reached 49%"}, headers=tok)

    assert refused.status_code == 422
    assert c.get("/api/memory", headers=tok).json()["notes"][0]["text"] == "the legal objective is running"


def test_correcting_or_deleting_a_note_that_is_not_there_is_a_404(tmp_path: Path) -> None:
    root = tmp_path
    init_project(root)
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    tok = {TOKEN_HEADER: app_token(root)}

    assert c.patch("/api/memory/notes/no-such-note", json={"text": "anything"}, headers=tok).status_code == 404
    assert c.delete("/api/memory/notes/no-such-note", headers=tok).status_code == 404


def test_a_workspace_configures_its_own_telegram_bot_and_the_token_never_comes_back(tmp_path: Path) -> None:
    """A user's notifications reach their own bot or nobody's: the engine's credential is the operator's, and
    `messaging.resolve_telegram` will not hand it to a workspace. So configuring this is the only way a user's
    notifications reach a phone — and the token, once stored, is never readable through any route."""
    root = tmp_path / "workspace"
    root.mkdir()
    init_project(root)
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    tok = {TOKEN_HEADER: app_token(root)}

    assert c.get("/api/messaging/telegram", headers=tok).json() == {
        "configured": False, "enabled": False, "chat_id": "", "from_environment": False,
    }

    stored = c.put(
        "/api/messaging/telegram",
        json={"token": "123456:ABC-DEF-secret", "chat_id": "8679892510"}, headers=tok,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json() == {
        "configured": True, "enabled": True, "chat_id": "8679892510", "from_environment": False,
    }

    # Every route that could plausibly echo it, checked against the response text rather than a field name.
    for path in ("/api/messaging/telegram", "/api/providers", "/api/status"):
        body = c.get(path, headers=tok)
        assert "ABC-DEF-secret" not in body.text, f"the bot token was readable through {path}"

    silenced = c.put("/api/messaging/telegram", json={"enabled": False}, headers=tok)
    assert silenced.json() == {
        "configured": True, "enabled": False, "chat_id": "8679892510", "from_environment": False,
    }, "silencing delivery must not forget the credential"

    assert c.delete("/api/messaging/telegram", headers=tok).json()["configured"] is False


def test_a_bot_token_with_nowhere_to_deliver_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    init_project(root)
    c = TestClient(create_app(root), base_url="http://127.0.0.1:8008")
    tok = {TOKEN_HEADER: app_token(root)}

    refused = c.put("/api/messaging/telegram", json={"token": "123456:ABC"}, headers=tok)
    assert refused.status_code == 422
    assert c.get("/api/messaging/telegram", headers=tok).json()["configured"] is False


def test_a_workspace_that_has_run_nothing_still_serves_every_page(tmp_path: Path) -> None:
    """A fresh install has no ledger — `research/` is gitignored and nothing has run a night — and two
    endpoints opened it unconditionally. So installing this engine and opening the Candidates or Nights page
    produced a FileNotFoundError from the server, before the user had done anything wrong.

    It surfaced as CI's live-interface job failing with `[WebServer] FileNotFoundError`, on the one job that
    drives a real engine from a clean checkout. Nothing run yet means no nights and no candidates, which is an
    empty answer rather than a crash.
    """
    root = tmp_path / "never-used"
    root.mkdir()
    assert not (root / "research" / "ledger.jsonl").exists()
    client = TestClient(create_app(root), base_url="http://127.0.0.1:8008")

    for path in ("/api/nights", "/api/candidates"):
        answer = client.get(path)
        assert answer.status_code == 200, f"{path} answered {answer.status_code} on a workspace with no ledger"

    assert client.get("/api/nights").json() in ([], {"nights": []})
