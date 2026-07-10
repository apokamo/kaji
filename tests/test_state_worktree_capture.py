"""Tests for SessionState.capture_worktree (Issue #218)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaji_harness.state import STATE_FILE, SessionState


@pytest.mark.small
class TestCaptureWorktree:
    def test_capture_sets_fields_and_persists(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("218", artifacts_dir=tmp_path)
        state.capture_worktree("/path/kaji-fix-218", "fix/218")
        assert state.worktree_dir == "/path/kaji-fix-218"
        assert state.branch_name == "fix/218"

        loaded = SessionState.load_or_create("218", artifacts_dir=tmp_path)
        assert loaded.worktree_dir == "/path/kaji-fix-218"
        assert loaded.branch_name == "fix/218"

    def test_capture_is_idempotent(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("218", artifacts_dir=tmp_path)
        state.capture_worktree("/path/kaji-fix-218", "fix/218")
        # 2 回目は上書きしない
        state.capture_worktree("/path/kaji-feat-218", "feat/218")
        assert state.worktree_dir == "/path/kaji-fix-218"
        assert state.branch_name == "fix/218"

    def test_load_legacy_state_without_keys(self, tmp_path: Path) -> None:
        """新規 key が無い旧 state file を load しても両フィールドが None で復元される。"""
        state_dir = tmp_path / "218"
        state_dir.mkdir(parents=True)
        legacy = {
            "issue_number": "218",
            "sessions": {},
            "step_history": [],
            "cycle_counts": {},
            "last_completed_step": None,
            "last_transition_verdict": None,
        }
        (state_dir / STATE_FILE).write_text(json.dumps(legacy), encoding="utf-8")

        loaded = SessionState.load_or_create("218", artifacts_dir=tmp_path)
        assert loaded.worktree_dir is None
        assert loaded.branch_name is None

    def test_json_includes_new_keys(self, tmp_path: Path) -> None:
        state = SessionState.load_or_create("218", artifacts_dir=tmp_path)
        state.capture_worktree("/p/kaji-fix-218", "fix/218")
        data = json.loads((tmp_path / "218" / STATE_FILE).read_text())
        assert data["worktree_dir"] == "/p/kaji-fix-218"
        assert data["branch_name"] == "fix/218"
