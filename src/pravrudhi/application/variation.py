"""Archive variation without changing the kernel's live pool or statistical boundary.

Call ``generate`` with the kernel's diff blocklist and a run budget, then append
returned payloads as propose rows. The default target is twice the run budget.
The nightly caller must opt in; importing this module does not write a ledger.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from pravrudhi.targets.harness_grammar import HarnessRecipe, parse_harness
from pravrudhi.targets.lora_grammar import LoraRecipe, parse_recipe

from .archive import parent_map

Recipe = LoraRecipe | HarnessRecipe
Operator = Literal["mutation", "block_crossover", "field_crossover"]


@dataclass(frozen=True)
class Parent:
    candidate_id: str
    recipe: Recipe


@dataclass
class Counts:
    attempts: int = 0
    invalid: int = 0
    duplicate: int = 0
    blocked: int = 0
    accepted: int = 0

    @property
    def rejection_rate(self) -> float:
        return (self.attempts - self.accepted) / self.attempts if self.attempts else 0.0


def canonical(recipe: Recipe) -> str:
    """Exactly the identity used by propose.py, including its JSON spacing."""
    return json.dumps(recipe.model_dump(exclude={"rationale"}), sort_keys=True)


def diff_hash(recipe: Recipe) -> str:
    return hashlib.sha256(canonical(recipe).encode()).hexdigest()


def load_archive(path: Path, *, harness: bool = False) -> list[Parent]:
    """Keep every valid proposed recipe, including later pruned/promoted parents."""
    parents = parent_map(path)
    recipes = {}
    if not path.exists():
        return []
    parse = parse_harness if harness else parse_recipe
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("kind") != "propose":
            continue
        cid = row.get("candidate_id")
        payload = row.get("payload")
        if not isinstance(cid, str) or cid not in parents or not isinstance(payload, dict):
            continue
        obj = payload.get("recipe")
        if isinstance(obj, dict):
            recipe = parse(obj)
            if not isinstance(recipe, str):
                recipes[cid] = Parent(cid, recipe)
    return list(recipes.values())


def _leaves(
    model: BaseModel, prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], FieldInfo, Any]]:
    for name, field in type(model).model_fields.items():
        if name == "rationale":
            continue
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            yield from _leaves(value, (*prefix, name))
        else:
            yield (*prefix, name), field, value


def _bounds(field: FieldInfo) -> tuple[float | None, float | None]:
    lo = next((m.ge for m in field.metadata if hasattr(m, "ge")), None)
    hi = next((m.le for m in field.metadata if hasattr(m, "le")), None)
    return lo, hi


def _put(obj: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for name in path[:-1]:
        obj = obj[name]
    obj[path[-1]] = value


def _draw(parent: Recipe, other: Recipe | None, operator: Operator, rng: random.Random) -> dict[str, Any]:
    obj = parent.model_dump()
    leaves = list(_leaves(parent))
    if operator == "mutation":
        # Free text has no numeric domain to resample. Inherit it unchanged;
        # harness prompt text can still be inherited by field crossover.
        choices = [(p, f, v) for p, f, v in leaves
                   if get_origin(f.annotation) is Literal or f.annotation is bool
                   or all(b is not None for b in _bounds(f))]
        path, field, old = rng.choice(choices)
        if get_origin(field.annotation) is Literal:
            value = rng.choice([v for v in get_args(field.annotation) if v != old])
        elif field.annotation is bool:
            value = not old
        else:
            lo, hi = _bounds(field)
            if lo is None or hi is None:
                # A numeric field with no declared range has no domain to resample within, and inventing one
                # would put the search outside the pre-registered grammar. Inherit it unchanged. This was a
                # crash rather than a judgement call before: the bounds were read straight into randint.
                return obj
            if path[-1] in {"lr", "alpha", "n_kept"}:
                value = math.exp(rng.uniform(math.log(lo), math.log(hi)))
                if field.annotation is int:
                    value = round(value)
            elif field.annotation is int:
                value = rng.randint(int(lo), int(hi))
            else:
                value = rng.uniform(lo, hi)
        _put(obj, path, value)
    elif operator == "block_crossover":
        assert other is not None, "a crossover needs two parents; mutation is the single-parent operator"
        for name in type(parent).model_fields:
            if name != "rationale" and rng.getrandbits(1):
                obj[name] = other.model_dump()[name]
    else:
        assert other is not None, "a crossover needs two parents; mutation is the single-parent operator"
        for path, _, _ in leaves:
            if rng.getrandbits(1):
                value = other.model_dump()
                for name in path:
                    value = value[name]
                _put(obj, path, value)
    return obj


@dataclass(frozen=True)
class Offspring:
    recipe: Recipe
    operator: Operator
    parents: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        key = canonical(self.recipe)
        return {
            "op": "harness" if isinstance(self.recipe, HarnessRecipe) else "adapter",
            "recipe": self.recipe.model_dump(), "strategy": self.recipe.strategy,
            "edit_family": self.recipe.execution_family,
            "vak": {"para": self.recipe.rationale, "pasyanti": key[:600]},
            "diff": {"sha256": diff_hash(self.recipe)},
            "cost_estimate": {"gpu_h": self.recipe.cost_est_gpu_h()},
            # archive.parent_map treats the last entry as nearest parent. Preserve
            # both direct parents separately: crossover is a DAG, not a chain.
            "lineage": [self.parents[0]], "variation_parents": list(self.parents),
            "variation_operator": self.operator,
        }


def vary(parent: Parent, *, operator: Operator = "mutation", other: Parent | None = None,
         rng: random.Random, seen: set[str] | None = None,
         blocklist: set[str] | frozenset[str] = frozenset(), counts: Counts | None = None) -> Offspring | None:
    """Try at most 32 raw draws, without repair; record all rejection causes.

    ``seen`` contains propose.py canonical JSON keys and is updated on acceptance.
    Parent identities are also excluded, so provenance alone never makes novelty.
    """
    if operator not in {"mutation", "block_crossover", "field_crossover"}:
        raise ValueError(f"unknown operator: {operator}")
    if operator != "mutation" and (other is None or type(other.recipe) is not type(parent.recipe)):
        raise ValueError("crossover requires two parents from the same grammar")
    counts = counts if counts is not None else Counts()
    seen = seen if seen is not None else set()
    assert parent is not None
    parent_keys = {canonical(parent.recipe)}
    ids: tuple[str, ...] = (parent.candidate_id,)
    if operator != "mutation":
        assert other is not None, "a crossover needs two parents"
        parent_keys.add(canonical(other.recipe))
        ids += (other.candidate_id,)
    rationale = "variation:" + operator + " parents=" + json.dumps(ids)
    if len(rationale) > 400:
        raise ValueError("parent provenance exceeds grammar rationale limit")
    parse = parse_harness if isinstance(parent.recipe, HarnessRecipe) else parse_recipe
    for _ in range(32):
        counts.attempts += 1
        obj = _draw(parent.recipe, other.recipe if other else None, operator, rng)
        obj["rationale"] = rationale
        recipe = parse(obj)
        if isinstance(recipe, str):
            counts.invalid += 1
            continue
        key = canonical(recipe)
        if diff_hash(recipe) in blocklist:
            counts.blocked += 1
            continue
        if key in seen or key in parent_keys:
            counts.duplicate += 1
            continue
        seen.add(key)
        counts.accepted += 1
        return Offspring(recipe, operator, ids)
    return None


def generate(archive: list[Parent], *, budget: int, rng: random.Random,
             blocklist: set[str] | frozenset[str], multiplier: int = 2,
             counts: Counts | None = None) -> list[Offspring]:
    """Uniform archive selection, independent of prune status or incumbent deltas.

    Target ``multiplier * budget`` novel proposals with bounded work. May return
    fewer on exhaustion: the caller must measure actual pressure, not assume it.
    Scores are deliberately not used; weighting would require anchor.py scores
    from a common difficulty epoch, not incomparable historical deltas.
    """
    if budget < 0 or multiplier < 2:
        raise ValueError("budget must be nonnegative and multiplier at least two")
    seen = {canonical(p.recipe) for p in archive}
    out: list[Offspring] = []
    target = budget * multiplier
    for _ in range(target * 4):
        if not archive or len(out) >= target:
            break
        parent = rng.choice(archive)
        peers = [p for p in archive if p.candidate_id != parent.candidate_id
                 and type(p.recipe) is type(parent.recipe)]
        choices: list[Operator] = ["mutation", "block_crossover", "field_crossover"] if peers else ["mutation"]
        operator = rng.choice(choices)
        child = vary(parent, operator=operator, other=rng.choice(peers) if peers else None,
                     rng=rng, seen=seen, blocklist=blocklist, counts=counts)
        if child is not None:
            out.append(child)
    return out
