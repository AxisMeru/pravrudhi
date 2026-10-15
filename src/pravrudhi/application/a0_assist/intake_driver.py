"""The intake driver: the agent-facing surface of the intake tools (pravrudhi#414), same shape as the A0 screening driver.

The agent calls tools; the driver holds all state in memory for the request only and returns SMALL outputs (handles, counts,
spans as indexes), never the cleaned text. The result is `driver.result`, produced by the deterministic final tool
(`intake.build_bundle`) from the driver's OWN state; nothing the agent carries can add text, and the agent cannot pass cleaned
text, maps or deletions at all.

Stage order: read_input -> clean -> split_facts -> extract_citations -> (verify_citation per citation, optional) -> bundle.
Every input the agent carries must equal what an earlier tool returned (canonical JSON), else the call is REFUSED
({"error": ..., "refused": true}, nothing runs), recorded in `refusals`, and the run is incomplete. Every call is recorded as
{tool, input, output}. The agent may reshape the fact split only as index spans passed to `bundle`, which re-validates them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pravrudhi.application.a0_assist import intake

STAGES = ("read_input", "clean", "split_facts", "extract_citations", "verify_citation", "bundle")


class IntakeDriver:
    def __init__(
        self, raw: str, *, exists_fn: Callable[[str], str] | None = None, timeout_s: float | None = None
    ) -> None:
        """raw: the engine-extracted text (the binding reference). exists_fn: citation text -> verifier status string.
        timeout_s: the wall-clock budget of EACH tool call (the intake text work; not the model session around it); an exceeded
        budget raises `IntakeTimeout` out of `call`. None or 0 sets none."""
        self.raw = raw
        self.timeout_s = timeout_s
        self.exists_fn = exists_fn
        self.calls: list[dict[str, Any]] = []
        self.refusals: list[str] = []
        self.result: dict[str, Any] | None = None
        self._info: dict[str, Any] | None = None
        self._clean: dict[str, Any] | None = None
        self._cands: list[dict[str, int]] | None = None
        self._cits: list[dict[str, Any]] | None = None

    def call(self, tool: str, inp: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any]
        try:
            if tool not in STAGES:
                raise intake.IntakeRefused(f"unknown tool {tool!r}")
            with intake.deadline(self.timeout_s):
                out = getattr(self, "_t_" + tool)(inp if isinstance(inp, dict) else {})
        except intake.IntakeRefused as e:
            self.refusals.append(f"{tool}: {e}")
            out = {"error": str(e), "refused": True}
        self.calls.append({"tool": tool, "input": inp, "output": out})
        return out

    @staticmethod
    def _need(cond: bool, msg: str) -> None:
        if not cond:
            raise intake.IntakeRefused(msg)

    def _same_sha(self, inp: dict[str, Any]) -> None:
        self._need(self._info is not None, "read_input has not run")
        assert self._info is not None
        self._need(inp.get("raw_sha256") == self._info["raw_sha256"], "raw_sha256 is not the one read_input returned")

    # ---- tools ---------------------------------------------------------------------------------------------------
    def _t_read_input(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(not self.calls, "read_input is called exactly once, as the first call")
        self._info = intake.read_input(self.raw)
        return {
            "raw_sha256": self._info["raw_sha256"],
            "n_chars": self._info["n_chars"],
            "n_pages": len(self._info["page_starts"]),
        }

    def _t_clean(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._same_sha(inp)
        self._need(self._clean is None, "clean is called once")
        c = intake.clean(self.raw)
        ck = intake.check_clean(self.raw, c["cleaned"], c["segments"], c["deletions"])
        self._clean = c
        return {
            "cleaned_chars": len(c["cleaned"]),
            "deleted_spans": len(c["deletions"]),
            "checks": {k: v["pass"] for k, v in ck.items()},
        }

    def _t_split_facts(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(self._clean is not None, "split_facts comes after clean")
        self._need(self._cands is None, "split_facts is called once")
        assert self._clean is not None
        self._cands = intake.split_facts(self._clean["cleaned"])
        return {"candidates": self._cands}

    def _t_extract_citations(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._same_sha(inp)
        self._need(self._cits is None, "extract_citations is called once")
        self._cits = intake.extract_citations(self.raw)
        return {"citations": [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in self._cits]}

    def _t_verify_citation(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(self._cits is not None, "verify_citation comes after extract_citations")
        assert self._cits is not None
        hit = [c for c in self._cits if c["start"] == inp.get("start") and c["end"] == inp.get("end")]
        self._need(len(hit) == 1, "verify_citation names a citation extract_citations returned (start and end)")
        text = hit[0]["text"]
        try:
            status = self.exists_fn(text) if self.exists_fn else "NOT_CHECKED"
        except Exception:  # a verifier fault is shown, never a guessed status
            status = "CHECK_UNAVAILABLE"
        return {"start": hit[0]["start"], "end": hit[0]["end"], "verifier_status": str(status)}

    def _t_bundle(self, inp: dict[str, Any]) -> dict[str, Any]:
        self._need(
            self._clean is not None and self._cands is not None and self._cits is not None, "bundle comes after the other stages"
        )
        self._need(self.result is None, "bundle is called once")
        self._same_sha(inp)
        assert self._clean is not None and self._cands is not None
        facts = inp.get("facts", self._cands)
        self._need(
            isinstance(facts, list) and all(isinstance(f, dict) and set(f) == {"cleaned_start", "cleaned_end"} for f in facts),
            "facts are index spans only: {cleaned_start, cleaned_end}",
        )
        prop = {**{k: self._clean[k] for k in ("cleaned", "segments", "deletions")}, "facts": facts}
        self.result = intake.build_bundle(self.raw, proposal=prop, verify_fn=self.exists_fn)
        return {
            "bundle_sha256": self.result["bundle_sha256"],
            "facts": len(self.result["facts"]),
            "not_citable": len(self.result["not_citable"]),
            "citations": len(self.result["citations"]),
            "checks": {k: v["pass"] for k, v in self.result["checks"].items()},
        }

    # ---- completion ----------------------------------------------------------------------------------------------
    def complete(self) -> list[str]:
        """Problems that make the run incomplete (empty = complete): the final tool must have run, once, after the stages."""
        problems = []
        if self.result is None:
            problems.append("bundle was not called")
        if self.refusals:
            problems.append("a tool call was refused")
        return problems
