"""Tests for ``WorkflowRunner._resolve_pr_context_safe`` and prompt-injection
provider neutrality (Issue ``local-pc5090-7``).

The runner is exercised in two slices:

1. ``_resolve_pr_context_safe`` — verifies the helper catches
   ``GitLabProviderError`` only and re-raises everything else (implementation
   bugs / signal interrupts) as required by
   ``docs/reference/python/error-handling.md`` § 基本原則 1.
2. Provider-neutrality grep — ensures ``kaji_harness/prompt.py`` and
   ``kaji_harness/runner.py`` do not branch on ``provider.type == "gitlab"``
   (Issue 完了条件「skill のプロンプト注入経路に GitHub/GitLab 分岐が入って
   いない」).
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kaji_harness.config import ExecutionConfig, KajiConfig, PathsConfig
from kaji_harness.models import Workflow
from kaji_harness.providers import PRContext
from kaji_harness.providers.gitlab import GitLabProviderError
from kaji_harness.runner import WorkflowRunner


@pytest.fixture
def runner(tmp_path: Path) -> WorkflowRunner:
    workflow = Workflow(
        name="test",
        description="test",
        execution_policy="sequential",
        steps=[],
        cycles=[],
    )
    return WorkflowRunner(
        workflow=workflow,
        issue_number="1",
        project_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        config=KajiConfig(
            repo_root=tmp_path,
            paths=PathsConfig(skill_dir=".claude/skills", artifacts_dir=".kaji/artifacts"),
            execution=ExecutionConfig(default_timeout=1800),
        ),
    )


@pytest.mark.medium
class TestResolvePrContextSafe:
    """``_resolve_pr_context_safe`` の例外吸収範囲（Issue local-pc5090-7）。"""

    def test_returns_pr_context_on_success(self, runner: WorkflowRunner) -> None:
        provider = MagicMock()
        provider.resolve_pr_context.return_value = PRContext(pr_id="42", pr_ref="gl:42")
        result = runner._resolve_pr_context_safe(provider, "feat/x")
        assert result == PRContext(pr_id="42", pr_ref="gl:42")
        provider.resolve_pr_context.assert_called_once_with("feat/x")

    def test_returns_none_passthrough(self, runner: WorkflowRunner) -> None:
        provider = MagicMock()
        provider.resolve_pr_context.return_value = None
        assert runner._resolve_pr_context_safe(provider, "feat/x") is None

    def test_gitlab_provider_error_is_caught_and_warned(self, runner: WorkflowRunner) -> None:
        provider = MagicMock()
        provider.resolve_pr_context.side_effect = GitLabProviderError("API down")
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = runner._resolve_pr_context_safe(provider, "feat/x")
        assert result is None
        stderr = buf.getvalue()
        assert "WARNING" in stderr
        assert "feat/x" in stderr
        assert "API down" in stderr

    def test_attribute_error_is_not_caught(self, runner: WorkflowRunner) -> None:
        """Implementation bugs must surface, not be swallowed."""
        provider = MagicMock()
        provider.resolve_pr_context.side_effect = AttributeError("bug")
        with pytest.raises(AttributeError, match="bug"):
            runner._resolve_pr_context_safe(provider, "feat/x")

    def test_keyboard_interrupt_is_not_caught(self, runner: WorkflowRunner) -> None:
        provider = MagicMock()
        provider.resolve_pr_context.side_effect = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            runner._resolve_pr_context_safe(provider, "feat/x")

    def test_type_error_is_not_caught(self, runner: WorkflowRunner) -> None:
        provider = MagicMock()
        provider.resolve_pr_context.side_effect = TypeError("contract violation")
        with pytest.raises(TypeError, match="contract violation"):
            runner._resolve_pr_context_safe(provider, "feat/x")


@pytest.mark.small
class TestProviderNeutralInjectionPath:
    """注入経路に GitHub/GitLab 分岐が入っていないことを diff レベルで担保する。"""

    @pytest.fixture
    def kaji_root(self) -> Path:
        # tests/ -> repo root
        return Path(__file__).resolve().parent.parent

    @pytest.mark.parametrize("module", ["prompt.py", "runner.py"])
    def test_no_provider_type_gitlab_branching(self, kaji_root: Path, module: str) -> None:
        path = kaji_root / "kaji_harness" / module
        source = path.read_text(encoding="utf-8")
        # 禁止パターン: provider.type / provider_type の "gitlab" / "github" 文字列比較
        forbidden = [
            r'provider\.type\s*==\s*["\']gitlab["\']',
            r'provider\.type\s*==\s*["\']github["\']',
            r'provider_type\s*==\s*["\']gitlab["\']',
            r'provider_type\s*==\s*["\']github["\']',
            r"isinstance\([^)]*GitLabProvider\)",
            r"isinstance\([^)]*GitHubProvider\)",
        ]
        for pattern in forbidden:
            assert not re.search(pattern, source), (
                f"{module} contains provider-type branching: {pattern!r}"
            )
