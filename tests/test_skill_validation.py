"""Tests for skill existence validation.

Covers validate_skill_exists: skill_dir-based directory resolution,
not-found error, path traversal security checks, and symlink resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.errors import SecurityError, SkillNotFound
from kaji_harness.skill import validate_skill_exists

# ============================================================
# Helper: create skill directory structure in tmp_path
# ============================================================


def _create_skill(tmp_path: Path, skill_dir: str, skill_name: str) -> None:
    """Create a SKILL.md file under the given skill directory."""
    skill_path = tmp_path / skill_dir / skill_name
    skill_path.mkdir(parents=True, exist_ok=True)
    (skill_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")


# ============================================================
# Small tests — skill_dir parameter-based resolution
# ============================================================


@pytest.mark.small
class TestSkillDirResolution:
    """validate_skill_exists resolves skills via skill_dir parameter."""

    def test_default_claude_skill_dir(self, tmp_path: Path) -> None:
        _create_skill(tmp_path, ".claude/skills", "my-skill")

        validate_skill_exists("my-skill", tmp_path, ".claude/skills")

    def test_custom_skill_dir(self, tmp_path: Path) -> None:
        _create_skill(tmp_path, "custom/skills", "my-skill")

        validate_skill_exists("my-skill", tmp_path, "custom/skills")

    def test_agents_skill_dir(self, tmp_path: Path) -> None:
        _create_skill(tmp_path, ".agents/skills", "my-skill")

        validate_skill_exists("my-skill", tmp_path, ".agents/skills")


# ============================================================
# Small tests — SkillNotFound
# ============================================================


@pytest.mark.small
class TestSkillNotFound:
    """validate_skill_exists raises SkillNotFound when the skill does not exist."""

    def test_missing_skill_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SkillNotFound):
            validate_skill_exists("nonexistent-skill", tmp_path, ".claude/skills")


# ============================================================
# Small tests — Path traversal security
# ============================================================


@pytest.mark.small
class TestPathTraversalPasswd:
    """Path traversal with ../../etc/passwd raises SecurityError."""

    def test_path_traversal_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError):
            validate_skill_exists("../../etc/passwd", tmp_path, ".claude/skills")


@pytest.mark.small
class TestPathTraversalDotDot:
    """Skill name containing '..' raises SecurityError."""

    def test_dotdot_in_skill_name_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError):
            validate_skill_exists("../secret-skill", tmp_path, ".claude/skills")


# ============================================================
# Medium tests — Symlink resolution
# ============================================================


@pytest.mark.medium
class TestSymlinkResolution:
    """validate_skill_exists resolves skills through symlinks."""

    def test_symlink_skill_dir_resolves(self, tmp_path: Path) -> None:
        """skill_dir pointing to a symlinked directory resolves correctly."""
        # Create actual skill in .claude/skills
        _create_skill(tmp_path, ".claude/skills", "my-skill")

        # Create .agents/skills as symlink to .claude/skills
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        (agents_dir / "skills").symlink_to(tmp_path / ".claude" / "skills")

        # Validate via the symlink path
        validate_skill_exists("my-skill", tmp_path, ".agents/skills")

    def test_symlink_individual_skill_resolves(self, tmp_path: Path) -> None:
        """Individual skill symlinks resolve correctly."""
        # Create actual skill
        _create_skill(tmp_path, ".claude/skills", "real-skill")

        # Create skill_dir with a symlinked skill
        custom_dir = tmp_path / "custom" / "skills" / "linked-skill"
        custom_dir.mkdir(parents=True)
        (custom_dir / "SKILL.md").symlink_to(
            tmp_path / ".claude" / "skills" / "real-skill" / "SKILL.md"
        )

        validate_skill_exists("linked-skill", tmp_path, "custom/skills")


# ============================================================
# Medium tests — Runner integration (skill_dir from config)
# ============================================================


@pytest.mark.medium
class TestRunnerSkillDirIntegration:
    """WorkflowRunner passes config.paths.skill_dir to validate_skill_exists."""

    def test_runner_validates_with_skill_dir(self, tmp_path: Path) -> None:
        """Runner uses config.paths.skill_dir for skill validation."""
        from unittest.mock import patch

        from kaji_harness.config import KajiConfig
        from kaji_harness.models import CLIResult, Step, Workflow

        # Create skill in custom directory
        _create_skill(tmp_path, "my-skills", "test-skill")

        # Create config with custom skill_dir
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = "my-skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n'
        )
        config = KajiConfig._load(config_dir / "config.toml")

        workflow = Workflow(
            name="test",
            description="test",
            execution_policy="auto",
            steps=[
                Step(
                    id="step1",
                    skill="test-skill",
                    agent="claude",
                    on={"PASS": "end"},
                ),
            ],
        )

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return CLIResult(
                full_output='---VERDICT---\nstatus: PASS\nreason: "ok"\nevidence: "ok"\nsuggestion: ""\n---END_VERDICT---\n',
                session_id="sess-1",
            )

        from kaji_harness.runner import WorkflowRunner

        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=999,
            project_root=tmp_path,
            artifacts_dir=artifacts_dir,
            config=config,
        )

        with patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli):
            state = runner.run()

        assert state.last_completed_step == "step1"


# ============================================================
# Large tests — E2E kaji run with real config
# ============================================================


@pytest.mark.large
class TestSkillDirE2E:
    """E2E: kaji run with skill_dir config resolves skills correctly."""

    def test_kaji_run_step_with_skill_dir(self, tmp_path: Path) -> None:
        """kaji run --step uses config skill_dir for pre-flight validation."""
        import os
        import subprocess
        import sys

        # Set up project structure
        kaji_dir = tmp_path / ".kaji"
        kaji_dir.mkdir()
        (kaji_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n'
        )

        # Create skill
        _create_skill(tmp_path, ".claude/skills", "issue-design")

        # Create workflow
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "test.yaml").write_text(
            """name: test-workflow
description: test
execution_policy: auto
steps:
  - id: design
    skill: issue-design
    agent: claude
    on:
      PASS: end
      ABORT: end
"""
        )

        python_dir = str(Path(sys.executable).parent)
        env = {**os.environ, "PATH": python_dir}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(workflows_dir / "test.yaml"),
                "999",
                "--step",
                "design",
                "--workdir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # Exit code 3 = CLIExecutionError/CLINotFoundError (past skill validation)
        assert result.returncode == 3, f"Expected exit 3, got {result.returncode}: {result.stderr}"

    def test_kaji_run_step_with_custom_skill_dir(self, tmp_path: Path) -> None:
        """kaji run --step with non-default skill_dir resolves correctly."""
        import os
        import subprocess
        import sys

        kaji_dir = tmp_path / ".kaji"
        kaji_dir.mkdir()
        (kaji_dir / "config.toml").write_text(
            '[paths]\nskill_dir = "custom/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n'
        )

        _create_skill(tmp_path, "custom/skills", "my-skill")

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "test.yaml").write_text(
            """name: test-workflow
description: test
execution_policy: auto
steps:
  - id: step1
    skill: my-skill
    agent: claude
    on:
      PASS: end
      ABORT: end
"""
        )

        python_dir = str(Path(sys.executable).parent)
        env = {**os.environ, "PATH": python_dir}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(workflows_dir / "test.yaml"),
                "999",
                "--step",
                "step1",
                "--workdir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # Exit 3 = past skill validation, failed at CLI execution
        assert result.returncode == 3, f"Expected exit 3, got {result.returncode}: {result.stderr}"

    def test_kaji_run_missing_skill_dir_in_config_fails(self, tmp_path: Path) -> None:
        """kaji run fails when skill_dir is not set in config."""
        import subprocess
        import sys

        kaji_dir = tmp_path / ".kaji"
        kaji_dir.mkdir()
        # No skill_dir in config (but artifacts_dir is present to isolate the error)
        (kaji_dir / "config.toml").write_text(
            '[paths]\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n'
        )

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "test.yaml").write_text(
            """name: test-workflow
description: test
execution_policy: auto
steps:
  - id: step1
    skill: my-skill
    agent: claude
    on:
      PASS: end
"""
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(workflows_dir / "test.yaml"),
                "999",
                "--step",
                "step1",
                "--workdir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail with config error (exit 2)
        assert result.returncode == 2, f"Expected exit 2, got {result.returncode}: {result.stderr}"
        assert "skill_dir" in result.stderr
