"""Tests for prompts.py - Devil's Advocate pattern implementation."""

import pytest

from bugfix_agent.prompts import (
    REVIEW_STATES,
    VERDICT_REQUIRED_STATES,
    load_prompt,
)


class TestLoadPromptBasic:
    """Basic functionality tests for load_prompt."""

    def test_load_prompt_returns_string(self) -> None:
        """load_prompt should return a string."""
        result = load_prompt("investigate_review", issue_url="http://example.com")
        assert isinstance(result, str)

    def test_load_prompt_file_not_found(self) -> None:
        """load_prompt should raise FileNotFoundError for non-existent state."""
        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent_state")

    def test_load_prompt_substitutes_variables(self) -> None:
        """load_prompt should substitute template variables."""
        result = load_prompt(
            "investigate_review",
            issue_url="https://github.com/test/repo/issues/123",
        )
        assert "https://github.com/test/repo/issues/123" in result


class TestDevilsAdvocatePreamble:
    """Tests for Devil's Advocate preamble inclusion."""

    def test_review_state_includes_preamble(self) -> None:
        """REVIEW states should include Devil's Advocate preamble."""
        result = load_prompt("investigate_review", issue_url="http://example.com")
        assert "Devil's Advocate" in result

    def test_non_review_state_excludes_preamble(self) -> None:
        """Non-REVIEW states should not include preamble."""
        result = load_prompt(
            "investigate",
            issue_url="http://example.com",
            artifacts_dir="/tmp/test",
        )
        assert "Devil's Advocate" not in result

    def test_init_excludes_preamble(self) -> None:
        """INIT state should not include preamble (not a REVIEW state)."""
        result = load_prompt("init", issue_url="http://example.com")
        assert "Devil's Advocate" not in result

    def test_force_include_preamble(self) -> None:
        """Force include preamble on non-REVIEW state."""
        result = load_prompt(
            "investigate",
            include_review_preamble=True,
            issue_url="http://example.com",
            artifacts_dir="/tmp/test",
        )
        assert "Devil's Advocate" in result

    def test_force_exclude_preamble(self) -> None:
        """Force exclude preamble on REVIEW state."""
        result = load_prompt(
            "investigate_review",
            include_review_preamble=False,
            issue_url="http://example.com",
        )
        assert "Devil's Advocate" not in result


class TestFooterVerdict:
    """Tests for VERDICT footer inclusion."""

    def test_verdict_state_includes_footer(self) -> None:
        """VERDICT_REQUIRED_STATES should include footer."""
        result = load_prompt("investigate_review", issue_url="http://example.com")
        assert "Evidence Quality Check" in result

    def test_work_state_excludes_footer(self) -> None:
        """Work states should not include footer."""
        result = load_prompt(
            "investigate",
            issue_url="http://example.com",
            artifacts_dir="/tmp/test",
        )
        assert "Evidence Quality Check" not in result

    def test_init_includes_footer(self) -> None:
        """INIT state should include footer (VERDICT required)."""
        result = load_prompt("init", issue_url="http://example.com")
        assert "Evidence Quality Check" in result

    def test_force_include_footer(self) -> None:
        """Force include footer on work state."""
        result = load_prompt(
            "investigate",
            include_footer=True,
            issue_url="http://example.com",
            artifacts_dir="/tmp/test",
        )
        assert "Evidence Quality Check" in result

    def test_force_exclude_footer(self) -> None:
        """Force exclude footer on VERDICT state."""
        result = load_prompt(
            "init",
            include_footer=False,
            issue_url="http://example.com",
        )
        assert "Evidence Quality Check" not in result


class TestPromptStructure:
    """Tests for prompt assembly structure."""

    def test_review_prompt_has_all_sections(self) -> None:
        """REVIEW prompts should have: common + preamble + main + footer."""
        result = load_prompt("investigate_review", issue_url="http://example.com")

        # Common (from _common.md)
        assert "VERDICT" in result

        # Preamble (from _review_preamble.md)
        assert "Devil's Advocate" in result

        # Main (from investigate_review.md)
        assert "INVESTIGATE Review Prompt" in result

        # Footer (from _footer_verdict.md)
        assert "Evidence Quality Check" in result

    def test_init_prompt_structure(self) -> None:
        """INIT prompts should have: common + main + footer (no preamble)."""
        result = load_prompt("init", issue_url="http://example.com")

        # Common
        assert "VERDICT" in result

        # No preamble
        assert "Devil's Advocate" not in result

        # Main
        assert "INIT" in result

        # Footer
        assert "Evidence Quality Check" in result

    def test_work_prompt_structure(self) -> None:
        """Work prompts should have: main only."""
        result = load_prompt(
            "investigate",
            issue_url="http://example.com",
            artifacts_dir="/tmp/test",
        )

        # No common/preamble/footer
        assert "Devil's Advocate" not in result
        assert "Evidence Quality Check" not in result

        # Main only
        assert "INVESTIGATE" in result


class TestStateConstants:
    """Tests for state constant definitions."""

    def test_review_states_subset_of_verdict_states(self) -> None:
        """REVIEW_STATES should be subset of VERDICT_REQUIRED_STATES."""
        assert REVIEW_STATES.issubset(VERDICT_REQUIRED_STATES)

    def test_init_in_verdict_not_review(self) -> None:
        """INIT should be in VERDICT but not REVIEW states."""
        assert "init" in VERDICT_REQUIRED_STATES
        assert "init" not in REVIEW_STATES

    def test_review_states_contain_expected(self) -> None:
        """REVIEW_STATES should contain expected states."""
        expected = {"investigate_review", "detail_design_review", "implement_review"}
        assert expected == REVIEW_STATES
