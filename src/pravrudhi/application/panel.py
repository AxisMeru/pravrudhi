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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


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
    note: str = ""

    def key(self) -> str | None:
        """The credential, from the environment or from the 0600 file this project stores keys in.

        Both, in that order, because bring-your-own-key is an environment variable when the product is in
        someone else's hands, and a mode-0600 file under `~/.config` on the operator's own machine -- which
        is where `DASHSCOPE_API_KEY` already lives. Reading only the environment reported a configured key as
        missing.
        """
        if not self.credential:
            return None
        from_env = os.environ.get(self.credential)
        if from_env:
            return from_env
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
        """Whether this vendor can be asked right now, and why not if it cannot."""
        if self.interface == "cli":
            import shutil

            exe = self.model.split(":")[0]
            return (True, "ready") if shutil.which(exe) else (False, f"{exe} not on PATH")
        if self.interface == "openai_compat" and self.credential:
            if os.environ.get(self.credential):
                return True, "key in environment"
            if self.key():
                return True, f"key in {self.credential_file}"
            where = f" or {self.credential_file}" if self.credential_file else ""
            return False, f"{self.credential} is not set in the environment{where} (bring your own key)"
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


# Reachable today. `claude` and `codex` are agentic CLIs in print mode; the operator's judgement is that this
# does not change the verdict, and `interface` records it either way.
_CLI = {"temperature": 0.0, "max_tokens": 2048}

# Declared before any key exists, on the operator's instruction, so that when a key lands nothing has to be
# designed under time pressure. Each names the variable it reads. `temperature` is pinned on every one of them
# because an unpinned sampler makes a comparison unrepeatable.
_API = {"temperature": 0.0, "max_tokens": 2048, "top_p": 1.0, "seed": 0}

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
        id="qwen-dashscope", interface="openai_compat", model="qwen3-coder-plus",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        credential="DASHSCOPE_API_KEY", credential_file="~/.config/llm/dashscope.env", params=dict(_API),
        note="the operator's Singapore credential; the file it comes from picks the endpoint",
    ),
    "anthropic-api": Vendor(
        id="anthropic-api", interface="openai_compat", model="claude-opus-5",
        base_url="https://api.anthropic.com/v1", credential="ANTHROPIC_API_KEY", params=dict(_API),
        note="built, unrunnable until a key exists; not a show stopper for A1.1",
    ),
    "openai-api": Vendor(
        id="openai-api", interface="openai_compat", model="gpt-5",
        base_url="https://api.openai.com/v1", credential="OPENAI_API_KEY", params=dict(_API),
        note="built, unrunnable until a key exists",
    ),
    "google-api": Vendor(
        id="google-api", interface="openai_compat", model="gemini-3-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        credential="GOOGLE_API_KEY", params=dict(_API),
        note="built, unrunnable until a key exists",
    ),
    "glm-local": Vendor(
        id="glm-local", interface="local_gguf",
        model=str(Path.home() / ".cache/huggingface/hub/models--ggml-org--GLM-4.7-Flash-GGUF"),
        params={"temperature": 0.0, "max_tokens": 2048, "seed": 0},
        note="local weights: deterministic, no network, no marginal cost",
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


def ask_vendor(vendor: Vendor, prompt: str) -> Answer:
    """Ask one vendor one prompt. Raises on transport failure; `run_panel` turns that into a recorded gap."""
    if vendor.interface == "cli":
        from pravrudhi.agents.cli_agents import _run

        if vendor.model == "claude":
            cmd = ["claude", "-p", prompt, "--output-format", "text"]
        else:
            cmd = ["codex", "exec", "--skip-git-repo-check", prompt]
        code, out, err, wall = _run(cmd, Path.cwd(), int(vendor.params.get("timeout_s", 900)))
        if code != 0:
            raise RuntimeError((err or out or f"{vendor.model} exited {code}")[-400:])
        return Answer(vendor.id, vendor.interface, vendor.model, "", out.strip(), wall, None, None)

    if vendor.interface == "openai_compat":
        from pravrudhi.models.openai_compat import ChatClient

        key = vendor.key()
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
                "credential_env": v.credential or None, "note": v.note,
                "reachable": v.reachable[0], "reachable_detail": v.reachable[1],
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
                    row = Answer(vendor.id, vendor.interface, vendor.model, pid, answer.text,
                                 answer.wall_s, answer.tokens, None)
                except Exception as exc:  # noqa: BLE001 - a vendor that cannot answer is data, not a crash
                    row = Answer(vendor.id, vendor.interface, vendor.model, pid, "", 0.0, None, str(exc)[:400])
                answers.append(row)
                fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")
                fh.flush()
    return answers
