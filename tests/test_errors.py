"""Tests for ``kaji_harness.errors`` message assembly (Issue #527)."""

from __future__ import annotations

import pytest

from kaji_harness.errors import CLIExecutionError, SessionResolution


@pytest.mark.small
class TestCLIExecutionErrorSummary:
    """``summary`` は Herdr no_pattern 経路専用。``None`` は従来の ``stderr[:200]``。"""

    def test_without_summary_keeps_first_200_chars(self) -> None:
        stderr = "a" * 150 + "b" * 150

        exc = CLIExecutionError("implement", 1, stderr)

        assert str(exc) == f"Step 'implement' CLI exited with code 1: {stderr[:200]}"
        assert exc.summary is None

    def test_summary_is_not_truncated(self) -> None:
        stderr = "prefix; " + "x" * 400
        summary = "prefix; last error line: " + "y" * 400

        exc = CLIExecutionError("review-ready", 1, stderr, summary=summary)

        assert str(exc) == f"Step 'review-ready' CLI exited with code 1: {summary}"
        assert str(exc).endswith("y" * 400)

    def test_attributes_are_unchanged_with_summary(self) -> None:
        resolution = SessionResolution("11111111-1111-4111-8111-111111111111")

        exc = CLIExecutionError(
            "review-ready", 3, "raw detail", session_resolution=resolution, summary="short"
        )

        assert exc.step_id == "review-ready"
        assert exc.returncode == 3
        assert exc.stderr == "raw detail"
        assert exc.session_resolution == resolution
        assert exc.summary == "short"
