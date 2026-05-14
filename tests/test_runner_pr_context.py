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
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.config import ExecutionConfig, KajiConfig, PathsConfig
from kaji_harness.models import CLIResult, CostInfo, Step, Verdict, Workflow
from kaji_harness.providers import LocalProvider, PRContext
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


def _make_verdict_output(status: str = "PASS") -> str:
    suggestion = "fix it" if status in ("ABORT", "BACK") else ""
    return (
        "Some output text.\n\n"
        "---VERDICT---\n"
        f"status: {status}\n"
        'reason: "ok"\n'
        'evidence: "test"\n'
        f'suggestion: "{suggestion}"\n'
        "---END_VERDICT---\n"
    )


def _make_cli_result(status: str = "PASS") -> CLIResult:
    return CLIResult(
        full_output=_make_verdict_output(status),
        session_id="sess-001",
        cost=CostInfo(usd=0.0),
        stderr="",
    )


def _one_step_workflow() -> Workflow:
    return Workflow(
        name="one-step",
        description="single step",
        execution_policy="auto",
        steps=[
            Step(
                id="implement",
                skill="impl-skill",
                agent="claude",
                on={"PASS": "end", "ABORT": "end"},
            ),
        ],
    )


def _bootstrap_local_runner(tmp_path: Path) -> WorkflowRunner:
    """``provider.type=local`` で 1-step workflow を回せる WorkflowRunner を構築する。

    Phase 3-e 以降は ``WorkflowRunner.run()`` 前に IssueContext 解決が走る
    ため、Issue dir を事前に作っておく（``test_workflow_execution.py`` の
    ``_ensure_local_issue`` と同パターン）。
    """
    import subprocess as _sp

    # gl:21: provider.type='local' requires a git repo.
    _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(parents=True, exist_ok=True)
    config_file = kaji_dir / "config.toml"
    config_file.write_text(
        "[paths]\n"
        'skill_dir = ".claude/skills"\n'
        'artifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\n"
        "default_timeout = 1800\n\n"
        "[provider]\n"
        'type = "local"\n\n'
        "[provider.local]\n"
        'machine_id = "pc1"\n'
        'default_branch = "main"\n'
    )
    config = KajiConfig._load(config_file)

    issue_no = 1
    counter_path = kaji_dir / "counters" / "pc1.txt"
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    (kaji_dir / "issues").mkdir(parents=True, exist_ok=True)
    counter_path.write_text(str(issue_no - 1))
    LocalProvider(repo_root=tmp_path, machine_id="pc1").create_issue(
        title="test issue",
        body="body",
        labels=["type:feature"],
        slug="test-1",
    )

    return WorkflowRunner(
        workflow=_one_step_workflow(),
        issue_number=issue_no,
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=config,
    )


@pytest.mark.medium
class TestRunnerPromptInjectionRoundTrip:
    """Issue 完了条件: ``WorkflowRunner.run()`` 経由で prompt に
    ``pr_id`` / ``pr_ref`` が自動注入されることの round-trip 検証。

    LocalProvider 配下で ``resolve_pr_context`` を patch することで、
    GitLab 設定なしに「provider が ``PRContext`` を返す経路」を再現する
    （注入経路は provider 中立: 完了条件 4 で grep 検証済）。
    """

    def test_pr_context_present_injects_pr_id_and_pr_ref_into_prompt(self, tmp_path: Path) -> None:
        runner = _bootstrap_local_runner(tmp_path)
        captured: dict[str, str] = {}

        def fake_execute_cli(**kwargs: object) -> CLIResult:
            captured["prompt"] = str(kwargs["prompt"])
            return _make_cli_result("PASS")

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=fake_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            patch.object(
                LocalProvider,
                "resolve_pr_context",
                return_value=PRContext(pr_id="42", pr_ref="gl:42"),
            ),
        ):
            runner.run()

        prompt = captured["prompt"]
        assert "- pr_id: 42" in prompt
        assert "- pr_ref: gl:42" in prompt

    def test_pr_context_none_omits_pr_variables_from_prompt(self, tmp_path: Path) -> None:
        runner = _bootstrap_local_runner(tmp_path)
        captured: dict[str, str] = {}

        def fake_execute_cli(**kwargs: object) -> CLIResult:
            captured["prompt"] = str(kwargs["prompt"])
            return _make_cli_result("PASS")

        # LocalProvider.resolve_pr_context は既定で None を返すが、明示的に
        # patch して「None を返す provider」のケースを意図として固定する。
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=fake_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            patch.object(LocalProvider, "resolve_pr_context", return_value=None),
        ):
            runner.run()

        prompt = captured["prompt"]
        assert "- pr_id:" not in prompt
        assert "- pr_ref:" not in prompt
        # IssueContext 由来の他の変数は注入され続けることを併せて確認する
        # （pr_* 不在 = 注入経路全体が壊れた、という誤検出を防ぐ）。
        assert "- issue_id: local-pc1-1" in prompt

    def test_gitlab_provider_error_warns_and_continues_without_pr_variables(
        self, tmp_path: Path
    ) -> None:
        """``GitLabProviderError`` raise 時、workflow は止まらず prompt から
        ``pr_*`` を除外する（``_resolve_pr_context_safe`` の WARN + None 経路）。
        """
        runner = _bootstrap_local_runner(tmp_path)
        captured: dict[str, str] = {}

        def fake_execute_cli(**kwargs: object) -> CLIResult:
            captured["prompt"] = str(kwargs["prompt"])
            return _make_cli_result("PASS")

        buf = io.StringIO()
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=fake_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
            patch.object(
                LocalProvider,
                "resolve_pr_context",
                side_effect=GitLabProviderError("API down"),
            ),
            redirect_stderr(buf),
        ):
            state = runner.run()

        assert "- pr_id:" not in captured["prompt"]
        assert "WARNING" in buf.getvalue()
        assert "API down" in buf.getvalue()
        # workflow は止まっていない（1 step 完了している）
        assert state.last_completed_step == "implement"
        assert isinstance(state.last_transition_verdict, Verdict)
        assert state.last_transition_verdict.status == "PASS"


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
