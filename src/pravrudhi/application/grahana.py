"""Grahaṇa: the cow's first stomach — cheap, bounded grazing over what is already readable from this host.

The operator's direction was "consume everything possible but bring it back and slowly digest it — breadth
first and depth later." Grazing is the breadth half. It never evaluates anything: it walks a small, declared
set of local sources, records what it finds as an `IntakeItem` with an honest (never fabricated) relevance
score, and stops well inside a bounded budget. `application/manthana.py` is the digestion half — deciding what
a grazed item is worth and running the one real trial that can promote it.

Grazing NEVER launches inference, installs a package, executes a repository script, pulls model weights,
downloads a dataset body, or opens sealed evaluation content. Every source scanner in this module reads only
small, already-local metadata: this repository's packaged tool/recipe catalogues (`application/tools.py`,
`application/recipes.py`), `SKILL.md` frontmatter under a skills directory, and the directory names plus the
tiny `refs/main` text file inside a Hugging Face hub cache — never a model's weight files. Network sources
(GitHub, Hugging Face's API, arXiv, PyPI) are declared in `assets/configs/intake.yaml` with `status:
unconfigured` and their host; nothing in this module ever opens a socket.

Design doc §6.1 calls for a holding-store database (`grahana_store.py` owning a sqlite catalogue at
`.pravrudhi/intake/catalogue.sqlite3`). This module keeps that ownership but stores the catalogue as one
JSON-Lines file, `.pravrudhi/intake/items.jsonl` (one item per line, the file rewritten atomically on every
save) — the same choice this codebase already makes for every other small mutable store (`application/
kshudha.py`'s `appetite.json`, `application/requests.py`), and JSONL rather than a single JSON blob only
because a catalogue is naturally one record per line. A corrupt or unparseable line is skipped rather than
failing the whole load: this store holds untrusted, reversible intake material, not the ledger's evidentiary
record, so the request-journal's "a malformed record is a visible integrity failure" rule does not apply here.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application import recipes, tools

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "intake.yaml"

KINDS: tuple[str, ...] = ("model", "paper", "dataset", "tool", "benchmark", "technique")
STATES: tuple[str, ...] = (
    "grazed", "shortlisted", "trial_planned", "trial_running", "ruminated",
    "deferred", "quarantined", "forgotten",
)

# States design §6.4 forbids evicting: an active or admitted trial's evidence, and anything linked to an open
# request or capability gap (checked separately via `IntakeItem.linked_requirements`).
PINNED_STATES: frozenset[str] = frozenset({"trial_planned", "trial_running", "ruminated"})


class GrahanaError(ValueError):
    """A packaged/override config or a stored item violates its own declared shape."""


def _iso(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# --------------------------------------------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class SourceConfig:
    id: str
    kind: str  # "local" | "network"
    status: str  # free text, e.g. "local" or "unconfigured"; reported back, never used to gate correctness
    enabled: bool
    path: str  # local sources: a filesystem path ("" means the reading module's own packaged default)
    host: str  # network sources: a hostname, declared but never contacted by this module


@dataclass(frozen=True)
class IntakeConfig:
    policy_version: str = "1"
    relevance_weights: dict[str, float] = field(
        default_factory=lambda: {"need": 0.4, "fit": 0.2, "novelty": 0.2, "provenance": 0.2}
    )
    unknown_penalty: float = 0.1
    collector_items_per_source: int = 20
    collector_bytes_per_source: int = 262144
    collector_wall_s: float = 30.0
    excerpt_max_chars: int = 600
    forget_max_items: int = 500
    forget_max_tombstones: int = 200
    forget_relevance_floor: float = 0.2
    forget_default_revisit_days: float = 30.0
    sources: dict[str, SourceConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        needed = {"need", "fit", "novelty", "provenance"}
        got = set(self.relevance_weights)
        if got != needed:
            raise GrahanaError(f"relevance_weights must declare exactly {sorted(needed)}, got {sorted(got)}")
        if any(w < 0 for w in self.relevance_weights.values()):
            raise GrahanaError("relevance_weights must be nonnegative")
        total = sum(self.relevance_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise GrahanaError(f"relevance_weights must sum to 1.0, got {total}")


_DEFAULT_WEIGHTS = {"need": 0.4, "fit": 0.2, "novelty": 0.2, "provenance": 0.2}


def load_config(path: Path | None = None) -> IntakeConfig:
    raw: dict[str, Any] = yaml.safe_load((path or PACKAGED_CONFIG).read_text(encoding="utf-8")) or {}
    weights_raw = raw.get("relevance_weights") or {}
    sources_raw = raw.get("sources") or {}
    sources = {
        str(sid): SourceConfig(
            id=str(sid),
            kind=str(s.get("kind") or "local"),
            status=str(s.get("status") or ("local" if (s.get("kind") or "local") == "local" else "unconfigured")),
            enabled=bool(s.get("enabled", True)),
            path=str(s.get("path") or ""),
            host=str(s.get("host") or ""),
        )
        for sid, s in sources_raw.items()
    }
    return IntakeConfig(
        policy_version=str(raw.get("policy_version") or "1"),
        relevance_weights=({str(k): float(v) for k, v in weights_raw.items()} or dict(_DEFAULT_WEIGHTS)),
        unknown_penalty=float(raw.get("unknown_penalty", 0.1)),
        collector_items_per_source=int(raw.get("collector_items_per_source", 20)),
        collector_bytes_per_source=int(raw.get("collector_bytes_per_source", 262144)),
        collector_wall_s=float(raw.get("collector_wall_s", 30.0)),
        excerpt_max_chars=int(raw.get("excerpt_max_chars", 600)),
        forget_max_items=int(raw.get("forget_max_items", 500)),
        forget_max_tombstones=int(raw.get("forget_max_tombstones", 200)),
        forget_relevance_floor=float(raw.get("forget_relevance_floor", 0.2)),
        forget_default_revisit_days=float(raw.get("forget_default_revisit_days", 30.0)),
        sources=sources,
    )


# --------------------------------------------------------------------------------------------------------------
# RelevanceScore and IntakeItem


@dataclass(frozen=True)
class RelevanceScore:
    """G = w_need*N + w_fit*F + w_new*U + w_prov*P (design §6.1), every component and weight kept explicit."""

    need: float
    fit: float
    novelty: float
    provenance: float
    weight_need: float
    weight_fit: float
    weight_novelty: float
    weight_provenance: float
    total: float
    matched_requirement_ids: tuple[str, ...]
    unknown_components: tuple[str, ...]  # which of need/fit/novelty/provenance used unknown_penalty, not a real measurement

    def to_dict(self) -> dict[str, Any]:
        return {
            "need": self.need, "fit": self.fit, "novelty": self.novelty, "provenance": self.provenance,
            "weight_need": self.weight_need, "weight_fit": self.weight_fit,
            "weight_novelty": self.weight_novelty, "weight_provenance": self.weight_provenance,
            "total": self.total, "matched_requirement_ids": list(self.matched_requirement_ids),
            "unknown_components": list(self.unknown_components),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> RelevanceScore:
        return RelevanceScore(
            need=float(d["need"]), fit=float(d["fit"]), novelty=float(d["novelty"]),
            provenance=float(d["provenance"]), weight_need=float(d["weight_need"]),
            weight_fit=float(d["weight_fit"]), weight_novelty=float(d["weight_novelty"]),
            weight_provenance=float(d["weight_provenance"]), total=float(d["total"]),
            matched_requirement_ids=tuple(str(x) for x in d.get("matched_requirement_ids", ())),
            unknown_components=tuple(str(x) for x in d.get("unknown_components", ())),
        )


def compute_relevance(
    *, item_tags: frozenset[str], requirements: Mapping[str, Sequence[str]], fit_hint: float | None,
    is_new_or_revised: bool, licence: str, cfg: IntakeConfig,
) -> RelevanceScore:
    """The four components of G, each either a real measurement or the config's stated `unknown_penalty` — never
    a fabricated value. `fit_hint=None` means platform/host compatibility was not measured this pass; an
    `licence` of `"unknown"` means no licence metadata was found. Neither is guessed at."""
    unknown: list[str] = []

    if requirements:
        matched = tuple(sorted(
            rid for rid, terms in requirements.items() if item_tags & {t.lower() for t in terms}
        ))
    else:
        matched = ()
    need = 1.0 if matched else 0.0  # zero linked requirements is a real "no demand signal", not missing metadata

    if fit_hint is None:
        fit = cfg.unknown_penalty
        unknown.append("fit")
    else:
        fit = _clip01(fit_hint)

    novelty = 1.0 if is_new_or_revised else 0.0

    if licence == "unknown":
        provenance = cfg.unknown_penalty
        unknown.append("provenance")
    else:
        provenance = 1.0

    w = cfg.relevance_weights
    total = w["need"] * need + w["fit"] * fit + w["novelty"] * novelty + w["provenance"] * provenance
    return RelevanceScore(
        need=need, fit=fit, novelty=novelty, provenance=provenance,
        weight_need=w["need"], weight_fit=w["fit"], weight_novelty=w["novelty"], weight_provenance=w["provenance"],
        total=total, matched_requirement_ids=matched, unknown_components=tuple(unknown),
    )


@dataclass(frozen=True)
class IntakeItem:
    item_id: str  # stable source identity: "<source_id>:<item-within-source key>"
    canonical_path: str
    source_revision: str  # "unknown" allowed
    first_seen: str
    last_seen: str
    content_hash: str
    kind: str
    licence: str  # "unknown" allowed
    excerpt: str  # bounded per config's excerpt_max_chars
    trust_class: str
    tags: tuple[str, ...]
    linked_requirements: tuple[str, ...]
    relevance: RelevanceScore
    duplication_cluster: str  # "" if this item shares its content_hash with no other item
    state: str
    revisit_at: str
    supersession_reason: str  # "" unless superseded

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise GrahanaError(f"item {self.item_id!r}: kind {self.kind!r} not in {KINDS}")
        if self.state not in STATES:
            raise GrahanaError(f"item {self.item_id!r}: state {self.state!r} not in {STATES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id, "canonical_path": self.canonical_path, "source_revision": self.source_revision,
            "first_seen": self.first_seen, "last_seen": self.last_seen, "content_hash": self.content_hash,
            "kind": self.kind, "licence": self.licence, "excerpt": self.excerpt, "trust_class": self.trust_class,
            "tags": list(self.tags), "linked_requirements": list(self.linked_requirements),
            "relevance": self.relevance.to_dict(), "duplication_cluster": self.duplication_cluster,
            "state": self.state, "revisit_at": self.revisit_at, "supersession_reason": self.supersession_reason,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> IntakeItem:
        return IntakeItem(
            item_id=str(d["item_id"]), canonical_path=str(d["canonical_path"]),
            source_revision=str(d["source_revision"]), first_seen=str(d["first_seen"]),
            last_seen=str(d["last_seen"]), content_hash=str(d["content_hash"]), kind=str(d["kind"]),
            licence=str(d["licence"]), excerpt=str(d["excerpt"]), trust_class=str(d["trust_class"]),
            tags=tuple(str(x) for x in d.get("tags", ())),
            linked_requirements=tuple(str(x) for x in d.get("linked_requirements", ())),
            relevance=RelevanceScore.from_dict(d["relevance"]), duplication_cluster=str(d["duplication_cluster"]),
            state=str(d["state"]), revisit_at=str(d["revisit_at"]),
            supersession_reason=str(d.get("supersession_reason", "")),
        )


# --------------------------------------------------------------------------------------------------------------
# The holding store: one JSONL file, rewritten atomically (design §3.1's grahana_store, §6.1).


def store_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "intake" / "items.jsonl"


def load_items(root: Path) -> dict[str, IntakeItem]:
    """Every stored item, keyed by `item_id`. A line that fails to parse is skipped, not fatal: this catalogue
    holds untrusted, reversible material, never the ledger's evidentiary record."""
    path = store_path(root)
    if not path.exists():
        return {}
    out: dict[str, IntakeItem] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            item = IntakeItem.from_dict(row) if isinstance(row, dict) else None
        except (GrahanaError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            item = None
        if item is not None:
            out[item.item_id] = item
    return out


def save_items(root: Path, items: Mapping[str, IntakeItem]) -> Path:
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(items[iid].to_dict(), sort_keys=True) for iid in sorted(items)]
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------------------------------------------
# Source scanners — each reads only small, already-local metadata and respects the per-source item/byte caps.


@dataclass(frozen=True)
class SourceScan:
    source_id: str
    items: tuple[IntakeItem, ...]
    bytes_read: int
    truncated: bool  # hit collector_items_per_source or collector_bytes_per_source before finishing this source


def _revisit(now: datetime, cfg: IntakeConfig) -> str:
    return _iso(now + timedelta(days=cfg.forget_default_revisit_days))


def _file_revision(path: Path) -> str:
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "unknown"


def _scan_repo_tools(
    source_id: str, source_cfg: SourceConfig, cfg: IntakeConfig, requirements: Mapping[str, Sequence[str]],
    now: datetime,
) -> SourceScan:
    """This repository's tool catalogue (`application/tools.py`). Grazing reads the packaged/overridden catalogue
    JSON directly (`tools.catalogue`, not `tools.availability`) — no `shutil.which`/env probing here, so a
    missing host-detection result is honestly `fit=unknown` rather than a cheap-pass side effect."""
    path = Path(source_cfg.path).expanduser() if source_cfg.path else tools.PACKAGED_CATALOGUE
    if not path.is_file():
        return SourceScan(source_id, (), 0, False)
    revision = _file_revision(path)
    rows = tools.catalogue(path)
    out: list[IntakeItem] = []
    bytes_read = 0
    truncated = False
    for t in rows:
        if len(out) >= cfg.collector_items_per_source:
            truncated = True
            break
        excerpt = f"{t.title}: {t.provides}"[: cfg.excerpt_max_chars]
        size = len(excerpt.encode("utf-8"))
        if bytes_read + size > cfg.collector_bytes_per_source:
            truncated = True
            break
        bytes_read += size
        tags = tuple(sorted({t.category.lower(), t.id.lower()} - {""}))
        relevance = compute_relevance(
            item_tags=frozenset(tags), requirements=requirements, fit_hint=None,
            is_new_or_revised=True, licence="unknown", cfg=cfg,
        )
        out.append(IntakeItem(
            item_id=f"{source_id}:{t.id}", canonical_path=f"pravrudhi:tools/{t.id}", source_revision=revision,
            first_seen=_iso(now), last_seen=_iso(now), content_hash=_hash(t.id, t.title, t.provides), kind="tool",
            licence="unknown", excerpt=excerpt, trust_class="local_catalogue", tags=tags,
            linked_requirements=relevance.matched_requirement_ids, relevance=relevance, duplication_cluster="",
            state="grazed", revisit_at=_revisit(now, cfg), supersession_reason="",
        ))
    return SourceScan(source_id, tuple(out), bytes_read, truncated)


def _scan_repo_recipes(
    source_id: str, source_cfg: SourceConfig, cfg: IntakeConfig, requirements: Mapping[str, Sequence[str]],
    now: datetime,
) -> SourceScan:
    """This repository's recipe library (`application/recipes.py`), read directly (`recipes.library`, not
    `recipes.availability`) for the same cheap-pass reason as `_scan_repo_tools`."""
    path = Path(source_cfg.path).expanduser() if source_cfg.path else recipes.PACKAGED_LIBRARY
    if not path.is_file():
        return SourceScan(source_id, (), 0, False)
    revision = _file_revision(path)
    rows = recipes.library(path)
    out: list[IntakeItem] = []
    bytes_read = 0
    truncated = False
    for r in rows:
        if len(out) >= cfg.collector_items_per_source:
            truncated = True
            break
        excerpt = f"{r.title}: {r.summary}"[: cfg.excerpt_max_chars]
        size = len(excerpt.encode("utf-8"))
        if bytes_read + size > cfg.collector_bytes_per_source:
            truncated = True
            break
        bytes_read += size
        tags = tuple(sorted({r.capability.lower(), r.id.lower()} - {""}))
        relevance = compute_relevance(
            item_tags=frozenset(tags), requirements=requirements, fit_hint=None,
            is_new_or_revised=True, licence="unknown", cfg=cfg,
        )
        out.append(IntakeItem(
            item_id=f"{source_id}:{r.id}", canonical_path=f"pravrudhi:recipes/{r.id}", source_revision=revision,
            first_seen=_iso(now), last_seen=_iso(now), content_hash=_hash(r.id, r.title, r.summary),
            kind="technique", licence="unknown", excerpt=excerpt, trust_class="local_catalogue", tags=tags,
            linked_requirements=relevance.matched_requirement_ids, relevance=relevance, duplication_cluster="",
            state="grazed", revisit_at=_revisit(now, cfg), supersession_reason="",
        ))
    return SourceScan(source_id, tuple(out), bytes_read, truncated)


def _read_skill_frontmatter(skill_md: Path, max_bytes: int) -> dict[str, Any]:
    """The YAML frontmatter of a `SKILL.md` (`---\\n...\\n---`), never the body — this is metadata scanning, not
    reading the skill's instructions as if they were data to act on."""
    text = skill_md.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _scan_skill_dir(
    source_id: str, source_cfg: SourceConfig, cfg: IntakeConfig, requirements: Mapping[str, Sequence[str]],
    now: datetime,
) -> SourceScan:
    """`~/.claude/skills` or `~/.codex/skills` frontmatter (design §6.2). A skill already installed here is, by
    definition, compatible with this host, so `fit_hint=1.0` is a real measurement, not a guess."""
    root_dir = Path(source_cfg.path).expanduser()
    if not root_dir.is_dir():
        return SourceScan(source_id, (), 0, False)
    out: list[IntakeItem] = []
    bytes_read = 0
    truncated = False
    for skill_dir in sorted(p for p in root_dir.iterdir() if p.is_dir()):
        if len(out) >= cfg.collector_items_per_source:
            truncated = True
            break
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        size = skill_md.stat().st_size
        if bytes_read + size > cfg.collector_bytes_per_source:
            truncated = True
            break
        fm = _read_skill_frontmatter(skill_md, cfg.excerpt_max_chars * 4)
        bytes_read += size
        name = str(fm.get("name") or skill_dir.name)
        description = str(fm.get("description") or "")
        licence = str(fm.get("license") or fm.get("licence") or "unknown")
        raw_tags = fm.get("tags")
        extra_tags = [str(t).lower() for t in raw_tags] if isinstance(raw_tags, list) else []
        tags = tuple(sorted({name.lower(), *extra_tags} - {""}))
        excerpt = description[: cfg.excerpt_max_chars]
        content_hash = _hash(name, description, licence)
        relevance = compute_relevance(
            item_tags=frozenset(tags), requirements=requirements, fit_hint=1.0,
            is_new_or_revised=True, licence=licence, cfg=cfg,
        )
        out.append(IntakeItem(
            item_id=f"{source_id}:{skill_dir.name}", canonical_path=str(skill_md),
            source_revision=_file_revision(skill_md), first_seen=_iso(now), last_seen=_iso(now),
            content_hash=content_hash, kind="tool", licence=licence, excerpt=excerpt,
            trust_class="local_skill_frontmatter", tags=tags, linked_requirements=relevance.matched_requirement_ids,
            relevance=relevance, duplication_cluster="", state="grazed", revisit_at=_revisit(now, cfg),
            supersession_reason="",
        ))
    return SourceScan(source_id, tuple(out), bytes_read, truncated)


def _scan_hf_hub(
    source_id: str, source_cfg: SourceConfig, cfg: IntakeConfig, requirements: Mapping[str, Sequence[str]],
    now: datetime,
) -> SourceScan:
    """`~/.cache/huggingface/hub` cache directory names plus each entry's tiny `refs/main` text file — never a
    model's weight files (design §6.2: existence of the cache says nothing about which usable weights it has)."""
    root_dir = Path(source_cfg.path).expanduser()
    if not root_dir.is_dir():
        return SourceScan(source_id, (), 0, False)
    out: list[IntakeItem] = []
    bytes_read = 0
    truncated = False
    for entry in sorted(p for p in root_dir.iterdir() if p.is_dir()):
        if len(out) >= cfg.collector_items_per_source:
            truncated = True
            break
        name = entry.name
        if name.startswith("models--"):
            kind = "model"
        elif name.startswith("datasets--"):
            kind = "dataset"
        else:
            continue  # e.g. "spaces--*": no matching kind in KINDS, so skip rather than invent one
        ref_file = entry / "refs" / "main"
        size = ref_file.stat().st_size if ref_file.is_file() else 0
        if bytes_read + size > cfg.collector_bytes_per_source:
            truncated = True
            break
        revision = ref_file.read_text(encoding="utf-8", errors="replace").strip() if ref_file.is_file() else "unknown"
        bytes_read += size
        repo_id = name.split("--", 1)[1].replace("--", "/") if "--" in name else name
        tags = tuple(sorted({kind, *repo_id.lower().split("/")} - {""}))
        excerpt = f"{kind} cache entry {repo_id} at revision {revision}"[: cfg.excerpt_max_chars]
        relevance = compute_relevance(
            item_tags=frozenset(tags), requirements=requirements, fit_hint=1.0,
            is_new_or_revised=True, licence="unknown", cfg=cfg,
        )
        out.append(IntakeItem(
            item_id=f"{source_id}:{name}", canonical_path=str(entry), source_revision=revision,
            first_seen=_iso(now), last_seen=_iso(now), content_hash=_hash(name, revision), kind=kind,
            licence="unknown", excerpt=excerpt, trust_class="local_hf_cache_metadata", tags=tags,
            linked_requirements=relevance.matched_requirement_ids, relevance=relevance, duplication_cluster="",
            state="grazed", revisit_at=_revisit(now, cfg), supersession_reason="",
        ))
    return SourceScan(source_id, tuple(out), bytes_read, truncated)


_SCANNERS = {
    "repo_tools": _scan_repo_tools,
    "repo_recipes": _scan_repo_recipes,
    "claude_skills": _scan_skill_dir,
    "codex_skills": _scan_skill_dir,
    "hf_hub": _scan_hf_hub,
}


# --------------------------------------------------------------------------------------------------------------
# graze()


@dataclass(frozen=True)
class GrazeResult:
    items_added: int
    items_updated: int
    items_deduplicated: int
    sources_scanned: tuple[str, ...]
    sources_skipped: tuple[str, ...]  # "<source_id>: <reason>"
    sources_truncated: tuple[str, ...]
    bytes_read: int
    elapsed_s: float


def _assign_duplication_clusters(items: dict[str, IntakeItem]) -> None:
    """Independent sources reporting the same `content_hash` become linked mirrors, never merged into one item
    (design §6.1: "keep independent sources as separate provenance, even when their abstracts match")."""
    by_hash: dict[str, list[str]] = {}
    for iid, item in items.items():
        by_hash.setdefault(item.content_hash, []).append(iid)
    for h, ids in by_hash.items():
        cluster = h if len(ids) > 1 else ""
        for iid in ids:
            if items[iid].duplication_cluster != cluster:
                items[iid] = replace(items[iid], duplication_cluster=cluster)


def graze(
    root: Path, *, sources: Sequence[str] | None = None, budget: IntakeConfig | None = None,
    requirements: Mapping[str, Sequence[str]] | None = None, now: datetime | None = None,
) -> GrazeResult:
    """One bounded grazing pass (design §6.1): scan the requested (or all enabled local) sources, merge freshly
    scanned items into the holding store, and stop the moment the wall-clock budget is spent. Never raises for a
    missing or unreadable source directory — that source simply contributes nothing and is recorded as scanned
    with zero items; only bad configuration (`GrahanaError`) can stop the whole pass."""
    cfg = budget or load_config()
    root = Path(root)
    reqs = requirements or {}
    as_of = now or datetime.now(UTC)
    started = time.monotonic()

    items = load_items(root)
    wanted = list(sources) if sources is not None else [
        sid for sid, s in cfg.sources.items() if s.kind == "local" and s.enabled
    ]

    added = updated = deduplicated = 0
    bytes_read = 0
    scanned: list[str] = []
    skipped: list[str] = []
    truncated_sources: list[str] = []

    for source_id in wanted:
        src = cfg.sources.get(source_id)
        if src is None:
            skipped.append(f"{source_id}: unknown source")
            continue
        if src.kind != "local":
            skipped.append(f"{source_id}: unconfigured network adapter (host={src.host or 'unknown'})")
            continue
        if not src.enabled:
            skipped.append(f"{source_id}: disabled in policy")
            continue
        if time.monotonic() - started >= cfg.collector_wall_s:
            skipped.append(f"{source_id}: wall budget exhausted")
            continue
        scanner = _SCANNERS.get(source_id)
        if scanner is None:
            skipped.append(f"{source_id}: no scanner implemented")
            continue
        try:
            scan = scanner(source_id, src, cfg, reqs, as_of)
        except OSError as exc:
            skipped.append(f"{source_id}: {exc}")
            continue

        scanned.append(source_id)
        bytes_read += scan.bytes_read
        if scan.truncated:
            truncated_sources.append(source_id)
        for item in scan.items:
            prior = items.get(item.item_id)
            if prior is not None and prior.source_revision == item.source_revision and prior.content_hash == item.content_hash:
                items[item.item_id] = replace(prior, last_seen=item.last_seen)
                deduplicated += 1
                continue
            if prior is not None:
                updated += 1
            else:
                added += 1
            items[item.item_id] = item

    _assign_duplication_clusters(items)
    save_items(root, items)
    return GrazeResult(
        items_added=added, items_updated=updated, items_deduplicated=deduplicated,
        sources_scanned=tuple(scanned), sources_skipped=tuple(skipped), sources_truncated=tuple(truncated_sources),
        bytes_read=bytes_read, elapsed_s=time.monotonic() - started,
    )


# --------------------------------------------------------------------------------------------------------------
# forget() — design §6.4's bounded intake rule. Owned here because the holding store is owned here.


@dataclass(frozen=True)
class ForgetResult:
    tombstoned: tuple[str, ...]  # item_id, still present in the store as a compact `state == "forgotten"` record
    dropped: tuple[str, ...]  # item_id, removed from the store entirely (a tombstone that exceeded its own cap)
    kept: int


def _tombstone(item: IntakeItem, reason: str) -> IntakeItem:
    """A compact record — identity, revision, hash, reason, revisit condition — with the bulk excerpt/tags
    dropped (design §6.4), so forgetting does not create an endless rediscovery loop for the same rejected item."""
    return replace(item, state="forgotten", excerpt="", tags=(), supersession_reason=reason)


def forget(root: Path, now: datetime, *, config: IntakeConfig | None = None) -> ForgetResult:
    """Evict what design §6.4 says to evict, in order, and nothing else: never an item linked to an open
    request/gap, never one in an active or admitted trial state (`PINNED_STATES`), never by touching the store
    at all when nothing is over its bound."""
    cfg = config or load_config()
    items = load_items(root)
    pinned = {iid for iid, i in items.items() if i.linked_requirements or i.state in PINNED_STATES}

    tombstoned: list[str] = []

    for iid, item in list(items.items()):
        if iid in pinned or item.state == "forgotten":
            continue
        revisit = _parse_iso(item.revisit_at)
        if revisit is not None and revisit <= now and item.relevance.total < cfg.forget_relevance_floor:
            items[iid] = _tombstone(item, "expired past its revisit time with low relevance")
            tombstoned.append(iid)

    over = len(items) - cfg.forget_max_items
    if over > 0:
        live_unpinned = sorted(
            (
                (iid, i) for iid, i in items.items()
                if iid not in pinned and i.state != "forgotten"
            ),
            key=lambda kv: (kv[1].relevance.total, kv[1].last_seen, kv[0]),
        )
        for iid, item in live_unpinned[:over]:
            items[iid] = _tombstone(item, "evicted to hold the store under its item cap")
            tombstoned.append(iid)

    dropped: list[str] = []
    tombstones = sorted(
        ((iid, i) for iid, i in items.items() if i.state == "forgotten"),
        key=lambda kv: kv[1].last_seen,
    )
    excess = len(tombstones) - cfg.forget_max_tombstones
    if excess > 0:
        for iid, _ in tombstones[:excess]:
            del items[iid]
            dropped.append(iid)

    if tombstoned or dropped:
        save_items(root, items)
    return ForgetResult(tombstoned=tuple(tombstoned), dropped=tuple(dropped), kept=len(items))


__all__ = [
    "PACKAGED_CONFIG", "KINDS", "STATES", "PINNED_STATES", "GrahanaError", "SourceConfig", "IntakeConfig",
    "load_config", "RelevanceScore", "compute_relevance", "IntakeItem", "store_path", "load_items", "save_items",
    "SourceScan", "GrazeResult", "graze", "ForgetResult", "forget",
]
