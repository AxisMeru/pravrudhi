"""Pre-flight checks that need both Anthropic keys (the engine's and the backstop's).

`same_workspace` proves that every key can act inside the ONE workspace the deployment names: each key is used with the
configured workspace id (the header every call must carry) for a read-only GET /v1/models, which answers 200 only for a key
that is valid in that workspace. Anything else is a failure: the check fails closed (a key it could not probe is never
assumed to match). It returns the NAMES of the keys that failed, never a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from pravrudhi.application.a0_assist.transport import TransportRefused, UpstreamError


def same_workspace(transports: Mapping[str, Any]) -> list[str]:
    """Names of the keys that do not get a 200 from GET /v1/models in the configured workspace (empty = all match)."""
    if not transports:
        return ["no_keys"]
    failed: list[str] = []
    for name, tr in transports.items():
        try:
            ok = tr.workspace_ok() is True
        except (UpstreamError, TransportRefused, httpx.HTTPError):
            ok = False
        if not ok:
            failed.append(name)
    return sorted(failed)
