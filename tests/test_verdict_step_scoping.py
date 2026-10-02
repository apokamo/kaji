"""Issue #449: verdict 解決の step scoping 回帰テスト。

``_StepExecutor._resolve_step_verdict`` が、直前の別 step の作業報告コメントを
現在 step の verdict として採用しないことを検証する。

- exec / exec_script step は comment fallback を行わない（stdout / ``verdict.yaml`` のみ）
- agent step は comment fallback を維持する（自 step marker 付きコメントは採用）

Small は I/O 境界を mock で置換して判断層を決定的に駆動する。Medium は
``verdict.yaml`` と run.log の実ファイル書き込みを検証する。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.logger import RunLogger
from kaji_harness.models import CLIResult, Step, Verdict
from kaji_harness.providers.models import Comment
from kaji_harness.runner import RunIssueContext, _ExecutionSettings, _StepExecutor
from kaji_harness.verdict import load_verdict_yaml

# 前 step の作業報告コメント（marker 無し）と現 step の開始が同一秒に収まる OB の再現値
PREV_COMMENT_CREATED_AT = "2026-06-04T12:00:00Z"
ATTEMPT_STARTED_AT = datetime(2026, 6, 4, 12, 0, 0, 500_000, tzinfo=UTC)


def _block(status: str) -> str:
    return (
        f"---VERDICT---\nstatus: {status}\nreason: |\n  r\n"
        f"evidence: |\n  e\nsuggestion: |\n  s\n---END_VERDICT---\n"
    )


def _prev_step_comment() -> Comment:
    """前 step が投稿した作業報告コメント（末尾 PASS、marker 無し）。"""
    return Comment(
        author="bot",
        body="PR 作成を完了した\n\n" + _block("PASS"),
        created_at=PREV_COMMENT_CREATED_AT,
    )


def _settings(kind: str, workdir: Path) -> _ExecutionSettings:
    return _ExecutionSettings(
        kind=kind,
        is_script_like=kind in ("exec", "exec_script"),
        default_timeout=60,
        timeout=60,
        workdir=workdir,
    )


def _step(kind: str) -> Step:
    on = {"PASS": "end", "RETRY": "end", "ABORT": "end"}
    if kind == "exec":
        return Step(id="review-poll", exec=["true"], on=on)
    if kind == "exec_script":
        return Step(id="review-poll", skill="review-poll-script", agent=None, on=on)
    return Step(id="review-poll", skill="review", agent="claude", on=on)


def _executor(logger: Any, comments: list[Comment]) -> tuple[_StepExecutor, MagicMock]:
    provider = MagicMock()
    provider.view_issue.return_value = MagicMock(comments=comments)
    run_ctx = RunIssueContext(
        input_id="99",
        canonical_id="99",
        issue_ref="#99",
        issue_context=MagicMock(),
    )
    executor = _StepExecutor(
        workflow=MagicMock(),
        config=MagicMock(),
        provider=provider,
        run_ctx=run_ctx,
        run_dir=Path("/fake/run"),
        logger=logger,
        state=MagicMock(),
        project_root=Path("/fake"),
        verbose=False,
        resolve_pr_context=MagicMock(),
    )
    return executor, provider


@pytest.mark.small
class TestResolveStepVerdictScopingSmall:
    @pytest.mark.parametrize("kind", ["exec", "exec_script"])
    def test_script_like_step_ignores_previous_step_comment(self, kind: str) -> None:
        """OB 再現: 同一秒の前 step PASS コメントではなく、自身の stdout RETRY が採用される。"""
        logger = MagicMock()
        executor, provider = _executor(logger, [_prev_step_comment()])
        attempt_dir = Path("/fake/attempt-001")
        verdict_path = attempt_dir / "verdict.yaml"

        with (
            patch.object(Path, "exists", return_value=False) as mock_exists,
            patch("kaji_harness.runner.write_verdict_yaml") as mock_write,
        ):
            verdict = executor._resolve_step_verdict(
                _step(kind),
                _settings(kind, Path("/fake")),
                CLIResult(full_output=_block("RETRY")),
                attempt_dir,
                verdict_path,
                ATTEMPT_STARTED_AT,
            )

        mock_exists.assert_called()
        assert verdict.status == "RETRY"
        logger.log_verdict_source.assert_called_once_with("review-poll", "stdout", "attempt-001")
        mock_write.assert_called_once()
        saved = mock_write.call_args.args[1]
        assert saved.status == "RETRY"
        provider.view_issue.assert_not_called()

    def test_agent_step_keeps_comment_fallback_for_own_marker(self) -> None:
        """agent step は自 step marker 付きコメントを引き続き採用する（過剰無効化の防止）。"""
        own = Comment(
            author="bot",
            body="<!-- kaji-verdict: step=review-poll status=PASS -->\n\n作業報告\n\n"
            + _block("PASS"),
            created_at=PREV_COMMENT_CREATED_AT,
        )
        logger = MagicMock()
        executor, provider = _executor(logger, [own])
        attempt_dir = Path("/fake/attempt-001")

        with (
            patch.object(Path, "exists", return_value=False),
            patch("kaji_harness.runner.write_verdict_yaml"),
            patch("kaji_harness.runner.create_verdict_formatter"),
        ):
            verdict = executor._resolve_step_verdict(
                _step("agent"),
                _settings("agent", Path("/fake")),
                CLIResult(full_output="verdict 無し"),
                attempt_dir,
                attempt_dir / "verdict.yaml",
                ATTEMPT_STARTED_AT,
            )

        assert verdict.status == "PASS"
        logger.log_verdict_source.assert_called_once_with("review-poll", "comment", "attempt-001")
        provider.view_issue.assert_called_once()

    def test_agent_step_excludes_other_step_marker_comment(self) -> None:
        """agent step は別 step marker 付きコメントを採用せず stdout で解決する。"""
        other = Comment(
            author="bot",
            body="<!-- kaji-verdict: step=pr status=PASS -->\n\n作業報告\n\n" + _block("PASS"),
            created_at=PREV_COMMENT_CREATED_AT,
        )
        logger = MagicMock()
        executor, _ = _executor(logger, [other])
        attempt_dir = Path("/fake/attempt-001")

        with (
            patch.object(Path, "exists", return_value=False),
            patch("kaji_harness.runner.write_verdict_yaml"),
            patch("kaji_harness.runner.create_verdict_formatter"),
        ):
            verdict = executor._resolve_step_verdict(
                _step("agent"),
                _settings("agent", Path("/fake")),
                CLIResult(full_output=_block("RETRY")),
                attempt_dir,
                attempt_dir / "verdict.yaml",
                ATTEMPT_STARTED_AT,
            )

        assert verdict.status == "RETRY"
        logger.log_verdict_source.assert_called_once_with("review-poll", "stdout", "attempt-001")


@pytest.mark.medium
class TestResolveStepVerdictScopingMedium:
    @pytest.mark.parametrize("kind", ["exec", "exec_script"])
    def test_retry_is_persisted_and_logged_with_stdout_source(
        self, kind: str, tmp_path: Path
    ) -> None:
        """実 verdict.yaml / run.log に前 step の PASS ではなく RETRY が残る。"""
        log_path = tmp_path / "run.log"
        logger = RunLogger(log_path)
        executor, _ = _executor(logger, [_prev_step_comment()])
        attempt_dir = tmp_path / "attempt-001"
        attempt_dir.mkdir()
        verdict_path = attempt_dir / "verdict.yaml"

        verdict = executor._resolve_step_verdict(
            _step(kind),
            _settings(kind, tmp_path),
            CLIResult(full_output=_block("RETRY")),
            attempt_dir,
            verdict_path,
            ATTEMPT_STARTED_AT,
        )

        assert verdict.status == "RETRY"
        persisted: Verdict = load_verdict_yaml(verdict_path, {"PASS", "RETRY", "ABORT"})
        assert persisted.status == "RETRY"
        events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
        sources = [e for e in events if e["event"] == "verdict_source"]
        assert len(sources) == 1
        assert sources[0]["source"] == "stdout"
