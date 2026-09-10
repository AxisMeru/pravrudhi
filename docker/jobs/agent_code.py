"""Harness-track job: a fixed model solves code problems under a MUTABLE harness (system prompt, answer template,
retry-with-visible-tests policy, best-of-n). Reads /in/items.jsonl (id, question) and /in/harness.json; writes
/out/samples.jsonl (id, solution, attempts) and /out/job_meta.json. Hidden tests are never available here."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import re
import sys
import time
from pathlib import Path

from checks import visible_tests as checks_visible_tests
from common import load_model, model_dir_hash, read_jsonl, sha256_file, write_jsonl

CODE_RE = re.compile(r"```(?:python)?\n(.*?)```", re.S)
#: An opening fence with no closing one. `max_new_tokens` truncates long APPS solutions mid-code, so the
#: closing fence never arrives, `CODE_RE` matches nothing, and the fallback returned the raw text WITH the
#: opening fence on it -- which is a guaranteed `SyntaxError: invalid syntax` at line 1 pointing at
#: "```python". Every such item scored zero for a reason that was ours, and read as a model that cannot code.
OPEN_FENCE_RE = re.compile(r"```(?:python)?\n(.*)\Z", re.S)


def extract_code(text: str) -> str:
    """The last fenced block, or an unterminated one, or the text as given.

    A truncated solution is usually wrong anyway. The point is that it fails as the incomplete code it is,
    where the failure names the missing line, instead of as a syntax error in a fence we left on.
    """
    m = CODE_RE.findall(text)
    if m:
        return m[-1].strip()
    unterminated = OPEN_FENCE_RE.search(text)
    return (unterminated.group(1) if unterminated else text).strip()


def visible_tests(question: str) -> list[str]:
    """The checks a harness may select on: the explicit `assert ` lines an MBPP+ prompt carries, and the checks
    derived from the `>>>` doctest examples a HumanEval prompt carries. Reading only `assert ` lines meant every
    selection strategy -- retry, best-of-n, feedback -- was a silent no-op on HumanEval, where the examples are
    doctests; see docker/jobs/checks.py."""
    return checks_visible_tests(question)


def _run(code: str, tests: list[str], q: mp.Queue) -> None:  # type: ignore[type-arg]
    try:
        ns: dict[str, object] = {}
        exec(code, ns)  # noqa: S102 - sandboxed container, no network, disposable
        failures = []
        for t in tests:
            try:
                exec(t, ns)  # noqa: S102
            except Exception as e:  # noqa: BLE001
                failures.append(f"{t} -> {type(e).__name__}: {e}")
        q.put(failures)
    except Exception as e:  # noqa: BLE001
        q.put([f"definition error: {type(e).__name__}: {e}"])


def run_visible(code: str, tests: list[str], timeout: float = 5.0) -> list[str]:
    q: mp.Queue = mp.Queue()  # type: ignore[type-arg]
    p = mp.Process(target=_run, args=(code, tests, q))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.kill()
        return ["timeout"]
    return q.get() if not q.empty() else ["no result"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--items", default="/in/items.jsonl")
    ap.add_argument("--harness", default="/in/harness.json")
    ap.add_argument("--out", default="/out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=16)
    a = ap.parse_args()
    import torch

    torch.manual_seed(a.seed)
    torch.cuda.manual_seed_all(a.seed)
    h = json.loads(Path(a.harness).read_text())
    t0 = time.monotonic()
    model, tok = load_model(Path(a.model_dir), None)
    model.eval()
    items = read_jsonl(Path(a.items))
    pad = tok.pad_token_id or tok.eos_token_id
    temperature, max_new, retries, n_samples = (
        float(h["temperature"]),
        int(h["max_new_tokens"]),
        int(h["retries"]),
        int(h["n_samples"]),
    )

    def gen(prompts: list[str]) -> list[str]:
        outs: list[str] = []
        for b in range(0, len(prompts), a.batch_size):
            enc = tok(prompts[b : b + a.batch_size], return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                g = model.generate(
                    **enc,
                    max_new_tokens=max_new,
                    do_sample=temperature > 0,
                    temperature=max(temperature, 1e-5),
                    top_p=0.95,
                    pad_token_id=pad,
                )
            outs += [tok.decode(r, skip_special_tokens=True) for r in g[:, enc["input_ids"].shape[1] :]]
        return outs

    def build(question: str, feedback: str | None) -> str:
        user = h["template"].replace("{question}", question)
        if feedback:
            user += "\n\n" + h["feedback_template"].replace("{feedback}", feedback)
        msgs = [{"role": "system", "content": h["system_prompt"]}, {"role": "user", "content": user}]
        return str(
            tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True, enable_thinking=bool(h.get("thinking", False))
            )
        )

    n_tok_calls = 0
    results: dict[str, dict[str, object]] = {}
    pending = {it["id"]: {"q": it["question"], "feedback": None, "attempt": 0} for it in items}
    while pending:
        ids = list(pending)
        prompts = [build(pending[i]["q"], pending[i]["feedback"]) for i in ids for _ in range(n_samples)]  # type: ignore[arg-type]
        outs = gen(prompts)
        n_tok_calls += len(prompts)
        nxt = {}
        for k, i in enumerate(ids):
            cands = [extract_code(o) for o in outs[k * n_samples : (k + 1) * n_samples]]
            tests = visible_tests(str(pending[i]["q"])) if h.get("use_visible_tests", True) else []
            best, best_fail = cands[0], None
            for c in cands:
                fails = run_visible(c, tests) if tests else []
                if not fails:
                    best, best_fail = c, []
                    break
                if best_fail is None or len(fails) < len(best_fail):
                    best, best_fail = c, fails
            attempt = int(pending[i]["attempt"]) + 1  # type: ignore[arg-type]
            if best_fail and attempt <= retries:
                nxt[i] = {"q": pending[i]["q"], "feedback": "\n".join(best_fail)[:1200], "attempt": attempt}
            else:
                results[i] = {"id": i, "solution": best, "attempts": attempt, "visible_failures": best_fail or []}
        pending = nxt
        print(f"solved-or-final {len(results)}/{len(items)}; retrying {len(pending)}", file=sys.stderr)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "samples.jsonl", [results[it["id"]] for it in items])
    meta = {
        "job": "agent_code",
        "model_sha256": model_dir_hash(Path(a.model_dir)),
        "items_sha256": sha256_file(Path(a.items)),
        "harness_sha256": sha256_file(Path(a.harness)),
        "n_items": len(items),
        "model_calls": n_tok_calls,
        "wall_s": time.monotonic() - t0,
        "peak_gib_torch": torch.cuda.max_memory_allocated() / 2**30,
        "seed": a.seed,
        "harness": h,
    }
    (out / "job_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: meta[k] for k in ("n_items", "model_calls", "wall_s")}))
    return 0


if __name__ == "__main__":
    main()
