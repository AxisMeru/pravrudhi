"""Report installation readiness so callers can diagnose missing setup without starting a run."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pravrudhi.agents.registry import build_agent as _build_agent
from pravrudhi.application import routing
from pravrudhi_kernel.ledger.verify import verify
from pravrudhi_kernel.sandbox.runner import docker_available

PREREG_FILES = ("lora_night.yaml", "harness_night.yaml", "controller.yaml", "canaries.md")

ROUTED_TIERS = ("mechanical", "standard", "design", "critical")

# The CLI each agent adapter shells out to, so a missing one can be looked for rather than merely reported absent.
_AGENT_CLI = {"claude-code": "claude", "codex": "codex", "opencode": "opencode", "orca": "orca-ide"}

# Where a CLI commonly lives when it is installed but absent from a service's PATH. nvm is first because it is the
# case that actually bit: npm globals land in a node-version-specific directory that only an interactive shell
# that sourced nvm ever puts on PATH, and `systemd --user` never does.
_SEARCH_ROOTS = (
    Path.home() / ".nvm" / "versions" / "node",
    Path.home() / ".local" / "bin",
    Path.home() / ".bun" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def _cli_for(agent: str) -> str | None:
    for prefix, binary in _AGENT_CLI.items():
        if agent == prefix or agent.startswith(prefix + ":"):
            return binary
    return None


def _found_off_path(agent: str) -> str | None:
    """The CLI's real location when it exists but this process cannot see it.

    "opencode CLI not installed" sent a reader to reinstall a CLI that was installed and working in their own
    shell; the only thing wrong was which PATH the process inherited. Naming the file turns that into an obvious
    diagnosis. Returns None when the CLI is genuinely absent, which is a different and honest message.
    """
    binary = _cli_for(agent)
    if not binary or shutil.which(binary):
        return None
    for root in _SEARCH_ROOTS:
        try:
            if not root.is_dir():
                continue
            candidates = [root / binary, *(child / "bin" / binary for child in sorted(root.iterdir()) if child.is_dir())]
        except OSError:
            continue
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return None


def _build_validate_check(root: Path) -> dict[str, Any]:
    """The command a build-mode criterion in this root will actually validate with: its own declaration
    (r-9c8646fc, `.pravrudhi/config.yaml`'s `build.validate`) when it has one, the engine's own `BUILD_VALIDATE`
    otherwise. Always ok - there is nothing to fail here, only something worth a reader knowing before a
    heartbeat runs it unattended, the same reason `gpu` reports rather than fails."""
    from pravrudhi.application.build_config import load_build_config, resolved_build_validate

    command = resolved_build_validate(root)
    declared = load_build_config(root).validate is not None
    detail = (
        f"Build-mode criteria validate with this root's own command: {command}"
        if declared else
        f"Build-mode criteria validate with the engine's own command: {command}"
    )
    return {"name": "build_validate", "ok": True, "detail": detail}


def _telegram_check(root: Path) -> dict[str, Any]:
    """Whether this workspace's bot can speak to anyone, or only answer.

    The product bot polls every two minutes, exits `{"answered": 0}`, and is reported as "not active at all".
    Both are true at once: its timer runs and its `TELEGRAM_CHAT_ID` is blank DELIBERATELY, because Telegram
    will not reveal a chat id until someone opens the conversation, so the poller pairs with the first chat
    that messages it and writes the id down. Until that happens the bot cannot initiate anything, which from
    the outside is indistinguishable from a bot that is dead -- and six other checks stayed green throughout.

    A ten-second human action, so the detail names it rather than merely reporting a state. Not paired is a
    warning about the install, not a fault in the engine.

    A bot this workspace stored itself through `messaging.set_telegram` (what `pravrudhi messaging set` and
    `/api/messaging/telegram` both call) is paired from the moment it exists: `set_telegram` refuses a token
    with no chat id, so a stored bot always has somewhere to deliver. It never touches telegram_inbox's pairing
    file, which is why `telegram_status` is consulted here rather than only `paired_chat`.
    """
    from pravrudhi.application.messaging import telegram_status
    from pravrudhi.application.telegram_inbox import paired_chat

    chat = (
        telegram_status(root, engine_root=root).chat_id
        or paired_chat(root)
        or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    )
    if chat:
        return {"name": "telegram", "ok": True, "detail": "Paired: the bot can start a conversation, not only answer."}
    # A workspace with no bot token has no bot, and failing it would make this the check people learn to
    # ignore -- the reasoning `_routing_check` already applies to a machine with no agent installed. A
    # CONFIGURED but unpaired bot is the fault worth reporting, because that is the one that looks alive.
    configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()) or any(
        (Path.home() / ".config" / "pravrudhi" / name).is_file()
        for name in ("telegram.env", "telegram-product.env")
    )
    if not configured:
        return {"name": "telegram", "ok": True, "detail": "No bot token configured here, so there is no bot to pair."}
    return {
        "name": "telegram",
        "ok": False,
        "detail": (
            "A bot token is configured but NOT PAIRED, so the bot can only answer and can never message you "
            "first. Send the bot any message once in Telegram and the next poll pairs it and writes the chat "
            "id down. Telegram does not reveal a chat id until someone opens the conversation."
        ),
    }


def _routing_check(root: Path) -> dict[str, Any]:
    """Whether the route the router would actually pick at each tier has an agent that can run.

    A route's configuration says which tiers it serves; it says nothing about whether its CLI is on PATH. On
    2026-09-08 `systemd --user` handed the heartbeat a PATH without nvm's bin directory, so `opencode` and `codex`
    were invisible while `claude` was not. The router went on choosing the Lite Plan seat, every dispatch was
    rejected with "no agent available", and the unit reported success each hour because an instant rejection is
    still a completed run. Six checks were green throughout. This is the seventh, and it asks the only question
    that distinguishes a loop that is working from one that is merely running.

    Failing to build an agent is the signal, not an error: `build_agent` returns None precisely when the agent
    cannot run here, which is the condition being reported.

    A machine where NO routed agent runs is not the fault being looked for. That is an install nobody has
    provisioned yet, and failing every fresh workspace would make this check the thing people learn to ignore.
    What is reported as broken is the asymmetry that actually happened: some routes run, and the one the router
    picked does not.
    """
    table = routing.load_table()
    rows = routing.outcomes(root)

    def runnable(route: routing.Route) -> bool:
        try:
            return _build_agent(root, route.agent, route.model) is not None
        except Exception:  # noqa: BLE001 - a broken adapter must read as unavailable, not crash doctor
            return False

    broken: list[str] = []
    for tier in ROUTED_TIERS:
        try:
            choice = routing.choose(table, rows, tier, root)
        except routing.RoutingError as error:
            broken.append(f"{tier}: {error}")
            continue
        if not runnable(choice.route):
            note = f"{tier}: would route to {choice.route.id} ({choice.route.agent}), which cannot run here"
            found = _found_off_path(choice.route.agent)
            if found:
                note += f" - its CLI is at {found} but not on this process's PATH"
            broken.append(note)

    if broken and not any(runnable(route) for route in table.routes.values()):
        return {
            "name": "routing",
            "ok": True,
            "detail": "No routed agent is installed on this machine yet, so no route can be judged.",
        }
    return {
        "name": "routing",
        "ok": not broken,
        "detail": "; ".join(broken) if broken else f"Every routed tier can run what it chose ({len(ROUTED_TIERS)} tiers).",
    }


def _loop_alive_check(root: Path) -> dict[str, Any]:
    """Whether the unattended loop has beaten recently enough to be running at all.

    `svasthya._check_scheduler_fresh` has computed this all along and `pravrudhi publish` puts it in the
    snapshot, where a cloud routine read it correctly and said so. It was absent from `doctor` -- the command
    `RESTART.md` tells a session to run in its first five minutes -- so on 2026-09-10 a session onboarded past
    an eleven-hour-old alarm and started other work. A check nobody runs at the moment they could act on it is
    a check that exists rather than one that works.
    """
    from pravrudhi.application import heartbeat
    from pravrudhi.application.svasthya import assess

    try:
        beats = heartbeat.history(root, n=1)
        if not beats:
            # Never beaten is unobserved, not stalled: a fresh install has no heartbeat and has not failed.
            # `watchdog._silent_loop` draws the same line, and `svasthya` deliberately does not, because for a
            # survival check a scheduler that has never run IS a problem. For `doctor`, which runs on machines
            # that have only just been initialised, it is not.
            return {"name": "loop_alive", "ok": True, "detail": "no heartbeat recorded yet; nothing to judge."}
        for check in assess(root).checks:
            if check.name == "scheduler_fresh":
                return {
                    "name": "loop_alive",
                    "ok": check.ok,
                    "detail": (
                        check.detail
                        if check.ok
                        else f"{check.detail} -- the loop is stopped, not slow. "
                        "`journalctl --user -u pravrudhi-heartbeat.service -o cat -n 40` for the reason, then "
                        "`systemctl --user reset-failed pravrudhi-heartbeat.service && "
                        "systemctl --user start pravrudhi-heartbeat.service`"
                    ),
                }
    except Exception as exc:  # noqa: BLE001 -- an unreadable log is a failing check, never a crashing doctor
        return {"name": "loop_alive", "ok": False, "detail": f"heartbeat freshness could not be read: {exc}"}
    return {"name": "loop_alive", "ok": False, "detail": "no heartbeat freshness check was produced"}


def _stale_worktrees_check(root: Path) -> dict[str, Any]:
    """How many dispatched tasks' worktrees are still on disk, and the commands that would remove each --
    informational only, never a failing check: `delegate.dispatch` keeps every worktree it creates so a
    rejected diff stays readable, and accumulating them is the cost of that, not a broken installation.
    Reported so an operator can prune deliberately (2026-09-12: 89 of them in one Studio checkout) rather than
    read `git worktree list` by hand to find out how many there are.
    """
    from pravrudhi.application.diffs import stale_agent_worktrees

    try:
        stale = stale_agent_worktrees(root)
    except OSError as exc:
        return {"name": "stale_worktrees", "ok": True, "detail": f"could not enumerate .worktrees/: {exc}"}
    if not stale:
        return {"name": "stale_worktrees", "ok": True, "detail": "no dispatched-task worktrees on disk."}
    merged = [w for w in stale if w.merged]
    lines = [f"{len(stale)} dispatched-task worktree(s) on disk, {len(merged)} already merged into HEAD:"]
    for w in stale:
        lines.append(
            f"  {w.task_id} ({w.age_days}d old, {'merged' if w.merged else 'unmerged'}"
            f"{f', {w.files} file(s)' if w.files is not None else ', unreadable'}): "
            f"git worktree remove --force {w.path}" + (f" && git branch -D {w.branch}" if w.merged else "")
        )
    return {"name": "stale_worktrees", "ok": True, "detail": "\n".join(lines)}


def run_doctor(root: Path) -> dict[str, Any]:
    """Check required files, ledger integrity, Docker, and sealed pool presence without changing state."""
    checks: list[dict[str, Any]] = []
    missing = [name for name in (".pravrudhi/config.yaml", "research/ledger.jsonl") if not (root / name).is_file()]
    checks.append({
        "name": "initialised",
        "ok": not missing,
        "detail": "Missing: " + ", ".join(missing) if missing else "Config and ledger exist.",
    })

    ledger = root / "research" / "ledger.jsonl"
    try:
        result = verify(ledger)
        ledger_ok = result.ok
        ledger_detail = f"Verified {result.n} ledger events." if result.ok else f"Ledger verification failed: {result.reason}"
    except (OSError, UnicodeError) as exc:
        ledger_ok = False
        ledger_detail = f"Cannot read research/ledger.jsonl: {exc.strerror if isinstance(exc, OSError) else 'invalid encoding'}"
    checks.append({"name": "ledger", "ok": ledger_ok, "detail": ledger_detail})

    docker_path = shutil.which("docker")
    if docker_path is None:
        docker_ok = False
        docker_detail = "Docker binary not installed: 'docker' executable is missing from PATH."
    elif docker_available():
        docker_ok = True
        docker_detail = f"Docker available at {docker_path}."
    else:
        info = subprocess.run(["docker", "info"], capture_output=True, text=True)
        stderr = info.stderr.strip()
        docker_ok = False
        if "permission denied" in stderr.lower():
            docker_detail = (
                "Permission denied running 'docker info': add your user to the docker group "
                "(then log out and back in) or use sudo."
            )
        else:
            docker_detail = "Docker daemon is not running: " + (stderr or f"'docker info' exited {info.returncode}.")
    checks.append({"name": "docker", "ok": docker_ok, "detail": docker_detail})

    gpu_path = shutil.which("nvidia-smi")
    if gpu_path is None:
        gpu_detail = "No GPU detected: 'nvidia-smi' is not on PATH."
    else:
        try:
            probe = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            gpu_detail = f"No GPU detected: 'nvidia-smi' could not be run ({exc})."
        else:
            lines = [line.strip() for line in probe.stdout.strip().splitlines() if line.strip()]
            if probe.returncode == 0 and lines:
                gpu_detail = "; ".join(lines)
            else:
                reason = probe.stderr.strip() or f"exited {probe.returncode}"
                gpu_detail = f"No GPU detected: 'nvidia-smi' failed ({reason})."
    checks.append({"name": "gpu", "ok": True, "detail": gpu_detail})

    pools = root / ".pravrudhi" / "kernel" / "pools"
    pool_ok = any(path.is_file() for path in pools.glob("*/manifest.json"))
    checks.append({
        "name": "pools",
        "ok": pool_ok,
        "detail": "A sealed pool manifest exists." if pool_ok else "No sealed pool manifest under .pravrudhi/kernel/pools.",
    })

    missing = [name for name in PREREG_FILES if not (root / "research" / "prereg" / name).is_file()]
    checks.append({
        "name": "prereg",
        "ok": not missing,
        "detail": "Missing pre-registration files: " + ", ".join(missing) if missing else "All pre-registration files exist.",
    })
    checks.append(_routing_check(root))
    checks.append(_loop_alive_check(root))
    checks.append(_telegram_check(root))
    checks.append(_build_validate_check(root))
    checks.append(_stale_worktrees_check(root))
    return {"ok": all(check["ok"] for check in checks), "checks": checks}
