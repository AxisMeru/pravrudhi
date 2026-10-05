"""One prompt set, many vendors, one scoring path.

Track A of `prabhasa-nyaya` is a verification layer over ANY vendor model, and comparing the vendors is one of
its outputs rather than an afterthought (ADR-0001, AxisMeru/prabhasa-nyaya). The fleet already reaches Claude,
GPT, Qwen and GLM from a product install: `pravrudhi agents` reports `ready` for four seats. What the fleet
does not do is compare them, and the reason is a difference in purpose rather than a missing feature.

`swarm` and `routing` exist to get work DONE. They pick one seat by cost and availability, fall back when a
seat is down, and treat the fallback as a success -- which is correct there, and exactly wrong here. A
comparison needs the opposite discipline:

* every named vendor is asked every prompt,
* no vendor is ever substituted for another,
* a vendor that could not answer is recorded as a GAP, because a silently substituted answer in a comparison
  reads as a result,
* and the parameters are part of the record, since the same vendor at a different temperature is a different
  measurement.

On interfaces. The operator's judgement is that a CLI and a direct API do not differ for the *verdict* being
measured, and this module does not argue with it. It records which interface answered anyway: one field, and
the only way a later reader can check that judgement instead of inheriting it.

On keys. A vendor record holds the NAME of the environment variable its key comes from, never a value. That is
what makes bring-your-own-key work when the product reaches someone else's hands, and it is why no manifest
this module writes can contain a secret.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pravrudhi.application.credentials import CredentialStore


@dataclass(frozen=True)
class Vendor:
    """One thing that can answer a prompt, and everything needed to ask it identically twice."""

    id: str
    interface: str          # cli | openai_compat | local_gguf
    model: str
    params: dict[str, Any] = field(default_factory=dict)
    base_url: str = ""
    credential: str = ""    # the NAME of an environment variable, never a value
    credential_file: str = ""   # optional 0600 file holding `NAME=value`, this project's own convention
    provider: str = ""      # the `credentials.PROVIDERS` id whose stored key this vendor can use, if any
    note: str = ""

    def key(self, root: Path | None = None, *, store: CredentialStore | None = None) -> str | None:
        """The credential: the environment, then the product's own credential store, then the 0600 file.

        Three places because bring-your-own-key means three different things depending on whose hands this is
        in. An environment variable is how a CI or a headless install supplies one. The product's store --
        `<root>/.pravrudhi/credentials/<provider>.key`, written by `/api/providers/{id}/key` -- is how a USER
        supplies one, by pasting it into the app; without this the panel could not see a key the product had
        already accepted, which would make "the fleet is in the product" untrue in the only way that matters.
        A mode-0600 file under `~/.config` is the operator's own machine, which is where `DASHSCOPE_API_KEY`
        already lives; reading only the environment reported that configured key as missing.

        `store` is the resolved `credentials.store_for_session` store for an actual HTTP caller (admin or
        BYOK user alike); it wins over `root` when given, since a caller with a resolved session already knows
        which project's store it is allowed to read, and re-deriving that from a bare root here would let this
        method quietly re-decide a boundary `credentials.py` already settled.
        """
        if not self.credential:
            return None
        if store is None:
            from pravrudhi.application.credentials import API_WITHOUT_TENANT_STORE, serving_api

            if serving_api.get():
                raise RuntimeError(API_WITHOUT_TENANT_STORE)
        if store is not None and getattr(store, "tenant_only", False):
            # A signed-in tenant: their own stored key or nothing. Never the operator's env or credential file.
            stored = store.get(self.provider) if self.provider else None
            return stored.reveal() if stored else None
        from_env = os.environ.get(self.credential)
        if from_env:
            return from_env
        if self.provider and store is not None:
            stored = store.get(self.provider)
            if stored:
                return stored.reveal()
        elif self.provider and root is not None:
            from pravrudhi.application.credentials import store_for

            stored = store_for(Path(root), None).get(self.provider)
            if stored:
                return stored.reveal()
        if not self.credential_file:
            return None
        path = Path(self.credential_file).expanduser()
        try:
            import stat

            info = path.stat()
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                return None   # a permissive credential file is not a credential
            for line in path.read_text().splitlines():
                text = line.strip()
                if text.startswith("export "):
                    text = text[len("export ") :]
                name, _, value = text.partition("=")
                if name.strip() == self.credential and value.strip():
                    return value.strip()
        except OSError:
            return None
        return None

    @property
    def reachable(self) -> tuple[bool, str]:
        """`reachable_in` against the current directory: the operator's own install."""
        return self.reachable_in(Path.cwd())

    def reachable_in(self, root: Path, *, store: CredentialStore | None = None) -> tuple[bool, str]:
        """Whether this vendor can be asked from `root`, and why not if it cannot.

        The root is a parameter because a stored key belongs to one project. Resolving it against the
        engine's own directory would show one user's configured key as everyone's -- the failure
        `workspace_root` exists to prevent, arrived at from a different direction. `store` is the same
        session-resolved store `key()` prefers, threaded through for the same reason.
        """
        if self.interface == "cli":
            import shutil

            exe = self.model.split(":")[0]
            if not shutil.which(exe):
                return False, f"{exe} not on PATH"
            if exe == "claude":
                # On PATH is not enough: this project uses its own Claude account, so a reachable binary with
                # only the operator's personal login is deliberately NOT reachable here. Reporting it ready
                # would send a panel run into 80 refusals.
                from pravrudhi.agents.account import account_status

                return account_status()
            return True, "ready"
        if self.interface == "openai_compat" and self.credential:
            if not getattr(store, "tenant_only", False) and os.environ.get(self.credential):
                return True, "key in environment"
            if self.provider and self.key(root, store=store) and not self.credential_file:
                return True, f"key stored for provider {self.provider}"
            if self.key(root, store=store):
                return True, f"key in {self.credential_file or f'the store for {self.provider}'}"
            places = ["the environment"]
            if self.provider:
                places.append(f"the stored key for {self.provider}")
            if self.credential_file:
                places.append(self.credential_file)
            return False, f"{self.credential} is in none of {', '.join(places)} (bring your own key)"
        if self.interface == "local_gguf":
            return (True, "local weights") if Path(self.model).exists() else (False, f"{self.model} missing")
        return True, "no credential required"


@dataclass(frozen=True)
class Answer:
    """What one vendor said to one prompt, or why it said nothing."""

    vendor: str
    interface: str
    model: str
    prompt_id: str
    text: str
    wall_s: float
    tokens: int | None
    error: str | None
    #: The model claude-cli actually billed, read back from the JSON envelope's `modelUsage` (Tag review,
    #: 2026-09-26) -- `model` above is the `--model` flag PINNED before the call; this is what the vendor
    #: resolved that alias to. `None` for every interface that cannot report one (openai_compat vendors
    #: already put their real model id in `model`; a failed call has no envelope to read it from).
    resolved_model: str | None = None
    #: Real cache read/write counts from the same envelope, never a hardcoded null (Tag review, 2026-09-26):
    #: cache tokens can dwarf the visible turn (`cli_agents._usage`'s own docstring: a real envelope carried
    #: cache write 47,852 against output 4), and `tokens` alone would hide that.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    #: The envelope's own `total_cost_usd`; `None` when the vendor reported none ("unobserved", never 0.0).
    cost_usd: float | None = None
    #: Every model the CLI billed (claude: the `modelUsage` keys), so an auxiliary Haiku call is visible
    #: rather than silently dropped by taking only the first key.
    billed_models: tuple[str, ...] = ()


# Reachable today. `claude` and `codex` are agentic CLIs in print mode; the operator's judgement is that this
# does not change the verdict, and `interface` records it either way.
_CLI = {"temperature": 0.0, "max_tokens": 2048}

#: Issue #59: a bare `claude -p` call loads every plugin/MCP/skill/CLAUDE.md the CLI knows about before it
#: ever reads the prompt -- Lead-2's own measurement (2026-09-26, TEAM-RULES.md's Claude usage cost rules),
#: ~105k tokens per call against ~3.3k for the slim invocation below; this PR does not re-derive that figure,
#: only applies it. `ask_vendor`'s claude-cli path is a one-shot "ask this vendor one prompt" comparison,
#: never an agentic coding turn (unlike `ClaudeCodeAgent`/`orca_agent.headless_command`'s own `claude -p`
#: calls, which grant real tools -- Read/Edit/Write/Grep/Glob/Bash -- and so cannot use these flags without
#: breaking the thing they exist for): it never needs a tool, a skill, an MCP server, or this repo's own
#: CLAUDE.md, so there is nothing here to pay 105k tokens of context for. `--tools ""` and
#: `--disable-slash-commands` turn off the two things a genuinely bare `-p` call could still reach for;
#: `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` and `--setting-sources ""` are what actually stop
#: the plugin/MCP/CLAUDE.md load. Model is capped at `sonnet` (TEAM-RULES.md's Claude usage cost rules --
#: never opus/default for this path); a caller who wants a different pinned model still overrides
#: `vendor.params["model"]` (see `ask_vendor` below), this is only the default.
CLAUDE_CLI_MODEL_DEFAULT = "sonnet"
CLAUDE_CLI_SLIM_FLAGS = (
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
    "--setting-sources", "",
    "--disable-slash-commands",
    "--tools", "",
)

#: Issue #59 follow-up (Lead-2, 2026-09-26): `ask_vendor`'s claude-cli comparison uses its OWN dedicated
#: credential (the operator's new `sharath.ai.colab` login), never one of `account.claude_env`'s CLI-agent
#: seats -- this is a one-shot vendor comparison, not agentic coding work, and competing with those seats
#: for the same weekly quota would undo the whole point of giving this path a separate account.
#: Config-driven (env var, matching `account.py`'s own `PRAVRUDHI_CLAUDE_CONFIG_DIR` convention) rather than
#: hardcoded, so this can be redirected without a code change.
CLAUDE_CLI_CONFIG_DIR_ENV = "PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR"
#: Seat rule (operator 2026-09-27, supersedes the 09-26 colab-dir rule): every scripted `claude -p` runs on TEAM
#: SEAT 2 via the `claude-loop` dir, and `claude auth status --json` must show this email before any call
#: (refuse on mismatch; a login is never switched here). Seat 0 (`claude-colab`) is R1/R2 only.
CLAUDE_CLI_CONFIG_DIR_DEFAULT = Path("~/.config/pravrudhi/claude-loop")
CLAUDE_CLI_EXPECTED_EMAIL = "sharath.sathish@gmail.com"

#: Model cap (Lead-2 2026-09-26): nothing above Sonnet. Aliases `sonnet`/`haiku` or an exact
#: `claude-sonnet-*`/`claude-haiku-*` id. Opus is allowed only for the pre-registered M4 comparison, which must
#: also say `params["effort"] == "low"`. Anything else (the default, fable, a typo) is refused, not passed on.
_CLAUDE_MODEL_OK = re.compile(r"(sonnet|haiku|claude-(sonnet|haiku)-[A-Za-z0-9._-]+)")
_CLAUDE_OPUS = re.compile(r"(opus|claude-opus-[A-Za-z0-9._-]+)")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

#: A quota/limit notice prints with exit 0 (2026-09-26: 847 audit rows contaminated). Same pattern as
#: `scripts/adversarial_reviewer.py`. It is only applied to SHORT results: a real judgement that happens to
#: mention a "rate limit" is long; a notice is one line.
QUOTA_RE = re.compile(
    r"session limit|hit your|usage limit|rate limit|limit reached|resets? (at|in)|out of (extra )?usage|"
    r"quota|try again later|overloaded", re.I)
QUOTA_MAX_CHARS = 400


class ClaudeCliNotProvisioned(RuntimeError):
    """`ask_vendor`'s dedicated claude-cli credential directory has no login. Raised rather than falling
    back to whatever account happens to be ambient -- `account.py`'s own "the refusal is the other half"
    principle (a silent fallback would look like compliance while being the opposite), applied to this
    separate, comparison-only seat."""


def _claude_cli_env() -> dict[str, str]:
    from pravrudhi.agents.account import CREDENTIAL_FILES

    config_dir = Path(os.environ.get(CLAUDE_CLI_CONFIG_DIR_ENV) or CLAUDE_CLI_CONFIG_DIR_DEFAULT).expanduser()
    if not any((config_dir / f).is_file() for f in CREDENTIAL_FILES):
        raise ClaudeCliNotProvisioned(
            f"no claude login at {config_dir} (set {CLAUDE_CLI_CONFIG_DIR_ENV} to redirect, or log in there)"
        )
    return {"CLAUDE_CONFIG_DIR": str(config_dir)}


def _claude_cli_pinned_model(vendor: Vendor) -> str:
    """The `--model` this vendor's claude-cli call will actually use.

    Shared by `ask_vendor` and `panel_manifest` (Tag review, 2026-09-26) so the two cannot drift: an ABSENT
    `params["model"]` is the ordinary case the default exists for, but an explicit `None`/empty value means the
    caller asked for something and got nothing -- that is a config error, not "fall back to the default", so
    it raises rather than silently substituting `CLAUDE_CLI_MODEL_DEFAULT` via `or`.
    """
    if "model" not in vendor.params:
        return CLAUDE_CLI_MODEL_DEFAULT
    requested = vendor.params["model"]
    if not requested:
        raise ValueError(
            f"vendor {vendor.id!r} params['model'] is {requested!r} -- omit the key to use the default "
            f"({CLAUDE_CLI_MODEL_DEFAULT!r}), or give it a real model id"
        )
    model = str(requested)
    if _CLAUDE_MODEL_OK.fullmatch(model):
        return model
    if _CLAUDE_OPUS.fullmatch(model):
        if vendor.params.get("effort") != "low":
            raise ValueError(
                f"vendor {vendor.id!r}: opus is allowed only for the pre-registered M4 comparison at "
                f"params['effort'] == 'low' (got {vendor.params.get('effort')!r})")
        return model
    raise ValueError(
        f"vendor {vendor.id!r} params['model'] {model!r} is not a sonnet/haiku alias or id (the model cap is "
        f"Sonnet; opus needs effort 'low')")


def _claude_cli_effort(vendor: Vendor) -> str | None:
    effort = vendor.params.get("effort")
    if effort is None:
        return None
    if effort not in CLAUDE_EFFORTS:
        raise ValueError(f"vendor {vendor.id!r} params['effort'] {effort!r} not in {CLAUDE_EFFORTS}")
    return str(effort)


def _claude_auth_email(env: dict[str, str]) -> str | None:
    """The email `claude auth status --json` reports for this config dir (the only truth for the seat)."""
    p = subprocess.run(["claude", "auth", "status", "--json"], env={**os.environ, **env},
                       capture_output=True, text=True, timeout=60)
    try:
        email = json.loads(p.stdout).get("email")
    except (ValueError, AttributeError):
        return None
    return email if isinstance(email, str) else None


def _assert_claude_seat(env: dict[str, str]) -> None:
    email = _claude_auth_email(env)
    if email != CLAUDE_CLI_EXPECTED_EMAIL:
        raise ClaudeCliNotProvisioned(
            f"refusing: claude auth status shows {email!r}, not {CLAUDE_CLI_EXPECTED_EMAIL!r} "
            f"(config dir {env.get('CLAUDE_CONFIG_DIR')})")


def _model_in_family(pinned: str, key: str) -> bool:
    """Does a billed `modelUsage` key belong to what `--model` pinned? An alias matches its family prefix; an
    exact id matches itself (a `[1m]`-style suffix is ignored)."""
    key = key.split("[", 1)[0]
    if pinned in ("sonnet", "haiku", "opus"):
        return key.startswith(f"claude-{pinned}")
    return key == pinned.split("[", 1)[0]


def _check_claude_models(pinned: str, model_usage: Any) -> tuple[str, tuple[str, ...]]:
    """`(resolved_model, all billed models)`, or RuntimeError. The pinned family must be billed, and the only
    other model allowed beside it is Haiku (the CLI's own auxiliary call) -- same rule as the reviewer script."""
    if not isinstance(model_usage, dict) or not model_usage:
        raise RuntimeError(f"model unverifiable: envelope has no modelUsage (pinned {pinned!r})")
    keys = tuple(model_usage)
    matched = [k for k in keys if _model_in_family(pinned, k)]
    if not matched:
        raise RuntimeError(f"model mismatch: CLI billed {sorted(keys)}, pinned {pinned!r}")
    extra = [k for k in keys if k not in matched]
    if any(not k.startswith("claude-haiku") for k in extra):
        raise RuntimeError(f"model mismatch: unexpected models {sorted(extra)} beside pinned {pinned!r}")
    return matched[0], keys


# Declared before any key exists, on the operator's instruction, so that when a key lands nothing has to be
# designed under time pressure. Each names the variable it reads. `temperature` is pinned on every one of them
# because an unpinned sampler makes a comparison unrepeatable.
_API = {"temperature": 0.0, "max_tokens": 2048, "top_p": 1.0, "seed": 0}

def _from_provider(provider_id: str, model: str, compat_suffix: str = "") -> Vendor:
    """A panel vendor built from the product's BYOK provider of the same name.

    The key variable, the title and the key validation stay in the registry that owns them; only the model id
    and the OpenAI-compatibility suffix are the panel's business.

    `compat_suffix` is per provider and not derived, because the shape differs and a rule would be wrong for
    one of them: Google's OpenAI-compatible endpoint is `.../v1beta/openai` while Anthropic's is the same
    `.../v1` the native API uses. A single "append /openai unless openai_compatible" rule produced
    `https://api.anthropic.com/v1/openai`, which does not exist.
    """
    from pravrudhi.application.credentials import PROVIDERS

    provider = PROVIDERS[provider_id]
    return Vendor(
        id=f"{provider_id}-api",
        interface="openai_compat",
        model=model,
        base_url=provider.base_url.rstrip("/") + compat_suffix,
        credential=provider.key_env,
        provider=provider_id,
        params=dict(_API),
        note=f"{provider.title}; bring your own key via /api/providers/{provider_id}/key -- unrunnable until one exists",
    )


VENDORS: dict[str, Vendor] = {
    "claude-cli": Vendor(
        id="claude-cli", interface="cli", model="claude", params=dict(_CLI),
        note="claude -p, agentic print mode",
    ),
    "codex-cli": Vendor(
        id="codex-cli", interface="cli", model="codex", params=dict(_CLI),
        note="codex exec, agentic non-interactive",
    ),
    "qwen-dashscope": Vendor(
        # Free-tier DashScope retired everywhere (operator instruction, 2026-09-25): the paid Lite Plan
        # endpoint, credential file and model below, never the free tier's.
        id="qwen-dashscope", interface="openai_compat", model="qwen3.8-max",
        base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        credential="DASHSCOPE_API_KEY", credential_file="~/.config/llm/dashscope-plan.env",
        provider="alibaba-plan", params=dict(_API),
        note="the operator's Lite Plan credential; the file it comes from picks the endpoint",
    ),
    # Derived from the product's own BYOK registry, not restated. `credentials.PROVIDERS` already holds the
    # base URL and key variable for each of these, the product already has `/api/providers/{id}/key` to put a
    # key there, and two registries naming the same endpoint drift -- one of them gets a new base URL and the
    # other keeps answering. So a key a user pastes into the app is the key a panel run uses, which is what
    # "expand the fleet into the product" has to mean if it means anything.
    **{
        f"{_pid}-api": _from_provider(_pid, _model, _suffix)
        for _pid, _model, _suffix in (
            ("anthropic", "claude-opus-5", ""),          # OpenAI-compatible on the same /v1 as the native API
            ("openai", "gpt-5", ""),
            ("google", "gemini-3-pro", "/openai"),       # the native /v1beta is a different protocol
        )
    },
    "glm-local": Vendor(
        id="glm-local", interface="local_gguf",
        model=str(Path.home() / ".cache/huggingface/hub/models--ggml-org--GLM-4.7-Flash-GGUF"),
        params={"temperature": 0.0, "max_tokens": 2048, "seed": 0},
        note="local weights: deterministic, no network, no marginal cost",
    ),
    "nyaya-p2b-local": Vendor(
        id="nyaya-p2b-local", interface="openai_compat", model="nyaya-p2b-arm_c",
        base_url="http://172.17.0.1:8099/v1", params={"temperature": 0.0, "max_tokens": 300},
        note="local arm_c via host shim (on-demand), reached over the docker bridge gateway",
    ),
}


#: Where per-vendor parameters live when they are not the registry's defaults. Config-driven because the
#: house rule puts constants in `configs/`, and because "the same vendor at a different temperature is a
#: different measurement" is only checkable if the setting is a file someone can read.
PANEL_CONFIG = Path("configs") / "panel.yaml"


def parse_override(text: str) -> tuple[str, str, Any]:
    """`vendor:key=value` -> (vendor, key, value), with the value typed by what it looks like.

    Typed rather than left as a string because `temperature="0.2"` reaches an HTTP body as a string and some
    endpoints accept it, coerce it, and answer -- so the run succeeds and the manifest records a parameter
    nobody set to that.
    """
    vendor, sep, rest = text.partition(":")
    key, eq, value = rest.partition("=")
    if not (sep and eq and vendor.strip() and key.strip()):
        raise ValueError(f"expected vendor:key=value, got {text!r}")
    raw = value.strip()
    typed: Any = raw
    if raw.lower() in ("true", "false"):
        typed = raw.lower() == "true"
    elif raw.lower() in ("none", "null"):
        typed = None
    else:
        for cast in (int, float):
            try:
                typed = cast(raw)
                break
            except ValueError:
                continue
    return vendor.strip(), key.strip(), typed


def tuned(
    vendors: Sequence[Vendor], *, config: Path | None = None, overrides: Iterable[str] = ()
) -> list[Vendor]:
    """The same vendors with their parameters replaced, config first and explicit overrides last.

    A vendor is frozen, so this returns new ones: the registry's defaults stay the defaults, and what ran is
    whatever `panel_manifest` recorded.
    """
    from dataclasses import replace

    layered: dict[str, dict[str, Any]] = {}
    path = Path(config) if config else None
    if path and path.exists():
        import yaml

        body = yaml.safe_load(path.read_text()) or {}
        for vid, params in (body.get("vendors") or {}).items():
            layered.setdefault(str(vid), {}).update(dict(params or {}))
    known = {v.id for v in vendors}
    for text in overrides:
        vid, key, value = parse_override(text)
        if vid not in known:
            # Refused rather than ignored: a typo in a tuning flag that silently does nothing produces a run
            # labelled with parameters it did not use.
            raise KeyError(f"--param names vendor {vid!r}, which is not in this panel: {', '.join(sorted(known))}")
        layered.setdefault(vid, {})[key] = value
    return [replace(v, params={**v.params, **layered.get(v.id, {})}) for v in vendors]


def load_vendors(ids: Iterable[str]) -> list[Vendor]:
    """The named vendors, refusing an unknown id.

    Refusing rather than skipping: a typo that silently drops a vendor from a comparison produces a result
    that looks complete and is not, and nothing downstream can tell.
    """
    out: list[Vendor] = []
    for vid in ids:
        if vid not in VENDORS:
            raise KeyError(f"unknown vendor {vid!r}; known: {', '.join(sorted(VENDORS))}")
        out.append(VENDORS[vid])
    return out


def _looks_like_quota(text: str) -> bool:
    return (len(text) <= QUOTA_MAX_CHARS and bool(QUOTA_RE.search(text))
            and not text.lstrip().startswith(("{", "`")))


def _cost(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


_CODEX_TEXT_ITEMS = ("agent_message", "assistant_message")


def _codex_rollout_models(thread_id: Any) -> set[str]:
    """The model id(s) on the `turn_context` lines of this thread's codex rollout file; empty if not found."""
    import glob

    if not isinstance(thread_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", thread_id):
        return set()
    home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    models: set[str] = set()
    for path in glob.glob(str(home / "sessions" / "*" / "*" / "*" / f"rollout-*-{thread_id}.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if '"turn_context"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    m = (ev.get("payload") or {}).get("model") if ev.get("type") == "turn_context" else None
                    if isinstance(m, str) and m:
                        models.add(m)
        except OSError:
            continue
    return models


def _codex_answer(vendor: Vendor, model: str, out: str, wall: float) -> Answer:
    """Parse a `codex exec --json` event stream into an Answer, or raise.

    The answer is the last completed agent message. OBSERVED 2026-10-02 (codex-cli 0.153.4): the `--json` stream
    (thread.started, turn.started, item.completed/agent_message, turn.completed) carries NO model id. The id is
    in codex's own session rollout, `<CODEX_HOME>/sessions/Y/M/D/rollout-<ts>-<thread_id>.jsonl`, on the
    `turn_context` line's `payload.model`; `thread_id` comes from the stream's `thread.started`. That is the
    source of `resolved_model`. A `model` key in the stream, if a later codex adds one, must agree with it. With
    `params["codex_model"]` pinned the resolved id MUST equal it: an unreadable rollout is an error, not a pass.
    A quota/limit notice, an `error`/`turn.failed` event, or an empty answer is an ERROR.
    No cost field exists in the stream, so `cost_usd` stays None ("unobserved").
    """
    from pravrudhi.agents.cli_agents import _codex_usage

    events: list[dict[str, Any]] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                events.append(ev)
    if not events:
        raise RuntimeError(f"codex produced no JSON event stream: {(out or '')[:120]!r}")
    for ev in events:
        if ev.get("type") in ("error", "turn.failed"):
            raise RuntimeError(f"codex reported an error: {json.dumps(ev)[:300]}")
    text = ""
    for ev in events:
        item = ev.get("item")
        if ev.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") in _CODEX_TEXT_ITEMS:
            text = str(item.get("text", "")).strip()
    if not text:
        raise RuntimeError("codex produced no completed agent message")
    if _looks_like_quota(text):
        raise RuntimeError(f"quota/limit notice (ERROR, not an answer): {text[:120]!r}")
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "model" and isinstance(v, str) and v:
                    seen.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(events)
    thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    from_rollout = _codex_rollout_models(thread_id)
    seen |= from_rollout
    if len(seen) > 1:
        raise RuntimeError(f"model mismatch: codex reports several models {sorted(seen)}")
    resolved = next(iter(seen), None)
    pinned = vendor.params.get("codex_model")
    if pinned and resolved != pinned:
        raise RuntimeError(f"model mismatch: codex reported {resolved!r}, pinned {pinned!r}")
    tokens, cache_read, cache_write = _codex_usage(out)
    return Answer(vendor.id, vendor.interface, model, "", text, wall, tokens, None, resolved,
                  cache_read, cache_write, None, (resolved,) if resolved else ())


def ask_vendor(
    vendor: Vendor, prompt: str, *, root: Path | None = None, store: CredentialStore | None = None
) -> Answer:
    """Ask one vendor one prompt. Raises on transport failure; `run_panel` turns that into a recorded gap.

    `root` and `store` reach the vendor's own key resolution (`Vendor.key`) unchanged; a session-aware caller
    (`application.nyaya.ask`, resolved from a signed-in account's own project) passes its own `store` so the
    OpenAI-compatible client below is built with that caller's key, never a bystander's.
    """
    from pravrudhi.application.credentials import API_WITHOUT_TENANT_STORE, serving_api

    if store is None and serving_api.get():
        raise RuntimeError(API_WITHOUT_TENANT_STORE)
    if serving_api.get():
        from pravrudhi.application import tenant_vendors
        from pravrudhi.application.credentials import serving_org

        tenant_vendors.require(vendor.id, serving_org.get())
    if vendor.interface == "cli":
        from pravrudhi.agents.cli_agents import _run, _usage

        env: dict[str, str] = {}
        if vendor.model == "claude":
            # The operator's personal login was the original problem (2026-09-10: its weekly limit ran out
            # mid-panel during gate A1.1, 68 of 80 prompts answered, 12 recorded as gaps). This path now
            # uses its OWN dedicated seat instead (`_claude_cli_env`, issue #59 follow-up) -- deliberately
            # NOT `account.claude_env`'s CLI-agent seat rotation, which this one-shot comparison would
            # otherwise compete with for the same quota.
            model = _claude_cli_pinned_model(vendor)
            # `--output-format json` (Tag review, 2026-09-26), not `text`: the JSON envelope carries the
            # resolved model (`modelUsage`) and the real usage breakdown that `text` throws away.
            cmd = ["claude", "-p", "--output-format", "json", *CLAUDE_CLI_SLIM_FLAGS, "--model", model]
            effort = _claude_cli_effort(vendor)
            if effort:
                cmd += ["--effort", effort]
            env = _claude_cli_env()
            _assert_claude_seat(env)
        else:
            model = vendor.model
            # `--json` for the event stream (model id, usage, errors); `-m` only when a model id is pinned.
            cmd = ["codex", "exec", "--skip-git-repo-check", "--json"]
            if vendor.params.get("codex_model"):
                cmd += ["-m", str(vendor.params["codex_model"])]
        # The prompt rides on stdin, never argv: a >128 KiB prompt is refused by the kernel as an argv string
        # (E2BIG) before the CLI starts. Both CLIs read the prompt from stdin when none is given positionally.
        code, out, err, wall = _run(
            cmd, Path.cwd(), int(vendor.params.get("timeout_s", 900)), env=env, stdin_text=prompt
        )
        if vendor.model != "claude":
            if code != 0:
                raise RuntimeError((err or out or f"{model} exited {code}")[-400:])
            return _codex_answer(vendor, model, out, wall)

        # A quota/limit notice prints to stdout with exit 0 (the 2026-09-26 incident that contaminated 847
        # audit rows before this was caught) -- exit 0 is not itself success here. An unparseable envelope, an
        # `is_error` envelope, or an empty `result` are the same kind of gap as a nonzero exit: recorded as a
        # failure for `run_panel` to catch, never returned as a scored answer.
        try:
            envelope = json.loads(out)
        except ValueError:
            envelope = None
        if code != 0 or not isinstance(envelope, dict) or envelope.get("is_error"):
            raise RuntimeError((err or out or f"{model} exited {code}")[-400:])
        text = str(envelope.get("result", "")).strip()
        if not text:
            raise RuntimeError(f"{model} exited 0 with an empty result (a quota/limit notice looks exactly like this)")
        if _looks_like_quota(text):
            raise RuntimeError(f"quota/limit notice (ERROR, not an answer): {text[:120]!r}")
        resolved_model, billed = _check_claude_models(model, envelope.get("modelUsage"))
        tokens, cache_read, cache_write = _usage(envelope)
        return Answer(vendor.id, vendor.interface, model, "", text, wall, tokens, None, resolved_model,
                      cache_read, cache_write, _cost(envelope.get("total_cost_usd")), billed)

    if vendor.interface == "openai_compat":
        from pravrudhi.models.openai_compat import ChatClient

        key = vendor.key(root or Path.cwd(), store=store)
        if vendor.credential and not key:
            raise RuntimeError(f"{vendor.credential} is not set")
        client = ChatClient(base_url=vendor.base_url, model=vendor.model, api_key=key)
        res = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=float(vendor.params.get("temperature", 0.0)),
            max_tokens=int(vendor.params.get("max_tokens", 2048)),
            seed=vendor.params.get("seed"),
        )
        return Answer(
            vendor.id, vendor.interface, vendor.model, "", res.text.strip(), res.wall_s,
            res.completion_tokens, None,
        )

    raise RuntimeError(f"interface {vendor.interface!r} cannot be asked yet: {vendor.note}")


AskFn = Callable[[Vendor, str], Answer]


def panel_manifest(prompts: Sequence[dict[str, str]], vendors: Sequence[Vendor]) -> dict[str, Any]:
    """What was asked, of whom, with which parameters. No secret can appear here: a vendor holds the NAME of
    its credential variable and never a value."""
    blob = json.dumps([dict(p) for p in prompts], sort_keys=True).encode()
    return {
        "n_prompts": len(prompts),
        "prompts_sha256": hashlib.sha256(blob).hexdigest(),
        "vendors": [
            {
                "id": v.id, "interface": v.interface, "model": v.model, "params": dict(v.params),
                "credential_env": v.credential or None, "provider": v.provider or None, "note": v.note,
                "reachable": v.reachable[0], "reachable_detail": v.reachable[1],
                # The `--model` claude-cli will actually pin (Tag review, 2026-09-26): `model` above is just
                # "claude", the interface literal, not the model. Raises the same way `ask_vendor` would on an
                # explicit null/empty override, so a bad config is caught building the manifest, not 40 calls
                # into a run.
                "pinned_model": _claude_cli_pinned_model(v) if v.interface == "cli" and v.model == "claude" else None,
                # What the call will actually look like, so a manifest shows the enforced invocation and seat.
                "claude_effort": _claude_cli_effort(v) if v.interface == "cli" and v.model == "claude" else None,
                "claude_slim_flags": list(CLAUDE_CLI_SLIM_FLAGS) if v.interface == "cli" and v.model == "claude" else None,
                "claude_expected_seat_email": CLAUDE_CLI_EXPECTED_EMAIL if v.interface == "cli" and v.model == "claude" else None,
                "codex_pinned_model": v.params.get("codex_model") if v.interface == "cli" and v.model == "codex" else None,
            }
            for v in vendors
        ],
    }


def run_panel(
    out_dir: Path,
    prompts: Sequence[dict[str, str]],
    vendors: Sequence[Vendor],
    *,
    ask: AskFn | None = None,
) -> list[Answer]:
    """Ask every vendor every prompt. One row per (vendor, prompt), including the ones that failed.

    A failure is recorded, never filled in by another vendor. That is the single rule separating this from
    `swarm`, where falling back to a working seat is the right behaviour.
    """
    ask = ask or ask_vendor
    dest = Path(out_dir) / "panel"
    dest.mkdir(parents=True, exist_ok=True)
    # The manifest first, then each answer as it arrives. Writing everything at the end meant a run of 240
    # CLI calls held its only copy in memory for the best part of an hour: nothing to watch while it ran, and
    # nothing left if it died on the last vendor. Flushed per row so `wc -l` is honest progress.
    (dest / "manifest.json").write_text(
        json.dumps(panel_manifest(prompts, vendors), indent=2, sort_keys=True) + "\n"
    )
    answers: list[Answer] = []
    with (dest / "answers.jsonl").open("w") as fh:
        for vendor in vendors:
            for item in prompts:
                pid = str(item["id"])
                try:
                    answer = ask(vendor, str(item["prompt"]))
                    # `answer.model`, not `vendor.model` (Tag review, 2026-09-26): for claude-cli, `vendor.model`
                    # is just the interface literal "claude" -- `answer.model` is the `--model` this call was
                    # actually pinned to, and `answer.resolved_model` is what the vendor billed it as.
                    row = Answer(vendor.id, vendor.interface, answer.model, pid, answer.text,
                                 answer.wall_s, answer.tokens, None, answer.resolved_model,
                                 answer.cache_read_tokens, answer.cache_write_tokens,
                                 answer.cost_usd, answer.billed_models)
                except Exception as exc:  # noqa: BLE001 - a vendor that cannot answer is data, not a crash
                    row = Answer(vendor.id, vendor.interface, vendor.model, pid, "", 0.0, None, str(exc)[:400])
                answers.append(row)
                fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")
                fh.flush()
    return answers
