"""Tests for recovery target selection without importing the command layer."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.errors import RecoveryTargetError
from kaji_harness.recovery.target import resolve_recover_issue_context, select_target_run_dir


@pytest.mark.small
class TestResolveRecoverIssueContext:
    def test_github_numeric_resolves_without_machine_id(self) -> None:
        config = SimpleNamespace(provider=SimpleNamespace(type="github"))
        expected = object()
        provider = MagicMock()
        provider.resolve_issue_context.return_value = expected

        result = resolve_recover_issue_context(config, provider, "282")

        assert result is expected
        provider.resolve_issue_context.assert_called_once_with("282")

    def test_local_numeric_uses_machine_id(self) -> None:
        config = SimpleNamespace(
            provider=SimpleNamespace(
                type="local",
                local=SimpleNamespace(machine_id="pc1"),
            )
        )
        expected = object()
        provider = MagicMock()
        provider.resolve_issue_context.return_value = expected

        result = resolve_recover_issue_context(config, provider, "3")

        assert result is expected
        provider.resolve_issue_context.assert_called_once_with("local-pc1-3")

    def test_github_rejects_local_form_id(self) -> None:
        config = SimpleNamespace(provider=SimpleNamespace(type="github"))
        provider = MagicMock()

        with pytest.raises(ValueError):
            resolve_recover_issue_context(config, provider, "local-pc1-3")

        provider.resolve_issue_context.assert_not_called()


def _write_run(run_dir: Path, events: list[dict[str, object]] | None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    if events is not None:
        lines = "\n".join(json.dumps(event) for event in events)
        (run_dir / "run.log").write_text(lines + "\n", encoding="utf-8")


@pytest.mark.medium
class TestSelectTargetRunDir:
    def test_latest_error_run_is_selected(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(runs / "20260101", [{"event": "workflow_end", "status": "ERROR"}])
        latest = runs / "20260202"
        _write_run(latest, [{"event": "workflow_end", "status": "ERROR"}])

        assert select_target_run_dir(runs, None) == latest

    def test_explicit_abort_run_is_selected(self, tmp_path: Path) -> None:
        target = tmp_path / "runs" / "r1"
        _write_run(target, [{"event": "workflow_end", "status": "ABORT"}])

        assert select_target_run_dir(target.parent, "r1") == target

    def test_missing_runs_dir_preserves_message(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        with pytest.raises(RecoveryTargetError) as caught:
            select_target_run_dir(runs, None)
        assert str(caught.value) == f"no runs found under {runs}"

    def test_missing_explicit_run_preserves_message(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        runs.mkdir()
        with pytest.raises(RecoveryTargetError) as caught:
            select_target_run_dir(runs, "missing")
        assert str(caught.value) == f"run dir not found: {runs / 'missing'}"

    def test_empty_runs_dir_preserves_message(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        runs.mkdir()
        with pytest.raises(RecoveryTargetError) as caught:
            select_target_run_dir(runs, None)
        assert str(caught.value) == f"no runs found under {runs}"

    def test_missing_run_log_preserves_message(self, tmp_path: Path) -> None:
        target = tmp_path / "runs" / "r1"
        _write_run(target, None)
        with pytest.raises(RecoveryTargetError) as caught:
            select_target_run_dir(target.parent, "r1")
        assert str(caught.value) == f"run.log not found: {target / 'run.log'}"

    def test_unreadable_run_log_preserves_message(self, tmp_path: Path) -> None:
        target = tmp_path / "runs" / "r1"
        _write_run(target, [])
        with (
            patch(
                "kaji_harness.recovery.target.read_run_log_events",
                side_effect=OSError("boom"),
            ),
            pytest.raises(RecoveryTargetError) as caught,
        ):
            select_target_run_dir(target.parent, "r1")
        assert str(caught.value) == f"cannot read {target / 'run.log'}: boom"

    def test_in_progress_run_preserves_message(self, tmp_path: Path) -> None:
        target = tmp_path / "runs" / "r1"
        _write_run(target, [{"event": "workflow_start"}])
        with pytest.raises(RecoveryTargetError) as caught:
            select_target_run_dir(target.parent, "r1")
        assert str(caught.value) == (
            "run r1 is still in progress (no workflow_end event); "
            "refusing to run failure triage against it"
        )

    def test_success_status_preserves_message(self, tmp_path: Path) -> None:
        target = tmp_path / "runs" / "r1"
        _write_run(target, [{"event": "workflow_end", "status": "SUCCESS"}])
        with pytest.raises(RecoveryTargetError) as caught:
            select_target_run_dir(target.parent, "r1")
        assert str(caught.value) == (
            "run r1 ended with status 'SUCCESS'; failure triage only applies to ERROR / ABORT runs"
        )
