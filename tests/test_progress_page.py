"""The progress page must show only what `demo.json` actually recorded: an intent, a baseline-to-current
reading per benchmark and its state word, never a raw ledger field like a hash or sample count that would read
as more precision than the page is claiming.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from progress_page import load_objectives, render_page  # noqa: E402


def test_progress_page_shows_intent_and_states_but_no_raw_ledger_fields(tmp_path: Path) -> None:
    demo = {
        "objectives": {
            "objectives": [
                {
                    "intent": "A small model that solves arithmetic more reliably.",
                    "progress": [
                        {
                            "benchmark": "gsm8k exact_match,strict-match",
                            "state": "measured",
                            "baseline": {"value": 0.41, "sha256": "deadbeef", "seq": 717, "n": 1319},
                            "latest": {"value": 0.49, "sha256": "beefdead", "seq": 718, "n": 1319},
                        },
                        {
                            "benchmark": "mmlu_professional_law acc,none",
                            "state": "unmeasured",
                            "baseline": None,
                            "latest": None,
                        },
                    ],
                }
            ],
            "problems": [],
        }
    }
    demo_path = tmp_path / "demo.json"
    demo_path.write_text(json.dumps(demo))

    objectives = load_objectives(demo_path)
    page = render_page(objectives=objectives, commits=[], version="0.1.0", app_present=False, paper_present=False)

    assert "A small model that solves arithmetic more reliably." in page
    assert "measured" in page
    assert "unmeasured" in page
    assert "0.410" in page
    assert "0.490" in page

    for key_like in ("sha256", "deadbeef", "beefdead", "seq", "717", "718"):
        assert key_like not in page


def test_progress_page_introduction_and_product_cards(tmp_path: Path) -> None:
    demo = {
        "objectives": {"objectives": [], "problems": []},
    }
    demo_path = tmp_path / "demo.json"
    demo_path.write_text(json.dumps(demo))

    objectives = load_objectives(demo_path)
    page = render_page(
        objectives=objectives, commits=[], version="0.1.0", app_present=True, paper_present=True
    )

    # Introduction should mention that it's an installable recursive self-improvement engine
    assert "installable" in page.lower()
    assert "self-improvement" in page.lower() or "recursive" in page.lower()

    # Product cards: Pravrudhi Studio
    assert "Pravrudhi Studio" in page
    assert "https://pravrudhi.vercel.app" in page

    # Product cards: Pravrudhi (the product for users)
    assert "Pravrudhi" in page
    assert "https://pravrudhi-app.vercel.app" in page
    assert "https://pravrudhi-app.vercel.app/signin" in page

    # Desktop installers links
    assert "github.com/AxisMeru/pravrudhi/releases" in page
    assert "github.com/AxisMeru/pravrudhi-app/releases" in page

    # Demo and paper links (relative)
    assert "app/" in page
    assert "paper/main.pdf" in page


def test_progress_page_maintains_progress_content(tmp_path: Path) -> None:
    demo = {
        "objectives": {
            "objectives": [
                {
                    "intent": "Test objective for progress section.",
                    "progress": [
                        {
                            "benchmark": "test_benchmark",
                            "state": "measured",
                            "baseline": {"value": 0.5},
                            "latest": {"value": 0.6},
                        },
                    ],
                }
            ],
            "problems": [],
        }
    }
    demo_path = tmp_path / "demo.json"
    demo_path.write_text(json.dumps(demo))

    objectives = load_objectives(demo_path)
    page = render_page(objectives=objectives, commits=[], version="0.1.0", app_present=False, paper_present=False)

    # Progress content should still be rendered
    assert "Test objective for progress section." in page
    assert "test_benchmark" in page
    assert "0.500" in page
    assert "0.600" in page

    # Should show a section heading for what the engine has done
    assert "What the engine has done" in page or "Objectives" in page


def _page_for(progress: dict) -> str:
    objectives = [{"intent": "x", "progress": [progress]}]
    return render_page(objectives=objectives, commits=[], version="0.1.0", app_present=False, paper_present=False)


def test_paired_counts_and_test_are_shown_and_a_non_separated_difference_says_so() -> None:
    page = _page_for(
        {
            "benchmark": "humaneval+ pass@1",
            "state": "measured",
            "baseline": {"value": 0.5976, "n": 164},
            "latest": {"value": 0.6463, "n": 164},
            "wins": 15,
            "losses": 7,
            "p_mcnemar": 0.1338,
        }
    )
    assert "0.598" in page and "0.646" in page
    assert "paired: 15 problems only the latest passes vs 7 only the baseline passes" in page
    assert "exact McNemar p = 0.134" in page and "not separated by the paired test" in page
    row = page[page.index('<li class="benchmark">') : page.index("</li>", page.index('<li class="benchmark">'))]
    assert "improve" not in row.lower() and "164" not in row  # no improvement wording, no sample count in the row


def test_a_separated_difference_is_stated_as_such_and_unpaired_rows_carry_no_note() -> None:
    base = {"benchmark": "b", "state": "measured", "baseline": {"value": 0.4}, "latest": {"value": 0.6}}
    sep = _page_for({**base, "wins": 59, "losses": 7, "p_mcnemar": 0.0})
    assert "separated by the paired test (p below 0.05)" in sep and "not separated" not in sep
    odd = (
        {},
        {"wins": 3, "losses": 1},
        {"wins": None, "losses": 2, "p_mcnemar": 0.5},
        {"wins": True, "losses": 1, "p_mcnemar": 0.5},
    )
    for p in odd:
        page = _page_for({**base, **p})
        assert "paired:" not in page


def test_a_tiny_p_on_the_page_is_not_printed_as_zero() -> None:
    page = _page_for(
        {
            "benchmark": "b",
            "state": "measured",
            "baseline": {"value": 0.4},
            "latest": {"value": 0.6},
            "wins": 59,
            "losses": 7,
            "p_mcnemar": 1e-9,
        }
    )
    assert "exact McNemar p < 0.001" in page and "0.000" not in page
