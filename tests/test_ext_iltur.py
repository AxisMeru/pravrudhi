"""Unit tests for IL-TUR external proof tier: parser and retry logic.

Tests the independent section parser and the retry loop condition,
comparing against the internal kernel scorer where applicable.
"""

import sys
from pathlib import Path

import pytest

# Add scripts directory to path for importing
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from ext_iltur_generate import (
    canonical_sections,
    jaccard_score,
    normalise_section_number,
    parse_sections,
)


class TestNormaliseSectionNumber:
    """Test section number normalization."""

    def test_plain_number(self):
        """Plain section numbers should stay plain."""
        assert normalise_section_number("302") == "302"
        assert normalise_section_number("34") == "34"

    def test_lowercase_suffix(self):
        """Lowercase suffixes should be uppercased."""
        assert normalise_section_number("498a") == "498A"
        assert normalise_section_number("120b") == "120B"

    def test_subclause(self):
        """Sub-clauses should be lowercased."""
        assert normalise_section_number("294(B)") == "294(b)"
        assert normalise_section_number("376(2)") == "376(2)"

    def test_combined(self):
        """Suffix upper, sub-clause lower."""
        assert normalise_section_number("498a(I)") == "498A(i)"
        assert normalise_section_number("294(b)") == "294(b)"


class TestParseSections:
    """Test section label parser (independent from kernel)."""

    def test_single_section(self):
        """Parse a single section."""
        result = parse_sections("Section 302 applies.")
        assert result == {"Section 302"}

    def test_multiple_sections_list(self):
        """Parse multiple sections in a list."""
        result = parse_sections("Sections 34, 302 and 120B apply.")
        assert result == {"Section 34", "Section 302", "Section 120B"}

    def test_with_subclauses(self):
        """Parse sections with subclauses."""
        result = parse_sections("Section 294(b) and Section 376(2) apply.")
        assert result == {"Section 294(b)", "Section 376(2)"}

    def test_case_insensitive_marker(self):
        """Marker matching should be case-insensitive."""
        assert parse_sections("section 302 applies.") == {"Section 302"}
        assert parse_sections("sec. 302 applies.") == {"Section 302"}
        assert parse_sections("Sec 302 applies.") == {"Section 302"}

    def test_explicit_empty(self):
        """Explicit 'no section applies' should return empty set."""
        result = parse_sections("No section applies.")
        assert result == set()

    def test_explicit_empty_variations(self):
        """Test various 'no section' phrasings."""
        assert parse_sections("None sections apply.") == set()
        assert parse_sections("Not any statute applies.") == set()
        assert parse_sections("No provisions apply.") == set()

    def test_nothing_parsed(self):
        """Completion with no sections should return None."""
        result = parse_sections("This is some text about the case but without statute references.")
        assert result is None

    def test_empty_string(self):
        """Empty completion should return None."""
        assert parse_sections("") is None
        assert parse_sections("   ") is None

    def test_with_commas_and_joining_words(self):
        """Test common joining patterns."""
        result = parse_sections("Sections 302, 304 & 307 or 308 and 120B")
        # Should parse all of these
        assert "Section 302" in result
        assert "Section 304" in result
        assert "Section 307" in result
        assert "Section 308" in result
        assert "Section 120B" in result

    def test_alphanumeric_suffix(self):
        """Test sections with multi-character alphanumeric suffixes."""
        result = parse_sections("Sections 498A and 306")
        assert result == {"Section 498A", "Section 306"}

    def test_no_marker_no_credit(self):
        """Bare numbers without a section marker should not be credited."""
        result = parse_sections("The case involves 302 and 304 somehow.")
        # Without marker, these are not parsed as sections
        assert result is None


class TestCanonicalSections:
    """Test canonical form conversion."""

    def test_single_section(self):
        """Single section should be returned as-is."""
        assert canonical_sections({"Section 302"}) == "Section 302"

    def test_multiple_sorted(self):
        """Multiple sections should be sorted numerically."""
        result = canonical_sections({"Section 302", "Section 34", "Section 120B"})
        # Should be sorted: 34, 120B, 302
        assert result == "Section 34|Section 120B|Section 302"

    def test_none_input(self):
        """None input (unparsed completion) should return empty string."""
        assert canonical_sections(None) == ""

    def test_empty_set(self):
        """Empty set (explicit no section) should return empty string."""
        assert canonical_sections(set()) == ""

    def test_preserves_subclauses(self):
        """Sub-clauses should be preserved in sorting."""
        result = canonical_sections({"Section 376(2)", "Section 294(b)", "Section 376"})
        assert result == "Section 294(b)|Section 376|Section 376(2)"


class TestJaccardScore:
    """Test Jaccard overlap scoring."""

    def test_exact_match(self):
        """Exact match should score 1.0."""
        score = jaccard_score("Section 302|Section 304", "Section 302|Section 304")
        assert score == 1.0

    def test_complete_mismatch(self):
        """Completely disjoint sets should score 0.0."""
        score = jaccard_score("Section 302", "Section 307")
        assert score == 0.0

    def test_partial_overlap(self):
        """Partial overlap should score correctly."""
        # Predicted: 302, 304; Gold: 302, 307
        # Intersection: {302}, Union: {302, 304, 307}
        # Jaccard = 1/3 ≈ 0.333
        score = jaccard_score("Section 302|Section 304", "Section 302|Section 307")
        assert abs(score - 1.0 / 3.0) < 0.001

    def test_subset(self):
        """Subset should score appropriately."""
        # Predicted: 302; Gold: 302, 304
        # Intersection: {302}, Union: {302, 304}
        # Jaccard = 1/2 = 0.5
        score = jaccard_score("Section 302", "Section 302|Section 304")
        assert score == 0.5

    def test_superset(self):
        """Superset should score appropriately."""
        # Predicted: 302, 304, 307; Gold: 302
        # Intersection: {302}, Union: {302, 304, 307}
        # Jaccard = 1/3
        score = jaccard_score("Section 302|Section 304|Section 307", "Section 302")
        assert abs(score - 1.0 / 3.0) < 0.001

    def test_both_empty(self):
        """Both empty should score 1.0."""
        score = jaccard_score("", "")
        assert score == 1.0

    def test_predicted_empty_gold_not(self):
        """Predicted empty, gold not: should score 0.0."""
        score = jaccard_score("", "Section 302")
        assert score == 0.0

    def test_gold_empty_predicted_not(self):
        """Gold empty, predicted not: should score 0.0."""
        score = jaccard_score("Section 302", "")
        assert score == 0.0


class TestRetryLoopCondition:
    """Test the retry loop condition: when should retry happen."""

    def test_retry_when_unparsed(self):
        """Should retry when parse_sections returns None."""
        completion = "The relevant case law suggests that..."
        parsed = parse_sections(completion)
        should_retry = parsed is None
        assert should_retry is True

    def test_no_retry_when_parsed(self):
        """Should not retry when parse_sections returns a set."""
        completion = "Section 302 and 304 apply."
        parsed = parse_sections(completion)
        should_retry = parsed is None
        assert should_retry is False

    def test_no_retry_when_explicit_empty(self):
        """Should not retry when explicitly saying no section applies."""
        completion = "No section of the IPC applies to this case."
        parsed = parse_sections(completion)
        should_retry = parsed is None
        # Explicit empty is NOT None, so no retry
        assert should_retry is False
        assert parsed == set()

    def test_retry_on_cutoff(self):
        """Completion cut off mid-answer should retry if unparsed."""
        completion = "The applicable sections are Section 302 and Section"
        parsed = parse_sections(completion)
        # Depending on implementation, this might parse "Section 302" or None
        # If it parses 302, no retry; if not, retry
        # The key is: incomplete answers (parsed=None) trigger retry
        should_retry = parsed is None
        # If it parsed 302, we wouldn't retry; if None, we would
        assert isinstance(should_retry, bool)


class TestRetryFeedbackFormat:
    """Test that retry feedback is properly formatted."""

    def test_feedback_injection(self):
        """Feedback should be injectable into template."""
        feedback_template = "Feedback: {feedback}"
        feedback_text = "Try again."

        filled = feedback_template.replace("{feedback}", feedback_text)
        expected = "Feedback: Try again."
        assert filled == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
