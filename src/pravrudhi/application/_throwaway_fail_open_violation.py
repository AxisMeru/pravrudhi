"""THROWAWAY file: verifies check_fail_open_defaults.py catches a real violation in scorer paths."""


def confidence_gate(record: dict) -> bool:
    return record.get("confidence", 0) >= 0.74
