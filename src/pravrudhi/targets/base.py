"""The Target protocol (spec §3).

A Target declares what may change and how a change is evaluated; it never scores.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from pravrudhi_kernel.sandbox import JobSpec
from pravrudhi_kernel.schema import Candidate


class Surface(BaseModel):
    """One thing a target lets a night change: its name, the strategies that may change it and the execution families
    they run under.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str  # e.g. W3.adapter
    strategies: list[str]
    execution_families: list[str]


class Artefact(BaseModel):
    """What a candidate is materialised into: the kind (baseline, adapter or harness), the folder on the host and a
    content hash.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str  # baseline | adapter | harness
    path: str | None  # host path of the adapter/harness dir; None for the unmodified baseline
    content_hash: str


class CanarySpec(BaseModel):
    """A pre-registered check a candidate must still pass: its name, its margin and the direction in which the margin is
    read.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    margin: float = Field(gt=0.0)
    direction: str  # "non_inferior" (score must not drop by margin) | "ratio_floor" | "abs_increase_cap"


class Target(Protocol):
    """What a harness exposes so a night can improve it: its surfaces, baseline, how a candidate is materialised, any
    training job and the canaries. The one extension point for new harnesses.
    """
    name: str

    def surfaces(self) -> list[Surface]: ...
    def baseline(self) -> Artefact: ...
    def materialise(self, cand: Candidate, recipe: dict[str, object], work: Path) -> Path: ...
    def train_job(self, work: Path, recipe: dict[str, object], resources: dict[str, str]) -> JobSpec | None: ...
    def canaries(self) -> list[CanarySpec]: ...
