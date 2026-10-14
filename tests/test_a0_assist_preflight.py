"""The two-key workspace check: fails closed, names the failing key, never a value (invented values only)."""

from __future__ import annotations

import httpx
import pytest

from pravrudhi.application.a0_assist import preflight as P
from pravrudhi.application.a0_assist import transport as TR


class Tr:
    def __init__(self, ok=True, raises=None):
        self.ok, self.raises, self.asked = ok, raises, 0

    def workspace_ok(self):
        self.asked += 1
        if self.raises:
            raise self.raises
        return self.ok


def test_both_keys_in_the_workspace_pass():
    a, b = Tr(), Tr()
    assert P.same_workspace({"engine": a, "sweeper": b}) == [] and a.asked == b.asked == 1


def test_a_key_that_does_not_get_a_200_is_named_and_the_other_still_passes():
    assert P.same_workspace({"engine": Tr(), "sweeper": Tr(ok=False)}) == ["sweeper"]
    assert P.same_workspace({"engine": Tr(ok=False), "sweeper": Tr(ok=False)}) == ["engine", "sweeper"]


@pytest.mark.parametrize(
    "exc", [TR.UpstreamError(403), TR.UpstreamError(599, "ReadTimeout"), TR.TransportRefused("x"), httpx.ConnectError("c")]
)
def test_an_error_while_probing_is_a_failure_not_a_pass(exc):
    assert P.same_workspace({"engine": Tr(), "sweeper": Tr(raises=exc)}) == ["sweeper"]


@pytest.mark.parametrize("v", [None, 1, "true", [], 0])
def test_only_exactly_true_counts_as_a_match(v):
    assert P.same_workspace({"engine": Tr(ok=v)}) == ["engine"]


def test_no_keys_fails_closed_and_the_output_is_names_only():
    assert P.same_workspace({}) == ["no_keys"]
    out = P.same_workspace({"engine": Tr(raises=TR.UpstreamError(403, "workspace wrkspc_marker-1 mismatch"))})
    assert out == ["engine"] and "wrkspc" not in repr(out)
