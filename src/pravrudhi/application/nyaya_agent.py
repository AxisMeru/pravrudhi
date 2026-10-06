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
* **Uncertainty is referred, not rounded.** Any judged element whose `p_established` (the PRIMARY judge's,
  always) falls in the configured `refer_band` [low, high) makes the contract REFER_TO_LAWYER; the Lean check
  still runs and is recorded. The shipped band [0.5, 0.74) is an UNVALIDATED DEFAULT, CALIBRATION PENDING M3 --
  not pre-registered, not fitted.
* **The second judge (config C) gets its own band, in logit distance, not probability.** The served 32B second
  judge's `p_established_second` is bf16-quantised (`sigmoid(k/8)`) and run-to-run non-deterministic near its
  own tau (e.g. logit(0.97)=3.4761) -- a probability-space band the width of the primary's would be too coarse
  at that resolution. `config.second_judge.refer_logit_delta` (env `NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA`),
  default `None` (off), makes the element 'uncertain' (`uncertain_second_judge`) when the second judge was
  actually consulted and `|logit(p_established_second) - logit(tau_second)| < delta` (strict; equal to delta is
  NOT in band). It never fires when the primary already rejected (second skipped) -- there is no second p to
  compare, and the element keeps its existing skipped outcome unchanged.
* **An unavailable second judge is referred, never silently denied (Lead-2, 2026-09-24).** When the second
  judge errors after being consulted (not a configuration fault -- those still raise via `JudgeMisconfigured`,
  unchanged), `AndGateJudge` fails the element closed (not established) exactly as before, but the CONTRACT now
  becomes REFER_TO_LAWYER (`second_judge_unavailable`), unconditionally -- with no `refer_logit_delta`
  required and independent of the primary's own outcome for other elements. Before this, a fail-closed element
  was scored as an ordinary not-established fact, which could surface to the user as a false DENIAL rather than
  a case that needed a lawyer's attention because the second opinion was never actually obtained.
* **The judge sees one statute text.** Its training text (config `judge_statute_text`) on every attempt; a
  contract with none is not judged (ABSTAIN, `no_training_statute_text`).
* **A judge that cannot answer is a gap.** An element whose judge raised on every attempt makes the contract
  ABSTAIN (`judge_error`) with no Lean call -- no outcome is decided over an element nobody judged.
* **Nothing here is evidence.** A run is a check of a judge's reading of the user's facts against the Lean
  registry; its record is testimony (`agama`), written under `research/nyaya/agent_runs/`, never the ledger.
"""

from __future__ import annotations

import contextvars
import difflib
import hashlib
import json
import logging
import math
import os
import queue
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_judges import (
    ClampKind,
    ElementJudgment,
    Judge,
    JudgeRequest,
    SecondJudgeCircuitBreaker,
    standard_for_posture,
)
from pravrudhi.application.nyaya_quote import QuoteLocation, locate_quote

Outcome = Literal["PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER"]

#: Every `reason` a contract result can carry. `finish()` takes this type, so a new reason string that is not
#: listed here fails mypy, and the partner API's published OpenAPI enum cannot drift from what the agent emits.
ContractReason = Literal[
    "all_elements_established", "denial_established", "missing_element", "no_training_statute_text",
    "judge_error", "assembly_lean_mismatch", "denial_unquotable", "second_judge_defeater_disagreement",
    "uncertain", "uncertain_second_judge", "second_judge_unavailable", "gate1_unavailable",
    "gate1_not_entailed", "gate1_contradiction", "contract_not_validated",
]
CONFIG_PATH = Path("configs") / "nyaya_agent.yaml"
#: What `ContractResult.rule_text_source` says: the provision text came from the pinned Lean binary's `--describe-source`,
#: which carries India Code text. Unofficial; the code, docs and UI never call it official.
RULE_TEXT_SOURCE = "lean_describe_source"


class BinaryShaMismatch(RuntimeError):
    """The score binary's sha256 is not the pinned one -- refused before any call."""


class JudgeMisconfigured(RuntimeError):
    """The judge refused the request with a 4xx (bad key, unknown model, bad request): a configuration fault, so the
    run stops rather than recording a per-element failure that would read as an ordinary "not established"."""


def _judge_config_fault(e: BaseException) -> int | None:
    """The HTTP status of a 4xx (other than 429) anywhere in `e`'s cause chain, else None."""
    from pravrudhi.models.openai_compat import HTTPStatusError

    seen: BaseException | None = e
    while seen is not None:
        if isinstance(seen, HTTPStatusError) and 400 <= seen.status < 500 and seen.status != 429:
            return seen.status
        seen = seen.__cause__
    return None


# -- config ------------------------------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


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
    #: T1 (docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md): when set, `NyayaAgent.house` builds
    #: `pravrudhi.application.typed.house_judge.TypedHouseJudge` from the same `house_judge` config instead
    #: of `nyaya_judges.HouseJudge`. Default False -- today's production behaviour is unchanged unless a
    #: caller opts in.
    typed_layer: bool = False
    #: Optional config C second judge (AND-gate, `nyaya_judges.AndGateJudge`). None -- the default, and every
    #: existing deployment's config -- means exactly today's single-judge behaviour (HouseJudge, or
    #: TypedHouseJudge when `typed_layer` is set); only a `second_judge:` block in the yaml (or an
    #: NYAYA_SECOND_JUDGE_* env var) turns the gate on. Composes with `typed_layer` independently -- see
    #: `NyayaAgent.house`.
    second_judge: Mapping[str, Any] | None = None
    #: `house_judge.max_concurrency` (or env NYAYA_JUDGE_MAX_CONCURRENCY): how many (contract, element/denial)
    #: judge requests `NyayaAgent.run` judges at once, bounded by a `ThreadPoolExecutor`. Default 1 -- today's
    #: serial behaviour, byte-identical results and audit ordering. See `NyayaAgent._judge_elements`.
    max_concurrency: int = 1
    #: ALLOWLIST (inverted from a `unvalidated_contracts` deny-list, Tag's review / issue #36, 2026-09-26+):
    #: the ONLY contracts the element judge has actually been trained or measured on -- the dual-signed eval
    #: (config C, checker_pass 109/377, false-prove 0/225) covers exactly these 14. A contract NOT in this
    #: set can never reach a final PROOF or DENIAL, no matter how it got onto the registry: `_run_contract`
    #: intercepts that outcome and returns REFER_TO_LAWYER, reason `contract_not_validated`, instead. Every
    #: element still judges normally (elements, quotes and the Lean result all stay visible in the audit and
    #: the response) -- only the FINAL outcome is intercepted, and only when it would otherwise be a definite
    #: PROOF/DENIAL; an outcome that would already be ABSTAIN or REFER_TO_LAWYER for another reason passes
    #: through unchanged.
    #:
    #: The deny-list this replaces was fail-OPEN: a registry id missing from it (e.g. every new id a pin bump
    #: adds) was validated BY DEFAULT until someone remembered to add it. This allowlist is fail-CLOSED: a
    #: registry id missing from it -- including every new id a future pin bump adds -- is REFER by default,
    #: with no action required to keep it that way. A contract joins this set only by Lead-2's explicit
    #: decision after a signed eval of the M2-trained judge covering it -- this is a deliberate, standing
    #: safety gate, not a TODO for anyone to clear later. `load_agent_config` asserts every id here actually
    #: exists in the pinned registry (`nyaya_lean_registry.KNOWN_CONTRACT_IDS`), so a typo'd id can never
    #: silently validate nothing.
    validated_contracts: frozenset[str] = field(default_factory=frozenset)
    #: Gate 1 (Track-C, GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md): threshold/model config, always present with
    #: its documented defaults even when Gate 1 is off (`gate1_enabled=False`) -- keeping the block configured
    #: while the gate itself stays off is deliberate (Track-C's own recommendation, §5): a threshold this
    #: consequential should never require a code change to adjust once there's a reason to turn it on.
    gate1: Mapping[str, Any] = field(default_factory=dict)
    #: Whether Gate 1 actually runs (`NYAYA_GATE1_ENABLED`). Default False -- today's behaviour, byte-identical
    #: responses, is unchanged unless a deployment opts in. Deliberately a SEPARATE switch from `gate1` above
    #: (unlike `second_judge`, whose mere presence turns config C on): the measured -19%/-9% coverage cost
    #: with zero demonstrated false-prove benefit on the only population tested means this must stay off by
    #: default even on a host that has the model/threshold configured, until Lead-2 decides otherwise.
    gate1_enabled: bool = False
    #: Obj-1 Fix 2 Option A (`SpanRelevanceJudge`): demote an established non-denial element whose cited span
    #: alone does not state it. Default False -- today's behaviour, byte-identical. Env
    #: `NYAYA_SPAN_RELEVANCE_ENABLED` or yaml `span_relevance_enabled`. Measured only on a sealed Obj-1b set.
    span_relevance_enabled: bool = False
    #: Issue #39 (interim posture until a partner onboards): how many days a run's audit record survives
    #: under `audit_dir` before `purge_stale_runs` deletes it. Config-driven, never hardcoded, so the window
    #: can be tightened or loosened with a config edit alone. 7.0 is the operator/Lead-2 decided default.
    retention_days: float = 7.0
    #: LICENCE HOLD (Lead-2, pending counsel #506): whether the partner API returns the provision text
    #: (`rule_text`, `judge_rule_text`, `rule_text_source`). OFF by default: with it off those fields are ABSENT from
    #: the partner response. It gates only that surface; the agent still records the text on its own result and audit.
    expose_rule_text: bool = False
    #: Issue #44 (standing second-judge positive control): record_path/max_age_hours -- `_build_judge` checks
    #: a fresh, matching passing live-check record before letting `AndGateJudge` reach the real second
    #: judge; empty (the default) means the record check is skipped -- a deployment that hasn't opted into
    #: the positive control yet keeps today's behaviour (second_judge configured -> used) unchanged.
    second_judge_positive_control: Mapping[str, Any] = field(default_factory=dict)
    #: Where `tau` came from: "yaml", or "env:NYAYA_HOUSE_JUDGE_TAU" (#134). Recorded in the run's config view only
    #: when it is not the yaml, so a default run's audit record is unchanged.
    tau_source: str = "yaml"

    def __post_init__(self) -> None:
        low, high = self.refer_band
        if not (0.0 <= low <= high <= 1.0):
            raise ValueError(f"refer_band must be 0 <= low <= high <= 1, got {self.refer_band}")
        if not (0.0 < self.tau <= 1.0):
            raise ValueError(f"tau must be in (0, 1], got {self.tau}")
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")
        delta = self.second_refer_logit_delta()
        if delta is not None and delta < 0:
            raise ValueError(f"second_judge.refer_logit_delta must be >= 0, got {delta}")
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {self.max_concurrency}")
        pc = self.second_judge_positive_control or {}
        if pc.get("record_path"):
            from pravrudhi.application.second_judge_positive_control import valid_max_age_hours

            if not valid_max_age_hours(pc.get("max_age_hours")):
                raise ValueError(
                    f"second_judge_positive_control.max_age_hours={pc.get('max_age_hours')!r} must be a finite number > 0 "
                    "when record_path is set (a missing, zero, negative or non-finite window would make a stale record "
                    "valid forever or never valid)"
                )

    def in_band(self, p: float) -> bool:
        low, high = self.refer_band
        return low <= p < high

    def second_refer_logit_delta(self) -> float | None:
        """`second_judge.refer_logit_delta` (config key or `NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA`, already
        merged into `second_judge` by `load_agent_config`), else None -- off, today's behaviour, byte-identical.
        Reads from `second_judge` rather than its own field so a bare env override on a host with no
        `second_judge:` yaml block still works, mirroring the other `NYAYA_SECOND_JUDGE_*` overrides."""
        if not self.second_judge:
            return None
        raw = self.second_judge.get("refer_logit_delta")
        return None if raw is None else float(raw)


# The primary the yaml tau was set for. Naming it by env (production does) is a pin, not a swap.
_YAML_TAU_PRIMARY_MODEL = "nyaya-judge-4b"

_UNSET_MODEL_STRINGS = frozenset({"", "null", "none", "~"})


def _model_pin_required() -> str | None:
    """The edition name when the judge model must be pinned, else None.

    Required for every edition except an explicit `PRAVRUDHI_EDITION=dev` on a development checkout: a release
    install is the product whatever the env says, and an unset, unknown or mislabelled value (`prod`, `null`)
    is enforced, never waved through."""
    from pravrudhi.deployment import declared_edition, resolved_edition

    resolved = resolved_edition()
    if declared_edition() == "dev" and resolved == "dev":
        return None  # an explicit dev edition on a development checkout (never on a release install or hosted image)
    return "studio" if resolved == "dev" else resolved


def _require_pinned_judge_models(house_judge: Mapping[str, Any], second_judge: Mapping[str, Any] | None) -> None:
    """Every judge block must name its model, except under an explicit development edition.

    An unset model resolves to the first id the server's /v1/models lists, which on the dev 32B host is the BASE
    snapshot path, so a null there silently judges with base Qwen2.5-32B instead of the fine-tuned judge. The
    strings "null", "none" and blanks are refused like a real null. Never defaulted: a missing value raises."""
    edition = _model_pin_required()
    if edition is None:
        return
    blocks = {"house_judge": house_judge, **({"second_judge": second_judge} if second_judge is not None else {})}
    for name, block in blocks.items():
        model = block.get("model")
        if not isinstance(model, str) or model.strip().lower() in _UNSET_MODEL_STRINGS:
            env = "NYAYA_HOUSE_JUDGE_MODEL" if name == "house_judge" else "NYAYA_SECOND_JUDGE_MODEL"
            raise ValueError(
                f"{name}.model is not set ({model!r}): in the {edition} edition a null model would resolve to the "
                f"first id the server lists (the base snapshot on the dev 32B). Name the served model id in the "
                f"config or set {env}; only PRAVRUDHI_EDITION=dev on a development checkout may leave it unset"
            )


def validated_contract_ids(root: Path) -> frozenset[str]:
    """The `validated_contracts` allowlist from `configs/nyaya_agent.yaml` -- the same set `load_agent_config`
    gives the scorer -- without needing the score binary or a judge to be configured."""
    import yaml

    from pravrudhi.application.config_files import config_file

    try:
        body = yaml.safe_load(config_file(Path(root), "nyaya_agent.yaml").read_text()) or {}
    except FileNotFoundError:
        return frozenset()  # no scorer config here: the scorer cannot run, so nothing is validated
    return frozenset(str(c) for c in (body.get("validated_contracts") or []))


def _host_class(base_url: Any) -> str | None:
    """`local` (loopback, private or .local host), `serverless` (RunPod) or `remote`; never the URL itself."""
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(str(base_url)).hostname or "").lower() if base_url else ""
    if not host:
        return None
    if host.endswith("runpod.ai") or host.endswith("runpod.io"):
        return "serverless"
    if host == "localhost" or host.endswith(".local"):
        return "local"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "remote"
    return "local" if ip.is_loopback or ip.is_private else "remote"


def _judge_provenance(config: AgentConfig) -> dict[str, Any]:
    """What produced the verdicts, for the audit trail: engine version, judge model ids (None when the config
    does not pin one) and where each judge runs. Host class only, so the audit never carries an endpoint."""
    from pravrudhi import __version__

    hj, sj = config.house_judge or {}, config.second_judge or {}
    return {
        "engine_version": __version__,
        "primary_judge_model": hj.get("model") or None,
        "primary_judge_host_class": _host_class(hj.get("base_url")),
        "second_judge_model": sj.get("model") or None,
        "second_judge_host_class": _host_class(sj.get("base_url")),
        # Issue #44: whether the positive-control record gate wraps the second judge. OFF unless record_path is set.
        "second_judge_record_gate": (
            None if not sj else ("on" if (config.second_judge_positive_control or {}).get("record_path") else "off")
        ),
    }


def load_agent_config(root: Path) -> AgentConfig:
    """`configs/nyaya_agent.yaml` under `root`, else the copy the wheel ships (`config_files.config_file`);
    relative paths resolve against `root`. The score binary path
    follows `nyaya_gold_score.score_bin_path`'s precedence (env var first) when the file names none. The house
    judge's base_url can be overridden by NYAYA_HOUSE_JUDGE_BASE_URL env var (for container deployments where
    localhost does not refer to the host)."""
    import yaml

    from pravrudhi.application.config_files import config_file
    from pravrudhi.application.nyaya_gold_score import SCORE_BIN_ENV, score_bin_path

    root = Path(root)
    body = yaml.safe_load(config_file(root, "nyaya_agent.yaml").read_text()) or {}
    env_bin = os.environ.get(SCORE_BIN_ENV)
    if env_bin:
        score_bin = Path(env_bin)
    elif body.get("score_bin"):
        score_bin = (root / str(body["score_bin"])).resolve()
    else:
        score_bin = score_bin_path(root)
    low, high = body["refer_band"]
    house_judge = dict(body.get("house_judge") or {})
    # The only knob that puts the standard line into the house prompt (default false: the training prompt,
    # byte for byte). The standard is resolved and audited either way; this decides only whether the judge sees it.
    standard_line = bool((body.get("judge_prompt") or {}).get("standard_line", False))
    derived_template = "standard_line_v1" if standard_line else "legacy"
    if house_judge.get("prompt_template", derived_template) != derived_template:
        raise ValueError(
            f"house_judge.prompt_template={house_judge['prompt_template']!r} conflicts with "
            f"judge_prompt.standard_line={standard_line}; set judge_prompt.standard_line only"
        )
    house_judge["prompt_template"] = derived_template
    # Allow env override for judge base_url (container deployments)
    if os.environ.get("NYAYA_HOUSE_JUDGE_BASE_URL"):
        house_judge["base_url"] = os.environ["NYAYA_HOUSE_JUDGE_BASE_URL"]
    # A deployment names the judge's model id (so no /models round-trip on a cold serverless worker) and an
    # ordered fallback list; both are the host's facts, not the release's.
    if os.environ.get("NYAYA_HOUSE_JUDGE_MODEL"):
        house_judge["model"] = os.environ["NYAYA_HOUSE_JUDGE_MODEL"]
    # Opt-in: with a model id named above, refuse any answer whose `model` field differs (HouseJudge.enforce_served_model;
    # fail closed). Off by default so an existing deployment does not change behaviour until it names the switch.
    if os.environ.get("NYAYA_HOUSE_JUDGE_ENFORCE_SERVED_MODEL", "").strip().lower() in ("1", "true", "yes", "on"):
        house_judge["enforce_served_model"] = True
    if os.environ.get("NYAYA_HOUSE_JUDGE_FALLBACK_URLS"):
        house_judge["base_urls_fallback"] = [
            u.strip() for u in os.environ["NYAYA_HOUSE_JUDGE_FALLBACK_URLS"].split(",") if u.strip()
        ]
    # A serverless deployment's real cold start (~200s judge + up to 300s idle-out) needs a longer timeout
    # than a local 5090's yaml default (60s) -- 2026-09-24, a real production regression: a fully-cold demo
    # visit hit ABSTAIN/judge_error every time until this was raised.
    if os.environ.get("NYAYA_HOUSE_JUDGE_TIMEOUT_S"):
        house_judge["timeout_s"] = int(os.environ["NYAYA_HOUSE_JUDGE_TIMEOUT_S"])
    # Bounded judge concurrency (docs/decisions, 2026-09-24): default 1 -- today's serial behaviour -- so an
    # existing deployment's yaml with no `max_concurrency` key and no env var is unchanged.
    if os.environ.get("NYAYA_JUDGE_MAX_CONCURRENCY"):
        house_judge["max_concurrency"] = int(os.environ["NYAYA_JUDGE_MAX_CONCURRENCY"])

    # The optional config-C second judge (AndGateJudge): absent block + no env var = None = today's single
    # HouseJudge, unchanged. An NYAYA_SECOND_JUDGE_* var can also introduce the block on a host with no yaml
    # entry for it (container deployments), mirroring the house_judge env overrides above; its api_key is
    # read directly by HouseJudge.from_config(api_key_env=...) in NyayaAgent.house, never merged in here, so
    # it is never written into a run's config_view audit record.
    second_body = body.get("second_judge")
    second_judge: dict[str, Any] | None = dict(second_body) if second_body else None

    def _second_override(env_var: str, key: str, cast: Any = str) -> None:
        nonlocal second_judge
        raw = os.environ.get(env_var)
        if raw:
            second_judge = dict(second_judge or {})
            second_judge[key] = cast(raw)

    _second_override("NYAYA_SECOND_JUDGE_BASE_URL", "base_url")
    _second_override("NYAYA_SECOND_JUDGE_MODEL", "model")
    _second_override("NYAYA_SECOND_JUDGE_TAU", "tau", float)
    _second_override("NYAYA_SECOND_JUDGE_TIMEOUT_S", "timeout_s", int)
    _second_override("NYAYA_SECOND_JUDGE_STATUTE_CHARS", "statute_chars", int)
    _second_override("NYAYA_SECOND_JUDGE_TOP_LOGPROBS", "top_logprobs", int)
    _second_override("NYAYA_SECOND_JUDGE_MAX_TOKENS", "max_tokens", int)
    _second_override("NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR", "label_mass_floor", float)
    # The second-judge REFER band (logit distance, not probability -- module doc): off (None) unless a
    # `refer_logit_delta:` key is in the yaml's `second_judge:` block or this env var is set. Reachable even
    # with no `second_judge:` yaml block, exactly like the overrides above -- though it is inert without a
    # second judge actually configured (`AgentConfig.second_refer_logit_delta` only ever reads it off a real
    # `second_judge` mapping, and `_run_contract` only ever sees a non-None `p_established_second` when config C
    # is on).
    _second_override("NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA", "refer_logit_delta", float)
    # 2026-09-27 (Lead-2, the 5090-second-judge-exposure design, docs/decisions/2026-09-27-5090-second-
    # judge-exposure-design.md sec 2.2): the house judge has had NYAYA_HOUSE_JUDGE_FALLBACK_URLS since
    # 2026-09-24 (above); the second judge never got the equivalent, so its own `base_urls_fallback` could
    # only ever be set by editing configs/nyaya_agent.yaml's second_judge: block -- a same-day image
    # rebuild for every fallback-endpoint change, unlike every OTHER second_judge setting in production
    # today (set purely via NYAYA_SECOND_JUDGE_* env, per the 0.5.41 ledger row). Not folded into
    # `_second_override` (that helper's `cast` is a bare type constructor; this needs list-splitting AND
    # validation). Fail-closed, deliberately stricter than the house-judge sibling above (which silently
    # accepts and filters a malformed value): an env var that is SET but produces zero URLs after
    # stripping, or contains an entry with no http(s) scheme, is refused outright with a raised
    # ValueError rather than silently running with an empty or broken fallback list -- a caller that
    # believes a fallback is configured must never silently get none.
    raw_second_fallback = os.environ.get("NYAYA_SECOND_JUDGE_FALLBACK_URLS")
    if raw_second_fallback:
        second_fallback_urls = [u.strip() for u in raw_second_fallback.split(",") if u.strip()]
        if not second_fallback_urls or any(
            not u.startswith(("http://", "https://")) for u in second_fallback_urls
        ):
            raise ValueError(
                "NYAYA_SECOND_JUDGE_FALLBACK_URLS is set but malformed "
                f"({raw_second_fallback!r}): every comma-separated entry must be a non-empty http(s) "
                "URL -- refusing to start with a broken second-judge fallback list."
            )
        second_judge = dict(second_judge or {})
        second_judge["base_urls_fallback"] = second_fallback_urls

    # An env-built second_judge inherits statute_chars/top_logprobs/max_tokens/label_mass_floor from the
    # primary house_judge block, unless the caller set them explicitly (in the yaml's own second_judge:
    # block, or via the NYAYA_SECOND_JUDGE_* overrides just above). `HouseJudge.from_config` requires all
    # four (`cfg["..."]`, no default) -- a second_judge built from NYAYA_SECOND_JUDGE_BASE_URL/_TAU alone,
    # with no yaml second_judge: block at all, used to crash with KeyError the first time
    # NyayaAgent.house() ran; this is what makes a purely env-driven switch-on possible (2026-09-25, the
    # RunPod endpoint is configured this way). Inheriting from house_judge, not a hardcoded default, keeps
    # the second judge's prompt shape (and its safety floor) identical to the primary's unless a
    # deployment deliberately diverges. label_mass_floor (2026-09-28, G-28 follow-up) added to this same
    # inherited set -- both judges share the same safety threshold by default, same as the prompt-shape
    # params, rather than needing a distinct required env var like tau/timeout_s do.
    if second_judge is not None:
        for key in ("statute_chars", "top_logprobs", "max_tokens", "label_mass_floor", "prompt_template"):
            if key not in second_judge and key in house_judge:
                second_judge[key] = house_judge[key]

    second_judge_positive_control = dict(body.get("second_judge_positive_control") or {})
    # Issue #44: env overrides mirror the house_judge/second_judge/gate1 pattern above -- NYAYA_SECOND_
    # JUDGE_POSITIVE_CONTROL_RECORD_PATH/_MAX_AGE_HOURS override or introduce the block's
    # own values. The gate is OFF unless record_path is set (Lead-2, 5 Oct): an engine with second_judge
    # configured and no record must keep serving, never REFER every case; run_start records which state it is in.
    if os.environ.get("NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_RECORD_PATH"):
        second_judge_positive_control["record_path"] = os.environ["NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_RECORD_PATH"]
    if os.environ.get("NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_MAX_AGE_HOURS"):
        second_judge_positive_control["max_age_hours"] = float(
            os.environ["NYAYA_SECOND_JUDGE_POSITIVE_CONTROL_MAX_AGE_HOURS"]
        )
    _require_pinned_judge_models(house_judge, second_judge)

    # Gate 1 (Track-C, GATE1-PRODUCT-WIRING-SPEC-2026-09-26.md §3/§5): the yaml block (threshold/model) and
    # the enable switch are deliberately independent -- NYAYA_GATE1_THRESHOLD/_MODEL override the block's own
    # values (or introduce it, mirroring the house_judge/second_judge env-override pattern above) whether or
    # not NYAYA_GATE1_ENABLED is set; the block being configured never itself turns the gate on.
    gate1 = dict(body.get("gate1") or {})
    if os.environ.get("NYAYA_GATE1_THRESHOLD"):
        gate1["threshold"] = float(os.environ["NYAYA_GATE1_THRESHOLD"])
    if os.environ.get("NYAYA_GATE1_MODEL"):
        gate1["model"] = os.environ["NYAYA_GATE1_MODEL"]
    # Arm C (GATE1-ARM-C-2026-09-26.md): a SEPARATE mode, not a separate gate -- "mode" picks which of
    # Gate1Judge's two check functions runs; "tau_c" is inert in the default "entailment" mode, just as
    # "threshold" is inert in "contradiction_veto" mode. Both stay configured either way, same rationale as
    # keeping the whole `gate1` block present while `gate1_enabled` is False (Track-C's own recommendation).
    if os.environ.get("NYAYA_GATE1_MODE"):
        gate1["mode"] = os.environ["NYAYA_GATE1_MODE"]
    if os.environ.get("NYAYA_GATE1_TAU_C"):
        gate1["tau_c"] = float(os.environ["NYAYA_GATE1_TAU_C"])
    gate1_enabled_raw = os.environ.get("NYAYA_GATE1_ENABLED", "")
    gate1_enabled = gate1_enabled_raw.strip().lower() in ("1", "true", "yes", "on")
    span_raw = os.environ.get("NYAYA_SPAN_RELEVANCE_ENABLED")
    span_relevance_enabled = (
        span_raw.strip().lower() in ("1", "true", "yes", "on") if span_raw is not None
        else bool(body.get("span_relevance_enabled", False))
    )

    # Fail-closed allowlist (issue #36): every id here must actually exist in the pinned registry, checked at
    # load time rather than left to surface later as a silently-inert typo -- an id that isn't real can never
    # validate anything, so a typo here would (correctly) REFER that contract forever, but SILENTLY, with no
    # signal that the allowlist entry was ever wrong.
    validated_contracts = frozenset(str(c) for c in (body.get("validated_contracts") or []))
    unknown = validated_contracts - reg.KNOWN_CONTRACT_IDS
    if unknown:
        raise ValueError(
            f"configs/nyaya_agent.yaml's validated_contracts names id(s) not in the pinned registry: "
            f"{sorted(unknown)} -- known ids: {sorted(reg.KNOWN_CONTRACT_IDS)}"
        )

    # #134: the primary's tau had no env override, so a sole-judge arm (NYAYA_HOUSE_JUDGE_BASE_URL/_MODEL pointed at a
    # different model) silently decided at the 4B's yaml tau. Swapping the primary by env now requires an explicit
    # NYAYA_HOUSE_JUDGE_TAU: a number, or the literal "yaml" to say the yaml tau is intended. refer_band is untouched
    # (set it in the yaml); AgentConfig validates the tau range, and a non-numeric value raises rather than falling back.
    tau, tau_source = float(body["tau"]), "yaml"
    raw_tau = (os.environ.get("NYAYA_HOUSE_JUDGE_TAU") or "").strip()
    env_model = os.environ.get("NYAYA_HOUSE_JUDGE_MODEL")
    # Only a different model id is a swap; a base_url alone or the 4B's own id is not.
    swapped = ["NYAYA_HOUSE_JUDGE_MODEL"] if env_model and env_model != _YAML_TAU_PRIMARY_MODEL else []
    if raw_tau == "yaml":
        tau_source = "env:NYAYA_HOUSE_JUDGE_TAU=yaml"
    elif raw_tau:
        try:
            tau, tau_source = float(raw_tau), "env:NYAYA_HOUSE_JUDGE_TAU"
        except ValueError as e:
            raise ValueError(f"NYAYA_HOUSE_JUDGE_TAU is not a number ({raw_tau!r}); refusing to start") from e
        # An env tau may tighten the primary threshold freely, but LOOSENING it below the yaml tau is a safety change by
        # environment (R1 on #185; #303 excludes the same for the night loop): refused unless a second explicit flag is set.
        if tau < float(body["tau"]):
            if (os.environ.get("NYAYA_HOUSE_JUDGE_TAU_ALLOW_LOWER") or "").strip() != "1":
                raise ValueError(
                    f"NYAYA_HOUSE_JUDGE_TAU={tau:g} is below the yaml tau {float(body['tau']):g}: "
                    "lowering the primary threshold by environment is refused; "
                    "set NYAYA_HOUSE_JUDGE_TAU_ALLOW_LOWER=1 to allow it "
                    "(recorded in the audit as tau_source); refusing to start"
                )
            tau_source = "env:NYAYA_HOUSE_JUDGE_TAU(lower-than-yaml,allowed)"
    elif swapped:
        raise ValueError(f"{' and '.join(swapped)} override the primary judge but NYAYA_HOUSE_JUDGE_TAU is not set; "
                         "set it to the tau for that model, or to 'yaml' to keep the yaml tau; refusing to start")

    return AgentConfig(
        tau=tau,
        tau_source=tau_source,
        refer_band=(float(low), float(high)),
        max_retries=int(body["max_retries"]),
        audit_dir=root / str(body["audit_dir"]),
        judge_statute_text={str(k): str(v) for k, v in (body.get("judge_statute_text") or {}).items()},
        pinned_score_sha256=body.get("pinned_score_sha256"),
        score_bin=score_bin,
        house_judge=house_judge,
        typed_layer=bool(body.get("typed_layer", False)),
        second_judge=second_judge,
        second_judge_positive_control=second_judge_positive_control,
        max_concurrency=int(house_judge.get("max_concurrency", 1)),
        validated_contracts=validated_contracts,
        gate1=gate1,
        gate1_enabled=gate1_enabled,
        span_relevance_enabled=span_relevance_enabled,
        retention_days=float(body.get("retention_days", 7.0)),
        expose_rule_text=bool(body.get("expose_rule_text", False)),
    )


# -- facts -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    sha256: str


def _text_similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, " ".join(a.split()), " ".join(b.split())).ratio(), 3)


def element_stage(r: ElementResult) -> str | None:
    """Where an element that is not established stopped: `<status>:<leg>`, the leg being the judge leg that bound
    the verdict, or `quote_<reason>` when a claimed element's fact id / quote could not be validated. None for an
    established element. Derived only from fields the element already carries."""
    if r.status == "established":
        return None
    leg = r.binding_leg or (f"quote_{r.quote_check}" if r.claimed and r.quote_check else "none")
    return f"{r.status}:{leg}"


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


def assemble_assertions(contract: reg.DescribedContract, established: Mapping[str, bool | None]) -> dict[str, bool]:
    """The REG wire's assertions, deterministically: every required element in the Contract's order with its
    judged status, then each DENY defeater ONLY when established (asserting a defeater is a refutation; an
    absent one is simply not sent). Mirrors `element_first_harness.assemble_wire`'s claim set.

    `established` is tri-state (issue #56): True (established), False (a judge genuinely scored the element
    unmet), or None (never evaluated at all -- `not_evaluated_second_unavailable` or, issue #57/#63/#72,
    `not_evaluated_gate1_unavailable`). The Lean wire itself only ever carries True/False, so a None here
    degrades to False, NEVER to True -- `val is True` is the only way into the wire's `True`. This
    degradation is safe by construction, not merely convenient: a DENIAL only ever arises when a denial
    record's assertion is True, and a PROOF only when every element's is True, so a missing (None) answer can
    only ever BLOCK a PROOF/DENIAL that genuine evidence would otherwise have produced -- it can never
    MANUFACTURE one. The contract for an element either `not_evaluated_*` status names is referred
    unconditionally anyway (`_run_contract`'s `unavailable_second` and `gate1_unavailable` checks), entirely
    independent of what this function does with the None."""
    out = {e: established.get(e) is True for e in contract.elements}
    for d in contract.denials:
        if established.get(d) is True:
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

#: Issue #39: the exact text every anonymous-callable surface must show or return -- one place, so wording
#: changes (word choice, the exact day count) never drift between the engine, the partner API response, and
#: a future frontend. Kept in sync with `AgentConfig.retention_days` only by convention (a config change to
#: the number of days doesn't rewrite this string automatically) -- deliberately: the number this string
#: quotes is a promise to the caller, so bumping the config alone should never silently make the promise
#: wrong; whoever changes `retention_days` updates this string in the same change.
RETENTION_NOTICE = (
    "Don't submit real names or case details; anonymous submissions are kept up to 7 days for audit and "
    "are never used for training or evaluation."
)


def _hash_obj(obj: Any) -> str:
    return _sha(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))


def purge_stale_runs(audit_dir: Path, retention_days: float, *, now: float | None = None) -> list[Path]:
    """Deletes every `*.jsonl` run record under `audit_dir` whose own mtime is older than `retention_days`
    (issue #39's TTL purge). Returns the paths actually deleted, for a caller (or a test) that wants to know
    what happened without re-scanning the directory itself.

    `now` is a Unix timestamp (`time.time()`'s own epoch), injectable for tests -- never `time.monotonic()`,
    which has no fixed epoch to compare a file's mtime against. A missing `audit_dir` (nothing has ever run
    yet) is not an error: nothing to purge, an empty list. A file that disappears between the listing and the
    delete (another process racing this one) is not an error either -- it is already gone, which is exactly
    what this function wanted."""
    if not audit_dir.exists():
        return []
    cutoff = (now if now is not None else time.time()) - retention_days * 86400
    deleted: list[Path] = []
    for path in audit_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(path)
        except FileNotFoundError:
            continue
    return deleted


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


class _BufferedAudit:
    """Collects one (contract, element/denial) task's `.step()` calls while it runs concurrently, instead of
    writing them straight to the real `AuditTrail`. `flush_into` replays them, in the task's own order, once
    every task in the contract has finished -- so the audit trail a concurrent run produces is the same steps
    in the same order as a serial run, whichever task's HTTP call happens to answer first."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, Any, Any, float]] = []

    def step(self, name: str, inputs: Any, output: Any, wall_ms: float) -> None:
        self._entries.append((name, inputs, output, wall_ms))

    def flush_into(self, audit: AuditTrail, *, after_fault: bool = False) -> None:
        """Replays this task's buffered steps into the real trail. `after_fault=True` (a config-fault
        cancelled the request but THIS task had already started and could not be interrupted mid HTTP-call)
        tags every dict-shaped output with `after_fault: true` -- the audit must record reality, never pretend
        a call that actually happened did not. Never set otherwise, so the ordinary (no fault) path's output
        content is untouched -- byte-identical to a run with no concurrency at all."""
        for name, inputs, output, wall_ms in self._entries:
            if after_fault and isinstance(output, dict):
                output = {**output, "after_fault": True}
            audit.step(name, inputs, output, wall_ms)


@dataclass(frozen=True)
class JudgeCallRecord:
    """One `judge.judge(request)` invocation (one attempt of one element), for the per-request judge
    accounting -- no keys, no prompt text, just what a caller needs to reason about cost and latency."""

    wall_ms: float
    ok: bool
    backend_used: int | None
    second_called: bool
    second_backend_used: int | None


def _judge_call_record(judgment: ElementJudgment | None, wall_ms: float, *, ok: bool) -> JudgeCallRecord:
    if judgment is None:
        return JudgeCallRecord(wall_ms, ok, None, False, None)
    # `second_skipped` is True only on the "primary_not_established" skip; a second that was actually invoked
    # (whether it established, vetoed, or errored fail-closed) always leaves it False, see nyaya_judges.AndGateJudge.
    second_called = judgment.second_judge is not None and not judgment.second_skipped
    return JudgeCallRecord(
        wall_ms, ok, judgment.backend_used, second_called,
        judgment.backend_used_second if second_called else None,
    )


def _judge_accounting(records: Sequence[JudgeCallRecord]) -> dict[str, Any]:
    """Aggregates a run's `JudgeCallRecord`s into the per-request accounting the audit trail records: call
    counts and a `backend_used` histogram (concurrency-invariant, must match bit for bit between a serial and
    a concurrent run of the same request) plus total/max latency (real wall-clock, expected to shrink under
    concurrency -- never asserted equal across configs)."""
    wall = [r.wall_ms for r in records]
    backend_used_counts: dict[str, int] = {}
    backend_used_second_counts: dict[str, int] = {}
    for r in records:
        if r.backend_used is not None:
            key = str(r.backend_used)
            backend_used_counts[key] = backend_used_counts.get(key, 0) + 1
        if r.second_backend_used is not None:
            key = str(r.second_backend_used)
            backend_used_second_counts[key] = backend_used_second_counts.get(key, 0) + 1
    return {
        "judge_calls_primary": len(records),
        "judge_calls_second": sum(1 for r in records if r.second_called),
        "total_latency_ms": round(sum(wall), 3),
        "max_latency_ms": round(max(wall), 3) if wall else 0.0,
        "backend_used_counts": backend_used_counts,
        "backend_used_second_counts": backend_used_second_counts,
    }


# -- results -----------------------------------------------------------------------------------------------


#: Truthful element statuses (issue #37, Tag's review): a gold-established element the served judge(s) didn't
#: clear tau on must never look the same as one a judge actually scored unmet, or one the second judge never
#: got to evaluate at all, or one Gate 1's own model failed to evaluate (issue #57, Tag's review of #63: a
#: fail-closed Gate 1 error must not share `not_established`'s label with a genuine veto or a real unmet
#: score -- there IS no score here, only a model that couldn't answer, the same distinction
#: `not_evaluated_second_unavailable` already draws for the second judge) -- four previously-collapsed cases
#: now have four distinct labels below, alongside the original "established". `assemble_assertions` and every
#: other outcome check still treats every non-"established" value identically (via `_established_tristate`
#: below); this ONLY changes what a caller sees, never what the contract's outcome is.
ElementStatus = Literal[
    "established", "not_confirmed", "not_established",
    "not_evaluated_second_unavailable", "not_evaluated_gate1_unavailable",
]


def _established_tristate(status: ElementStatus) -> bool | None:
    """Issue #56: `assemble_assertions`' input must distinguish a judge's genuine "not met" from "nobody
    actually evaluated this" -- collapsing both to a bare `False` (what `status == "established"` did before
    this) let a caller of the assertions dict not tell the two apart. True only for "established"; None for
    either `not_evaluated_*` status -- `not_evaluated_second_unavailable` (the second judge never answered)
    and `not_evaluated_gate1_unavailable` (issue #57/#63/#72: Gate 1's own model never answered, so the
    element was never even taken to the judges) are both "nobody reached a verdict", not a judge's own
    negative one -- so there is no verdict at all, only a gap; `not_confirmed` and `not_established` both map
    to False -- both are a judge's own, evaluated verdict of "not met" (module doc / issue #37: identical
    outcome either way, only the label differs), so neither is a gap the way the two `not_evaluated_*`
    statuses are."""
    if status == "established":
        return True
    if status in ("not_evaluated_second_unavailable", "not_evaluated_gate1_unavailable"):
        return None
    return False


@dataclass
class ElementResult:
    element: str
    is_denial: bool
    status: ElementStatus
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
    #: Attempt 1's second-judge fields (config C, AND-gate only; all None/False when there is no second judge,
    #: or the second was never asked -- primary rejected outright or the second failed closed/unavailable).
    p_established_second: float | None = None
    tau_second: float | None = None
    second_skip_reason: str | None = None
    second_logit_distance: float | None = None
    second_refer_band_fired: bool = False
    #: True iff `second_skip_reason` records the second judge failing closed after an error (AndGateJudge's
    #: `second_unavailable: ...` prefix) -- distinct from `primary_not_established` (an ordinary skip, never a
    #: REFER on its own) and from the logit-distance band above (there is no `p_established_second` to compare
    #: in this case). Lead-2, 2026-09-24: a second-judge error must surface as REFER_TO_LAWYER, not silently
    #: become an ordinary not-established fact that can drive a false DENIAL.
    second_unavailable: bool = False
    #: The second judge's own fact_id, kept for the record even though `fact_id` above (the primary's span)
    #: is what the quote check and the Lean wire use -- AndGateJudge never substitutes it in
    #: (`nyaya_judges.AndGateJudge`'s own docstring). Behaviour-neutral: propagating these two fields changes
    #: no verdict, quote, or outcome; they only make an existing `ElementJudgment`-level signal visible past
    #: the judge call, where it was previously computed and then discarded (found 2026-09-27 investigating
    #: the fact-id convention skew: `judgment.as_dict()` already carries both fields into the audit log, but
    #: neither ever reached `ElementResult`, so no sealed run's response ever recorded a disagreement).
    #: None/False whenever the second judge was never asked (single-judge mode, or the primary already
    #: rejected) -- same convention as every other second-judge field above.
    second_fact_id: str | None = None
    #: True iff the second judge cited a different fact_id than the primary (both established, both cited
    #: something) -- `AndGateJudge`'s own computation, reused verbatim, never re-derived here.
    fact_id_disagreement: bool = False
    #: P0 #152: this DENY defeater was established by the primary and rejected by the second. The defeater
    #: stays established (the second never vetoes one) and the contract is REFER_TO_LAWYER
    #: (`second_judge_defeater_disagreement`), never PROOF.
    defeater_second_disagreement: bool = False
    #: Issue #37 (single-judge fix, #57): which judge's tau this non-established element failed to clear --
    #: "primary" (it rejected outright; also single-judge mode's ONLY possible value, since there is no
    #: second judge to blame instead) or "second" (the primary passed but the second didn't). None whenever
    #: the element IS established, or the non-established reason isn't a tau miss at all
    #: (`not_evaluated_second_unavailable`, or a Gate 1 veto -- Gate 1's own `gate1_veto_kind` field already
    #: names that reason). Reuses `ElementJudgment.vetoed_by` (issue #37's own suggestion) where AndGateJudge
    #: already set it; single-judge mode never sets `vetoed_by` at all (that field only exists on
    #: AndGateJudge's own output), so `_truthful_status` fills in "primary" itself in that case -- see its
    #: own docstring.
    #:
    #: There is no "both" value: it would mean both judges independently failed their own tau, which would
    #: require asking the second even after the primary has already failed -- the current AND-gate's own
    #: cost-saving early return (AndGateJudge.judge) never does that, so no code path can produce it. Declared
    #: here once (issue #57, Tag's review of #37) and then removed rather than left as a documented-but-dead
    #: type value once confirmed unreachable; implementing that mode is a materially different, more
    #: expensive design (always paying the second judge's call) that no issue has asked for.
    binding_leg: Literal["primary", "second"] | None = None
    #: Gate 1 (Track-C, 2026-09-26): a third gate, NLI entailment check, run only when the judge(s) above
    #: already established the element. All None/False when Gate 1 is not configured (`NYAYA_GATE1_ENABLED`
    #: unset/false, today's default) or was never asked (nothing established yet to check).
    gate1_score: float | None = None
    gate1_disjuncts: list[str] | None = None
    #: The Gate 1 model itself failed to load or errored on this call -- fail closed (see `nyaya_judges.
    #: Gate1Judge`'s own docstring), surfaced as REFER_TO_LAWYER (`gate1_unavailable`), never a silent
    #: not-established.
    gate1_unavailable: bool = False
    #: Gate 1 (entailment mode) answered but scored below its threshold -- REFER_TO_LAWYER
    #: (`gate1_not_entailed`), distinct from `gate1_unavailable`: there IS a score here, it just didn't
    #: clear the bar. Mutually exclusive with `gate1_contradiction` below (only one mode ever runs).
    gate1_not_entailed: bool = False
    #: Gate 1 (Arm C, `mode="contradiction_veto"`) found the fact explicitly contradicts the element --
    #: REFER_TO_LAWYER (`gate1_contradiction`), GATE1-ARM-C-2026-09-26.md. Mutually exclusive with
    #: `gate1_not_entailed` above.
    gate1_contradiction: bool = False
    #: "none" when both established/not tokens were in the judge's own top-k; "lower_bound" or
    #: "upper_bound" when one was missing and `p_established` is only a bound on the true value, not
    #: an exact softmax (2026-09-28, G-28 -- see `nyaya_judges.p_established_from_top_logprobs`'s own
    #: docstring for the derivation). "none" when there is no anchor at all (missing_element).
    clamp: ClampKind = "none"
    #: True iff a bound above did not itself resolve which side of tau the true value falls on, so the
    #: element was conservatively decided not_established without actually demonstrating it -- distinct
    #: from an ordinary tau-miss. See `HouseJudge.judge`'s own decision rule.
    bound_undetermined: bool = False


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
    #: Elements whose second-judge logit-distance band fired (config C only; always [] when
    #: `second_refer_logit_delta` is off -- the default). Distinct from `uncertain` (the primary's own band):
    #: an element can appear in either, both, or neither.
    uncertain_second: list[str] = field(default_factory=list)
    #: Elements whose second judge was unavailable (errored, not a config fault) and failed closed. Always []
    #: when there is no second judge. Distinct from `uncertain_second`: this fires unconditionally on a second-
    #: judge error, with no `second_refer_logit_delta` needed and no p-value to compare.
    unavailable_second: list[str] = field(default_factory=list)
    #: Elements whose Gate 1 model was unavailable (errored, or never loaded). Always [] when Gate 1 is not
    #: configured. Distinct from `gate1_failed`: this is the model itself failing, not a real score below
    #: threshold.
    gate1_unavailable: list[str] = field(default_factory=list)
    #: Elements the judge(s) established but Gate 1's entailment check did not clear its threshold. Always []
    #: when Gate 1 is not configured, or configured in `mode="contradiction_veto"`.
    gate1_failed: list[str] = field(default_factory=list)
    #: Elements Gate 1 (Arm C, `mode="contradiction_veto"`) vetoed because the fact explicitly contradicts
    #: the element. Always [] when Gate 1 is not configured, or configured in the default entailment mode.
    gate1_contradiction: list[str] = field(default_factory=list)
    #: Whitespace-normalised difflib ratio of the two texts (1.0 = same words), so a mismatch that is only
    #: layout is distinguishable from a judge text that is a short paraphrase of a much longer official one.
    #: Informational only; nothing gates on it.
    statute_text_similarity: float | None = None
    #: element -> `element_stage(...)` for every element not established, so the contract-level `reason`
    #: ("missing_element") can be split by where each element stopped. Informational; no outcome reads it.
    element_stages: dict[str, str] = field(default_factory=dict)
    #: What the pinned Lean checker was given and returned: `{binary_sha256, wire_sha256, verdict}`.
    #: `binary_sha256` is the pinned `score` binary; `wire_sha256` is the SHA-256 of the exact REG wire line
    #: sent to it (recomputable from `assertions` + `contract_id`); `verdict` is the binary's own. It attests
    #: what was scored and by which binary -- a structural check, not that the assertions are true. None when
    #: no Lean call was made.
    lean_attestation: dict[str, str] | None = None
    #: The provision text from Lean `--describe-source` (India Code, unofficial: never call it official). Always set
    #: when the contract was described; the partner response's `rule_text`.
    rule_text: str | None = None
    #: Exactly the first `house_judge.statute_chars` characters of the judge's configured statute text, i.e. the text AS
    #: SENT in the judge prompt (`build_house_prompt` cuts to that length). Set when `statute_text_mismatch` is true
    #: OR when the judge's text was cut (longer than the cut); None otherwise, and None when no judge statute text is configured.
    judge_rule_text: str | None = None
    #: Where `rule_text` came from: `RULE_TEXT_SOURCE`.
    rule_text_source: str | None = None


@dataclass
class AgentRun:
    run_id: str
    judge: str
    score_sha256: str
    facts: list[dict[str, str]]
    contracts: list[ContractResult]
    audit_path: Path
    provenance: str = "agama"
    #: `_judge_accounting`'s summary of every judge call this run made (counts, latency, backend histogram).
    #: Content-identical across `max_concurrency` values except the latency numbers, which are real wall-clock.
    judge_accounting: dict[str, Any] = field(default_factory=dict)
    #: Issue #39's own guard: True unless the caller of `NyayaAgent.run` explicitly says otherwise. A future
    #: training/eval-corpus builder reading `audit_dir` must treat `client_data=True` runs as off-limits --
    #: the SAFE pole is the default, so forgetting to think about this for a new caller can never accidentally
    #: leak a real submission into training data; only a caller that deliberately knows its input is
    #: controlled/internal (e.g. a sealed eval harness calling `agent.run` directly, never a public endpoint)
    #: would ever pass `client_data=False`. No such caller exists in this repo today.
    client_data: bool = True
    #: Issue #39: the exact notice text every caller of `/api/v1/analyse-facts` (and any other surface this
    #: run reaches) must show or return, verbatim -- one place, so wording changes never drift between the
    #: engine, the partner API response, and a future frontend that reads this field instead of hardcoding
    #: its own copy.
    retention_notice: str = RETENTION_NOTICE
    #: #220: the standard this run applied, from the same resolved values the `run_start` audit row carries.
    #: `{"requested", "applied", "source", "proceeding_posture", "in_judge_prompt"}`; `applied` is None when the
    #: judge's prompt did not state the standard. None only for a hand-built run.
    standard: dict[str, Any] | None = None
    #: The contract -> source map this run already read from `--list-contracts` for selection, kept so a caller
    #: that needs the sources (the partner API's citations) does not spawn a second subprocess. Never part of
    #: a response: `to_dict` drops it.
    listed_sources: dict[str, list[str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("listed_sources", None)
        d["audit_path"] = str(self.audit_path)
        return d


def _build_house_judge(hj_cfg: Mapping[str, Any], *, tau: float, typed: bool, api_key_env: str) -> Judge:
    """One judge slot (primary or, for config C, second) from a `house_judge`-shaped config: `HouseJudge` by
    default, or `pravrudhi.application.typed.house_judge.TypedHouseJudge` over a `VLLMDecoder` when `typed`
    is set (T1) -- shared by `NyayaAgent.house` for BOTH slots, so the typed-layer flag and config C's second
    judge compose instead of the flag silently applying to only one of them."""
    if typed:
        from pravrudhi.application.typed.decoder import VLLMDecoder
        from pravrudhi.application.typed.house_judge import TypedHouseJudge

        api_key = os.environ.get(api_key_env) or hj_cfg.get("api_key") or None
        decoder = VLLMDecoder(
            base_url=str(hj_cfg["base_url"]),
            model=hj_cfg.get("model") or None,
            timeout_s=int(hj_cfg["timeout_s"]),
            api_key=api_key,
            fallback_urls=hj_cfg.get("base_urls_fallback") or [],
            enforce_served_model=bool(hj_cfg.get("enforce_served_model", False)),
        )
        return TypedHouseJudge(
            tau=tau,
            statute_chars=int(hj_cfg["statute_chars"]),
            decoder=decoder,
            max_tokens=int(hj_cfg["max_tokens"]),
            top_logprobs=int(hj_cfg["top_logprobs"]),
            label_mass_floor=float(hj_cfg["label_mass_floor"]),
            prompt_template=str(hj_cfg.get("prompt_template", "legacy")),
        )
    from pravrudhi.application.nyaya_judges import HouseJudge

    return HouseJudge.from_config(hj_cfg, tau=tau, api_key_env=api_key_env)


def _clamp_p(p: float) -> float:
    """`p` clamped into (1e-9, 1-1e-9) -- `logit` is defined nowhere else and a served, bf16-quantised
    probability can land exactly on 0.0 or 1.0."""
    return min(max(p, 1e-9), 1.0 - 1e-9)


def _logit(p: float) -> float:
    p = _clamp_p(p)
    return math.log(p / (1.0 - p))


def _second_band_info(anchor: ElementJudgment | None, delta: float | None) -> dict[str, Any]:
    """Attempt 1's second-judge fields for `ElementResult` (module doc, "the second judge gets its own band"):
    `second_refer_band_fired` is True only when the second judge was actually asked and answered --
    `p_established_second` and `tau_second` both present -- AND `delta` is configured AND the logit distance is
    STRICTLY less than it (`< delta`, never `<=`). The skip path (`second_skip_reason ==
    "primary_not_established"`) leaves `p_established_second` None here and never reaches the band -- a band
    REFER must come from genuine second-judge uncertainty, never as a side effect of a skip.

    `second_unavailable` is a SEPARATE signal (Lead-2, 2026-09-24): `AndGateJudge` records
    `second_skip_reason` starting with `"second_unavailable"` when the second judge errored (not a config
    fault -- those raise) and the element failed closed. That element has no `p_established_second` to band
    on, but it must still surface as a REFER, not an ordinary not-established fact -- `_run_contract` checks
    this flag unconditionally, independent of whether `delta` is even configured.

    `second_fact_id` / `fact_id_disagreement` (2026-09-27): read straight off `anchor` -- `AndGateJudge`
    already computed them, this function never re-derives the comparison. Behaviour-neutral: neither field
    feeds any decision here or in `_truthful_status`; they exist only so a caller can SEE the second judge's
    own citation and whether it matched the primary's, which the verdict itself has never depended on."""
    out: dict[str, Any] = {
        "p_established_second": None, "tau_second": None, "second_skip_reason": None,
        "second_logit_distance": None, "second_refer_band_fired": False, "second_unavailable": False,
        "second_fact_id": None, "fact_id_disagreement": False,
        "defeater_second_disagreement": False,
    }
    if anchor is None:
        return out
    out["second_skip_reason"] = anchor.second_skip_reason
    out["tau_second"] = anchor.tau_second
    out["second_unavailable"] = bool(
        anchor.second_skip_reason and anchor.second_skip_reason.startswith("second_unavailable")
    )
    out["second_fact_id"] = anchor.second_fact_id
    out["fact_id_disagreement"] = anchor.fact_id_disagreement
    out["defeater_second_disagreement"] = anchor.defeater_second_disagreement
    if anchor.p_established_second is None or anchor.tau_second is None:
        return out
    out["p_established_second"] = anchor.p_established_second
    distance = abs(_logit(anchor.p_established_second) - _logit(anchor.tau_second))
    out["second_logit_distance"] = distance
    out["second_refer_band_fired"] = delta is not None and distance < delta
    return out


def _truthful_status(
    claimed: bool, valid: bool, anchor: ElementJudgment | None, second_unavailable: bool,
    *, second_judge_configured: bool,
) -> tuple[ElementStatus, Literal["primary", "second"] | None]:
    """The final element status and binding leg (issue #37; single-judge fix, #57). Called only after
    `_second_band_info` has already computed `second_unavailable`, so the "second judge never answered"
    signal is read once, not re-derived. `claimed`/`valid` follow `_judge_element`'s own naming: `claimed` is
    attempt 1's own judge status, `valid` is claimed AND the quote verified.

    A claimed-but-unverifiable quote (`claimed and not valid`) is always `not_established` regardless of
    `p_established` -- a hallucination is a real negative, never a confidence question. Otherwise (`not
    claimed`, so `anchor` exists and its own status was already not "established"): a Gate 1 veto keeps its
    existing `not_established` label untouched (its own `gate1_veto_kind` field already names the real
    reason); the second-unavailable case gets its own distinct label; and what remains is split by whether
    the judge(s) that DID run leaned toward established (p >= 0.5) without clearing their tau
    (`not_confirmed`) or actually scored the element unmet (`not_established`) -- the outcome any of these
    three drives is identical (never "established"), only the label differs.

    `binding_leg` reuses `anchor.vetoed_by` where AndGateJudge already set it (its own primary-rejected
    branch sets `vetoed_by="primary"`, its second-rejected branch sets `"second"`) -- but a BARE single judge
    (no AndGateJudge wrapper at all) never touches `vetoed_by`; that field only exists on AndGateJudge's own
    output. `second_judge_configured` (Tag review, 2026-09-26 -- the caller passes `self.config.second_judge
    is not None`) is the discriminator for that case, NOT `anchor.tau_second is None`: the config is the
    actual ground truth for whether a second judge exists in this run, while `tau_second` is a side effect of
    AndGateJudge's own field-setting that this function has no business depending on -- a future judge
    implementation that shapes its output differently would silently break the old inference without
    touching the config at all. When no second judge is configured, the primary is the ONLY judge in play, so
    it alone decided any non-established outcome; `binding_leg` must be "primary", never null, matching its
    own docstring."""
    if claimed and valid:
        return "established", None
    if claimed and not valid:
        return "not_established", None
    assert anchor is not None  # claimed is False only when anchor.status != "established", so anchor exists
    if anchor.vetoed_by == "span_relevance":
        return "not_established", None
    if anchor.vetoed_by == "gate1":
        # A fail-closed Gate 1 error (the model never loaded or errored on this call) gets its own label,
        # never "not_established" (Tag review, 2026-09-26): there is no score to distrust here, unlike a real
        # veto (`gate1_not_entailed`/`gate1_contradiction`, which keep "not_established" -- the model DID
        # answer, it just didn't clear the bar). Same `gate1_skip_reason` prefix `_gate1_info` itself checks.
        if anchor.gate1_skip_reason and anchor.gate1_skip_reason.startswith("gate1_unavailable"):
            return "not_evaluated_gate1_unavailable", None
        return "not_established", None
    if second_unavailable:
        return "not_evaluated_second_unavailable", None
    binding_leg = anchor.vetoed_by if anchor.vetoed_by in ("primary", "second") else None
    if binding_leg is None and not second_judge_configured:
        binding_leg = "primary"  # single-judge mode: no second judge exists to blame instead
    second_leans_established = anchor.p_established_second is None or anchor.p_established_second >= 0.5
    if anchor.p_established >= 0.5 and second_leans_established:
        return "not_confirmed", binding_leg
    return "not_established", binding_leg


def _gate1_info(anchor: ElementJudgment | None) -> dict[str, Any]:
    """Attempt 1's Gate 1 fields for `ElementResult` -- mirrors `_second_band_info`'s shape exactly, reading
    only from `anchor` (attempt 1's own judgment; a quote-only retry never re-reads Gate 1's opinion, same
    convention the second judge's own fields already follow). `gate1_unavailable` is a fail-closed signal
    (the model errored or never loaded), distinct from the two POSSIBLE veto reasons (the model answered,
    the score just didn't clear the bar) -- `gate1_not_entailed` (entailment mode) and `gate1_contradiction`
    (Arm C, contradiction_veto mode) are mutually exclusive with each other and with `gate1_unavailable`; all
    three are False/None when Gate 1 is not configured at all."""
    out: dict[str, Any] = {
        "gate1_score": None, "gate1_disjuncts": None, "gate1_unavailable": False,
        "gate1_not_entailed": False, "gate1_contradiction": False,
    }
    if anchor is None:
        return out
    out["gate1_score"] = anchor.gate1_score
    out["gate1_disjuncts"] = anchor.gate1_disjuncts
    out["gate1_unavailable"] = bool(
        anchor.gate1_skip_reason and anchor.gate1_skip_reason.startswith("gate1_unavailable")
    )
    vetoed = anchor.vetoed_by == "gate1" and not out["gate1_unavailable"]
    out["gate1_not_entailed"] = vetoed and anchor.gate1_veto_kind == "not_entailed"
    out["gate1_contradiction"] = vetoed and anchor.gate1_veto_kind == "contradiction"
    return out


# -- the loop ----------------------------------------------------------------------------------------------


class NyayaAgent:
    def __init__(
        self, judge: Judge, registry: Registry, config: AgentConfig, *, judge_pool: Sequence[Judge] | None = None,
    ) -> None:
        self.judge = judge
        self.registry = registry
        self.config = config
        #: One independent judge stack per concurrent worker (own HouseJudge, own ChatClients): a HouseJudge's
        #: `complete` mutates its client's `.model` attribute per call (see nyaya_judges.HouseJudge, the
        #: `_complete_with_fallback` closure), so two concurrent judge calls sharing one instance would race
        #: on it. `NyayaAgent.house` builds this pool to `config.max_concurrency` size; a caller building the
        #: agent directly (e.g. a test) with `config.max_concurrency > 1` and no pool gets `[judge]` alone --
        #: safe only if that single `judge` is itself safe to share, which a bare HouseJudge is NOT.
        self.judge_pool: list[Judge] = list(judge_pool) if judge_pool else [judge]

    @classmethod
    def house(
        cls, root: Path, *, config: AgentConfig | None = None,
        second_judge_breaker: SecondJudgeCircuitBreaker | None = None,
    ) -> NyayaAgent:
        """The configured loop: the house judge from `house_judge`, the pinned binary from `score_bin`.

        Two independent flags compose:

        * `config.typed_layer` (T1, docs/decisions/TYPED-LAYER-PLAN-2026-09-24.md) -- each judge SLOT builds
          `pravrudhi.application.typed.house_judge.TypedHouseJudge` over a `VLLMDecoder` instead of
          `nyaya_judges.HouseJudge`, from the same config. Default False, unchanged production path.
        * `config.second_judge` (config C) -- when configured, the judge is `AndGateJudge(primary, second)`
          instead of the single judge alone: an element only reaches PROOF when BOTH clear their own tau.
          Default None, unchanged production path.

        `typed_layer` governs BOTH the primary and the second slot when config C is on: mixing a typed
        primary with an untyped second (or vice versa) would leave the flag's meaning ambiguous per element,
        for no benefit -- HouseJudge and TypedHouseJudge are independently verified at parity (0 flips over
        the live 279-prompt set, `scripts/typed_layer_parity*.py`), so building both slots the same way
        changes nothing about either judge's DECISION, only which construction path they share.

        `second_judge_breaker` (issue #35): strictly opt-in, forwarded as-is to every `AndGateJudge` this
        call builds (the whole `judge_pool`, so concurrent workers within one request share it too). `None`
        (the default, every existing caller) is byte-identical to before this parameter existed. The deployed
        app shares ONE instance across every `.house()` call via `partner.py`'s own per-process singleton, so
        a real failure trips the breaker for every request within its TTL, not just the rest of this one.
        """
        cfg = config or load_agent_config(root)
        if cfg.score_bin is None:
            raise ValueError("no score_bin configured")
        registry = BinaryRegistry(cfg.score_bin, pinned_sha256=cfg.pinned_score_sha256)
        from pravrudhi.application.nyaya_judges import (
            GATE1_MODEL_DEFAULT,
            GATE1_MODEL_REVISION_DEFAULT,
            GATE1_TAU_C_DEFAULT,
            GATE1_THRESHOLD_DEFAULT,
            AndGateJudge,
            Gate1Judge,
            Gate1NLIModel,
            SpanRelevanceJudge,
        )

        # One model instance shared across the whole judge_pool (max_concurrency > 1 builds several judge
        # stacks, but loading the same ~380MB weights once per worker would be pure waste) -- inference
        # through a loaded HF model is a stateless forward pass per call, safe to share this way.
        gate1_model = (
            Gate1NLIModel(
                model_id=str(cfg.gate1.get("model", GATE1_MODEL_DEFAULT)),
                revision=str(cfg.gate1.get("revision", GATE1_MODEL_REVISION_DEFAULT)),
            )
            if cfg.gate1_enabled
            else None
        )

        def _build_judge() -> Judge:
            primary = _build_house_judge(cfg.house_judge, tau=cfg.tau, typed=cfg.typed_layer,
                                         api_key_env="NYAYA_HOUSE_JUDGE_API_KEY")
            judge: Judge
            if cfg.second_judge:
                second_tau = float(cfg.second_judge["tau"])
                second: Judge = _build_house_judge(cfg.second_judge, tau=second_tau, typed=cfg.typed_layer,
                                                   api_key_env="NYAYA_SECOND_JUDGE_API_KEY")
                # Issue #44 (Lead-2, 2026-09-26): opt-in via second_judge_positive_control.record_path --
                # absent (the default, every deployment before this) means today's behaviour, unchanged.
                # When set, wrap `second` so the real endpoint is never reached without a fresh, matching
                # passing record; RecordCheckFailed then fails closed through AndGateJudge's own existing
                # except-Exception -> second_judge_unavailable path, no new logic there.
                record_path = cfg.second_judge_positive_control.get("record_path")
                if record_path:
                    from pravrudhi.application.second_judge_positive_control import RecordGatedJudge
                    # No default: an unset endpoint_id/adapter_sha must stay None, never fall back to "" --
                    # a deployment that forgot to configure BOTH the record and the live identity must not
                    # have them silently "match" as two equal empty strings (check_record refuses on None).
                    second = RecordGatedJudge(
                        second, record_path=Path(record_path),
                        max_age_hours=float(cfg.second_judge_positive_control["max_age_hours"]),
                        expected_endpoint_id=cfg.second_judge.get("endpoint_id"),
                        expected_adapter_sha=cfg.second_judge.get("adapter_sha"),
                    )
                judge = AndGateJudge(
                    primary, second, tau_primary=cfg.tau, tau_second=second_tau, breaker=second_judge_breaker
                )
            else:
                judge = primary
            if cfg.span_relevance_enabled:
                judge = SpanRelevanceJudge(judge, primary)
            if gate1_model is not None:
                threshold = float(cfg.gate1.get("threshold", GATE1_THRESHOLD_DEFAULT))
                tau_c = float(cfg.gate1.get("tau_c", GATE1_TAU_C_DEFAULT))
                mode = str(cfg.gate1.get("mode", "entailment"))
                if mode not in ("entailment", "contradiction_veto"):
                    raise ValueError(f"gate1.mode must be 'entailment' or 'contradiction_veto', got {mode!r}")
                judge = Gate1Judge(judge, gate1_model, mode=mode, threshold=threshold, tau_c=tau_c)  # type: ignore[arg-type]
            return judge

        judge = _build_judge()
        # `max_concurrency > 1` builds one independent judge stack per worker slot (own ChatClients, see
        # NyayaAgent.__init__) rather than sharing `judge` across threads.
        judge_pool = [judge] if cfg.max_concurrency <= 1 else [judge] + [_build_judge() for _ in range(cfg.max_concurrency - 1)]
        return cls(judge, registry, cfg, judge_pool=judge_pool)

    def _judge_element(
        self,
        judge: Judge,
        audit: AuditTrail | _BufferedAudit,
        contract_id: str,
        element: str,
        is_denial: bool,
        statute: str,
        facts: tuple[Fact, ...],
        narrative: str,
        proceeding_posture: str | None = None,
    ) -> tuple[ElementResult, list[JudgeCallRecord]]:
        """Attempt 1 decides the element's status and p_established. If it says established but its quote is
        not verbatim in the named fact, up to `max_retries` re-asks follow -- the SAME request, same training
        statute -- and each is used ONLY for its quote; its status and p are recorded and ignored.

        `judge` is passed explicitly (rather than read off `self.judge`) so a concurrent caller can hand this
        task its OWN judge instance (see `NyayaAgent.judge_pool`); `audit` may be the real `AuditTrail` (serial
        path) or a `_BufferedAudit` (concurrent path, flushed into the real trail only after every task in the
        contract has finished, in original order)."""
        fact_map = {f.id: f.text for f in facts}
        request = JudgeRequest(
            contract_id, element, is_denial, statute, narrative, tuple((f.id, f.text) for f in facts),
            skip_second=contract_id not in self.config.validated_contracts,
            proceeding_posture=proceeding_posture,
        )
        anchor: ElementJudgment | None = None
        fact_id: str | None = None
        quote: str | None = None
        quote_source: str | None = None
        loc: QuoteLocation | None = None
        error: str | None = None
        attempts = 0
        calls: list[JudgeCallRecord] = []
        for attempt in range(1, self.config.max_retries + 2):
            attempts = attempt
            uses = "status_and_quote" if anchor is None else "quote_only"
            head = {"contract_id": contract_id, "element": element, "attempt": attempt, "statute_source": "config",
                    "uses": uses}
            t0 = time.monotonic()
            try:
                judgment = judge.judge(request)
            except Exception as e:  # recorded and retried; a judge failure never becomes a status
                wall_ms = _ms(t0)
                status = _judge_config_fault(e)
                if status is not None:
                    audit.step("judge", asdict(request), {**head, "error": f"configuration fault {status}"}, wall_ms)
                    raise JudgeMisconfigured(f"the judge refused the request ({status}); check its key and model") from e
                if anchor is None:
                    error = f"{type(e).__name__}: {e}"[-400:]
                audit.step("judge", asdict(request), {**head, "error": f"{type(e).__name__}: {e}"[-400:]}, wall_ms)
                calls.append(_judge_call_record(None, wall_ms, ok=False))
                continue
            wall_ms = _ms(t0)
            calls.append(_judge_call_record(judgment, wall_ms, ok=True))
            audit.step("judge", asdict(request), {**head, "judge": judge.name, "judgment": judgment.as_dict()}, wall_ms)
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
            # A deterministic judge re-asked with the same request repeats itself, and an unresolvable fact id is
            # never replaced by a nearest match, so further attempts cannot change the outcome.
            if loc.reason == "unknown_fact" and getattr(judge, "deterministic", False):
                break
        delta = self.config.second_refer_logit_delta()
        if anchor is None:
            return ElementResult(element, is_denial, "not_established", False, None, None, None, None, None, None,
                                 attempts, error=error, **_second_band_info(None, delta), **_gate1_info(None)), calls
        claimed = anchor.status == "established"
        valid = claimed and loc is not None and loc.valid
        second_band = _second_band_info(anchor, delta)
        final_status, final_binding_leg = _truthful_status(
            claimed, valid, anchor, second_band["second_unavailable"],
            second_judge_configured=self.config.second_judge is not None,
        )
        result = ElementResult(
            element, is_denial, final_status, claimed, anchor.p_established,
            fact_id, quote, loc.start if loc else None, loc.end if loc else None, loc.reason if loc else None, attempts,
            occurrences=loc.occurrences if loc else 0, offsets_source=loc.offsets_source if loc and loc.valid else None,
            quote_source=quote_source, binding_leg=final_binding_leg, clamp=anchor.clamp,
            bound_undetermined=anchor.bound_undetermined, **second_band, **_gate1_info(anchor),
        )
        return result, calls

    def _judge_elements(
        self,
        audit: AuditTrail,
        contract_id: str,
        tasks: Sequence[tuple[str, bool]],
        statute: str,
        facts: tuple[Fact, ...],
        narrative: str,
        proceeding_posture: str | None = None,
    ) -> tuple[list[ElementResult], list[JudgeCallRecord]]:
        """Judges every `(element_or_denial, is_denial)` task of one contract. `max_concurrency <= 1` (the
        default) or a single task runs them one at a time, writing straight to `audit` -- byte-for-byte the
        pre-concurrency code path. A pool > 1 runs every task's judge calls concurrently, bounded by
        `min(max_concurrency, len(tasks))` workers, each task drawing an exclusive judge instance from
        `self.judge_pool` (returned to the pool when it finishes, so no instance is ever used by two tasks at
        once); each task's audit steps are buffered while it runs and flushed into `audit`, in the tasks'
        ORIGINAL order, only once every task in this contract has finished -- so the audit trail and result
        order never depend on which task's HTTP call happens to answer first.

        A configuration fault (`JudgeMisconfigured`, one task's judge refused with a 4xx) is FAIL-FAST: every
        not-yet-started future is cancelled the moment the fault is seen, so a misconfigured deployment burns
        at most `workers` calls, never every task. A task already running cannot be interrupted mid HTTP-call
        and is let finish; its buffered steps are flushed anyway (in the `finally` below, alongside every
        other task that ran) so the audit records what actually happened, tagged `after_fault: true` since it
        completed only after the fault was already known. `JudgeMisconfigured` is then re-raised, exactly as
        the serial path already does."""
        if self.config.max_concurrency <= 1 or len(tasks) <= 1:
            pairs = [
                self._judge_element(
                    self.judge, audit, contract_id, name, is_denial, statute, facts, narrative, proceeding_posture)
                for name, is_denial in tasks
            ]
            return [r for r, _ in pairs], [c for _, calls in pairs for c in calls]

        workers = min(self.config.max_concurrency, len(tasks))
        pool_size = len(self.judge_pool)
        if pool_size >= workers:
            judges = list(self.judge_pool[:workers])
        elif pool_size:
            # Fewer distinct judge instances than workers (e.g. NyayaAgent built directly, bypassing `.house`,
            # with a short `judge_pool`): reuse instances round-robin rather than refuse to run concurrently.
            # A caller wanting every worker fully isolated must supply `workers` instances.
            judges = [self.judge_pool[i % pool_size] for i in range(workers)]
        else:
            judges = [self.judge]
        work_queue: queue.SimpleQueue[Judge] = queue.SimpleQueue()
        for j in judges:
            work_queue.put(j)
        buffers = [_BufferedAudit() for _ in tasks]

        def _run(i: int) -> tuple[ElementResult, list[JudgeCallRecord]]:
            j = work_queue.get()
            try:
                name, is_denial = tasks[i]
                return self._judge_element(
                    j, buffers[i], contract_id, name, is_denial, statute, facts, narrative, proceeding_posture)
            finally:
                work_queue.put(j)

        results_by_index: dict[int, tuple[ElementResult, list[JudgeCallRecord]]] = {}
        after_fault_indices: set[int] = set()
        fault: JudgeMisconfigured | None = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Each worker runs in a copy of this thread's context (the serving guards are ContextVars a pool thread
            # would not otherwise see), copied here on the calling thread.
            futures = {pool.submit(contextvars.copy_context().run, _run, i): i for i in range(len(tasks))}
            try:
                for fut in as_completed(futures):
                    i = futures[fut]
                    already_faulted = fault is not None
                    try:
                        results_by_index[i] = fut.result()
                    except CancelledError:
                        continue  # never started -- no judge call was ever made, nothing to flush
                    except JudgeMisconfigured as e:
                        if fault is None:
                            fault = e
                            # Fail-fast: every future not yet started is cancelled now; one already running
                            # keeps running (see docstring) and is flushed below, tagged `after_fault`.
                            for other_fut, other_i in futures.items():
                                if other_i != i:
                                    other_fut.cancel()
                        else:
                            after_fault_indices.add(i)  # a second (or later) task also hit a config fault
                        continue
                    if already_faulted:
                        after_fault_indices.add(i)
            finally:
                # Every task that actually made a judge call is flushed, in ORIGINAL task order, whether or
                # not a fault occurred -- see the docstring.
                for i, buf in enumerate(buffers):
                    buf.flush_into(audit, after_fault=i in after_fault_indices)
        if fault is not None:
            raise fault
        pairs = [results_by_index[i] for i in range(len(tasks))]
        return [r for r, _ in pairs], [c for _, calls in pairs for c in calls]

    def _run_contract(
        self,
        audit: AuditTrail,
        contract_id: str,
        facts: tuple[Fact, ...],
        narrative: str,
        *,
        call_records_out: list[JudgeCallRecord] | None = None,
        proceeding_posture: str | None = None,
    ) -> ContractResult:
        t0 = time.monotonic()
        contract = self.registry.describe(contract_id)
        audit.step("describe", {"contract_id": contract_id}, asdict(contract), _ms(t0))

        # The judge sees ONLY its training statute text; the binary's official text is read for the audit
        # record (and the Lean side), never fed to the judge.
        t0 = time.monotonic()
        training = self.config.judge_statute_text.get(contract_id)
        official = self.registry.source_text(contract_id)
        mismatch = None if training is None else training != official
        cut = int(self.config.house_judge.get("statute_chars") or 0)
        # Carried when the judge's text differs from the official one, OR when the judge saw a CUT text (longer than `cut`),
        # even if the two texts are identical.
        judge_rule_text = (
            training[:cut] if (training is not None and cut > 0 and (mismatch is True or len(training) > cut)) else None
        )
        similarity = None if training is None else _text_similarity(training, official)
        audit.step("statute", {"contract_id": contract_id},
                   {"contract_id": contract_id, "judge_statute_source": "config" if training is not None else None,
                    "judge_statute_sha256": _sha(training) if training is not None else None,
                    "official_statute_sha256": _sha(official), "statute_text_mismatch": mismatch,
                    "statute_text_similarity": similarity}, _ms(t0))

        results: list[ElementResult] = []

        def finish(outcome: Outcome, reason: ContractReason, **kw: Any) -> ContractResult:
            res = ContractResult(contract_id, outcome, reason, results, kw.get("assertions"), kw.get("lean"),
                                 kw.get("lean_outcome"), kw.get("uncertain", []), mismatch,
                                 kw.get("uncertain_second", []),
                                 statute_text_similarity=similarity,
                                 element_stages={r.element: st for r in results if (st := element_stage(r))},
                                 unavailable_second=kw.get("unavailable_second", []),
                                 gate1_unavailable=kw.get("gate1_unavailable", []), gate1_failed=kw.get("gate1_failed", []),
                                 gate1_contradiction=kw.get("gate1_contradiction", []),
                                 lean_attestation=kw.get("lean_attestation"),
                                 rule_text=official, judge_rule_text=judge_rule_text,
                                 rule_text_source=RULE_TEXT_SOURCE)
            audit.step("outcome", {"contract_id": contract_id, "elements": [asdict(r) for r in results]},
                       {"contract_id": contract_id, "outcome": outcome, "reason": reason,
                        "lean_outcome": res.lean_outcome, "uncertain": res.uncertain,
                        "uncertain_second": res.uncertain_second, "unavailable_second": res.unavailable_second,
                        "gate1_unavailable": res.gate1_unavailable, "gate1_failed": res.gate1_failed,
                        "gate1_contradiction": res.gate1_contradiction,
                        "statute_text_mismatch": mismatch, "statute_text_similarity": similarity,
                        "element_stages": res.element_stages,
                        # Issue #37: the per-element truthful status/binding_leg (and every other
                        # ElementResult field) as readable OUTPUT, not just hashed into inputs_sha256 above --
                        # an auditor reading the JSONL directly must be able to see these without a matching
                        # copy of `results` to hash and compare against.
                        "elements": [asdict(r) for r in results]}, 0.0)
            return res

        if training is None:
            return finish("ABSTAIN", "no_training_statute_text")

        tasks = [(e, False) for e in contract.elements] + [(d, True) for d in contract.denials]
        elem_results, call_records = self._judge_elements(
            audit, contract_id, tasks, training, facts, narrative, proceeding_posture)
        results += elem_results
        if call_records_out is not None:
            call_records_out.extend(call_records)

        if any(r.error is not None for r in results):
            return finish("ABSTAIN", "judge_error")

        t0 = time.monotonic()
        assertions = assemble_assertions(contract, {r.element: _established_tristate(r.status) for r in results})
        local = expected_outcome(contract, assertions)
        audit.step("assemble", {"contract_id": contract_id, "elements": [asdict(r) for r in results]},
                   {"contract_id": contract_id, "assertions": assertions, "expected_outcome": local}, _ms(t0))
        t0 = time.monotonic()
        lean = self.registry.check(assertions, contract_id)
        audit.step("lean_check", {"contract_id": contract_id, "assertions": assertions, "score_sha256": self.registry.sha256},
                   {"contract_id": contract_id, **lean}, _ms(t0))
        lean_outcome = outcome_from_lean(lean)
        attestation = {"binary_sha256": self.registry.sha256,
                       "wire_sha256": reg.reg_wire_sha256(assertions, contract_id),
                       "verdict": str(lean.get("verdict"))}
        uncertain = [r.element for r in results if r.p_established is not None and self.config.in_band(r.p_established)]
        uncertain_second = [r.element for r in results if r.second_refer_band_fired]
        unavailable_second = [r.element for r in results if r.second_unavailable]
        defeater_disagreement = [r.element for r in results if r.defeater_second_disagreement]
        gate1_unavailable = [r.element for r in results if r.gate1_unavailable]
        gate1_failed = [r.element for r in results if r.gate1_not_entailed]
        gate1_contradiction = [r.element for r in results if r.gate1_contradiction]
        kw: dict[str, Any] = {"assertions": assertions, "lean": lean, "lean_outcome": lean_outcome,
                              "lean_attestation": attestation, "uncertain": uncertain, "uncertain_second": uncertain_second,
                              "unavailable_second": unavailable_second,
                              "gate1_unavailable": gate1_unavailable, "gate1_failed": gate1_failed,
                              "gate1_contradiction": gate1_contradiction}

        if lean_outcome != local:
            return finish("ABSTAIN", "assembly_lean_mismatch", **kw)
        if any(r.is_denial and r.claimed and r.status != "established" for r in results):
            return finish("REFER_TO_LAWYER", "denial_unquotable", **kw)
        if defeater_disagreement:
            return finish("REFER_TO_LAWYER", "second_judge_defeater_disagreement", **kw)
        if uncertain:
            return finish("REFER_TO_LAWYER", "uncertain", **kw)
        if uncertain_second:
            return finish("REFER_TO_LAWYER", "uncertain_second_judge", **kw)
        if unavailable_second:
            return finish("REFER_TO_LAWYER", "second_judge_unavailable", **kw)
        if gate1_unavailable:
            return finish("REFER_TO_LAWYER", "gate1_unavailable", **kw)
        if gate1_failed:
            return finish("REFER_TO_LAWYER", "gate1_not_entailed", **kw)
        if gate1_contradiction:
            return finish("REFER_TO_LAWYER", "gate1_contradiction", **kw)
        # Safety gate, allowlist form (Lead-2, 2026-09-25; inverted to fail-closed, issue #36, 2026-09-26+):
        # a contract not in `validated_contracts` never reaches the user as a PROOF or DENIAL. Every element
        # judged, quoted and Lean-checked above stays visible in `results` and the audit trail -- only the
        # FINAL outcome is intercepted, and only when it would otherwise be a definite PROOF/DENIAL; ABSTAIN
        # never reaches this line at all (both `expected_outcome` paths that produce it return earlier), so a
        # genuinely missing-element case still ABSTAINs untouched.
        if lean_outcome in ("PROOF", "DENIAL") and contract_id not in self.config.validated_contracts:
            return finish("REFER_TO_LAWYER", "contract_not_validated", **kw)
        reasons: dict[str, ContractReason] = {
            "PROOF": "all_elements_established", "DENIAL": "denial_established", "ABSTAIN": "missing_element",
        }
        reason = reasons[lean_outcome]
        return finish(lean_outcome, reason, **kw)

    def run(
        self,
        facts: Sequence[str],
        *,
        narrative: str = "",
        contract_ids: Iterable[str] | None = None,
        sections: Iterable[str] | None = None,
        client_data: bool = True,
        proceeding_posture: str | None = None,
    ) -> AgentRun:
        # Issue #39's TTL purge: opportunistic, on every new run (the same lazy-eviction discipline
        # `partner.py`'s own RateLimiter already uses) rather than a separate background process or cron --
        # no new infrastructure, and a request that never comes still means nothing accumulates unbounded
        # only because nothing new is being written either.
        standard, standard_source = standard_for_posture(proceeding_posture)  # an invalid posture raises before any work
        purge_stale_runs(Path(self.config.audit_dir), self.config.retention_days)
        run_id = f"nyaya-agent-{uuid.uuid4().hex[:10]}"
        audit = AuditTrail(Path(self.config.audit_dir) / f"{run_id}.jsonl", run_id)
        cfg_view = {"tau": self.config.tau, "refer_band": list(self.config.refer_band), "max_retries": self.config.max_retries,
                   "second_refer_logit_delta": self.config.second_refer_logit_delta()}
        if self.config.tau_source != "yaml":
            cfg_view["tau_source"] = self.config.tau_source

        in_prompt = getattr(self.judge, "prompt_template", "legacy") != "legacy"
        standard_view = {
            "proceeding_posture": proceeding_posture, "standard": standard, "standard_source": standard_source,
            "standard_in_judge_prompt": in_prompt,
        }
        standard_out = {
            "requested": standard,
            "applied": standard if in_prompt else None,
            "source": "proceeding_posture" if standard_source == "request" else "default",
            "proceeding_posture": proceeding_posture,
            "in_judge_prompt": in_prompt,
        }
        audit.step("run_start", cfg_view,
                   {"judge": self.judge.name, "score_sha256": self.registry.sha256, "client_data": client_data,
                    **_judge_provenance(self.config), **cfg_view, **standard_view}, 0.0)

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

        call_records: list[JudgeCallRecord] = []
        results = [
            self._run_contract(audit, cid, ingested, narrative, call_records_out=call_records,
                               proceeding_posture=proceeding_posture)
            for cid in chosen
        ]
        accounting = _judge_accounting(call_records)
        audit.step("judge_accounting", {"run_id": run_id}, accounting, 0.0)
        audit.step("run_end", {"run_id": run_id}, {c.contract_id: c.outcome for c in results}, 0.0)
        return AgentRun(run_id, self.judge.name, self.registry.sha256,
                        [{"id": f.id, "sha256": f.sha256} for f in ingested], results, audit.path,
                        judge_accounting=accounting, client_data=client_data, standard=standard_out,
                        listed_sources={cid: list(srcs) for cid, srcs in listed.items()})
