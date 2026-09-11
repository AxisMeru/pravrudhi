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
