"""Tests for kaji_harness.config — KajiConfig discovery and loading.

Covers:
- TOML parsing (valid, empty, invalid, unknown keys)
- PathsConfig defaults
- Repo root calculation from config path
- artifacts_dir resolution
- ConfigNotFoundError
- Config discovery walk-up algorithm
- CLI integration with config discovery
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from kaji_harness.config import KajiConfig, PathsConfig
from kaji_harness.errors import ConfigNotFoundError

# ============================================================
# Small tests — TOML parsing and data model
# ============================================================


@pytest.mark.small
class TestPathsConfigDefaults:
    """PathsConfig provides correct default values."""

    def test_default_artifacts_dir(self) -> None:
        config = PathsConfig()
        assert config.artifacts_dir == ".kaji-artifacts"


@pytest.mark.small
class TestKajiConfigLoadValid:
    """KajiConfig._load parses valid TOML with [paths] section."""

    def test_load_with_paths_section(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text('[paths]\nartifacts_dir = "custom-artifacts"\n')

        config = KajiConfig._load(config_file)

        assert config.repo_root == tmp_path
        assert config.paths.artifacts_dir == "custom-artifacts"

    def test_load_empty_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("")

        config = KajiConfig._load(config_file)

        assert config.repo_root == tmp_path
        assert config.paths.artifacts_dir == ".kaji-artifacts"  # default

    def test_load_empty_paths_section(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("[paths]\n")

        config = KajiConfig._load(config_file)

        assert config.paths.artifacts_dir == ".kaji-artifacts"  # default

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nartifacts_dir = "out"\nunknown_key = "value"\n\n[unknown_section]\nfoo = 42\n'
        )

        config = KajiConfig._load(config_file)

        assert config.paths.artifacts_dir == "out"


@pytest.mark.small
class TestKajiConfigLoadInvalid:
    """KajiConfig._load raises on invalid TOML."""

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("this is not valid toml [[[")

        with pytest.raises(tomllib.TOMLDecodeError):
            KajiConfig._load(config_file)


@pytest.mark.small
class TestKajiConfigRepoRoot:
    """repo_root is correctly derived from config.toml path."""

    def test_repo_root_is_grandparent_of_config(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("")

        config = KajiConfig._load(config_file)

        assert config.repo_root == tmp_path


@pytest.mark.small
class TestKajiConfigArtifactsDir:
    """artifacts_dir property resolves repo_root + paths.artifacts_dir."""

    def test_default_artifacts_dir(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("")

        config = KajiConfig._load(config_file)

        assert config.artifacts_dir == tmp_path / ".kaji-artifacts"

    def test_custom_artifacts_dir(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text('[paths]\nartifacts_dir = "build/artifacts"\n')

        config = KajiConfig._load(config_file)

        assert config.artifacts_dir == tmp_path / "build/artifacts"


@pytest.mark.small
class TestConfigNotFoundErrorMessage:
    """ConfigNotFoundError includes search start path."""

    def test_error_message_contains_path(self) -> None:
        err = ConfigNotFoundError(Path("/some/start/path"))
        assert "/some/start/path" in str(err)

    def test_error_message_descriptive(self) -> None:
        err = ConfigNotFoundError(Path("/tmp"))
        msg = str(err)
        assert ".kaji/config.toml" in msg


# ============================================================
# Medium tests — Config discovery with filesystem
# ============================================================


@pytest.mark.medium
class TestKajiConfigDiscover:
    """Config discovery walk-up from start directory."""

    def test_discover_from_root(self, tmp_path: Path) -> None:
        """Discover config when start_dir contains .kaji/config.toml."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("")

        config = KajiConfig.discover(start_dir=tmp_path)

        assert config.repo_root == tmp_path

    def test_discover_from_subdir(self, tmp_path: Path) -> None:
        """Discover config from a nested subdirectory (walk-up)."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("")

        subdir = tmp_path / "src" / "deep" / "nested"
        subdir.mkdir(parents=True)

        config = KajiConfig.discover(start_dir=subdir)

        assert config.repo_root == tmp_path

    def test_discover_not_found_raises(self, tmp_path: Path) -> None:
        """Raises ConfigNotFoundError when no config exists."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with pytest.raises(ConfigNotFoundError) as exc_info:
            KajiConfig.discover(start_dir=empty_dir)

        assert str(empty_dir) in str(exc_info.value)

    def test_discover_with_custom_artifacts_dir(self, tmp_path: Path) -> None:
        """Discovered config correctly loads custom artifacts_dir."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('[paths]\nartifacts_dir = "my-output"\n')

        config = KajiConfig.discover(start_dir=tmp_path)

        assert config.artifacts_dir == tmp_path / "my-output"

    def test_discover_ignores_inner_kaji_dirs(self, tmp_path: Path) -> None:
        """Discovery finds the nearest .kaji/config.toml, not a deeper one."""
        # Create config at root level
        root_config = tmp_path / ".kaji"
        root_config.mkdir()
        (root_config / "config.toml").write_text('[paths]\nartifacts_dir = "root-arts"\n')

        # Create a subdirectory with its own .kaji/config.toml
        inner = tmp_path / "sub"
        inner.mkdir()
        inner_config = inner / ".kaji"
        inner_config.mkdir()
        (inner_config / "config.toml").write_text('[paths]\nartifacts_dir = "inner-arts"\n')

        # Discover from inner - should find inner's config
        config = KajiConfig.discover(start_dir=inner)
        assert config.repo_root == inner
        assert config.artifacts_dir == inner / "inner-arts"


@pytest.mark.medium
class TestSessionStateWithArtifactsDir:
    """SessionState uses artifacts_dir parameter for path resolution."""

    def test_load_or_create_with_artifacts_dir(self, tmp_path: Path) -> None:
        from kaji_harness.state import SessionState

        arts_dir = tmp_path / "custom-artifacts"
        state = SessionState.load_or_create(42, artifacts_dir=arts_dir)

        assert state.issue_number == 42

    def test_persist_writes_to_artifacts_dir(self, tmp_path: Path) -> None:
        from kaji_harness.models import Verdict
        from kaji_harness.state import SessionState

        arts_dir = tmp_path / "my-artifacts"
        state = SessionState.load_or_create(55, artifacts_dir=arts_dir)
        state.record_step(
            "design",
            Verdict(status="PASS", reason="ok", evidence="ok", suggestion=""),
        )

        # Verify files are written under artifacts_dir
        state_file = arts_dir / "55" / "session-state.json"
        assert state_file.exists()
        progress_file = arts_dir / "55" / "progress.md"
        assert progress_file.exists()

    def test_load_round_trip_with_artifacts_dir(self, tmp_path: Path) -> None:
        from kaji_harness.models import Verdict
        from kaji_harness.state import SessionState

        arts_dir = tmp_path / "arts"
        state = SessionState.load_or_create(77, artifacts_dir=arts_dir)
        state.save_session_id("design", "sess-abc")
        state.record_step(
            "design",
            Verdict(status="PASS", reason="done", evidence="ev", suggestion=""),
        )

        loaded = SessionState.load_or_create(77, artifacts_dir=arts_dir)
        assert loaded.sessions["design"] == "sess-abc"
        assert loaded.last_completed_step == "design"


@pytest.mark.medium
class TestRunnerWithConfig:
    """WorkflowRunner uses project_root and artifacts_dir from config."""

    def test_runner_accepts_project_root_and_artifacts_dir(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from kaji_harness.models import CLIResult, CostInfo, Step, Workflow
        from kaji_harness.runner import WorkflowRunner

        workflow = Workflow(
            name="test",
            description="test",
            execution_policy="auto",
            steps=[
                Step(
                    id="step1",
                    skill="test-skill",
                    agent="claude",
                    on={"PASS": "end", "ABORT": "end"},
                ),
            ],
        )

        project_root = tmp_path / "project"
        project_root.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            return CLIResult(
                full_output=(
                    "---VERDICT---\n"
                    'status: PASS\nreason: "ok"\n'
                    'evidence: "test"\nsuggestion: ""\n'
                    "---END_VERDICT---\n"
                ),
                session_id="sess-1",
                cost=CostInfo(usd=0.01),
                stderr="",
            )

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                project_root=project_root,
                artifacts_dir=artifacts_dir,
            )
            state = runner.run()

        assert state.last_completed_step == "step1"
        # Verify artifacts were written to artifacts_dir
        assert (artifacts_dir / "99").exists()

    def test_runner_passes_project_root_to_cli(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from kaji_harness.models import CLIResult, CostInfo, Step, Workflow
        from kaji_harness.runner import WorkflowRunner

        workflow = Workflow(
            name="test",
            description="test",
            execution_policy="auto",
            steps=[
                Step(
                    id="step1",
                    skill="test-skill",
                    agent="claude",
                    on={"PASS": "end", "ABORT": "end"},
                ),
            ],
        )

        project_root = tmp_path / "project"
        project_root.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        captured_workdir: list[object] = []

        def mock_execute_cli(**kwargs: object) -> CLIResult:
            captured_workdir.append(kwargs.get("workdir"))
            return CLIResult(
                full_output=(
                    "---VERDICT---\n"
                    'status: PASS\nreason: "ok"\n'
                    'evidence: "test"\nsuggestion: ""\n'
                    "---END_VERDICT---\n"
                ),
                session_id="sess-1",
                cost=CostInfo(usd=0.01),
                stderr="",
            )

        with (
            patch("kaji_harness.runner.execute_cli", side_effect=mock_execute_cli),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            runner = WorkflowRunner(
                workflow=workflow,
                issue_number=99,
                project_root=project_root,
                artifacts_dir=artifacts_dir,
            )
            runner.run()

        assert captured_workdir[0] == project_root


@pytest.mark.medium
class TestCLIConfigIntegration:
    """CLI cmd_run integrates with config discovery."""

    def test_cmd_run_discovers_config(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from kaji_harness.cli_main import cmd_run, create_parser
        from kaji_harness.models import Verdict

        # Create config
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("")

        # Create workflow file
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            parser = create_parser()
            args = parser.parse_args(["run", str(wf), "1", "--workdir", str(tmp_path)])
            exit_code = cmd_run(args)

        assert exit_code == 0
        # Verify project_root was passed correctly
        call_kwargs = mock_runner.call_args.kwargs
        assert call_kwargs["project_root"] == tmp_path
        assert call_kwargs["artifacts_dir"] == tmp_path / ".kaji-artifacts"

    def test_cmd_run_config_not_found_exits_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from kaji_harness.cli_main import cmd_run, create_parser

        # No .kaji/config.toml exists
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        parser = create_parser()
        args = parser.parse_args(["run", str(wf), "1", "--workdir", str(tmp_path)])
        exit_code = cmd_run(args)

        assert exit_code == 2
        captured = capsys.readouterr()
        assert ".kaji/config.toml" in captured.err

    def test_validate_without_config_still_works(self, tmp_path: Path) -> None:
        """kaji validate works without .kaji/config.toml (backward compat)."""
        from kaji_harness.cli_main import cmd_validate, create_parser

        # Create a valid workflow with matching skill
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        # Create skill directory relative to tmp_path (pyproject.toml marker)
        (tmp_path / "pyproject.toml").write_text("")
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        parser = create_parser()
        args = parser.parse_args(["validate", str(wf), "--project-root", str(tmp_path)])
        exit_code = cmd_validate(args)

        assert exit_code == 0

    def test_validate_with_config_uses_config_root(self, tmp_path: Path) -> None:
        """kaji validate prefers .kaji/config.toml root over pyproject.toml."""
        from kaji_harness.cli_main import cmd_validate, create_parser

        # Create config
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("")

        # Create workflow inside .kaji/workflows/
        wf_dir = tmp_path / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        # Skill at repo root (not at .kaji/workflows/)
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        parser = create_parser()
        args = parser.parse_args(["validate", str(wf)])
        exit_code = cmd_validate(args)

        assert exit_code == 0


# ============================================================
# Large tests — E2E with real subprocess
# ============================================================


@pytest.mark.large
class TestConfigE2E:
    """E2E tests with real subprocess execution."""

    def test_kaji_run_with_config(self, tmp_path: Path) -> None:
        """kaji run with .kaji/config.toml creates artifacts in correct location."""
        # Create config
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("")

        # Create workflow
        wf_dir = tmp_path / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        # Create skill
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        # Run with restricted PATH so agent CLI is not found (expected exit 3)
        python_dir = str(Path(sys.executable).parent)
        env = {**__import__("os").environ, "PATH": python_dir}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "999",
                "--workdir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # Exit 3 = runtime error (agent CLI not found), not exit 2 (config not found)
        assert result.returncode == 3
        assert "not found" in result.stderr.lower()

    def test_kaji_run_without_config_exits_2(self, tmp_path: Path) -> None:
        """kaji run without .kaji/config.toml exits with code 2."""
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "1",
                "--workdir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 2
        assert ".kaji/config.toml" in result.stderr

    def test_kaji_validate_without_config_backward_compat(self, tmp_path: Path) -> None:
        """kaji validate still works without .kaji/config.toml."""
        # Create workflow with a skill and pyproject.toml marker
        (tmp_path / "pyproject.toml").write_text("")
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "validate",
                str(wf),
                "--project-root",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
