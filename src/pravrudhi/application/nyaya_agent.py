"""The Nyaya agentic loop: a user's facts in; per registry Contract, PROOF / DENIAL / ABSTAIN / REFER_TO_LAWYER
out; every step on a JSONL audit trail.

    ingest facts -> F1..Fn (sha256 each)
    select contracts (deterministic, from the binary's own --list-contracts)
    for each contract: describe it (--describe-contract: required elements + DENY defeaters)
        record whether the judge's training statute text differs from the binary's official text
            (--describe-source): `statute_text_mismatch`, for the audit and M2 -- the official text is never
            fed to the judge
        judge every element (Judge protocol, nyaya_judges) -> {status, fact_id, quote}; the SYSTEM locates
            the quote verbatim in the fact and computes the offsets (nyaya_quote, `offsets_source: "system"`)
            bounded retry: <= max_retries re-asks with the SAME request (same training statute text), used
            only for the quote -- attempt 1's status and p_established stand
        assemble the REG wire deterministically (assertions in the Contract's own order)
        Lean check (nyaya_lean_registry.check_registry, pinned binary sha256)
        decide the outcome

This is the pravrudhi-side mirror of prabhasa-nyaya's element-first harness (`element_first_harness.
assemble_wire`, WS-B): the same three record shapes decide the outcome -- denial established -> DENIAL (a
definite negative, not an abstention); every required element established and no denial -> PROOF; otherwise
ABSTAIN (missing element). It is mirrored, never imported (ADR-0001 independence); the pravrudhi wire is the
`REG` assertion line the Lean binary scores, and the outcome is read from the binary's answer, then cross-
checked against the local mirror (`expected_outcome`) -- a disagreement is recorded, never smoothed over.

Rules enforced here rather than asked of a judge:

* **A quote is checked, never repaired.** An element whose quote is not a verbatim substring of the named fact
  after the retries is not established. A DENY defeater the judge calls present but cannot quote is the one exception to "treat
  as absent": treating it as absent would let the contract PROVE on the strength of a failed quote, so the
  contract is referred instead (`denial_unquotable`).
* **Uncertainty is referred, not rounded.** Any judged element whose `p_established` falls in the configured
  `refer_band` [low, high) makes the contract REFER_TO_LAWYER; the Lean check still runs and is recorded. The
  shipped band [0.5, 0.74) is an UNVALIDATED DEFAULT, CALIBRATION PENDING M3 -- not pre-registered, not fitted.
* **The judge sees one statute text.** Its training text (config `judge_statute_text`) on every attempt; a
  contract with none is not judged (ABSTAIN, `no_training_statute_text`).
* **A judge that cannot answer is a gap.** An element whose judge raised on every attempt makes the contract
  ABSTAIN (`judge_error`) with no Lean call -- no outcome is decided over an element nobody judged.
* **Nothing here is evidence.** A run is a check of a judge's reading of the user's facts against the Lean
  registry; its record is testimony (`agama`), written under `research/nyaya/agent_runs/`, never the ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_judges import ElementJudgment, Judge, JudgeRequest
from pravrudhi.application.nyaya_quote import QuoteLocation, locate_quote

Outcome = Literal["PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER"]
CONFIG_PATH = Path("configs") / "nyaya_agent.yaml"


class BinaryShaMismatch(RuntimeError):
    """The score binary's sha256 is not the pinned one -- refused before any call."""


# -- config ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentConfig:
    tau: float
    refer_band: tuple[float, float]
    max_retries: int
    audit_dir: Path
    judge_statute_text: Mapping[str, str] = field(default_factory=dict)
    pinned_score_sha256: str | None = None
    score_bin: Path | None = None
    house_judge: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        low, high = self.refer_band
        if not (0.0 <= low <= high <= 1.0):
            raise ValueError(f"refer_band must be 0 <= low <= high <= 1, got {self.refer_band}")
        if not (0.0 < self.tau <= 1.0):
            raise ValueError(f"tau must be in (0, 1], got {self.tau}")
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")

    def in_band(self, p: float) -> bool:
        low, high = self.refer_band
        return low <= p < high


def load_agent_config(root: Path) -> AgentConfig:
    """`configs/nyaya_agent.yaml` under `root`; relative paths resolve against `root`. The score binary path
    follows `nyaya_gold_score.score_bin_path`'s precedence (env var first) when the file names none. The house
    judge's base_url can be overridden by NYAYA_HOUSE_JUDGE_BASE_URL env var (for container deployments where
    localhost does not refer to the host)."""
    import yaml

    from pravrudhi.application.nyaya_gold_score import SCORE_BIN_ENV, score_bin_path

    root = Path(root)
    body = yaml.safe_load((root / CONFIG_PATH).read_text()) or {}
    env_bin = os.environ.get(SCORE_BIN_ENV)
    if env_bin:
        score_bin = Path(env_bin)
    elif body.get("score_bin"):
        score_bin = (root / str(body["score_bin"])).resolve()
    else:
        score_bin = score_bin_path(root)
    low, high = body["refer_band"]
    house_judge = dict(body.get("house_judge") or {})
    # Allow env override for judge base_url (container deployments)
    if os.environ.get("NYAYA_HOUSE_JUDGE_BASE_URL"):
        house_judge["base_url"] = os.environ["NYAYA_HOUSE_JUDGE_BASE_URL"]
    return AgentConfig(
        tau=float(body["tau"]),
        refer_band=(float(low), float(high)),
        max_retries=int(body["max_retries"]),
        audit_dir=root / str(body["audit_dir"]),
        judge_statute_text={str(k): str(v) for k, v in (body.get("judge_statute_text") or {}).items()},
        pinned_score_sha256=body.get("pinned_score_sha256"),
        score_bin=score_bin,
        house_judge=house_judge,
    )


# -- facts -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    sha256: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_facts(texts: Sequence[str]) -> tuple[Fact, ...]:
    """`F1..Fn` in the caller's order, each stripped and hashed. An empty fact is refused rather than numbered:
    a span into it could never be valid and its id would still reach the judge."""
    if not texts:
        raise ValueError("no facts to judge")
    out: list[Fact] = []
    for i, raw in enumerate(texts, start=1):
        text = raw.strip()
        if not text:
            raise ValueError(f"fact {i} is empty")
        out.append(Fact(f"F{i}", text, _sha(text)))
    return tuple(out)


# -- Lean side ---------------------------------------------------------------------------------------------


class Registry(Protocol):
    sha256: str

    def list_contracts(self) -> dict[str, list[str]]: ...

    def describe(self, contract_id: str) -> reg.DescribedContract: ...

    def source_text(self, contract_id: str) -> str: ...

    def check(self, assertions: Mapping[str, bool], contract_id: str) -> dict[str, Any]: ...


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BinaryRegistry:
    """The real Lean `score` binary, sha256-pinned: a binary whose FULL hash differs is refused at construction,
    before any subprocess call, so no run can mix two binaries' verdicts."""

    def __init__(self, score_bin: Path, *, pinned_sha256: str | None) -> None:
        self.score_bin = Path(score_bin)
        if not self.score_bin.exists():
            raise FileNotFoundError(f"score binary not found: {self.score_bin}")
        self.sha256 = file_sha256(self.score_bin)
        if pinned_sha256 is not None and self.sha256 != pinned_sha256:
            raise BinaryShaMismatch(f"{self.score_bin} sha256 {self.sha256} != pinned {pinned_sha256}")

    def list_contracts(self) -> dict[str, list[str]]:
        return reg.list_contracts(score_bin=self.score_bin)

    def describe(self, contract_id: str) -> reg.DescribedContract:
        return reg.describe_contract_detail(contract_id, score_bin=self.score_bin)

    def source_text(self, contract_id: str) -> str:
        return "\n".join(reg.describe_source(contract_id, score_bin=self.score_bin))

    def check(self, assertions: Mapping[str, bool], contract_id: str) -> dict[str, Any]:
        return reg.check_registry(dict(assertions), contract_id, score_bin=self.score_bin)


def select_contracts(
    listed: Mapping[str, Sequence[str]],
    *,
    contract_ids: Iterable[str] | None = None,
    sections: Iterable[str] | None = None,
) -> list[str]:
    """Which contracts to test, always in the binary's own `--list-contracts` order (so the same request
    selects the same list, however the caller ordered it). Candidates are the listed ids `check_registry`
    also knows. `contract_ids` names them; `sections` matches the source column (`Indian Penal Code §405`);
    neither means every candidate. A name or section that selects nothing is refused, never dropped."""
    candidates = [c for c in listed if c in reg.KNOWN_CONTRACT_IDS]
    if contract_ids is not None:
        wanted = set(contract_ids)
        bad = sorted(wanted - set(candidates))
        if bad:
            raise reg.UnknownContractError(f"not a checkable registry contract: {bad}; candidates: {candidates}")
        return [c for c in candidates if c in wanted]
    if sections is not None:
        secs = set(sections)
        chosen = [c for c in candidates if secs & set(listed[c])]
        if not chosen:
            raise reg.UnknownContractError(f"no checkable registry contract cites {sorted(secs)}")
        return chosen
    return candidates


# -- assembly ----------------------------------------------------------------------------------------------


def assemble_assertions(contract: reg.DescribedContract, established: Mapping[str, bool]) -> dict[str, bool]:
    """The REG wire's assertions, deterministically: every required element in the Contract's order with its
    judged status, then each DENY defeater ONLY when established (asserting a defeater is a refutation; an
    absent one is simply not sent). Mirrors `element_first_harness.assemble_wire`'s claim set."""
    out = {e: bool(established.get(e, False)) for e in contract.elements}
    for d in contract.denials:
        if established.get(d, False):
            out[d] = True
    return out


def expected_outcome(contract: reg.DescribedContract, assertions: Mapping[str, bool]) -> Outcome:
    """The outcome the element-first harness's three record shapes give these assertions: denial -> DENIAL,
    all required -> PROOF, else ABSTAIN. The local cross-check on the Lean binary's own answer."""
    if any(assertions.get(d, False) for d in contract.denials):
        return "DENIAL"
    if all(assertions.get(e, False) for e in contract.elements):
        return "PROOF"
    return "ABSTAIN"


def outcome_from_lean(lean: Mapping[str, Any]) -> Outcome:
    """The Lean binary's answer as an outcome. An unlicensed claim means the wire named an element the Contract
    does not have -- an assembly bug, raised, never read as any outcome."""
    if lean["unlicensed_claims"]:
        raise RuntimeError(f"Lean reported unlicensed claims (assembly bug): {lean['unlicensed_claims']}")
    if lean["denied_claims"]:
        return "DENIAL"
    if lean["verdict"] == "grounded":
        return "PROOF"
    return "ABSTAIN"


# -- audit -------------------------------------------------------------------------------------------------


def _hash_obj(obj: Any) -> str:
    return _sha(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))


class AuditTrail:
    """One JSONL line per step: `{run_id, seq, ts, step, inputs_sha256, output, wall_ms}`. Inputs are hashed,
    never copied; outputs are recorded as produced."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def step(self, name: str, inputs: Any, output: Any, wall_ms: float) -> None:
        line = {
            "run_id": self.run_id,
            "seq": self._seq,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "step": name,
            "inputs_sha256": _hash_obj(inputs),
            "output": output,
            "wall_ms": round(float(wall_ms), 3),
        }
        self._seq += 1
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def _ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000.0


# -- results -----------------------------------------------------------------------------------------------


@dataclass
class ElementResult:
    element: str
    is_denial: bool
    status: Literal["established", "not_established"]
    claimed: bool  # attempt 1's own status was "established", before any quote check
    p_established: float | None  # always attempt 1's -- a retry never changes it
    fact_id: str | None
    quote: str | None
    start: int | None  # system-computed (nyaya_quote.locate_quote), never a model's
    end: int | None
    quote_check: str | None
    attempts: int
    occurrences: int = 0
    offsets_source: Literal["system"] | None = None
    quote_source: str | None = None
    error: str | None = None


@dataclass
class ContractResult:
    contract_id: str
    outcome: Outcome
    reason: str
    elements: list[ElementResult]
    assertions: dict[str, bool] | None
    lean: dict[str, Any] | None
    lean_outcome: Outcome | None
    uncertain: list[str] = field(default_factory=list)
    #: The judge's training statute text differs from the binary's official --describe-source text (None: no
    #: training text to compare). An M2 retraining-on-official-texts target list, not an outcome input.
    statute_text_mismatch: bool | None = None


@dataclass
class AgentRun:
    run_id: str
    judge: str
    score_sha256: str
    facts: list[dict[str, str]]
    contracts: list[ContractResult]
    audit_path: Path
    provenance: str = "agama"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["audit_path"] = str(self.audit_path)
        return d


# -- the loop ----------------------------------------------------------------------------------------------


class NyayaAgent:
    def __init__(self, judge: Judge, registry: Registry, config: AgentConfig) -> None:
        self.judge = judge
        self.registry = registry
        self.config = config

    @classmethod
    def house(cls, root: Path, *, config: AgentConfig | None = None) -> NyayaAgent:
        """The configured loop: the house judge from `house_judge`, the pinned binary from `score_bin`."""
        from pravrudhi.application.nyaya_judges import HouseJudge

        cfg = config or load_agent_config(root)
        if cfg.score_bin is None:
            raise ValueError("no score_bin configured")
        registry = BinaryRegistry(cfg.score_bin, pinned_sha256=cfg.pinned_score_sha256)
        return cls(HouseJudge.from_config(cfg.house_judge, tau=cfg.tau), registry, cfg)

    def _judge_element(
        self,
        audit: AuditTrail,
        contract_id: str,
        element: str,
        is_denial: bool,
        statute: str,
        facts: tuple[Fact, ...],
        narrative: str,
    ) -> ElementResult:
        """Attempt 1 decides the element's status and p_established. If it says established but its quote is
        not verbatim in the named fact, up to `max_retries` re-asks follow -- the SAME request, same training
        statute -- and each is used ONLY for its quote; its status and p are recorded and ignored."""
        fact_map = {f.id: f.text for f in facts}
        request = JudgeRequest(contract_id, element, is_denial, statute, narrative, tuple((f.id, f.text) for f in facts))
        anchor: ElementJudgment | None = None
        fact_id: str | None = None
        quote: str | None = None
        quote_source: str | None = None
        loc: QuoteLocation | None = None
        error: str | None = None
        attempts = 0
        for attempt in range(1, self.config.max_retries + 2):
            attempts = attempt
            uses = "status_and_quote" if anchor is None else "quote_only"
            head = {"contract_id": contract_id, "element": element, "attempt": attempt, "statute_source": "config",
                    "uses": uses}
            t0 = time.monotonic()
            try:
                judgment = self.judge.judge(request)
            except Exception as e:  # recorded and retried; a judge failure never becomes a status
                if anchor is None:
                    error = f"{type(e).__name__}: {e}"[-400:]
                audit.step("judge", asdict(request), {**head, "error": f"{type(e).__name__}: {e}"[-400:]}, _ms(t0))
                continue
            audit.step("judge", asdict(request), {**head, "judge": self.judge.name, "judgment": judgment.as_dict()},
                       _ms(t0))
            if anchor is None:
                anchor, error = judgment, None
                if judgment.status != "established":
                    break
            established = judgment.status == "established"
            fact_id = judgment.fact_id if established else None
            quote = judgment.quote if established else None
            quote_source = judgment.quote_source if established else None
            t0 = time.monotonic()
            loc = locate_quote(fact_map, fact_id=fact_id, quote=quote)
            audit.step("quote_check", {"fact_id": fact_id, "quote": quote, "facts": [f.sha256 for f in facts]},
                       {"contract_id": contract_id, "element": element, "attempt": attempt, "valid": loc.valid,
                        "reason": loc.reason, "start": loc.start, "end": loc.end, "occurrences": loc.occurrences,
                        "offsets_source": loc.offsets_source, "quote_source": quote_source}, _ms(t0))
            if loc.valid:
                break
        if anchor is None:
            return ElementResult(element, is_denial, "not_established", False, None, None, None, None, None, None,
                                 attempts, error=error)
        claimed = anchor.status == "established"
        valid = claimed and loc is not None and loc.valid
        return ElementResult(
            element, is_denial, "established" if valid else "not_established", claimed, anchor.p_established,
            fact_id, quote, loc.start if loc else None, loc.end if loc else None, loc.reason if loc else None, attempts,
            occurrences=loc.occurrences if loc else 0, offsets_source=loc.offsets_source if loc and loc.valid else None,
            quote_source=quote_source,
        )

    def _run_contract(self, audit: AuditTrail, contract_id: str, facts: tuple[Fact, ...], narrative: str) -> ContractResult:
        t0 = time.monotonic()
        contract = self.registry.describe(contract_id)
        audit.step("describe", {"contract_id": contract_id}, asdict(contract), _ms(t0))

        # The judge sees ONLY its training statute text; the binary's official text is read for the audit
        # record (and the Lean side), never fed to the judge.
        t0 = time.monotonic()
        training = self.config.judge_statute_text.get(contract_id)
        official = self.registry.source_text(contract_id)
        mismatch = None if training is None else training != official
        audit.step("statute", {"contract_id": contract_id},
                   {"contract_id": contract_id, "judge_statute_source": "config" if training is not None else None,
                    "judge_statute_sha256": _sha(training) if training is not None else None,
                    "official_statute_sha256": _sha(official), "statute_text_mismatch": mismatch}, _ms(t0))

        results: list[ElementResult] = []

        def finish(outcome: Outcome, reason: str, **kw: Any) -> ContractResult:
            res = ContractResult(contract_id, outcome, reason, results, kw.get("assertions"), kw.get("lean"),
                                 kw.get("lean_outcome"), kw.get("uncertain", []), mismatch)
            audit.step("outcome", {"contract_id": contract_id, "elements": [asdict(r) for r in results]},
                       {"contract_id": contract_id, "outcome": outcome, "reason": reason,
                        "lean_outcome": res.lean_outcome, "uncertain": res.uncertain,
                        "statute_text_mismatch": mismatch}, 0.0)
            return res

        if training is None:
            return finish("ABSTAIN", "no_training_statute_text")

        results += [self._judge_element(audit, contract_id, e, False, training, facts, narrative) for e in contract.elements]
        results += [self._judge_element(audit, contract_id, d, True, training, facts, narrative) for d in contract.denials]

        if any(r.error is not None for r in results):
            return finish("ABSTAIN", "judge_error")

        t0 = time.monotonic()
        assertions = assemble_assertions(contract, {r.element: r.status == "established" for r in results})
        local = expected_outcome(contract, assertions)
        audit.step("assemble", {"contract_id": contract_id, "elements": [asdict(r) for r in results]},
                   {"contract_id": contract_id, "assertions": assertions, "expected_outcome": local}, _ms(t0))
        t0 = time.monotonic()
        lean = self.registry.check(assertions, contract_id)
        audit.step("lean_check", {"contract_id": contract_id, "assertions": assertions, "score_sha256": self.registry.sha256},
                   {"contract_id": contract_id, **lean}, _ms(t0))
        lean_outcome = outcome_from_lean(lean)
        uncertain = [r.element for r in results if r.p_established is not None and self.config.in_band(r.p_established)]
        kw: dict[str, Any] = {"assertions": assertions, "lean": lean, "lean_outcome": lean_outcome, "uncertain": uncertain}

        if lean_outcome != local:
            return finish("ABSTAIN", "assembly_lean_mismatch", **kw)
        if any(r.is_denial and r.claimed and r.status != "established" for r in results):
            return finish("REFER_TO_LAWYER", "denial_unquotable", **kw)
        if uncertain:
            return finish("REFER_TO_LAWYER", "uncertain", **kw)
        reason = {"PROOF": "all_elements_established", "DENIAL": "denial_established", "ABSTAIN": "missing_element"}[lean_outcome]
        return finish(lean_outcome, reason, **kw)

    def run(
        self,
        facts: Sequence[str],
        *,
        narrative: str = "",
        contract_ids: Iterable[str] | None = None,
        sections: Iterable[str] | None = None,
    ) -> AgentRun:
        run_id = f"nyaya-agent-{uuid.uuid4().hex[:10]}"
        audit = AuditTrail(Path(self.config.audit_dir) / f"{run_id}.jsonl", run_id)
        cfg_view = {"tau": self.config.tau, "refer_band": list(self.config.refer_band), "max_retries": self.config.max_retries}
        audit.step("run_start", cfg_view, {"judge": self.judge.name, "score_sha256": self.registry.sha256, **cfg_view}, 0.0)

        t0 = time.monotonic()
        ingested = ingest_facts(facts)
        audit.step("ingest", {"facts": list(facts), "narrative": narrative},
                   {"facts": [{"id": f.id, "sha256": f.sha256} for f in ingested], "narrative_sha256": _sha(narrative)}, _ms(t0))

        t0 = time.monotonic()
        listed = self.registry.list_contracts()
        chosen = select_contracts(listed, contract_ids=contract_ids, sections=sections)
        audit.step("select_contracts",
                   {"listed": listed, "contract_ids": sorted(contract_ids) if contract_ids is not None else None,
                    "sections": sorted(sections) if sections is not None else None},
                   {"selected": chosen}, _ms(t0))

        results = [self._run_contract(audit, cid, ingested, narrative) for cid in chosen]
        audit.step("run_end", {"run_id": run_id}, {c.contract_id: c.outcome for c in results}, 0.0)
        return AgentRun(run_id, self.judge.name, self.registry.sha256,
                        [{"id": f.id, "sha256": f.sha256} for f in ingested], results, audit.path)
