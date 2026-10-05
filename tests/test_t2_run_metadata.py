"""`RunMetadata.finish` must not let a caller omit the completeness record (#92 f)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _t2_run_metadata import RunMetadata  # type: ignore[import-not-found]  # noqa: E402


def _meta() -> RunMetadata:
    m = RunMetadata(script_path=Path(__file__), base_url="http://127.0.0.1:1/v1", container_name=None,
                    concurrency_description="test", delay_s=0.0)
    m._t0 = 0.0  # noqa: SLF001 -- finish() without a live start(): no network or subprocess in this test
    return m


@pytest.mark.parametrize("extra", [None, {}, {"n_planned": 3}])
def test_finish_raises_without_complete(extra: dict[str, int] | None) -> None:
    with pytest.raises(ValueError, match="complete"):
        _meta().finish(extra=extra)
