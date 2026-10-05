"""T1 typed-layer parity gate, one command (pravrudhi #196). Exit 0 = gate pass, 1 = gate fail, 2 = refusal.

    PRAVRUDHI_T1_PARITY_PROMPTS=<279-prompt jsonl> PRAVRUDHI_T2_RESULTS_DIR=<dir> \
        uv run python scripts/typed_layer_parity.py [--base-url URL] [--prompt-template FILE]

One live completion per prompt is fed to both `HouseJudge` and `TypedHouseJudge`; the gate is 0 decision flips
and max|dp| <= 1e-6. Writes `typed_layer_parity_result.json`. Results on a local endpoint are labelled
"dev stack (local)". `--prompt-template FILE` wraps every prompt (file must contain `{prompt}` once) for the
standard-line variant (#192); the report records the template sha256. See application/typed/parity.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_judges import HouseJudge  # noqa: E402
from pravrudhi.application.typed.parity import (  # noqa: E402
    MAX_TOKENS,
    PROMPTS_SHA256,
    TAU,
    TOP_LOGPROBS,
    ParityError,
    run_parity,
    sha256_text,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8110/v1"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompts", default=os.environ.get("PRAVRUDHI_T1_PARITY_PROMPTS"))
    ap.add_argument("--results-dir", default=os.environ.get("PRAVRUDHI_T2_RESULTS_DIR"))
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--prompt-template", default=None, help="file containing {prompt} exactly once")
    args = ap.parse_args(argv)
    if not args.prompts:
        print("REFUSING: PRAVRUDHI_T1_PARITY_PROMPTS / --prompts is not set (no host-path default)", file=sys.stderr)
        return 2
    if not args.results_dir:
        print("REFUSING: PRAVRUDHI_T2_RESULTS_DIR / --results-dir is not set (no results inside the repo)", file=sys.stderr)
        return 2
    prompts_path = Path(args.prompts)
    digest = hashlib.sha256(prompts_path.read_bytes()).hexdigest()
    if digest != PROMPTS_SHA256:
        print(f"REFUSING: {prompts_path} sha256 {digest} != expected {PROMPTS_SHA256}", file=sys.stderr)
        return 2
    template = Path(args.prompt_template).read_text() if args.prompt_template else None
    rows = [json.loads(line) for line in prompts_path.read_text().splitlines() if line.strip()]

    judge = HouseJudge(
        tau=TAU, statute_chars=600, base_url=args.base_url, max_tokens=MAX_TOKENS, top_logprobs=TOP_LOGPROBS, timeout_s=60
    )
    print(f"{len(rows)} prompts, sha256 confirmed. Backend {args.base_url}, model {judge.model}, concurrency 1.")
    try:
        report = run_parity(rows, judge._complete, template=template)  # noqa: SLF001 -- the raw single-completion call
    except ParityError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2

    host = urlparse(args.base_url).hostname
    try:
        runner_sha = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        runner_sha = None
    report.update(
        label="dev stack (local)" if host in ("127.0.0.1", "localhost", "::1") else "non-local endpoint",
        base_url=args.base_url,
        model=judge.model,
        prompts_sha256=digest,
        prompt_template_sha256=sha256_text(template) if template is not None else None,
        tau=TAU,
        runner_git_sha=runner_sha,
    )
    out = Path(args.results_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / "typed_layer_parity_result.json"
    out_path.write_text(json.dumps(report, indent=2))
    g = report["gate"]
    print(f"T1 parity: {g['status'].upper()}  flips={len(report['flips'])}  max|dp|={report['max_abs_dp']:.3e}  "
          f"compared={report['n_compared']}/{report['n_prompts']}  both_error={report['n_both_error']}")
    for f in g["failures"]:
        print(f"  FAIL: {f}", file=sys.stderr)
    print(f"report: {out_path}")
    return 0 if g["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
