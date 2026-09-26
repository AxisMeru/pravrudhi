"""Bounded judge concurrency (`house_judge.max_concurrency` / `NYAYA_JUDGE_MAX_CONCURRENCY`, docs/decisions
2026-09-24): `NyayaAgent._judge_elements` judges a contract's elements/denials one at a time by default
(byte-identical to the pre-concurrency code path) or, with a pool > 1, through a bounded `ThreadPoolExecutor`
-- collecting results and audit steps, then emitting both in the tasks' ORIGINAL order regardless of which
task's (fake) HTTP call finished first.

The judge here is a thread-safe, deterministic test double: a fixed script keyed by element name, with a
small random sleep so concurrent tasks really do interleave, never a live model. `ScriptedJudge` in
`test_nyaya_agent.py` is NOT thread-safe (it pops from a per-element list without a lock) and is not reused
here for that reason.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import (
    AgentConfig,
    JudgeMisconfigured,
    NyayaAgent,
    _judge_accounting,
    _judge_call_record,
    load_agent_config,
)
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest
from pravrudhi.models.openai_compat import HTTPStatusError

REPO = Path(__file__).resolve().parent.parent

N_ELEMENTS = 6
ELEMENTS = [f"el{i}" for i in range(1, N_ELEMENTS + 1)]
FACTS = [f"F{i} fact text carrying marker quote{i} for the element." for i in range(1, N_ELEMENTS + 1)]
CONTRACT = reg.DescribedContract("bns69", list(ELEMENTS), [])


def _script_all_established() -> dict[str, ElementJudgment]:
    return {
        el: ElementJudgment("established", 0.9, f"F{i}", f"quote{i}", "model")
        for i, el in enumerate(ELEMENTS, start=1)
    }


def _script_mixed() -> dict[str, ElementJudgment]:
    """`el3` not established; the rest established -- so the contract ABSTAINs (missing_element), the same
    outcome regardless of concurrency."""
    out = _script_all_established()
    out["el3"] = ElementJudgment("not_established", 0.1)
    return out


@dataclass
class ConcurrentFakeJudge:
    """A thread-safe, deterministic `Judge` test double: `judge()` looks up the reply for `request.element` in
    a fixed script (never mutated after construction) and sleeps a random, bounded interval first so
    concurrent tasks really do race. `calls` records every invocation under a lock -- the only mutable state,
    and it is never read by `judge()` itself, so it introduces no timing dependency into the JUDGMENT."""

    script: Mapping[str, ElementJudgment | Exception]
    name: str = "concurrent_fake"
    min_sleep_s: float = 0.0
    max_sleep_s: float = 0.008
    calls: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        if self.max_sleep_s > 0:
            time.sleep(random.uniform(self.min_sleep_s, self.max_sleep_s))
        with self._lock:
            self.calls.append(request.element)
        item = self.script[request.element]
        if isinstance(item, Exception):
            raise item
        return item


@dataclass
class SlowFaultJudge:
    """Thread-safe fake for the config-fault fail-fast tests: `fault_element`'s call raises `fault`
    IMMEDIATELY (no sleep); every other element's call sleeps `slow_s` before answering established -- long
    enough that a fail-fast cancellation has time to reach every task still queued behind the busy workers,
    while a task already running (and so NOT cancellable) is still caught mid-sleep when the fault lands."""

    fault_element: str
    fault: Exception
    established: Mapping[str, ElementJudgment]
    slow_s: float = 0.3
    name: str = "slow_fault_fake"
    calls: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        with self._lock:
            self.calls.append(request.element)
        if request.element == self.fault_element:
            raise self.fault
        time.sleep(self.slow_s)
        return self.established[request.element]


@dataclass
class FakeRegistry:
    """The Lean side, scored exactly the way the real binary and `ScriptedRegistry` (test_nyaya_agent.py)
    are: an omitted required element or an asserted denial flags the verdict."""

    contract: reg.DescribedContract
    sha256: str = "fake"

    def list_contracts(self) -> dict[str, list[str]]:
        return {self.contract.contract_id: ["Fake Statute §1"]}

    def describe(self, contract_id: str) -> reg.DescribedContract:
        assert contract_id == self.contract.contract_id
        return self.contract

    def source_text(self, contract_id: str) -> str:
        return "OFFICIAL fake statute text (never shown to the judge)"

    def check(self, assertions: Mapping[str, bool], contract_id: str) -> dict[str, Any]:
        c = self.contract
        met = {k for k, v in assertions.items() if v}
        denied = [e for e in c.denials if e in met]
        omitted = [e for e in c.elements if e not in met]
        unlicensed = [e for e in met if e not in c.elements and e not in c.denials]
        flagged = denied or omitted or unlicensed
        return {
            "verdict": "flagged" if flagged else "grounded",
            "denied_claims": denied,
            "unlicensed_claims": unlicensed,
            "omitted_claims": omitted,
        }


def _config(tmp_path: Path, max_concurrency: int, **over: Any) -> AgentConfig:
    base: dict[str, Any] = {
        "tau": 0.74,
        "refer_band": (0.5, 0.74),
        "max_retries": 2,
        "audit_dir": tmp_path / f"audit_c{max_concurrency}",
        "judge_statute_text": {CONTRACT.contract_id: "TRAINING statute text for manyel"},
        "max_concurrency": max_concurrency,
        # This file's tests are about concurrency, not the validated_contracts allowlist (issue #36) --
        # explicitly validating CONTRACT.contract_id here is what keeps them testing that, unaffected by the
        # gate's own production-safe fail-closed default.
        "validated_contracts": frozenset({CONTRACT.contract_id}),
    }
    base.update(over)
    return AgentConfig(**base)


def _run_with_concurrency(
    tmp_path: Path, script: Mapping[str, ElementJudgment | Exception], max_concurrency: int, **cfg_over: Any
) -> tuple[Any, ConcurrentFakeJudge]:
    judge = ConcurrentFakeJudge(dict(script))
    registry = FakeRegistry(CONTRACT)
    agent = NyayaAgent(judge, registry, _config(tmp_path, max_concurrency, **cfg_over))
    run = agent.run(FACTS, narrative="", contract_ids=[CONTRACT.contract_id])
    return run, judge


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def _normalized(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strips real wall-clock values (`ts`, every `wall_ms`, and `judge_accounting`'s latency fields) so a
    serial and a concurrent run's audit trails can be compared for identical CONTENT and ORDER -- concurrency
    is expected to change how long things take, never what happened or in what order it is recorded."""
    out = []
    for line in lines:
        line = dict(line)
        line.pop("ts", None)
        line.pop("run_id", None)
        # `inputs_sha256` for a handful of steps (run_start, ingest, judge_accounting, run_end) hashes an
        # object that itself carries the run_id -- a fresh uuid every run, by design (AuditTrail's docstring),
        # never meant to repeat. It is irrelevant to whether concurrency changed anything.
        line.pop("inputs_sha256", None)
        line["wall_ms"] = None
        if line.get("step") == "judge_accounting":
            output = dict(line["output"])
            output["total_latency_ms"] = None
            output["max_latency_ms"] = None
            line["output"] = output
        out.append(line)
    return out


class TestConcurrencyIdentical:
    def test_concurrency_1_vs_4_identical_agentrun(self, tmp_path: Path) -> None:
        run1, _ = _run_with_concurrency(tmp_path, _script_all_established(), 1)
        run4, _ = _run_with_concurrency(tmp_path, _script_all_established(), 4)
        d1, d4 = run1.to_dict(), run4.to_dict()
        for d in (d1, d4):
            d.pop("run_id")
            d.pop("audit_path")
            d["judge_accounting"] = {**d["judge_accounting"], "total_latency_ms": None, "max_latency_ms": None}
        assert d1 == d4

    def test_concurrency_1_vs_4_identical_audit_trail(self, tmp_path: Path) -> None:
        run1, _ = _run_with_concurrency(tmp_path, _script_all_established(), 1)
        run4, _ = _run_with_concurrency(tmp_path, _script_all_established(), 4)
        lines1 = _normalized(_audit_lines(run1.audit_path))
        lines4 = _normalized(_audit_lines(run4.audit_path))
        assert lines1 == lines4

    def test_ordering_preserved_regardless_of_random_sleep(self, tmp_path: Path) -> None:
        run4, _ = _run_with_concurrency(tmp_path, _script_all_established(), 4)
        contract_result = run4.contracts[0]
        assert [r.element for r in contract_result.elements] == ELEMENTS

    def test_mixed_outcome_identical_across_concurrency(self, tmp_path: Path) -> None:
        run1, _ = _run_with_concurrency(tmp_path, _script_mixed(), 1)
        run4, _ = _run_with_concurrency(tmp_path, _script_mixed(), 4)
        assert run1.contracts[0].outcome == run4.contracts[0].outcome == "ABSTAIN"
        assert run1.contracts[0].reason == run4.contracts[0].reason == "missing_element"

    def test_default_max_concurrency_is_1_today_unchanged(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path, max_concurrency=1)
        assert cfg.max_concurrency == 1
        judge = ConcurrentFakeJudge(_script_all_established())
        agent = NyayaAgent(judge, FakeRegistry(CONTRACT), cfg)
        # With max_concurrency == 1, `_judge_elements` takes the serial branch: every call happens on this
        # thread, so `judge.calls` is filled in exactly the tasks' order.
        agent.run(FACTS, narrative="", contract_ids=[CONTRACT.contract_id])
        assert judge.calls == ELEMENTS


class TestExceptionPropagation:
    def _script_with_failure(self) -> dict[str, ElementJudgment | Exception]:
        out: dict[str, ElementJudgment | Exception] = dict(_script_all_established())
        out["el4"] = RuntimeError("judge backend unreachable (test)")
        return out

    def test_one_elements_judge_error_never_cancels_the_others(self, tmp_path: Path) -> None:
        run4, judge = _run_with_concurrency(tmp_path, self._script_with_failure(), 4)
        contract_result = run4.contracts[0]
        # Every OTHER element was still judged and recorded, even though el4's judge kept raising.
        by_name = {r.element: r for r in contract_result.elements}
        assert by_name["el4"].error is not None
        assert by_name["el4"].status == "not_established"
        for el in ELEMENTS:
            if el != "el4":
                assert by_name[el].status == "established"
        assert contract_result.outcome == "ABSTAIN"
        assert contract_result.reason == "judge_error"

    def test_exception_path_identical_across_concurrency(self, tmp_path: Path) -> None:
        run1, _ = _run_with_concurrency(tmp_path, self._script_with_failure(), 1)
        run4, _ = _run_with_concurrency(tmp_path, self._script_with_failure(), 4)
        d1, d4 = run1.to_dict(), run4.to_dict()
        for d in (d1, d4):
            d.pop("run_id")
            d.pop("audit_path")
            d["judge_accounting"] = {**d["judge_accounting"], "total_latency_ms": None, "max_latency_ms": None}
        assert d1 == d4


class TestJudgeAccounting:
    def test_call_counts_correct_single_judge_no_second(self, tmp_path: Path) -> None:
        run1, judge1 = _run_with_concurrency(tmp_path, _script_all_established(), 1)
        run4, judge4 = _run_with_concurrency(tmp_path, _script_all_established(), 4)
        # Every element establishes on attempt 1 (a valid quote), so exactly one judge call per element.
        assert run1.judge_accounting["judge_calls_primary"] == N_ELEMENTS
        assert run4.judge_accounting["judge_calls_primary"] == N_ELEMENTS
        assert run1.judge_accounting["judge_calls_second"] == 0
        assert run4.judge_accounting["judge_calls_second"] == 0
        assert len(judge1.calls) == N_ELEMENTS
        assert len(judge4.calls) == N_ELEMENTS

    def test_call_counts_include_retried_failures(self, tmp_path: Path) -> None:
        script: dict[str, ElementJudgment | Exception] = dict(_script_all_established())
        script["el4"] = RuntimeError("boom")
        run, _ = _run_with_concurrency(tmp_path, script, 1, max_retries=2)
        # el4's judge raises on every one of its (max_retries + 1) attempts; the other 5 elements succeed on
        # attempt 1 each.
        assert run.judge_accounting["judge_calls_primary"] == (N_ELEMENTS - 1) + 3

    def test_judge_call_record_and_accounting_helpers(self) -> None:
        j1 = ElementJudgment("established", 0.9, "F1", "q", "model", backend_used=0)
        j2 = ElementJudgment(
            "established", 0.9, "F1", "q", "model", backend_used=1,
            second_judge="house", second_status="established", backend_used_second=0,
        )
        records = [
            _judge_call_record(j1, 12.0, ok=True),
            _judge_call_record(j2, 34.0, ok=True),
            _judge_call_record(None, 5.0, ok=False),
        ]
        acc = _judge_accounting(records)
        assert acc["judge_calls_primary"] == 3
        assert acc["judge_calls_second"] == 1
        assert acc["total_latency_ms"] == pytest.approx(51.0)
        assert acc["max_latency_ms"] == pytest.approx(34.0)
        assert acc["backend_used_counts"] == {"0": 1, "1": 1}
        assert acc["backend_used_second_counts"] == {"0": 1}

    def test_second_skipped_is_not_a_second_call(self) -> None:
        skipped = ElementJudgment(
            "not_established", 0.9, second_judge=None, second_skipped=False,
        )
        # second_judge is None -- no second judge configured at all -- never counted as a second call.
        rec = _judge_call_record(skipped, 1.0, ok=True)
        assert rec.second_called is False
        primary_rejected_before_second = ElementJudgment(
            "not_established", 0.1, second_judge="house", second_skipped=True, second_skip_reason="primary_not_established",
        )
        rec2 = _judge_call_record(primary_rejected_before_second, 1.0, ok=True)
        assert rec2.second_called is False

    def test_judge_accounting_no_calls(self) -> None:
        assert _judge_accounting([]) == {
            "judge_calls_primary": 0,
            "judge_calls_second": 0,
            "total_latency_ms": 0.0,
            "max_latency_ms": 0.0,
            "backend_used_counts": {},
            "backend_used_second_counts": {},
        }


class TestMaxConcurrencyConfig:
    def test_bad_max_concurrency_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            _config(tmp_path, max_concurrency=0)

    def test_repo_config_default_max_concurrency_is_1(self) -> None:
        """The shipped config has no `max_concurrency:` key under `house_judge`: bounded concurrency is
        opt-in, never the default -- an existing deployment's behaviour does not change."""
        assert load_agent_config(REPO).max_concurrency == 1

    def test_env_var_introduces_max_concurrency_with_no_yaml_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NYAYA_JUDGE_MAX_CONCURRENCY", "6")
        assert load_agent_config(REPO).max_concurrency == 6

    def test_env_var_overrides_a_configured_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import yaml

        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        body = yaml.safe_load((REPO / "configs" / "nyaya_agent.yaml").read_text())
        body.setdefault("house_judge", {})["max_concurrency"] = 3
        (cfg_dir / "nyaya_agent.yaml").write_text(yaml.safe_dump(body))
        monkeypatch.delenv("NYAYA_JUDGE_MAX_CONCURRENCY", raising=False)
        assert load_agent_config(tmp_path).max_concurrency == 3
        monkeypatch.setenv("NYAYA_JUDGE_MAX_CONCURRENCY", "8")
        assert load_agent_config(tmp_path).max_concurrency == 8


class TestJudgePoolIsolation:
    def test_house_builds_one_independent_judge_stack_per_worker(self, tmp_path: Path) -> None:
        """`NyayaAgent.house` must never share a single `HouseJudge` (or `AndGateJudge`) instance across
        concurrent workers -- its `ChatClient.model` attribute is mutated per call (nyaya_judges.HouseJudge's
        `_complete_with_fallback`), so two threads sharing one instance could race and send the wrong model
        id. This does not call `.house()` (it would need a live judge endpoint); it asserts the INVARIANT
        `_judge_elements` relies on: a `judge_pool` sized to `max_concurrency` gives each worker its own
        instance when there are at least that many."""
        judges = [ConcurrentFakeJudge(_script_all_established(), name=f"w{i}") for i in range(4)]
        cfg = _config(tmp_path, max_concurrency=4)
        agent = NyayaAgent(judges[0], FakeRegistry(CONTRACT), cfg, judge_pool=judges)
        assert agent.judge_pool == judges
        run = agent.run(FACTS, narrative="", contract_ids=[CONTRACT.contract_id])
        assert run.contracts[0].outcome == "PROOF"
        # Every one of the 4 instances actually did some of the work (no instance sat idle while another was
        # reused for everything) -- the queue-based checkout spread the 6 tasks across all 4 workers.
        assert sum(1 for j in judges if j.calls) >= 2


class TestRealJudgeConcurrencyIsUnsafeWithoutAPool:
    def test_house_judge_client_model_is_shared_mutable_state(self) -> None:
        """Documents WHY `NyayaAgent.house` builds a pool instead of sharing one judge: `HouseJudge`'s
        `_complete_with_fallback` closure (nyaya_judges.py) does `client.model = _model_for(i)` on `self`'s
        OWN `ChatClient` immediately before each `client.complete(...)` call -- a plain attribute write with no
        lock. Two threads calling the SAME `HouseJudge.judge()` concurrently could interleave their
        `client.model = ...` writes and reads, so one thread's request could be sent under the other's model
        id. This test does not spin up threads against a real server (there is none here); it is a static
        assertion that the race exists in the source, so a future refactor of `HouseJudge` cannot silently
        drop the pooling this module relies on without failing a test."""
        import inspect

        from pravrudhi.application import nyaya_judges

        src = inspect.getsource(nyaya_judges.HouseJudge)
        assert "client.model = _model_for(i)" in src, (
            "HouseJudge no longer mutates a shared ChatClient.model per call -- if it is now provably "
            "thread-safe, NyayaAgent.house's per-worker judge_pool may be relaxed to share one instance; "
            "until then this test's premise, and the pooling, must both be revisited together."
        )


class TestConfigFaultFailFast:
    """R1 (2026-09-24): a config fault (`JudgeMisconfigured`, a 4xx like a bad key) under concurrency must
    (a) fail fast -- cancel every not-yet-started task rather than burning N calls for a misconfigured
    deployment, and (b) still record every call that ACTUALLY happened, in original task order, tagging any
    task that only finished after the fault `after_fault: true` -- never silently drop it, and never let one
    task's fault erase every other task's already-buffered audit steps."""

    N = 8
    FAULT_EL = "elF"
    SLOW_S = 0.25
    ELEMENTS_F = [FAULT_EL] + [f"el{i}" for i in range(1, N)]
    FACTS_F = [f"F{i} fact text carrying marker quote{i}." for i in range(1, N + 1)]
    CONTRACT_F = reg.DescribedContract("bns69", list(ELEMENTS_F), [])

    def _judge(self) -> SlowFaultJudge:
        established = {
            el: ElementJudgment("established", 0.9, f"F{i}", f"quote{i}", "model")
            for i, el in enumerate(self.ELEMENTS_F[1:], start=2)
        }
        return SlowFaultJudge(self.FAULT_EL, HTTPStatusError(401, "bad key (test)"), established, slow_s=self.SLOW_S)

    def _agent(self, tmp_path: Path, judge: SlowFaultJudge, max_concurrency: int) -> NyayaAgent:
        cfg = AgentConfig(
            tau=0.74, refer_band=(0.5, 0.74), max_retries=0, audit_dir=tmp_path / f"audit_fault_{max_concurrency}",
            judge_statute_text={self.CONTRACT_F.contract_id: "TRAINING statute text"},
            max_concurrency=max_concurrency,
        )
        return NyayaAgent(judge, FakeRegistry(self.CONTRACT_F), cfg)

    def _audit_path(self, agent: NyayaAgent) -> Path:
        files = list(agent.config.audit_dir.glob("*.jsonl"))
        assert len(files) == 1, f"expected exactly one audit file, found {files}"
        return files[0]

    def test_serial_raises_judge_misconfigured_and_calls_only_up_to_the_fault(self, tmp_path: Path) -> None:
        judge = self._judge()
        agent = self._agent(tmp_path, judge, max_concurrency=1)
        with pytest.raises(JudgeMisconfigured):
            agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])
        # Serial: the fault element is first in the list, so nothing after it is ever even attempted.
        assert judge.calls == [self.FAULT_EL]
        lines = _audit_lines(self._audit_path(agent))
        judge_steps = [x for x in lines if x["step"] == "judge"]
        assert [s["output"]["element"] for s in judge_steps] == [self.FAULT_EL]
        assert not any(s["output"].get("after_fault") for s in judge_steps)

    def test_concurrent_raises_the_same_exception_type_as_serial(self, tmp_path: Path) -> None:
        serial_agent = self._agent(tmp_path, self._judge(), max_concurrency=1)
        with pytest.raises(JudgeMisconfigured) as serial_exc:
            serial_agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])

        concurrent_agent = self._agent(tmp_path, self._judge(), max_concurrency=2)
        with pytest.raises(JudgeMisconfigured) as concurrent_exc:
            concurrent_agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])

        assert type(serial_exc.value) is type(concurrent_exc.value)

    def test_concurrent_fails_fast_some_tasks_never_start(self, tmp_path: Path) -> None:
        judge = self._judge()
        agent = self._agent(tmp_path, judge, max_concurrency=2)
        with pytest.raises(JudgeMisconfigured):
            agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])
        # Fail-fast: with 8 tasks and only 2 workers, and every non-fault call sleeping 0.25s, the tasks still
        # queued behind the busy workers are cancelled long before they could ever start -- at most the two
        # tasks already dispatched to workers when the (near-instant) fault landed were ever called.
        assert len(judge.calls) <= 3
        assert self.FAULT_EL in judge.calls

    def test_audit_records_every_call_actually_made_in_original_order(self, tmp_path: Path) -> None:
        judge = self._judge()
        agent = self._agent(tmp_path, judge, max_concurrency=2)
        with pytest.raises(JudgeMisconfigured):
            agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])
        lines = _audit_lines(self._audit_path(agent))
        judge_steps = [x for x in lines if x["step"] == "judge"]
        audited_elements = [s["output"]["element"] for s in judge_steps]
        # Every element the fake judge actually recorded a call for -- and ONLY those -- appears in the
        # audit, in the tasks' ORIGINAL order (not completion order).
        assert set(audited_elements) == set(judge.calls)
        assert audited_elements == [el for el in self.ELEMENTS_F if el in judge.calls]
        # A task never called leaves no audit trace at all (no partial/placeholder step).
        for el in self.ELEMENTS_F:
            if el not in judge.calls:
                assert el not in audited_elements

    def test_tasks_already_running_are_flushed_tagged_after_fault(self, tmp_path: Path) -> None:
        judge = self._judge()
        agent = self._agent(tmp_path, judge, max_concurrency=2)
        with pytest.raises(JudgeMisconfigured):
            agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])
        lines = _audit_lines(self._audit_path(agent))
        judge_steps = {x["output"]["element"]: x["output"] for x in lines if x["step"] == "judge"}
        # The fault element's own step is never tagged (it is the CAUSE, not a consequence).
        assert judge_steps[self.FAULT_EL].get("after_fault") is not True
        # Any OTHER element that was actually called (it was already running -- sleeping SLOW_S -- when the
        # fault landed, and could not be interrupted) is flushed anyway, tagged `after_fault: true`.
        for el, output in judge_steps.items():
            if el != self.FAULT_EL:
                assert output.get("after_fault") is True, f"{el} was called but not tagged after_fault"

    def test_cancelled_before_starting_tasks_make_no_call_and_no_audit_step(self, tmp_path: Path) -> None:
        judge = self._judge()
        agent = self._agent(tmp_path, judge, max_concurrency=2)
        with pytest.raises(JudgeMisconfigured):
            agent.run(self.FACTS_F, narrative="", contract_ids=[self.CONTRACT_F.contract_id])
        never_called = [el for el in self.ELEMENTS_F if el not in judge.calls]
        assert never_called  # the whole point of fail-fast: SOME tasks were cancelled before they could start
        lines = _audit_lines(self._audit_path(agent))
        judge_steps = [x for x in lines if x["step"] == "judge"]
        audited = {s["output"]["element"] for s in judge_steps}
        assert not (set(never_called) & audited)
