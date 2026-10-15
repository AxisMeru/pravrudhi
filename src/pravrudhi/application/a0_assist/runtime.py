"""The assisted tier's process-wide state, built once from the config and the environment.

`get(root)` returns a `Runtime` (or None when the config cannot be read). While the route is disabled in the config the
runtime holds no store and no transport and starts nothing. When it is enabled the runtime builds the transport and the
store from environment variables (names in the config; values never in config, logs or responses), installs the pause
source into the assistant tier, and starts the engine sweeper thread. Until the sweeper's first pass has read the store,
the tier is paused ("first_check_pending"): an unchecked store never counts as healthy.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pravrudhi.application import assistant_tier
from pravrudhi.application.a0_assist import config as C
from pravrudhi.application.a0_assist import guard
from pravrudhi.application.a0_assist import store as ST
from pravrudhi.application.a0_assist import sweeper as SW
from pravrudhi.application.a0_assist import transport as TR
from pravrudhi.application.a0_assist.limits import AssistLimiter


@dataclass
class Runtime:
    cfg: C.AssistConfig
    pauses: guard.Pauses = field(default_factory=guard.Pauses)
    store: ST.SpendStore | None = None
    transport: TR.AnthropicTransport | None = None
    limiter: AssistLimiter | None = None
    slots: threading.BoundedSemaphore | None = None
    sweeper: SW.Sweeper | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.cfg.route_enabled


_lock = threading.Lock()
_runtime: Runtime | None = None


def _first_env(env: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((env[n].strip() for n in names if env.get(n, "").strip()), "")


def build(root: Path, env: Mapping[str, str] | None = None, *, start_thread: bool = True) -> Runtime | None:
    env = os.environ if env is None else env
    cfg, _why = C.load_or_none(Path(root))
    if cfg is None:
        return None
    rt = Runtime(cfg)
    if not cfg.route_enabled:
        return rt
    rt.pauses.set("startup", "first_check_pending")
    rt.limiter = AssistLimiter(cfg.runs_per_user_per_day, cfg.runs_per_org_per_day, cfg.per_ip_per_minute)
    rt.slots = threading.BoundedSemaphore(cfg.max_concurrent_sessions)
    try:
        rt.transport = TR.AnthropicTransport(
            _first_env(env, cfg.key_env_names),
            env.get(cfg.workspace_id_env, ""),
            timeout_s=cfg.call_timeout_s,
            record=assistant_tier.record_outcome,
        )
    except TR.TransportRefused:
        rt.transport = None
        rt.pauses.set("transport", "transport_not_configured")
    rt.store, _ = ST.from_env(env, cfg.store_dsn_env, timeout_s=cfg.store_timeout_s, ca_file=cfg.store_ca_file)
    assistant_tier.set_pause_source(rt.pauses.reason)
    rt.sweeper = SW.Sweeper(cfg, rt.store, rt.transport, rt.pauses, engine_store=rt.store, mode="engine")
    if start_thread:
        rt.thread = threading.Thread(target=_loop, args=(rt,), name="a0-assist-sweeper", daemon=True)
        rt.thread.start()
    return rt


def _loop(rt: Runtime) -> None:
    assert rt.sweeper is not None
    first = True
    while not rt.stop.is_set():
        try:
            rt.sweeper.run_once()
        except Exception:  # noqa: BLE001 - the loop must survive anything; an unchecked state stays paused
            rt.pauses.set("sweeper", "sweeper_crashed")
        if first:
            rt.pauses.set("startup", None)
            first = False
        rt.stop.wait(rt.cfg.sweep_interval_min * 60)


def get(root: Path, env: Mapping[str, str] | None = None) -> Runtime | None:
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = build(root, env)
        return _runtime


def reset() -> None:
    """Stop and forget the runtime (tests, and a clean restart of the process-wide state)."""
    global _runtime
    with _lock:
        if _runtime is not None:
            _runtime.stop.set()
        _runtime = None
    assistant_tier.set_pause_source(None)


def status_document(rt: Runtime | None, *, signed_in: bool) -> dict[str, Any]:
    """The body of GET /api/assistant. The two pending-delete ages are published to signed-in callers only."""
    from pravrudhi.application.a0_assist.config import AssistConfig

    cfg: AssistConfig | None = rt.cfg if rt is not None else None
    doc: dict[str, Any] = {
        "provider": "Anthropic",
        "call_timeout_s": cfg.call_timeout_s if cfg else None,
        "session_budget_usd": cfg.session_budget_cents / 100 if cfg else None,
        "consent_required": True,
    }
    if signed_in and rt is not None and rt.store is not None:
        try:
            s = rt.store.status()
        except ST.StoreError:
            s = {}
        doc["oldest_pending_delete_age_s"] = s.get("oldest_pending_delete_age_s")
        doc["sweep_last_ok_age_s"] = s.get("sweep_last_ok_age_s")
    return doc
