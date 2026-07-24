"""Tests for CLI argument builder.

Verifies that build_cli_args produces correct command-line arguments
for each supported agent (Claude, Codex, Antigravity) across various configurations.
"""

from pathlib import Path

import pytest

from kaji_harness.cli import build_cli_args
from kaji_harness.models import Step


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Temporary working directory for CLI args."""
    return tmp_path


def _make_step(
    agent: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: float | None = None,
) -> Step:
    """Helper to create a Step with minimal boilerplate."""
    return Step(
        id="test-step",
        skill="test-skill",
        agent=agent,
        model=model,
        effort=effort,
        max_budget_usd=max_budget_usd,
    )


# ==========================================
# Claude args
# ==========================================


class TestClaudeArgs:
    """build_cli_args for agent=claude."""

    @pytest.mark.small
    def test_basic_new_session(self, workdir: Path) -> None:
        """Basic Claude invocation without optional parameters."""
        step = _make_step("claude")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert args == ["claude", "-p", "--output-format", "stream-json", "--verbose", "do stuff"]

    @pytest.mark.small
    def test_with_model(self, workdir: Path) -> None:
        """Model flag is included when step has model set."""
        step = _make_step("claude", model="sonnet")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "--model" in args
        assert "sonnet" in args

    @pytest.mark.small
    def test_with_effort(self, workdir: Path) -> None:
        """Effort flag is included when step has effort set."""
        step = _make_step("claude", effort="high")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "--effort" in args
        assert "high" in args

    @pytest.mark.small
    def test_with_max_budget_usd(self, workdir: Path) -> None:
        """Max budget flag is included when step has max_budget_usd."""
        step = _make_step("claude", max_budget_usd=5.0)
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "--max-budget-usd" in args
        assert "5.0" in args

    @pytest.mark.small
    def test_with_session_id_resume(self, workdir: Path) -> None:
        """Resume flag is included when session_id is provided."""
        step = _make_step("claude")
        args = build_cli_args(
            step, "do stuff", workdir, session_id="session-123", execution_policy="sandbox"
        )
        assert "--resume" in args
        assert "session-123" in args

    @pytest.mark.small
    def test_execution_policy_auto(self, workdir: Path) -> None:
        """Auto execution policy includes bypassPermissions flag."""
        step = _make_step("claude")
        args = build_cli_args(step, "do stuff", workdir, session_id=None, execution_policy="auto")
        assert "--permission-mode" in args
        assert "bypassPermissions" in args

    @pytest.mark.small
    def test_execution_policy_sandbox(self, workdir: Path) -> None:
        """Sandbox execution policy does not include permission flag."""
        step = _make_step("claude")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "--permission-mode" not in args
        assert "bypassPermissions" not in args

    @pytest.mark.small
    def test_execution_policy_interactive(self, workdir: Path) -> None:
        """Interactive execution policy does not include permission flag."""
        step = _make_step("claude")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="interactive"
        )
        assert "--permission-mode" not in args
        assert "bypassPermissions" not in args


# ==========================================
# Codex args
# ==========================================


class TestCodexArgs:
    """build_cli_args for agent=codex."""

    @pytest.mark.small
    def test_basic_new_session(self, workdir: Path) -> None:
        """Basic Codex invocation without optional parameters."""
        step = _make_step("codex")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="interactive"
        )
        assert args == ["codex", "exec", "--json", "-C", str(workdir), "do stuff"]

    @pytest.mark.small
    def test_with_session_id_resume(self, workdir: Path) -> None:
        """Resume uses 'codex exec resume <id>' prefix."""
        step = _make_step("codex")
        args = build_cli_args(
            step, "do stuff", workdir, session_id="thread-456", execution_policy="interactive"
        )
        assert args[:4] == ["codex", "exec", "resume", "thread-456"]
        assert "--json" in args

    @pytest.mark.small
    def test_with_model(self, workdir: Path) -> None:
        """Model flag uses -m for Codex."""
        step = _make_step("codex", model="model-name")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "-m" in args
        assert "model-name" in args

    @pytest.mark.small
    def test_with_effort(self, workdir: Path) -> None:
        """Effort is passed via -c config flag."""
        step = _make_step("codex", effort="high")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "-c" in args
        assert 'model_reasoning_effort="high"' in args

    @pytest.mark.small
    def test_max_budget_usd_ignored(self, workdir: Path) -> None:
        """max_budget_usd is not passed to Codex."""
        step = _make_step("codex", max_budget_usd=5.0)
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "--max-budget-usd" not in args
        assert "5.0" not in args

    @pytest.mark.small
    def test_execution_policy_auto(self, workdir: Path) -> None:
        """Auto policy includes dangerously-bypass flag."""
        step = _make_step("codex")
        args = build_cli_args(step, "do stuff", workdir, session_id=None, execution_policy="auto")
        assert "--dangerously-bypass-approvals-and-sandbox" in args

    @pytest.mark.small
    def test_execution_policy_sandbox(self, workdir: Path) -> None:
        """Sandbox policy includes -s workspace-write."""
        step = _make_step("codex")
        args = build_cli_args(
            step, "do stuff", workdir, session_id=None, execution_policy="sandbox"
        )
        assert "-s" in args
        assert "workspace-write" in args


# ==========================================
# Antigravity args
# ==========================================


class TestAntigravityArgs:
    """build_cli_args for agent=antigravity."""

    @pytest.mark.small
    @pytest.mark.parametrize(
        ("execution_policy", "expected_flag"),
        [
            ("auto", "--dangerously-skip-permissions"),
            ("sandbox", "--sandbox"),
            ("interactive", None),
        ],
    )
    def test_execution_policy_mapping(
        self,
        workdir: Path,
        execution_policy: str,
        expected_flag: str | None,
    ) -> None:
        """AGY headless argv に policy を permission と sandbox の別軸で反映する。"""
        step = _make_step("antigravity")
        args = build_cli_args(
            step,
            "do stuff",
            workdir,
            session_id=None,
            execution_policy=execution_policy,
        )

        assert args[:3] == ["agy", "-p", "do stuff"]
        for policy_flag in ("--dangerously-skip-permissions", "--sandbox"):
            assert (policy_flag in args) is (policy_flag == expected_flag)

    @pytest.mark.small
    def test_model_and_effort_are_passed_after_prompt(self, workdir: Path) -> None:
        """AGY の model と effort を公開 CLI flag へ渡す。"""
        step = _make_step("antigravity", model="gemini-3-pro", effort="high")

        args = build_cli_args(
            step,
            "do stuff",
            workdir,
            session_id=None,
            execution_policy="auto",
        )

        assert args == [
            "agy",
            "-p",
            "do stuff",
            "--model",
            "gemini-3-pro",
            "--effort",
            "high",
            "--dangerously-skip-permissions",
        ]

    @pytest.mark.small
    def test_resume_session_is_rejected_defensively(self, workdir: Path) -> None:
        """validation を迂回した session_id を fail-loud に拒否する。"""
        step = _make_step("antigravity")

        with pytest.raises(AssertionError, match="does not support resume"):
            build_cli_args(
                step,
                "do stuff",
                workdir,
                session_id="unsupported-session",
                execution_policy="auto",
            )


class TestUnknownAgentArgs:
    """Defensive build_cli_args behavior for unsupported agents."""

    @pytest.mark.small
    def test_removed_gemini_agent_is_rejected(self, workdir: Path) -> None:
        """Validation を迂回した Gemini agent を fail-loud に拒否する。"""
        step = _make_step("gemini")

        with pytest.raises(ValueError, match=r"^Unknown agent: gemini$"):
            build_cli_args(
                step,
                "do stuff",
                workdir,
                session_id=None,
                execution_policy="auto",
            )
