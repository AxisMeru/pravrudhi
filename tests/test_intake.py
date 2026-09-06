"""Grazing must score honestly (missing metadata is `unknown` and penalised, never invented) and stay bounded;
rumination must never let a summary — only a completed, checked, useful trial — reach `ruminated`.

Every test builds its own fake source tree under `tmp_path` and points an explicit `IntakeConfig` at it, so
nothing here depends on this machine's real `~/.claude/skills`, `~/.codex/skills`, or Hugging Face cache.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pravrudhi.application import grahana, manthana


def write_skill(
    base: Path, name: str, *, description: str = "", license: str | None = None, tags: list[str] | None = None,
) -> None:
    base.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", f"description: {description}"]
    if license is not None:
        lines.append(f"license: {license}")
    if tags:
        lines.append("tags: [" + ", ".join(tags) + "]")
    content = "---\n" + "\n".join(lines) + "\n---\nBody text is never read as metadata.\n"
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def make_cfg(tmp_path: Path, *, items_per_source: int = 20) -> grahana.IntakeConfig:
    def local(path: Path) -> grahana.SourceConfig:
        return grahana.SourceConfig(id="", kind="local", status="local", enabled=True, path=str(path), host="")

    return grahana.IntakeConfig(
        collector_items_per_source=items_per_source,
        collector_bytes_per_source=1_000_000,
        collector_wall_s=30.0,
        excerpt_max_chars=200,
        forget_max_items=500,
        forget_max_tombstones=200,
        forget_relevance_floor=0.2,
        forget_default_revisit_days=30.0,
        sources={
            "claude_skills": local(tmp_path / "claude_skills"),
            "codex_skills": local(tmp_path / "codex_skills"),
            "hf_hub": local(tmp_path / "hf_hub"),
            "repo_tools": local(tmp_path / "tools.json"),
            "repo_recipes": local(tmp_path / "recipes.json"),
            "github_releases": grahana.SourceConfig(
                id="github_releases", kind="network", status="unconfigured", enabled=True, path="",
                host="api.github.com",
            ),
        },
    )


class TestRelevance:
    def test_components_recorded_with_matched_requirement_ids(self, tmp_path: Path) -> None:
        write_skill(tmp_path / "claude_skills", "lora-trainer", description="Trains LoRA adapters", license="MIT", tags=["lora"])
        cfg = make_cfg(tmp_path)
        result = grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg, requirements={"REQ-1": ["lora"]})
        assert result.items_added == 1

        item = grahana.load_items(tmp_path)["claude_skills:lora-trainer"]
        assert item.relevance.matched_requirement_ids == ("REQ-1",)
        assert item.linked_requirements == ("REQ-1",)
        assert item.relevance.need == 1.0
        assert item.relevance.unknown_components == ()
        # every component known and 1.0 (fit: already installed here; novelty: first sighting; provenance: MIT)
        assert item.relevance.total == pytest.approx(1.0)

    def test_no_requirements_is_a_real_zero_not_unknown(self, tmp_path: Path) -> None:
        write_skill(tmp_path / "claude_skills", "unlinked-skill", description="Does a thing", license="MIT")
        cfg = make_cfg(tmp_path)
        grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg)
        item = grahana.load_items(tmp_path)["claude_skills:unlinked-skill"]
        assert item.relevance.need == 0.0
        assert "need" not in item.relevance.unknown_components
        assert item.linked_requirements == ()

    def test_missing_licence_is_unknown_and_penalised(self, tmp_path: Path) -> None:
        write_skill(tmp_path / "claude_skills", "no-licence-skill", description="Does a thing")
        cfg = make_cfg(tmp_path)
        grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg)

        item = grahana.load_items(tmp_path)["claude_skills:no-licence-skill"]
        assert item.licence == "unknown"
        assert "provenance" in item.relevance.unknown_components
        assert item.relevance.provenance == cfg.unknown_penalty


class TestGrazeBounds:
    def test_per_source_cap_holds(self, tmp_path: Path) -> None:
        for i in range(10):
            write_skill(tmp_path / "claude_skills", f"skill-{i}", description="x")
        cfg = make_cfg(tmp_path, items_per_source=3)

        result = grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg)

        assert result.items_added == 3
        assert "claude_skills" in result.sources_truncated
        assert len(grahana.load_items(tmp_path)) == 3

    def test_unconfigured_network_source_is_never_contacted(self, tmp_path: Path) -> None:
        cfg = make_cfg(tmp_path)
        result = grahana.graze(tmp_path, sources=["github_releases"], budget=cfg)

        assert result.sources_scanned == ()
        assert any("unconfigured" in s for s in result.sources_skipped)
        assert result.items_added == 0

    def test_repo_catalogues_are_read_directly(self, tmp_path: Path) -> None:
        (tmp_path / "tools.json").write_text(
            '{"tools": [{"id": "t1", "category": "coding", "title": "T1", "provides": "does a thing", '
            '"detect": {"kind": "path", "value": "ls"}}]}',
            encoding="utf-8",
        )
        (tmp_path / "recipes.json").write_text(
            '{"recipes": [{"id": "r1", "capability": "sft", "title": "R1", "skill": "some-skill", '
            '"summary": "fine-tunes a model", "source": "nvidia"}]}',
            encoding="utf-8",
        )
        cfg = make_cfg(tmp_path)
        result = grahana.graze(tmp_path, sources=["repo_tools", "repo_recipes"], budget=cfg)

        assert result.items_added == 2
        items = grahana.load_items(tmp_path)
        assert items["repo_tools:t1"].kind == "tool"
        assert items["repo_recipes:r1"].kind == "technique"
        # cheap grazing never runs the tool detector, so platform fit is honestly unmeasured, not guessed
        assert "fit" in items["repo_tools:t1"].relevance.unknown_components


class TestDeduplication:
    def test_duplicate_revision_same_source_no_second_item_independent_source_does(self, tmp_path: Path) -> None:
        write_skill(tmp_path / "claude_skills", "shared-skill", description="Same content", license="MIT")
        write_skill(tmp_path / "codex_skills", "shared-skill", description="Same content", license="MIT")
        cfg = make_cfg(tmp_path)

        first = grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg)
        assert first.items_added == 1

        regraze = grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg)
        assert regraze.items_added == 0
        assert regraze.items_deduplicated == 1
        assert len(grahana.load_items(tmp_path)) == 1

        independent = grahana.graze(tmp_path, sources=["codex_skills"], budget=cfg)
        assert independent.items_added == 1

        items = grahana.load_items(tmp_path)
        assert len(items) == 2
        a, b = items["claude_skills:shared-skill"], items["codex_skills:shared-skill"]
        assert a.content_hash == b.content_hash
        assert a.duplication_cluster == b.duplication_cluster != ""


class TestStateMachine:
    def _one_grazed_item(self, tmp_path: Path) -> str:
        write_skill(tmp_path / "claude_skills", "raw-skill", description="x")
        grahana.graze(tmp_path, sources=["claude_skills"], budget=make_cfg(tmp_path))
        return "claude_skills:raw-skill"

    def test_refuses_grazed_to_ruminated(self, tmp_path: Path) -> None:
        item_id = self._one_grazed_item(tmp_path)

        with pytest.raises(manthana.ManthanaError):
            manthana.advance(tmp_path, item_id, "ruminated")

        assert grahana.load_items(tmp_path)[item_id].state == "grazed"

    def test_a_summary_cannot_promote_an_item(self, tmp_path: Path) -> None:
        item_id = self._one_grazed_item(tmp_path)

        with pytest.raises(manthana.ManthanaError):
            manthana.propose_admission(tmp_path, item_id, note="looks great, ship it")

        assert grahana.load_items(tmp_path)[item_id].state == "grazed"

    def test_full_pipeline_requires_a_completed_checked_useful_trial(self, tmp_path: Path) -> None:
        write_skill(tmp_path / "claude_skills", "good-skill", description="Helps", license="MIT", tags=["lora"])
        cfg = make_cfg(tmp_path)
        grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg, requirements={"REQ-9": ["lora"]})
        item_id = "claude_skills:good-skill"

        shortlisted = manthana.shortlist(tmp_path, 1)
        assert [i.item_id for i in shortlisted] == [item_id]
        assert grahana.load_items(tmp_path)[item_id].state == "shortlisted"

        plan = manthana.plan_trial(tmp_path, item_id, hypothesis="does this help REQ-9?")
        assert plan.item_id == item_id
        assert grahana.load_items(tmp_path)[item_id].state == "trial_planned"

        manthana.start_trial(tmp_path, item_id)
        assert grahana.load_items(tmp_path)[item_id].state == "trial_running"

        with pytest.raises(manthana.ManthanaError):
            manthana.record_trial_outcome(tmp_path, item_id, outcome="useful", trial_id="t1", completed=False, checked=True)
        assert grahana.load_items(tmp_path)[item_id].state == "trial_running"

        manthana.record_trial_outcome(tmp_path, item_id, outcome="useful", trial_id="t1", completed=True, checked=True)
        assert grahana.load_items(tmp_path)[item_id].state == "ruminated"

    def test_not_useful_outcome_defers_rather_than_ruminates(self, tmp_path: Path) -> None:
        write_skill(tmp_path / "claude_skills", "mediocre-skill", description="Meh")
        grahana.graze(tmp_path, sources=["claude_skills"], budget=make_cfg(tmp_path))
        item_id = "claude_skills:mediocre-skill"
        manthana.shortlist(tmp_path, 1)
        manthana.plan_trial(tmp_path, item_id)
        manthana.start_trial(tmp_path, item_id)

        manthana.record_trial_outcome(tmp_path, item_id, outcome="not_useful", trial_id="t2", completed=True, checked=True)

        assert grahana.load_items(tmp_path)[item_id].state == "deferred"


class TestForget:
    def test_drops_expired_low_relevance_and_keeps_pinned_and_fresh(self, tmp_path: Path) -> None:
        cfg = make_cfg(tmp_path)
        now = datetime(2026, 9, 7, tzinfo=UTC)

        low = grahana.RelevanceScore(
            need=0.0, fit=0.1, novelty=0.0, provenance=0.1,
            weight_need=0.4, weight_fit=0.2, weight_novelty=0.2, weight_provenance=0.2,
            total=0.06, matched_requirement_ids=(), unknown_components=("fit", "provenance"),
        )
        high = grahana.RelevanceScore(
            need=1.0, fit=1.0, novelty=1.0, provenance=1.0,
            weight_need=0.4, weight_fit=0.2, weight_novelty=0.2, weight_provenance=0.2,
            total=1.0, matched_requirement_ids=("REQ-1",), unknown_components=(),
        )

        def item(
            item_id: str, *, relevance: grahana.RelevanceScore, linked: tuple[str, ...], state: str, revisit: str,
        ) -> grahana.IntakeItem:
            return grahana.IntakeItem(
                item_id=item_id, canonical_path="x", source_revision="1", first_seen="2026-01-01T00:00:00Z",
                last_seen="2026-01-01T00:00:00Z", content_hash=item_id, kind="tool", licence="unknown",
                excerpt="e", trust_class="local_catalogue", tags=("x",), linked_requirements=linked,
                relevance=relevance, duplication_cluster="", state=state, revisit_at=revisit,
                supersession_reason="",
            )

        expired_low = item("src:expired-low", relevance=low, linked=(), state="grazed", revisit="2026-01-02T00:00:00Z")
        pinned_by_link = item(
            "src:pinned-by-link", relevance=low, linked=("REQ-1",), state="grazed", revisit="2026-01-02T00:00:00Z",
        )
        not_yet_expired = item(
            "src:not-yet-expired", relevance=low, linked=(), state="grazed", revisit="2099-01-01T00:00:00Z",
        )
        trial_running = item(
            "src:trial-running", relevance=high, linked=(), state="trial_running", revisit="2026-01-02T00:00:00Z",
        )

        all_items = [expired_low, pinned_by_link, not_yet_expired, trial_running]
        grahana.save_items(tmp_path, {i.item_id: i for i in all_items})

        result = grahana.forget(tmp_path, now, config=cfg)

        assert result.tombstoned == ("src:expired-low",)
        items = grahana.load_items(tmp_path)
        assert items["src:expired-low"].state == "forgotten"
        assert items["src:expired-low"].excerpt == ""
        assert items["src:pinned-by-link"].state == "grazed"
        assert items["src:pinned-by-link"].excerpt == "e"
        assert items["src:not-yet-expired"].state == "grazed"
        assert items["src:trial-running"].state == "trial_running"

    def test_nothing_over_bound_leaves_the_store_untouched(self, tmp_path: Path) -> None:
        cfg = make_cfg(tmp_path)
        write_skill(tmp_path / "claude_skills", "fine-skill", description="x", license="MIT")
        grahana.graze(tmp_path, sources=["claude_skills"], budget=cfg)
        before = grahana.load_items(tmp_path)

        result = grahana.forget(tmp_path, datetime(2026, 9, 7, tzinfo=UTC), config=cfg)

        assert result.tombstoned == ()
        assert result.dropped == ()
        assert grahana.load_items(tmp_path) == before
