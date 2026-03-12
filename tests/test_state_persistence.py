"""Medium tests: Session state persistence.

Tests save/load/resume of issue-scoped session state:
- JSON serialization/deserialization
- StepRecord rehydration
- Verdict rehydration
- progress.md generation
- --from resume with restored state
"""

import json
from pathlib import Path

import pytest

from kaji_harness.models import Verdict
from kaji_harness.state import SessionState, StepRecord


@pytest.mark.medium
class TestStatePersistence:
    """Save → load round-trip tests."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        """State saved to disk can be loaded back."""
        state = SessionState.load_or_create(42, artifacts_dir=tmp_path)
        state.save_session_id("design", "sess-design-001")
        verdict = Verdict(status="PASS", reason="all good", evidence="tests pass", suggestion="")
        state.record_step("design", verdict)

        # Load from disk
        loaded = SessionState.load_or_create(42, artifacts_dir=tmp_path)
        assert loaded.issue_number == 42
        assert loaded.sessions["design"] == "sess-design-001"
        assert len(loaded.step_history) == 1
        assert loaded.step_history[0].step_id == "design"
        assert loaded.step_history[0].verdict_status == "PASS"
        assert loaded.last_completed_step == "design"

    def test_step_record_rehydration(self, tmp_path: Path) -> None:
        """StepRecord objects are correctly deserialized from JSON."""
        state = SessionState.load_or_create(100, artifacts_dir=tmp_path)
        verdict = Verdict(
            status="RETRY",
            reason="tests failed",
            evidence="3 failures",
            suggestion="fix tests",
        )
        state.record_step("review", verdict)

        loaded = SessionState.load_or_create(100, artifacts_dir=tmp_path)
        record = loaded.step_history[0]
        assert isinstance(record, StepRecord)
        assert record.step_id == "review"
        assert record.verdict_status == "RETRY"
        assert record.verdict_reason == "tests failed"
        assert record.verdict_evidence == "3 failures"
        assert record.verdict_suggestion == "fix tests"
        assert record.timestamp  # non-empty ISO 8601

    def test_last_transition_verdict_rehydration(self, tmp_path: Path) -> None:
        """last_transition_verdict is correctly deserialized as Verdict."""
        state = SessionState.load_or_create(200, artifacts_dir=tmp_path)
        verdict = Verdict(
            status="RETRY",
            reason="issues found",
            evidence="see comments",
            suggestion="fix them",
        )
        state.record_step("review", verdict)

        loaded = SessionState.load_or_create(200, artifacts_dir=tmp_path)
        assert loaded.last_transition_verdict is not None
        assert isinstance(loaded.last_transition_verdict, Verdict)
        assert loaded.last_transition_verdict.status == "RETRY"
        assert loaded.last_transition_verdict.reason == "issues found"

    def test_cycle_counts_persisted(self, tmp_path: Path) -> None:
        """Cycle iteration counts survive save/load."""
        state = SessionState.load_or_create(300, artifacts_dir=tmp_path)
        state.increment_cycle("code-review")
        state.increment_cycle("code-review")

        loaded = SessionState.load_or_create(300, artifacts_dir=tmp_path)
        assert loaded.cycle_iterations("code-review") == 2

    def test_session_id_persisted_immediately(self, tmp_path: Path) -> None:
        """save_session_id persists immediately without needing record_step."""
        state = SessionState.load_or_create(301, artifacts_dir=tmp_path)
        state.save_session_id("design", "sess-abc-123")

        # Load from disk without any record_step
        loaded = SessionState.load_or_create(301, artifacts_dir=tmp_path)
        assert loaded.sessions["design"] == "sess-abc-123"

    def test_increment_cycle_persisted_immediately(self, tmp_path: Path) -> None:
        """increment_cycle persists immediately without needing record_step."""
        state = SessionState.load_or_create(302, artifacts_dir=tmp_path)
        state.increment_cycle("code-review")

        loaded = SessionState.load_or_create(302, artifacts_dir=tmp_path)
        assert loaded.cycle_iterations("code-review") == 1

    def test_multiple_steps_persisted(self, tmp_path: Path) -> None:
        """Multiple step records are all persisted."""
        state = SessionState.load_or_create(400, artifacts_dir=tmp_path)
        for step_id in ["design", "review", "implement"]:
            verdict = Verdict(status="PASS", reason=f"{step_id} done", evidence="ok", suggestion="")
            state.record_step(step_id, verdict)

        loaded = SessionState.load_or_create(400, artifacts_dir=tmp_path)
        assert len(loaded.step_history) == 3
        assert [r.step_id for r in loaded.step_history] == [
            "design",
            "review",
            "implement",
        ]

    def test_load_nonexistent_creates_fresh(self, tmp_path: Path) -> None:
        """Loading non-existent state creates a fresh SessionState."""
        state = SessionState.load_or_create(999, artifacts_dir=tmp_path)
        assert state.issue_number == 999
        assert state.sessions == {}
        assert state.step_history == []
        assert state.cycle_counts == {}
        assert state.last_completed_step is None
        assert state.last_transition_verdict is None


@pytest.mark.medium
class TestProgressMd:
    """progress.md generation tests."""

    def test_progress_md_created(self, tmp_path: Path) -> None:
        """progress.md is created when state is persisted."""
        state = SessionState.load_or_create(500, artifacts_dir=tmp_path)
        verdict = Verdict(status="PASS", reason="done", evidence="ok", suggestion="")
        state.record_step("design", verdict)

        progress_path = tmp_path / "500" / "progress.md"
        assert progress_path.exists()
        content = progress_path.read_text()
        assert "design" in content
        assert "PASS" in content

    def test_progress_md_pass_checked(self, tmp_path: Path) -> None:
        """PASS steps get [x] checkmark in progress.md."""
        state = SessionState.load_or_create(501, artifacts_dir=tmp_path)
        state.record_step(
            "design", Verdict(status="PASS", reason="ok", evidence="ok", suggestion="")
        )

        content = (tmp_path / "501" / "progress.md").read_text()
        assert "[x] design" in content

    def test_progress_md_non_pass_unchecked(self, tmp_path: Path) -> None:
        """Non-PASS steps get [ ] in progress.md."""
        state = SessionState.load_or_create(502, artifacts_dir=tmp_path)
        state.record_step(
            "review",
            Verdict(status="RETRY", reason="issues", evidence="3 issues", suggestion="fix"),
        )

        content = (tmp_path / "502" / "progress.md").read_text()
        assert "[ ] review" in content

    def test_progress_md_includes_cycles(self, tmp_path: Path) -> None:
        """Cycle information appears in progress.md."""
        state = SessionState.load_or_create(503, artifacts_dir=tmp_path)
        state.increment_cycle("code-review")
        state.record_step("fix", Verdict(status="PASS", reason="ok", evidence="ok", suggestion=""))

        content = (tmp_path / "503" / "progress.md").read_text()
        assert "code-review" in content
        assert "1" in content


@pytest.mark.medium
class TestStateJsonStructure:
    """Verify the JSON structure on disk."""

    def test_json_structure(self, tmp_path: Path) -> None:
        """session-state.json has expected structure."""
        state = SessionState.load_or_create(600, artifacts_dir=tmp_path)
        state.save_session_id("design", "sess-123")
        state.record_step(
            "design",
            Verdict(status="PASS", reason="ok", evidence="ok", suggestion=""),
        )

        json_path = tmp_path / "600" / "session-state.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())

        assert data["issue_number"] == 600
        assert "design" in data["sessions"]
        assert len(data["step_history"]) == 1
        assert data["last_completed_step"] == "design"
        assert data["last_transition_verdict"] is not None
        assert data["last_transition_verdict"]["status"] == "PASS"
