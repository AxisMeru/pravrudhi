"""Fixture: every shape RULE 1 of `scripts/check_fail_open_defaults.py` must catch.

Deliberately wrong code. Never imported and never executed -- `tests/test_check_fail_open_defaults.py` copies
this file into a throwaway git repo and runs the guard over it. The guard itself skips this directory by name
(`FIXTURE_DIR_NAME`), so these lines never fail the real build.
"""

from __future__ import annotations

from typing import Any

THRESHOLD = 0.9


def canonical_instance(verdict: dict[str, Any]) -> bool:
    """The bug this guard exists for: a malformed record defaults to confidence 0, short-circuits before the
    verdict field is read, and the instance scores as a pass."""
    return verdict.get("confidence", 0) >= THRESHOLD


def falsy_shapes(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("verdict", ""),
        row.get("score", 0),
        row.get("p_established", 0.0),
        row.get("fooled", False),
        row.get("gold_status", ""),
        row.get("parity_agreement", 0.0),
        row.get("judge_scores", []),
        row.get("per_element_status", {}),
        row.get("pass_rate", 0.0),
    )


def identity_compares_equal_to_itself(cfg: dict[str, Any], record: dict[str, Any]) -> bool:
    """The specific hazard: both sides default to `""`, so an unverified endpoint reads as the verified one
    and the identity leg of the gate passes vacuously."""
    expected_endpoint = cfg.get("endpoint_id", "")
    expected_adapter = cfg.get("adapter_sha", "")
    return record.get("endpoint_id") == expected_endpoint and record.get("adapter_sha") == expected_adapter


def more_identity_keys(cfg: dict[str, Any]) -> tuple[Any, ...]:
    return (
        cfg.get("model_revision", ""),
        cfg.get("corpus_sha256", ""),
        cfg.get("binary_digest", ""),
        cfg.get("record_commit", ""),
    )


def true_is_fail_open_too(checks: dict[str, Any]) -> bool:
    """`True` is not falsy, but it IS fail-open for a check whose False means "a problem was found"."""
    return bool(checks.get("chain_ok", True))
