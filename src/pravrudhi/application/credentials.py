"""Bring-your-own-key credentials for OpenAI, Anthropic, Google, Alibaba and any OpenAI-compatible endpoint.

Until this module existed, the only model access this engine had was the operator's own hosted free-tier
key (`pravrudhi.models.hosted`) or a local GGUF server — nothing let a user plug in their own provider key,
and there was nowhere principled to put one if they could. Two failure modes made that dangerous to bolt on
carelessly: a key written into a project directory that happens to be a git work tree is one `git add .`
away from a public repository, and a key that turns up in a repr, a traceback, or a log line ends up in
whatever aggregates those — a bug report, a Sentry event, a CI log — none of which are secret stores.

`FileCredentialStore` mirrors `api.localguard.app_token`'s pattern: a 0600 file under
`<root>/.pravrudhi/`, created on first write, never group- or world-readable. It refuses outright to write
under a root that sits inside a git work tree, on the same reasoning `api.identity.guard_boot` refuses to
start unauthenticated on a deployment marker — better to fail loudly at the one moment a mistake is still
cheap to fix than to silently create a file someone later commits. `Secret` and `redact()` are the second
half of the same guard: a key must survive being displayed, logged or exception-propagated without ever
being read off directly, the same discipline `memory_store._httpx_fetch` uses to keep the Supabase service
key out of every request path but the header it belongs in.

`store_for` mirrors `application.memory_store.store_for`'s signature. Only the file store exists today; a
Supabase-backed one — one row per (user_id, provider), with the key itself encrypted or held in a vault
rather than a plain column — is a later milestone, and wiring it in ahead of that design would mean either
storing keys in the clear in a shared table or half-implementing encryption. It raises `NotImplementedError`
for a logged-in user rather than falling back to the file store, which would silently share one workspace's
keys across every account on a hosted engine.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from pravrudhi.api.identity import User


class UnknownProviderError(ValueError):
    """A provider id that `PROVIDERS` does not recognize."""


class GitTrackedPathError(RuntimeError):
    """Refusing to write a credential file under a root that sits inside a git work tree."""


@dataclass(frozen=True, slots=True)
class Provider:
    """One model provider a user can bring a key for.

    `probe_model` names a cheap, known-stable model id `validate()` can ask the provider's models endpoint
    about directly, rather than listing every model the account can see — a narrower, lighter-weight call.
    """

    id: str
    title: str
    base_url: str
    key_env: str
    key_prefix: str
    openai_compatible: bool
    probe_model: str


# Alibaba's Singapore (international) DashScope region — not the mainland China endpoint, which enforces
# different residency and content rules than the rest of this registry assumes.
PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        id="openai",
        title="OpenAI",
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        key_prefix="sk-",
        openai_compatible=True,
        probe_model="gpt-4o-mini",
    ),
    "anthropic": Provider(
        id="anthropic",
        title="Anthropic",
        base_url="https://api.anthropic.com/v1",
        key_env="ANTHROPIC_API_KEY",
        key_prefix="sk-ant-",
        openai_compatible=False,
        probe_model="claude-3-5-haiku-20241022",
    ),
    "google": Provider(
        id="google",
        title="Google (Gemini)",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        key_env="GOOGLE_API_KEY",
        key_prefix="AIza",
        openai_compatible=False,
        probe_model="gemini-1.5-flash",
    ),
    "alibaba": Provider(
        id="alibaba",
        title="Alibaba (Qwen, Singapore)",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_API_KEY",
        key_prefix="sk-",
        openai_compatible=True,
        probe_model="qwen-turbo",
    ),
    "alibaba-plan": Provider(
        id="alibaba-plan",
        title="Alibaba Lite Plan (Qwen, plan-exclusive)",
        base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_PLAN_API_KEY",
        key_prefix="sk-",
        openai_compatible=True,
        probe_model="qwen3.8-flash",
    ),
    "openai-compatible": Provider(
        id="openai-compatible",
        title="OpenAI-compatible endpoint",
        base_url="",
        key_env="OPENAI_COMPATIBLE_API_KEY",
        key_prefix="",
        openai_compatible=True,
        probe_model="",
    ),
}


class Secret:
    """A credential value that refuses to print itself. `reveal()` is the one deliberate escape hatch, for the
    call site that actually needs to put the key on the wire.

    Deliberately NOT a dataclass, and that is the whole point. As a frozen dataclass it silently leaked: this
    codebase serialises records with `dataclasses.asdict` in half a dozen places -- objectives, memory, subagents,
    the routing log -- and `asdict` reads the fields directly, walking straight past `__repr__`. A `Secret` handed
    to any of those would have written the raw key into a JSON line on disk. A plain slotted class makes `asdict`
    raise TypeError instead, which turns a silent leak into a loud stop, and having no `__dict__` closes the
    `json.dumps(obj.__dict__)` route with it.
    """

    __slots__ = ("provider", "_value")

    # Annotations only: with __slots__ these create no class attributes, and without them a type checker cannot
    # see through the object.__setattr__ the hand-written immutability requires.
    provider: str
    _value: str

    def __init__(self, provider: str, value: str) -> None:
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name: str, value: object) -> None:  # frozen by hand, since this is not a dataclass
        raise AttributeError("Secret is immutable")

    def __repr__(self) -> str:
        return f"Secret(provider={self.provider}, len={len(self._value)})"

    def __str__(self) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other.provider == self.provider and other._value == self._value

    def __hash__(self) -> int:
        return hash((self.provider, self._value))

    def __reduce__(self) -> tuple[Any, ...]:
        """Refuse to pickle. A pickled Secret is a key on disk in a format nothing here can redact."""
        raise TypeError("a Secret must not be pickled; call reveal() at the point of use instead")

    @property
    def value(self) -> str:
        """Kept for callers that read `.value`; identical to `reveal()` and equally deliberate."""
        return self._value

    def reveal(self) -> str:
        return self._value


_KEY_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{10,}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)


def redact(text: str) -> str:
    """Strip anything shaped like a provider key out of `text`, for use before logging it."""
    out = text
    for pattern in _KEY_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _is_inside_git_worktree(path: Path) -> bool:
    """Ask git itself, rather than looking for a `.git` entry: a stray, empty, or otherwise invalid
    `.git` directory (left over from some other tool, or a half-cleaned-up test fixture) is not a
    reason to refuse a write, and only git's own discovery reliably tells the two apart."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


class CredentialStore(Protocol):
    """Where a user's provider keys live, keyed by provider id."""

    def put(self, provider_id: str, key: str) -> None: ...

    def get(self, provider_id: str) -> Secret | None: ...

    def delete(self, provider_id: str) -> bool: ...

    def configured(self) -> list[str]: ...


class FileCredentialStore:
    """`CredentialStore` over `<root>/.pravrudhi/credentials/<provider>.key`, one 0600 file per provider."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _dir(self) -> Path:
        return self._root / ".pravrudhi" / "credentials"

    def _path(self, provider_id: str) -> Path:
        return self._dir() / f"{provider_id}.key"

    def put(self, provider_id: str, key: str) -> None:
        if provider_id not in PROVIDERS:
            raise UnknownProviderError(f"unknown provider {provider_id!r}")
        if _is_inside_git_worktree(self._root):
            raise GitTrackedPathError(
                f"refusing to write credentials under {self._root} — it is inside a git work tree"
            )
        value = key.strip()
        if not value:
            raise ValueError("refusing to store an empty key")
        directory = self._dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = self._path(provider_id)
        # Open with the restrictive mode baked into the O_CREAT call itself, so the file is never briefly
        # world- or group-readable at the umask's default mode between creation and a later chmod.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(value + "\n")
        os.chmod(path, 0o600)

    def get(self, provider_id: str) -> Secret | None:
        path = self._path(provider_id)
        if not path.exists():
            return None
        value = path.read_text().strip()
        if not value:
            return None
        return Secret(provider=provider_id, value=value)

    def delete(self, provider_id: str) -> bool:
        path = self._path(provider_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def configured(self) -> list[str]:
        directory = self._dir()
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.key"))


Probe = Callable[[str, dict[str, str]], httpx.Response]


def _default_probe(url: str, headers: dict[str, str]) -> httpx.Response:
    return httpx.get(url, headers=headers, timeout=10.0)


def _probe_request(provider: Provider, base: str, key: str) -> tuple[str, dict[str, str]]:
    segment = f"/{provider.probe_model}" if provider.probe_model else ""
    if provider.id == "google":
        return f"{base}/models{segment}?key={key}", {}
    if provider.id == "anthropic":
        return f"{base}/models{segment}", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return f"{base}/models{segment}", {"Authorization": f"Bearer {key}"}


def validate(
    provider_id: str, key: str, *, base_url: str | None = None, probe: Probe = _default_probe
) -> tuple[bool, str]:
    """A cheap probe call to `provider_id`'s models endpoint. Never puts `key` in the returned reason."""
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        return False, f"unknown provider {provider_id!r}"
    base = (base_url or provider.base_url).rstrip("/")
    if not base:
        return False, "no base_url configured for this provider; pass one explicitly"
    url, headers = _probe_request(provider, base, key)
    try:
        response = probe(url, headers)
    except Exception as exc:  # noqa: BLE001 — any transport failure is a rejection, not a crash
        return False, redact(f"probe failed: {exc}")
    if response.status_code == 200:
        return True, "ok"
    if response.status_code in (401, 403):
        return False, "key rejected by provider"
    return False, redact(f"probe returned status {response.status_code}")


class CredentialBoundaryError(RuntimeError):
    """Refusing to hand a signed-in user a store rooted at the engine's own project."""


def store_for(root: Path, user: User | None) -> CredentialStore:
    """The credential store for this request, for callers that hold only the engine's root.

    Prefer `store_for_project`, which knows whose project the request is about. This remains for the local,
    single-operator path where there is no signed-in user and the engine's root is the only project there is.
    """
    if user is not None:
        raise NotImplementedError(
            "a signed-in caller needs `store_for_project`, which resolves their own workspace"
        )
    return FileCredentialStore(root)


def store_for_project(
    project_root: Path, *, engine_root: Path, user: User | None
) -> CredentialStore:
    """The credential store for one project, refusing to give a user the operator's.

    The operator's keys live in the engine's project; a user's live in their workspace, which is a complete
    project root of its own, so this is the same file store either way — the same 0600 files and the same
    refusal to write inside a git work tree.

    What this adds is the boundary. The engine holds working keys for several providers, and a signed-in user
    who reached them could run the operator's account to its limit from a machine the operator does not control.
    That mistake is one wrong argument at a call site, so it is refused here, where both roots are visible, and
    the paths are resolved first because `engine/../engine` is the engine and a string comparison would let it
    through.
    """
    here, engine = Path(project_root).resolve(), Path(engine_root).resolve()
    if user is not None and here == engine:
        raise CredentialBoundaryError(
            "A signed-in user's provider keys live in their own workspace, not in the engine's project."
        )
    return FileCredentialStore(here)
