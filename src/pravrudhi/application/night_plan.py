"""Which night to run: the pre-registration, the noise floor that goes with it, and the pool they agree on.

`night.py` read `research/prereg/lora_night.yaml` and `research/prereg/variance.json` unconditionally, so the
engine could run exactly one track however many objectives a workspace held. Worse, nothing checked that the
floor had been measured on the pool the config names: both files are valid on their own, so a mismatched pair
sets the boundary from another pool's sigma and every promotion that night is unreproducible without anything
going red. ADR-0025 records what that costs when it happens to a committed evidence document.

Resolution runs from the pre-registration side rather than from the objective. The prereg is the frozen
artefact, it already names its own bench and floor, and an objective is the user's statement of intent that
should not have to carry a studio file path. So a prereg claims an objective with `objective: <id>`, and this
module finds it. `lora_night.yaml` claims none, which is what makes it the default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = "lora_night.yaml"
DEFAULT_VARIANCE = "variance.json"


class NightPlanError(ValueError):
    """A night that cannot say which evidence it is using is not a night."""


@dataclass(frozen=True)
class NightPlan:
    """One night's frozen inputs, resolved and checked against each other."""

    config: Path
    variance: Path
    cfg: dict[str, Any]
    floor: dict[str, Any]
    objective: str | None
    track: str
    bench: str
    train_corpus: Path | None

    @property
    def answer_kind(self) -> str:
        """How the bench's answers are read. Absent means numeric, matching the kernel's own default."""
        return str(self.cfg.get("answer_kind") or "numeric")


def _prereg(root: Path) -> Path:
    return Path(root) / "research" / "prereg"


def _load_cfg(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise NightPlanError(f"no pre-registration at {path}")
    body = yaml.safe_load(path.read_text())
    if not isinstance(body, dict):
        raise NightPlanError(f"{path} is not a pre-registration object")
    return body


def _for_objective(root: Path, objective: str) -> Path:
    claimants = [
        path
        for path in sorted(_prereg(root).glob("*.yaml"))
        if str((yaml.safe_load(path.read_text()) or {}).get("objective") or "") == objective
    ]
    if not claimants:
        raise NightPlanError(
            f"no pre-registration in {_prereg(root)} claims objective {objective!r}; add "
            f"`objective: {objective}` to the frozen night config that should run it"
        )
    if len(claimants) > 1:
        names = ", ".join(p.name for p in claimants)
        raise NightPlanError(f"{names} all claim objective {objective!r}; exactly one may")
    return claimants[0]


def resolve(root: Path, *, objective: str | None = None, config: Path | None = None) -> NightPlan:
    """The night's inputs. With neither argument this is the model track, unchanged.

    The bench check is the point of the exercise: a config and a floor are only a pair if the floor was
    measured on the bench the config names.
    """
    if objective and config:
        raise NightPlanError("name an objective or a config, not both")
    root = Path(root)
    path = Path(config) if config else (_for_objective(root, objective) if objective else _prereg(root) / DEFAULT_CONFIG)
    cfg = _load_cfg(path)
    bench = str(cfg.get("bench") or "")
    if not bench:
        raise NightPlanError(f"{path.name} names no bench, so nothing can be drawn or scored")

    declared = str(cfg.get("noise_floor") or "")
    variance = (root / declared) if declared else (_prereg(root) / DEFAULT_VARIANCE)
    if not variance.exists():
        # The two tracks measure their floors with different commands, and a config knows which it is. The
        # single model-track suggestion sent a reader of `harness_night.yaml` to `study noise-floor --bench`,
        # which measures the trainee rather than the harness and would write a floor for the wrong thing.
        track = str(cfg.get("track") or "lora")
        how = (
            f"pravrudhi study harness-noise-floor --config {path}"
            if track.startswith("harness")
            else f"pravrudhi study noise-floor --bench {bench} --out {variance}"
        )
        raise NightPlanError(
            f"{path.name} needs the noise floor at {variance}, which does not exist; measure it with "
            f"`{how}` before running a night on it"
        )
    floor = json.loads(variance.read_text())
    measured = str(floor.get("bench") or "")
    if measured != bench:
        raise NightPlanError(
            f"{path.name} runs bench {bench!r} but {variance.name} was measured on bench {measured!r}; "
            "the boundary would be set from another pool's sigma and the night would not be reproducible"
        )
    # The corpus rejection sampling draws from, when the config names one. `run_night` took this from the
    # CLI, whose default is the GSM8K training parquet -- so a choice track would have sampled numeric rows
    # and handed `steps\n#### 18` to `mmlu.gold_answer`, which refuses anything but an option letter. None
    # means the caller's own default stands, which is what keeps the model track unchanged.
    declared_corpus = str((cfg.get("training") or {}).get("corpus") or "")
    return NightPlan(
        config=path,
        variance=variance,
        cfg=cfg,
        floor=floor,
        objective=str(cfg.get("objective")) if cfg.get("objective") else None,
        track=str(cfg.get("track") or "lora"),
        bench=bench,
        train_corpus=(root / declared_corpus) if declared_corpus else None,
    )
