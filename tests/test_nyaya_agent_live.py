"""The Nyaya agentic loop end to end against the REAL house judge (the vLLM element-judge server) and the REAL
pinned Lean `score` binary. Skipped unless both are present on this host.

The server is shared: this module makes at most 1 + 7 * (1 + MAX_RETRIES) requests (one /v1/models probe,
then seven elements across three contracts, each retried at most once), sequentially.

The three fact patterns are TOY, hand-written for this test -- not drawn from, and not paraphrasing, any
evaluation set. What is asserted is the loop's shape (every element judged, quote-checked, Lean-checked,
audited, an outcome in the four) plus one outcome that cannot legitimately be PROOF or DENIAL.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from pravrudhi.application.nyaya_agent import BinaryRegistry, NyayaAgent, load_agent_config
from pravrudhi.application.nyaya_judges import HouseJudge

REPO = Path(__file__).resolve().parent.parent
SCORE_BIN = Path("/home/ss/projects/prabhasa-nyaya/.worktrees/trackA-wave1/lean/.lake/build/bin/score")
BASE_URL = "http://127.0.0.1:8110/v1"
MAX_RETRIES = 1  # keeps the shared server's worst case at 15 requests


def _served_model() -> str | None:
    try:
        with urllib.request.urlopen(BASE_URL + "/models", timeout=3) as r:
            data = json.loads(r.read().decode())
        return str(data["data"][0]["id"])
    except Exception:
        return None


@pytest.fixture(scope="module")
def served_model() -> str:
    """Probed only when a live test is actually selected (never at collection), so collecting the suite sends
    nothing to the shared server."""
    model = _served_model() if SCORE_BIN.exists() else None
    if model is None:
        pytest.skip(f"needs the vLLM element-judge server at {BASE_URL} and the score binary at {SCORE_BIN}")
    return model


TOY_PATTERNS = {
    # TOY 1 -- husband's cruelty over a money demand (BNS 85/86).
    "bns85": dict(
        narrative=(
            "TOY: Rohan married Priya in 2019. From 2021 Rohan repeatedly demanded that Priya obtain five lakh rupees "
            "from her parents, and beat and starved her whenever she said she could not."
        ),
        facts=[
            "TOY: Rohan and Priya were married to each other in 2019, and Rohan is Priya's husband.",
            "TOY: From 2021 Rohan repeatedly demanded that Priya get five lakh rupees from her parents, and he beat her "
            "and denied her food whenever she refused, to force her parents to pay.",
        ],
    ),
    # TOY 2 -- entrusted property, but nobody else was let misuse it (IPC 405, wilfully-suffers limb).
    "ipc405_wilfully_suffers": dict(
        narrative=(
            "TOY: Meera left her car with her neighbour Sunil while she travelled. Sunil kept it locked in his garage and "
            "returned it to her, unused and undamaged, when she came back."
        ),
        facts=[
            "TOY: Before travelling abroad, Meera handed her car and its keys to her neighbour Sunil to look after.",
            "TOY: Sunil kept the car locked in his own garage the whole time, let nobody drive it, and returned it to "
            "Meera unused and undamaged.",
        ],
    ),
    # TOY 3 -- entrusted money spent against the owner's direction, in the honest belief it helped her (IPC 405).
    "ipc405_misappropriation": dict(
        narrative=(
            "TOY: Anil managed Kavya's shop while she was in hospital and held its takings. Against her instruction to "
            "bank them, he used them to pay an overdue supplier, honestly believing this would save her business."
        ),
        facts=[
            "TOY: While Kavya was in hospital, Anil was entrusted with managing her shop and holding its daily takings.",
            "TOY: Kavya told Anil to deposit all takings in her bank account.",
            "TOY: Anil instead used the takings to pay Kavya's overdue supplier, honestly believing it was the only way "
            "to stop the supplier cutting off her stock, and kept every receipt for her.",
        ],
    ),
}


def test_house_judge_loop_end_to_end(tmp_path: Path, served_model: str) -> None:
    cfg = replace(load_agent_config(REPO), max_retries=MAX_RETRIES, audit_dir=tmp_path / "audit", score_bin=SCORE_BIN)
    registry = BinaryRegistry(SCORE_BIN, pinned_sha256=cfg.pinned_score_sha256)
    hj = cfg.house_judge
    judge = HouseJudge(
        tau=cfg.tau,
        statute_chars=int(hj["statute_chars"]),
        base_url=BASE_URL,
        model=served_model,
        max_tokens=int(hj["max_tokens"]),
        top_logprobs=int(hj["top_logprobs"]),
        timeout_s=int(hj["timeout_s"]),
    )
    agent = NyayaAgent(judge, registry, cfg)

    summary = {}
    judge_calls = 0
    for cid, pattern in TOY_PATTERNS.items():
        run = agent.run(pattern["facts"], narrative=pattern["narrative"], contract_ids=[cid])
        (c,) = run.contracts
        lines = [json.loads(x) for x in run.audit_path.read_text().splitlines()]
        judge_calls += sum(x["step"] == "judge" for x in lines)
        assert c.outcome in ("PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER")
        assert c.reason != "judge_error", [e.error for e in c.elements]
        assert c.lean is not None  # every contract reached the real binary
        assert all(e.p_established is not None and 0.0 <= e.p_established <= 1.0 for e in c.elements)
        assert {"judge", "assemble", "lean_check", "outcome"} <= {x["step"] for x in lines}
        summary[cid] = {
            "outcome": c.outcome,
            "reason": c.reason,
            "lean_outcome": c.lean_outcome,
            "elements": [
                {
                    "p": round(e.p_established or 0.0, 4),
                    "claimed": e.claimed,
                    "status": e.status,
                    "span": f"{e.fact_id}:{e.start}:{e.end}",
                    "quote_check": e.quote_check,
                    "attempts": e.attempts,
                }
                for e in c.elements
            ],
        }
    print(json.dumps(summary, indent=1))
    assert judge_calls <= 7 * (1 + MAX_RETRIES)
    # Nothing in TOY 2 has anyone else misusing the car: that contract cannot prove, and it has no defeater.
    assert summary["ipc405_wilfully_suffers"]["outcome"] in ("ABSTAIN", "REFER_TO_LAWYER")
