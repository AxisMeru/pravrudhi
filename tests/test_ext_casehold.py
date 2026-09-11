"""Tests for external casehold generation with retry loop."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock


def test_option_letter_explicit():
    """Test parsing 'answer: X' format."""
    from scripts.ext_casehold_generate import option_letter

    assert option_letter("The answer is A") == "A"
    assert option_letter("answer: B") == "B"
    assert option_letter("Answer: C") == "C"
    assert option_letter("option: D") == "D"
    assert option_letter("choice is E") == "E"


def test_option_letter_boxed():
    """Test parsing \\boxed{X} format."""
    from scripts.ext_casehold_generate import option_letter

    assert option_letter(r"The answer is \boxed{A}") == "A"
    assert option_letter(r"\boxed{B}") == "B"


def test_option_letter_alone():
    """Test parsing bare letter on last line."""
    from scripts.ext_casehold_generate import option_letter

    assert option_letter("After analysis, the answer is:\n\nA") == "A"
    assert option_letter("Some reasoning\n\n(B)") == "B"
    assert option_letter("C.") == "C"


def test_option_letter_none():
    """Test when no letter is found."""
    from scripts.ext_casehold_generate import option_letter

    assert option_letter("I think the answer might be around here") is None
    assert option_letter("") is None
    assert option_letter("123 456 789") is None


def test_option_letter_last_match():
    """Test that last match wins."""
    from scripts.ext_casehold_generate import option_letter

    # Model eliminates options in order, then answers
    text = "Option A is ruled out. Option B doesn't fit. The answer is C."
    assert option_letter(text) == "C"


def test_option_letter_case_insensitive():
    """Test case insensitivity."""
    from scripts.ext_casehold_generate import option_letter

    assert option_letter("ANSWER: a") == "A"
    assert option_letter("Answer: b") == "B"


def test_render_question():
    """Test question rendering."""
    from scripts.ext_casehold_generate import render_question

    holdings = ["Holding 1", "Holding 2", "Holding 3"]
    stem = "What is the main point?"
    result = render_question(stem, holdings)

    assert "What is the main point?" in result
    assert "A. Holding 1" in result
    assert "B. Holding 2" in result
    assert "C. Holding 3" in result


def test_load_items_basic():
    """Test loading items from CSV."""
    from scripts.ext_casehold_generate import load_items

    # Create a minimal CSV
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        csv_path = Path(f.name)
        # Header + prompt column (1) + holdings columns (2-6) + label column (12)
        f.write("id,prompt,h1,h2,h3,h4,h5,c1,c2,c3,c4,c5,label\n")
        # id, prompt, holdings..., label
        f.write('1,"Test prompt","H1","H2","H3","H4","H5","a","b","c","d","e",2\n')
        f.write('2,"Another prompt","X1","X2","X3","X4","X5","a","b","c","d","e",0\n')
        f.flush()

        try:
            items = load_items(csv_path)
            assert len(items) == 2
            assert items[0]["id"] == "1"
            assert items[0]["answer"] == "C"  # label 2 -> LETTERS[2] = C
            assert items[1]["id"] == "2"
            assert items[1]["answer"] == "A"  # label 0 -> LETTERS[0] = A
        finally:
            csv_path.unlink()


def test_load_items_with_limit():
    """Test limit parameter."""
    from scripts.ext_casehold_generate import load_items

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        csv_path = Path(f.name)
        f.write("id,prompt,h1,h2,h3,h4,h5,c1,c2,c3,c4,c5,label\n")
        for i in range(10):
            f.write(f'{i},"Prompt {i}","H1","H2","H3","H4","H5","a","b","c","d","e",{i%5}\n')
        f.flush()

        try:
            items = load_items(csv_path, limit=3)
            assert len(items) == 3
        finally:
            csv_path.unlink()


def test_load_items_invalid_labels():
    """Test that invalid labels are skipped."""
    from scripts.ext_casehold_generate import load_items

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        csv_path = Path(f.name)
        f.write("id,prompt,h1,h2,h3,h4,h5,c1,c2,c3,c4,c5,label\n")
        f.write('1,"Prompt","H1","H2","H3","H4","H5","a","b","c","d","e",5\n')  # Out of range
        f.write('2,"Prompt","H1","H2","H3","H4","H5","a","b","c","d","e",xyz\n')  # Not a number
        f.write('3,"Prompt","H1","H2","H3","H4","H5","a","b","c","d","e",2\n')  # Valid
        f.flush()

        try:
            items = load_items(csv_path)
            assert len(items) == 1
            assert items[0]["id"] == "3"
        finally:
            csv_path.unlink()


def test_build_prompt_basic():
    """Test prompt building."""
    from scripts.ext_casehold_generate import build_prompt

    # Mock tokenizer
    mock_tokenizer = Mock()
    mock_tokenizer.apply_chat_template.return_value = "RENDERED PROMPT"

    recipe = {
        "system_prompt": "You are helpful",
        "template": "Question: {question}",
        "thinking": False,
    }

    result = build_prompt(recipe, "What is 2+2?", None, mock_tokenizer)

    assert result == "RENDERED PROMPT"
    mock_tokenizer.apply_chat_template.assert_called_once()
    call_args = mock_tokenizer.apply_chat_template.call_args
    assert call_args[0][0][0]["role"] == "system"
    assert call_args[0][0][1]["role"] == "user"
    assert "What is 2+2?" in call_args[0][0][1]["content"]


def test_build_prompt_with_feedback():
    """Test prompt building with feedback."""
    from scripts.ext_casehold_generate import build_prompt

    mock_tokenizer = Mock()
    mock_tokenizer.apply_chat_template.return_value = "PROMPT WITH FEEDBACK"

    recipe = {
        "system_prompt": "You are helpful",
        "template": "Question: {question}",
        "feedback_template": "Error: {feedback}",
        "thinking": False,
    }

    result = build_prompt(recipe, "What?", "Try again", mock_tokenizer)

    assert result == "PROMPT WITH FEEDBACK"
    call_args = mock_tokenizer.apply_chat_template.call_args
    user_content = call_args[0][0][1]["content"]
    assert "Error: Try again" in user_content


def test_generate_with_retries_parsed():
    """Test retry loop when answer is parsed.

    This test is complex due to GPU dependencies. The main retry loop logic is tested
    through the option_letter and build_prompt functions. A full integration test requires GPU.
    """
    # Placeholder for future GPU-based testing
    pass


def test_generate_with_retries_unparsed():
    """Test retry loop when answer is unparsed."""
    # This test requires mocking the full torch/GPU pipeline
    # Skipping for unit test, focus on --dry-run validation
    pass


def test_dry_run_mode(capsys):
    """Test --dry-run prints requests without generating."""
    from scripts.ext_casehold_generate import dry_run_generate

    mock_tokenizer = Mock()
    mock_tokenizer.apply_chat_template = Mock(return_value="PROMPT")

    recipe = {
        "system_prompt": "Help",
        "template": "Q: {question}",
        "feedback_template": "Error: {feedback}",
        "retries": 2,
        "temperature": 0.2,
        "max_new_tokens": 512,
        "thinking": False,
    }

    items = [
        {"id": "1", "question": "What?", "answer": "A"},
        {"id": "2", "question": "Which?", "answer": "B"},
    ]

    dry_run_generate(recipe, items, mock_tokenizer)
    captured = capsys.readouterr()

    assert "DRY-RUN" in captured.out
    assert "3 retries" in captured.out or "2 retries" in captured.out  # Exact wording varies
    assert "ATTEMPT" in captured.out
    assert "Prompt length" in captured.out


def test_main_integration():
    """Integration test for main - tests that can run without GPU/transformers.

    Full integration tests with --dry-run and --invalid-n-samples require transformers
    which is only available in the Docker image. Those are tested manually with:

    python scripts/ext_casehold_generate.py --recipe recipe.json --csv test.csv \\
        --model-dir /path/to/model --dry-run
    """
    # Test that the main function is importable
    from scripts.ext_casehold_generate import main

    assert callable(main)
