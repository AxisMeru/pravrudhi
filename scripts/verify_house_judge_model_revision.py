"""Fail-closed HF revision check for a house-judge model (Lead-2, 2026-09-27, the 4B MODEL_REVISION gap:
`vwbrfgyiel1haq` served whatever HF's `main` currently resolved to, at every cold start, because no
MODEL_REVISION was set at all -- now pinned to 8ead9b1d95d843ae0d0883f97995a110274de1a8).

Mirrors the second-judge 32B's own boot-time verify step exactly (`entrypoint-hf-fetch.sh`, built ad hoc
for the custom `pravrudhi-second-judge-32b` image -- an ops-only artifact, not committed to any repo):
`HfApi().model_info(repo, revision=pin).sha.startswith(pin)`, fail closed (non-zero exit, a `FATAL:`
stderr message) on any mismatch or unresolvable revision. Never `==` -- the pin is often the short form of
a full commit sha, same convention the 32B wrapper uses for both its base model and its adapter.

Standalone, not wired into a container entrypoint: the 4B house judge runs on RunPod's own stock vLLM
worker image (`runpod-workers-worker-vllm-main-dockerfile`), unlike the 32B's custom image -- there is no
entrypoint of ours to drop this into today. Swapping to a custom image so this can run at boot, the way
the 32B's does, is a bigger, separate infrastructure decision (Lead-2-assistant owns routing that to
Lead-2), not made here. Until then, this is a manual/CI verification tool: run it against a deployment's
own MODEL_NAME/MODEL_REVISION (the exact env var names the 4B endpoint already uses) to confirm the pin
still resolves to what it should, without needing to change this script's own logic once the wiring
decision lands.

Single merged repo, no LoRA adapter -- unlike the 32B's base+adapter shape, the house judge is one
repo/revision pair.

Usage: verify_house_judge_model_revision.py [--repo REPO] [--revision REVISION]
Defaults to the MODEL_NAME / MODEL_REVISION environment variables when the flags are omitted.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from typing import Protocol, cast


class _ResolvedModelInfo(Protocol):
    sha: str | None


#: `(repo, revision) -> resolved model info` -- injectable so tests never hit the network; production
#: leaves this at its default (lazy `huggingface_hub.HfApi().model_info`, see `_default_model_info_fn`).
ModelInfoFn = Callable[[str, str], _ResolvedModelInfo]


def _default_model_info_fn(repo: str, revision: str) -> _ResolvedModelInfo:
    # Lazy: huggingface_hub is not a core pravrudhi dependency (mirrors `nyaya_judges.Gate1NLIModel`'s own
    # lazy import) -- importing it eagerly would make every caller of this module pay for an optional,
    # ops-tool-only dependency.
    from huggingface_hub import HfApi

    return cast(_ResolvedModelInfo, HfApi().model_info(repo, revision=revision))


class RevisionMismatch(RuntimeError):
    """The repo's resolved commit sha does not start with the pinned revision -- fail closed, never a
    silent pass."""


def verify_revision(repo: str, revision: str, *, model_info_fn: ModelInfoFn = _default_model_info_fn) -> str:
    """Returns the resolved sha on success. Raises `RevisionMismatch` on a mismatch or an empty/missing
    `sha` -- this function never returns a falsy value to signal failure, so a caller cannot accidentally
    treat a failure as success by forgetting to check a return value."""
    info = model_info_fn(repo, revision)
    sha = info.sha
    if not sha or not sha.startswith(revision):
        raise RevisionMismatch(f"{repo} resolved sha {sha!r} does not match pinned revision {revision!r}")
    return sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("MODEL_NAME"), help="HF repo id (default: $MODEL_NAME)")
    parser.add_argument(
        "--revision", default=os.environ.get("MODEL_REVISION"), help="pinned revision (default: $MODEL_REVISION)"
    )
    args = parser.parse_args(argv)

    if not args.repo:
        print("FATAL: no --repo given and MODEL_NAME is unset -- nothing to verify", file=sys.stderr)
        return 2
    if not args.revision:
        print(
            f"FATAL: no --revision given and MODEL_REVISION is unset for {args.repo!r} -- refusing to "
            "verify an unpinned repo (this is exactly the gap that left the 4B judge unpinned before "
            "2026-09-27)",
            file=sys.stderr,
        )
        return 2

    try:
        # Looked up as a module global at call time (not `verify_revision`'s own bound-at-definition
        # default) so tests can monkeypatch this module's `_default_model_info_fn` and have `main` actually
        # see the replacement -- a default *parameter* value would stay bound to the original function.
        sha = verify_revision(args.repo, args.revision, model_info_fn=_default_model_info_fn)
    except RevisionMismatch as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 -- ANY resolution failure (network, unknown repo/revision, auth) fails
        # closed too -- an exception escaping model_info_fn is not evidence the pin is fine, only that this
        # script couldn't check it, which must never be silently treated as a pass.
        print(
            f"FATAL: could not resolve {args.repo!r} at revision {args.revision!r}: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {args.repo} resolved sha {sha} matches pinned revision {args.revision}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
