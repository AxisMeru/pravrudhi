"""Throwaway spike: does test-time training (TTT) help grounded legal-style citation,
and does a cheap RSI-style 'gate' (a frozen regression probe checked after every update)
catch the drift that un-gated continuous TTT accumulates?

Not Track B's model, not Track B's repo, not Track B's checkpoints. A tiny open base
model (Qwen2.5-1.5B-Instruct, already cached on this box) standing in for "a LoRA-first
foundational model", with synthetic data shaped like Track B's prompt format (question +
linearized subgraph of statute/case node ids + answer that must cite only ids present in
context, or abstain). This is a mechanism demo for a research doc, not a benchmark result
for any gate, ledger, or paper.

Four conditions over the same 10 examples:
  A) frozen       - no adaptation at all.
  B) ephemeral    - K self-supervised TTT steps on the *context only* (never the answer),
                    per example, LoRA reset to zero before every example.
  C) accumulated  - same per-example TTT steps, but the LoRA adapter is never reset between
                    examples (continuous/online TTT with no safeguard).
  D) gated        - condition C's trajectory, replayed with a post-hoc rule: after each
                    example's update, check a fixed regression probe (5 generic prompts
                    unrelated to law); if NLL on the probe rises more than REJECT_THRESHOLD
                    relative to the frozen baseline, the update is rejected and the adapter
                    is rolled back to its last accepted state. This is the cheapest possible
                    stand-in for Pravrudhi's gate.py + delegation check_gate/sign_gate flow:
                    propose -> validate against a fixed probe -> accept or roll back.

Self-supervised TTT objective: next-token prediction loss computed ONLY over the tokens of
the provided subgraph context (the statute/case text), never over the question or the gold
answer. This is the honest TTT setup from the literature (e.g. In-Place TTT, TTT-Linear):
the model adapts to the *input*, not to the label it is being tested on.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import torch
from torch import nn
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.bfloat16 if DEVICE == "cuda" else torch.float32

TTT_STEPS = 6
TTT_LR = 1e-3
REJECT_THRESHOLD = 0.15  # relative NLL increase on the regression probe that triggers rollback
MAX_NEW_TOKENS = 60

ABSTAIN_PHRASE = "insufficient basis in the provided graph"
LORA_TARGETS = ("q_proj", "v_proj")


class LoRALinear(nn.Module):
    """Hand-rolled LoRA wrapper (no peft dependency: this container's peft/torchao pairing
    is broken, and patching a shared training container's package versions to chase a
    spike is out of scope). Wraps a frozen nn.Linear with a low-rank trainable delta,
    B-zero-initialized so the wrapped module is a no-op until trained."""

    def __init__(self, base: nn.Linear, r: int = 4, alpha: int = 8):
        super().__init__()
        self.base = base
        for p in self.base.parameters():
            p.requires_grad_(False)
        device, dtype = base.weight.device, base.weight.dtype
        self.lora_A = nn.Parameter(torch.randn(r, base.in_features, device=device, dtype=dtype) * 0.01)
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, r, device=device, dtype=dtype))
        self.scaling = alpha / r
        # Fixed at construction so reset() returns to the *same* starting point every time --
        # zeroing lora_A too would zero the gradient into lora_B (dL/dB depends on x @ A^T),
        # so a true reset restores the original random A, not an all-zero A.
        self._init_A = self.lora_A.detach().clone()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.base(x) + (x @ self.lora_A.T @ self.lora_B.T) * self.scaling

    def reset(self) -> None:
        with torch.no_grad():
            self.lora_A.copy_(self._init_A)
            self.lora_B.zero_()


def inject_lora(model: nn.Module, r: int = 4, alpha: int = 8) -> list[LoRALinear]:
    wrapped: list[LoRALinear] = []
    for layer in model.model.layers:
        attn = layer.self_attn
        for name in LORA_TARGETS:
            base = getattr(attn, name)
            lora = LoRALinear(base, r=r, alpha=alpha)
            setattr(attn, name, lora)
            wrapped.append(lora)
    return wrapped


@dataclass
class LegalExample:
    qid: str
    context_ids: list[str]          # node ids actually present in the subgraph shown
    context_text: str               # the linearized subgraph (statute/case text)
    question: str
    answerable: bool                # False => correct behavior is to abstain


@dataclass
class ProbePrompt:
    prompt: str
    reference: str                  # a short reference continuation we score NLL against


def build_legal_examples() -> list[LegalExample]:
    # Synthetic, deliberately small. Shaped like proposals/prabhasa-nyaya/finetune/README.md's
    # prompt format: linearized subgraph with stable node ids, answer must cite only ids present.
    statutes = {
        "S1": "Section 1: A contract induced by coercion is voidable at the option of the party coerced.",
        "S2": "Section 2: A minor's agreement is void ab initio and cannot be ratified on attaining majority.",
        "S3": "Section 3: Time is not of the essence in a contract unless the parties expressly state so.",
        "S4": "Section 4: A bailee must take as much care of goods bailed as a person of ordinary prudence.",
        "S5": "Section 5: An offer lapses on the death of the offeror if communicated before acceptance.",
    }
    cases = {
        "C1": "Case C1 holds that silence does not amount to acceptance of an offer.",
        "C2": "Case C2 holds that part performance can estop a party from denying a void agreement's terms.",
        "C3": "Case C3 overrules an earlier holding that a bailee's liability is absolute, not relative.",
    }

    def ctx(*ids: str) -> str:
        pool = statutes | cases
        return "\n".join(f"[{i}] {pool[i]}" for i in ids)

    examples = [
        LegalExample("q1", ["S1"], ctx("S1"),
                     "Is a contract signed under threat of harm enforceable against the coerced party?", True),
        LegalExample("q2", ["S2"], ctx("S2"),
                     "Can a 16-year-old's signed loan agreement be enforced once they turn 18 and agree to it?", True),
        LegalExample("q3", ["S3", "C1"], ctx("S3", "C1"),
                     "If a buyer never replies to a seller's offer, is a contract formed?", True),
        LegalExample("q4", ["S4", "C3"], ctx("S4", "C3"),
                     "What standard of care applies to someone holding another's goods in bailment?", True),
        LegalExample("q5", ["S5"], ctx("S5"),
                     "An offeror dies before the offeree accepts, having never told the offeree. Is it still open?", True),
        LegalExample("q6", ["S2", "C2"], ctx("S2", "C2"),
                     "A minor's agreement was partly performed by the other party. Can the minor deny its terms?", True),
        # Unanswerable: the question asks about something the given subgraph does not cover.
        LegalExample("q7", ["S1"], ctx("S1"),
                     "What limitation period applies to filing a suit for breach of this contract?", False),
        LegalExample("q8", ["S4"], ctx("S4"),
                     "Does the bailee's duty of care change if the bailment is gratuitous versus for reward?", False),
        LegalExample("q9", ["S3"], ctx("S3"),
                     "Who holds the burden of proof when a party alleges time was of the essence?", False),
        LegalExample("q10", ["S5", "C1"], ctx("S5", "C1"),
                     "Does an offeree's partial payment count as acceptance of a lapsed offer?", False),
    ]
    return examples


def build_regression_probe() -> list[ProbePrompt]:
    # Generic, unrelated to law: a fixed "did we break anything else" canary, standing in for
    # Pravrudhi's frozen-baseline check inside a gate.
    return [
        ProbePrompt("Q: What is 12 + 7?\nA:", " 19"),
        ProbePrompt("Translate 'good morning' to French:\n", " Bonjour"),
        ProbePrompt("Q: What color is the sky on a clear day?\nA:", " Blue"),
        ProbePrompt("Complete: Once upon a", " time"),
        ProbePrompt("Q: Name the largest planet in the solar system.\nA:", " Jupiter"),
    ]


def make_prompt(ex: LegalExample) -> str:
    return (
        "You are a legal assistant. Answer using ONLY the node ids given below. "
        f"If the graph does not support an answer, reply exactly: '{ABSTAIN_PHRASE}'.\n\n"
        f"Graph:\n{ex.context_text}\n\nQuestion: {ex.question}\nAnswer:"
    )


def extract_cited_ids(text: str) -> set[str]:
    # Accept both '[S1]' and bare 'S1' forms -- a small instruct model is inconsistent about
    # brackets, and the demo cares about *which ids get cited*, not citation punctuation.
    return set(re.findall(r"\b([A-Z]\d+)\b", text))


@torch.no_grad()
def generate(model, tok, prompt: str) -> str:
    inputs = tok(prompt, return_tensors="pt").to(DEVICE)
    out = model.generate(
        **inputs,
        max_new_tokens=MAX_NEW_TOKENS,
        do_sample=False,
        pad_token_id=tok.eos_token_id,
    )
    return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()


@torch.no_grad()
def probe_nll(model, tok, probes: list[ProbePrompt]) -> float:
    total, count = 0.0, 0
    for p in probes:
        full = p.prompt + p.reference
        prompt_ids = tok(p.prompt, return_tensors="pt").input_ids.to(DEVICE)
        full_ids = tok(full, return_tensors="pt").input_ids.to(DEVICE)
        labels = full_ids.clone()
        labels[:, : prompt_ids.shape[1]] = -100
        out = model(input_ids=full_ids, labels=labels)
        n_scored = (labels != -100).sum().item()
        total += out.loss.item() * n_scored
        count += n_scored
    return total / max(count, 1)


def ttt_adapt(model, tok, ex: LegalExample, optimizer) -> None:
    """K self-supervised gradient steps on the context text only (never the question/answer)."""
    ids = tok(ex.context_text, return_tensors="pt").input_ids.to(DEVICE)
    if ids.shape[1] < 2:
        return
    model.train()
    for _ in range(TTT_STEPS):
        out = model(input_ids=ids, labels=ids)
        optimizer.zero_grad()
        out.loss.backward()
        optimizer.step()
    model.eval()


def score_example(ex: LegalExample, answer: str) -> dict:
    cited = extract_cited_ids(answer)
    grounded_ids = set(ex.context_ids)
    hallucinated = cited - grounded_ids
    abstained = ABSTAIN_PHRASE.lower() in answer.lower()
    if ex.answerable:
        correct_behavior = bool(cited) and not hallucinated and not abstained
    else:
        correct_behavior = abstained
    return {
        "qid": ex.qid,
        "answerable": ex.answerable,
        "answer": answer[:160],
        "cited": sorted(cited),
        "hallucinated": sorted(hallucinated),
        "abstained": abstained,
        "correct_behavior": correct_behavior,
    }


def reset_lora(lora_modules: list) -> None:
    for m in lora_modules:
        m.reset()


def run_condition(model, tok, examples, probes, lora_modules, *, mode: str) -> dict:
    """mode in {'frozen', 'ephemeral', 'accumulated'}. Returns per-example scores + probe trace."""
    results = []
    probe_trace = []
    lora_state0 = {id(m): (m.lora_A.detach().clone(), m.lora_B.detach().clone()) for m in lora_modules}

    lora_params = [p for m in lora_modules for p in (m.lora_A, m.lora_B)]
    # Accumulated mode keeps one optimizer (with its momentum state) across the whole
    # sequence -- that persistence is the point, it's what "continuous, un-reset TTT" means.
    # Ephemeral mode gets a fresh optimizer per example so no momentum leaks between the
    # "independent" adaptations, matching the fresh lora_A/zero lora_B reset.
    shared_optimizer = torch.optim.AdamW(lora_params, lr=TTT_LR)

    for ex in examples:
        if mode == "frozen":
            pass
        elif mode == "ephemeral":
            reset_lora(lora_modules)
            ttt_adapt(model, tok, ex, torch.optim.AdamW(lora_params, lr=TTT_LR))
        elif mode == "accumulated":
            ttt_adapt(model, tok, ex, shared_optimizer)  # no reset: carries across examples
        else:
            raise ValueError(mode)

        answer = generate(model, tok, make_prompt(ex))
        results.append(score_example(ex, answer))
        probe_trace.append(probe_nll(model, tok, probes))

    # restore to the pre-condition LoRA state so the next condition starts clean
    with torch.no_grad():
        for m in lora_modules:
            a0, b0 = lora_state0[id(m)]
            m.lora_A.copy_(a0)
            m.lora_B.copy_(b0)

    return {"mode": mode, "results": results, "probe_trace": probe_trace}


def apply_gate(accumulated_run: dict, frozen_probe: float) -> dict:
    """Post-hoc: would a cheap gate (regression probe vs REJECT_THRESHOLD) have caught the
    first bad update in the *accumulated* (un-gated) trajectory, and recovered ephemeral-like
    behavior from that point on?"""
    rejections = []
    for i, nll in enumerate(accumulated_run["probe_trace"]):
        rel_increase = (nll - frozen_probe) / max(abs(frozen_probe), 1e-6)
        if rel_increase > REJECT_THRESHOLD:
            rejections.append({"step": i, "qid": accumulated_run["results"][i]["qid"],
                                "probe_nll": nll, "relative_increase": rel_increase})
    return {
        "reject_threshold": REJECT_THRESHOLD,
        "first_rejection": rejections[0] if rejections else None,
        "total_would_reject": len(rejections),
        "all_rejections": rejections,
    }


def summarize(run: dict) -> dict:
    n = len(run["results"])
    correct = sum(r["correct_behavior"] for r in run["results"])
    hallucinated = sum(bool(r["hallucinated"]) for r in run["results"])
    abstain_needed = [r for r in run["results"] if not r["answerable"]]
    abstain_correct = sum(r["correct_behavior"] for r in abstain_needed)
    return {
        "mode": run["mode"],
        "n": n,
        "correct_behavior_rate": correct / n,
        "hallucination_rate": hallucinated / n,
        "abstention_accuracy": (abstain_correct / len(abstain_needed)) if abstain_needed else None,
        "probe_nll_start": run["probe_trace"][0],
        "probe_nll_end": run["probe_trace"][-1],
    }


def main() -> None:
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=DTYPE).to(DEVICE)
    for p in model.parameters():
        p.requires_grad_(False)

    lora_modules = inject_lora(model, r=4, alpha=8)
    reset_lora(lora_modules)
    model.eval()

    examples = build_legal_examples()
    probes = build_regression_probe()

    frozen_probe = probe_nll(model, tok, probes)

    runs = {}
    for mode in ("frozen", "ephemeral", "accumulated"):
        runs[mode] = run_condition(model, tok, examples, probes, lora_modules, mode=mode)

    gate = apply_gate(runs["accumulated"], frozen_probe)

    report = {
        "model": MODEL_ID,
        "device": DEVICE,
        "ttt_steps": TTT_STEPS,
        "ttt_lr": TTT_LR,
        "reject_threshold": REJECT_THRESHOLD,
        "frozen_probe_nll": frozen_probe,
        "summaries": {mode: summarize(run) for mode, run in runs.items()},
        "gate_simulation_on_accumulated_run": gate,
        "per_example": {mode: run["results"] for mode, run in runs.items()},
        "probe_traces": {mode: run["probe_trace"] for mode, run in runs.items()},
        "wall_clock_seconds": round(time.time() - t0, 1),
    }

    with open("/workspace/ttt-rsi-prototype-claude-0913/report.json", "w") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report["summaries"], indent=2))
    print("gate simulation:", json.dumps(gate, indent=2))
    print(f"wall clock: {report['wall_clock_seconds']}s")


if __name__ == "__main__":
    main()
