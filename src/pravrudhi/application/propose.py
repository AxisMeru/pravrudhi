"""icchā: the proposer emits K recipes in the grammar; predictions are sealed and hash-committed (§1 of the mutation spec)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from pravrudhi.models.openai_compat import ChatClient
from pravrudhi.targets import LoraRecipe, parse_recipe
from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.ledger.verify import iter_events
from pravrudhi_kernel.stats import wilson_ci

GRAMMAR_DOC = """{
  "strategy": "sft_rejection" | "grpo_verifiable",
  "execution_family": "data_mixture" | "optimiser" | "adapter" | "grpo" | "template",
  "lora": {"r": 1..64, "alpha": 1..256, "dropout": 0..0.3, "target_modules": "all-linear"|"attention"|"mlp"},
  "sft":  {"n_kept": 32..4096, "teacher": "incumbent" | "Qwen/Qwen3-4B" (a stronger local model samples the data),
           "init": "base" | "incumbent" (continue training the incumbent adapter instead of starting from the base),
           "filter": "all_correct"|"shortest_correct"|"longest_correct"|"diverse_correct",
           "epochs": 1..3, "lr": 1e-6..5e-3, "warmup_ratio": 0..0.2, "max_seq_len": 256..2048, "batch_size": 1..32},
  "grpo": {"steps": 5..60, "group_size": 2..4, "prompts_per_step": 1..2, "max_completion_tokens": 64..192,
           "lr": 1e-7..1e-4, "beta_kl": 0..0.1},
  "eval_template": "gsm8k_v1" | "gsm8k_v2_terse" | "gsm8k_v3_boxed",
  "rationale": "<= 400 chars"
}
Constraints: execution_family "grpo" requires strategy "grpo_verifiable"; "data_mixture" requires "sft_rejection".
Omitted sub-objects take defaults (lora r=8 alpha=16; sft n_kept=512 lr=1e-4 epochs=1 init=base;
grpo steps=20 group_size=4 prompts_per_step=1 max_completion_tokens=128 beta_kl=0)."""


def _fill(template: str, **fields: str) -> str:
    """Placeholder substitution without str.format, so literal JSON braces in prompts survive."""
    for k, v in fields.items():
        template = template.replace("{" + k + "}", v)
    return template


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start = text.find("[")
    if start < 0:
        raise ValueError("no JSON array in proposer output")
    end = text.rfind("]")
    if end > start:
        try:
            arr = json.loads(text[start : end + 1])
            if not isinstance(arr, list):
                raise ValueError("proposer output is not a list")
            return [x for x in arr if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    # Truncated or malformed array: salvage the complete top-level objects in order (the audit row records it).
    text = text.replace("\\'", "'")  # the one invalid escape local models emit routinely
    dec, pos, out = json.JSONDecoder(), start + 1, []
    while True:
        nxt = text.find("{", pos)
        if nxt < 0:
            break
        try:
            obj, pos = dec.raw_decode(text, nxt)
        except json.JSONDecodeError:
            pos = nxt + 1  # skip this brace (a template placeholder or a malformed object) and keep looking
            continue
        if isinstance(obj, dict):
            out.append(obj)
    if not out:
        raise ValueError("no JSON array in proposer output")
    return out


def ledger_summary(ledger: Path, incumbent_id: str) -> tuple[str, str | None, int]:
    """Human-readable evidence table for the proposer; also returns the incumbent's strategy and the count of
    consecutive execution-level selections in one strategy without a confirm (ADR-0005)."""
    cands: dict[str, dict[str, Any]] = {}
    consecutive, last_strategy = 0, None
    inc_strategy: str | None = None
    for ev in iter_events(ledger):
        p, cid = ev.payload, ev.candidate_id
        if ev.kind == "propose" and cid:
            cands[cid] = {
                "strategy": p.get("strategy"),
                "family": p.get("edit_family"),
                "recipe": p.get("recipe"),
                "xs": [],
                "status": "proposed",
                "n_items": 0,
            }
        elif (
            ev.kind == "observe" and cid in cands and p.get("arm", "candidate") == "candidate" and p.get("study") != "noise_floor"
        ):
            cands[cid]["xs"].append(float(p["observed"]["delta_in"]))
            cands[cid]["n_items"] += int(p["observed"].get("n_items", 0))
            b = (p.get("stats") or {}).get("boundary")
            if b:
                cands[cid]["status"] = b
            if b == "confirm":
                consecutive = 0
        elif ev.kind == "select" and cid in cands:
            s = cands[cid]["strategy"]
            consecutive = consecutive + 1 if s == last_strategy else 1
            last_strategy = s
        elif ev.kind == "promote" and cid in cands:
            cands[cid]["status"] = "promoted"
            inc_strategy = cands[cid]["strategy"]
        elif ev.kind == "prune" and cid in cands:
            cands[cid]["status"] = f"pruned:{p.get('hetvabhasa')}"
    lines = [
        "| candidate | strategy | family | recipe (key params) | n_runs | mean delta_in | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for cid, c in cands.items():
        if cid == incumbent_id:
            continue
        r = c["recipe"] or {}
        brief = json.dumps({k: r.get(k) for k in ("lora", "sft", "grpo", "eval_template") if r.get(k)})[:160]
        mean = sum(c["xs"]) / len(c["xs"]) if c["xs"] else None
        lines.append(
            f"| {cid} | {c['strategy']} | {c['family']} | {brief} | {len(c['xs'])} | "
            f"{mean if mean is None else round(mean, 4)} | {c['status']} |"
        )
    if len(lines) == 2:
        lines.append("| (none yet) | | | | | | |")
    return "\n".join(lines), inc_strategy, consecutive


def next_candidate_id(ledger: Path) -> str:
    n = sum(1 for ev in iter_events(ledger) if ev.kind == "propose")
    return f"c-{n:04d}"


def propose_generic(
    root: Path,
    w: LedgerWriter,
    client: ChatClient,
    *,
    night: int,
    k: int,
    model: str,
    bucket: dict[str, str],
    prompts_dir: Path,
    sealed_dir: Path,
    incumbent_id: str,
    sigma_seed: float,
    temperature: float,
    max_tokens: int,
    rethink_m: int,
    log: Any = print,
    grammar_doc: str = GRAMMAR_DOC,
    parse_fn: Any = parse_recipe,
    prompt_file: str = "proposer/v1.md",
    surface: str = "W3.adapter",
    op: str = "adapter",
    json_schema: dict[str, Any] | None = None,
    extra_context: str = "",
) -> list[tuple[str, Any]]:
    summary, inc_strategy, consecutive = ledger_summary(root / "research" / "ledger.jsonl", incumbent_id)
    inc_strategy = inc_strategy or "none"
    rethink = consecutive >= rethink_m
    if rethink:
        w.append(
            "audit",
            "controller",
            {
                "kind": "rethink_checkpoint",
                "severity": "info",
                "consecutive": consecutive,
                "strategy": inc_strategy,
                "m": rethink_m,
            },
            epoch=0,
            night=night,
        )
    rethink_note = (
        (
            "REThINK CHECKPOINT: the last runs iterated inside one strategy without a confirmed gain; at least half of "
            "your candidates must change strategy or family."
        )
        if rethink
        else ""
    )
    prompt = _fill(
        (prompts_dir / prompt_file).read_text(),
        model=model,
        grammar=grammar_doc,
        state_summary=summary
        + f"\n\nMeasured noise floor: sigma_seed={sigma_seed:.4f} "
        + f"(pass-rate units, 100 items). Incumbent: {incumbent_id} (strategy {inc_strategy})."
        + (f"\n\n{extra_context}" if extra_context else ""),
        k=str(k),
        incumbent_strategy=inc_strategy,
        rethink_note=rethink_note,
    )
    res = client.chat(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        seed=night,
        json_schema=json_schema,
    )
    w.append(
        "audit",
        "proposer",
        {
            "kind": "proposer_call",
            "severity": "info",
            "prompt_version": "v1",
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "wall_s": res.wall_s,
            "model": res.model,
            "finish_reason": res.finish_reason,
        },
        epoch=0,
        night=night,
    )
    if res.finish_reason == "length":
        # Truncation is not a bad candidate, and recording it as one cost days. The proposer prompt is built from
        # `ledger_summary`, so it grows with the history; the context was sized by a formula that assumed a short
        # one. On night 17 a 16268-token prompt in a 16384-token context left 116 tokens to answer in, the object
        # was cut mid-field, and the only visible signal was "strategy: Field required" — which reads as a model
        # producing nonsense rather than a model never given room to finish.
        w.append(
            "audit",
            "proposer",
            {
                "kind": "proposer_truncated",
                "severity": "high",
                "detail": "the proposer hit the completion limit; the context leaves too little room to answer",
                "prompt_tokens": res.prompt_tokens,
                "completion_tokens": res.completion_tokens,
                "max_tokens": max_tokens,
            },
            epoch=0,
            night=night,
        )
        log(
            f"proposer: TRUNCATED — prompt {res.prompt_tokens} tokens left only {res.completion_tokens} for the "
            f"answer (max_tokens={max_tokens}); widen the proposer context"
        )
    try:
        raw = _extract_json_array(res.text)
    except ValueError as e:
        w.append(
            "audit",
            "proposer",
            {"kind": "bad_candidate", "severity": "medium", "detail": str(e), "text_tail": res.text[-500:], "text": res.text},
            epoch=0,
            night=night,
        )
        return []
    accepted: list[tuple[str, LoraRecipe]] = []
    seen: set[str] = set()
    for obj in raw[: 2 * k]:
        rec = parse_fn(obj)
        if isinstance(rec, str):
            w.append(
                "audit",
                "proposer",
                {"kind": "bad_candidate", "severity": "low", "detail": rec, "candidate": obj},
                epoch=0,
                night=night,
            )
            continue
        key = json.dumps(rec.model_dump(exclude={"rationale"}), sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        cid = next_candidate_id(root / "research" / "ledger.jsonl")
        diff_ref = hashlib.sha256(key.encode()).hexdigest()
        w.append(
            "propose",
            "proposer",
            {
                "op": op,
                "recipe": rec.model_dump(),
                "strategy": rec.strategy,
                "edit_family": rec.execution_family,
                "vak": {"para": rec.rationale[:400], "pasyanti": key[:600]},
                "diff": {"sha256": diff_ref},
                "cost_estimate": {"gpu_h": rec.cost_est_gpu_h()},
                "lineage": [incumbent_id],
            },
            epoch=0,
            night=night,
            cycle=len(accepted) + 1,
            candidate_id=cid,
            surface=surface,
            bucket=bucket,
            provenance="agama",
        )
        accepted.append((cid, rec))
        if len(accepted) >= k:
            break
    strategies = {r.strategy for _, r in accepted}
    if rethink and (len(strategies) < 2):
        w.append(
            "audit",
            "controller",
            {
                "kind": "rethink_declined",
                "severity": "medium",
                "reason": "proposer returned a single strategy after a rethink checkpoint",
            },
            epoch=0,
            night=night,
        )
    log(f"proposer: {len(raw)} raw, {len(accepted)} accepted, strategies {sorted(strategies)}")
    if accepted:
        _predict(
            w,
            client,
            accepted,
            prompts_dir,
            sealed_dir,
            night,
            sigma_seed,
            bucket,
            temperature,
            max_tokens,
            log,
            surface=surface,
        )
    return accepted


def _predict(  # noqa: PLR0913
    w: LedgerWriter,
    client: ChatClient,
    accepted: list[tuple[str, LoraRecipe]],
    prompts_dir: Path,
    sealed_dir: Path,
    night: int,
    sigma_seed: float,
    bucket: dict[str, str],
    temperature: float,
    max_tokens: int,
    log: Any,
    surface: str = "W3.adapter",
) -> None:
    listing = "\n".join(f"{i}: {json.dumps(r.model_dump())}" for i, (_, r) in enumerate(accepted))
    prompt = _fill((prompts_dir / "predictor" / "v1.md").read_text(), sigma_seed=f"{sigma_seed:.4f}", candidates=listing)
    res = client.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=max_tokens, seed=night + 1000)
    try:
        preds = {int(p["candidate_index"]): p for p in _extract_json_array(res.text)}
    except (ValueError, KeyError, TypeError) as e:
        w.append(
            "audit",
            "proposer",
            {"kind": "bad_prediction", "severity": "low", "detail": str(e)},
            epoch=0,
            night=night,
        )
        preds = {}
    sealed_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(sealed_dir, 0o700)
    sealed = sealed_dir / f"night_{night}.jsonl"
    with sealed.open("a") as fh:
        for i, (cid, _) in enumerate(accepted):
            p = preds.get(i)
            if p is None:
                continue
            try:
                d, c = max(-0.3, min(0.3, float(p["delta_in"]))), max(0.01, min(0.99, float(p["conf"])))
            except (KeyError, TypeError, ValueError):
                continue
            salt = secrets.token_hex(16)
            h = hashlib.sha256(f"{cid}|{d}|{c}|{salt}".encode()).hexdigest()
            fh.write(json.dumps({"candidate_id": cid, "delta_in": d, "conf": c, "salt": salt, "hash": h, "night": night}) + "\n")
            w.append(
                "predict",
                "proposer",
                {"predictor": "v1", "hash": h},
                epoch=0,
                night=night,
                candidate_id=cid,
                surface=surface,
                bucket=bucket,
                provenance="agama",
            )
    os.chmod(sealed, 0o600)
    log(f"predictor: {len(preds)} predictions sealed")


def strategy_switch_rate(ledger: Path) -> tuple[int, int, tuple[float, float]]:
    """Selections whose strategy differs from the previous selection's, over all selections (ADR-0005 metric)."""
    meta: dict[str, str | None] = {}
    switches = n = 0
    last: str | None = None
    for ev in iter_events(ledger):
        if ev.kind == "propose" and ev.candidate_id:
            meta[ev.candidate_id] = ev.payload.get("strategy")
        elif ev.kind == "select" and ev.candidate_id in meta:
            s = meta[ev.candidate_id]
            if last is not None:
                n += 1
                switches += int(s != last)
            last = s
    return switches, n, wilson_ci(switches, n) if n else (0.0, 0.0)


def propose(*args: Any, **kwargs: Any) -> list[tuple[str, LoraRecipe]]:
    """LoRA-track proposer (the original entry point)."""
    return propose_generic(*args, **kwargs)
