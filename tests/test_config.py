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
from pathlib import Path

import pytest

from kaji_harness.config import KajiConfig, PathsConfig
from kaji_harness.errors import ConfigLoadError, ConfigNotFoundError

# ============================================================
# Small tests — TOML parsing and data model
# ============================================================


@pytest.mark.small
class TestPathsConfigDefaults:
    """PathsConfig provides correct default values."""

    def test_default_artifacts_dir_is_empty_sentinel(self) -> None:
        """PathsConfig.artifacts_dir defaults to empty string (not-set sentinel)."""
        config = PathsConfig()
        assert config.artifacts_dir == ""


@pytest.mark.small
class TestKajiConfigLoadValid:
    """KajiConfig._load parses valid TOML with [paths] section."""

    def test_load_with_paths_section(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "custom-artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)

        assert config.repo_root == tmp_path
        assert config.paths.artifacts_dir == "custom-artifacts"

    def test_load_without_skill_dir_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="skill_dir is required"):
            KajiConfig._load(config_file)

    def test_load_without_artifacts_dir_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="artifacts_dir is required"):
            KajiConfig._load(config_file)

    def test_load_with_empty_artifacts_dir_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ""\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="artifacts_dir is required"):
            KajiConfig._load(config_file)

    def test_load_with_whitespace_only_artifacts_dir_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "   "\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="artifacts_dir is required"):
            KajiConfig._load(config_file)

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "out"\nunknown_key = "value"\n\n[unknown_section]\nfoo = 42\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)

        assert config.paths.artifacts_dir == "out"


@pytest.mark.small
class TestKajiConfigLoadInvalid:
    """KajiConfig._load raises ConfigLoadError on invalid input."""

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text("this is not valid toml [[[")

        with pytest.raises(ConfigLoadError, match="invalid TOML"):
            KajiConfig._load(config_file)

    def test_absolute_artifacts_dir_accepted(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "/tmp/outside"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)
        assert config.paths.artifacts_dir == "/tmp/outside"

    def test_dotdot_artifacts_dir_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "../escape"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="escape repo root"):
            KajiConfig._load(config_file)

    def test_nested_dotdot_artifacts_dir_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "sub/../../escape"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="escape repo root"):
            KajiConfig._load(config_file)

    def test_tilde_artifacts_dir_accepted(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "~/.kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)
        assert config.paths.artifacts_dir == "~/.kaji/artifacts"

    def test_expanduser_runtime_error_raises_config_load_error(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "~/.kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with patch("pathlib.Path.expanduser", side_effect=RuntimeError("no home")):
            with pytest.raises(ConfigLoadError, match="expand"):
                KajiConfig._load(config_file)

    def test_non_string_artifacts_dir_rejected(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = 42\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="must be a string"):
            KajiConfig._load(config_file)


@pytest.mark.small
class TestKajiConfigRepoRoot:
    """repo_root is correctly derived from config.toml path."""

    def test_repo_root_is_grandparent_of_config(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)

        assert config.repo_root == tmp_path


@pytest.mark.small
class TestKajiConfigArtifactsDir:
    """artifacts_dir property resolves paths correctly."""

    def test_missing_artifacts_dir_raises(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="artifacts_dir is required"):
            KajiConfig._load(config_file)

    def test_tilde_path_resolved_via_expanduser(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "~/.kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)

        assert config.artifacts_dir == Path("~/.kaji/artifacts").expanduser()
        assert config.artifacts_dir.is_absolute()

    def test_absolute_path_returned_as_is(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "/tmp/my-artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig._load(config_file)

        assert config.artifacts_dir == Path("/tmp/my-artifacts")

    def test_relative_path_resolved_from_repo_root(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "build/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

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
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig.discover(start_dir=tmp_path)

        assert config.repo_root == tmp_path

    def test_discover_from_subdir(self, tmp_path: Path) -> None:
        """Discover config from a nested subdirectory (walk-up)."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

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
        """Discovered config correctly loads custom relative artifacts_dir."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "my-output"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig.discover(start_dir=tmp_path)

        assert config.artifacts_dir == tmp_path / "my-output"

    def test_discover_with_tilde_artifacts_dir(self, tmp_path: Path) -> None:
        """Discovered config correctly resolves ~ in artifacts_dir."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "~/.kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig.discover(start_dir=tmp_path)

        assert config.artifacts_dir == Path("~/.kaji/artifacts").expanduser()

    def test_discover_with_absolute_artifacts_dir(self, tmp_path: Path) -> None:
        """Discovered config correctly returns absolute artifacts_dir."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        abs_dir = tmp_path / "external"
        (config_dir / "config.toml").write_text(
            f'[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "{abs_dir}"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        config = KajiConfig.discover(start_dir=tmp_path)

        assert config.artifacts_dir == abs_dir

    def test_discover_without_artifacts_dir_raises(self, tmp_path: Path) -> None:
        """Config without artifacts_dir raises ConfigLoadError."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        with pytest.raises(ConfigLoadError, match="artifacts_dir is required"):
            KajiConfig.discover(start_dir=tmp_path)

    def test_discover_ignores_inner_kaji_dirs(self, tmp_path: Path) -> None:
        """Discovery finds the nearest .kaji/config.toml, not a deeper one."""
        # Create config at root level
        root_config = tmp_path / ".kaji"
        root_config.mkdir()
        (root_config / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "root-arts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        # Create a subdirectory with its own .kaji/config.toml
        inner = tmp_path / "sub"
        inner.mkdir()
        inner_config = inner / ".kaji"
        inner_config.mkdir()
        (inner_config / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "inner-arts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

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

        assert state.issue_number == "42"

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

    def test_persist_to_external_dir_survives_workdir_removal(self, tmp_path: Path) -> None:
        """Artifacts written to an external dir survive removal of workdir."""
        import shutil

        from kaji_harness.models import Verdict
        from kaji_harness.state import SessionState

        workdir = tmp_path / "worktree"
        workdir.mkdir()
        external_arts = tmp_path / "external-artifacts"

        state = SessionState.load_or_create(42, artifacts_dir=external_arts)
        state.record_step(
            "design",
            Verdict(status="PASS", reason="ok", evidence="ok", suggestion=""),
        )

        # Remove the workdir (simulating worktree deletion)
        shutil.rmtree(workdir)

        # Artifacts should still exist
        assert (external_arts / "42" / "session-state.json").exists()
        assert (external_arts / "42" / "progress.md").exists()

        # State should be loadable after workdir removal
        loaded = SessionState.load_or_create(42, artifacts_dir=external_arts)
        assert loaded.last_completed_step == "design"

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

        import subprocess as _sp

        project_root = tmp_path / "project"
        project_root.mkdir()
        # gl:21: provider.type='local' requires a git repo.
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(project_root)], check=True)
        artifacts_dir = tmp_path / "artifacts"

        kaji_dir = project_root / ".kaji"
        kaji_dir.mkdir()
        (kaji_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )
        config = KajiConfig._load(kaji_dir / "config.toml")

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
                issue_number="99",
                project_root=project_root,
                artifacts_dir=artifacts_dir,
                config=config,
            )
            state = runner.run()

        assert state.last_completed_step == "step1"
        # Verify artifacts were written to artifacts_dir
        assert (artifacts_dir / "local-pc1-99").exists()

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

        import subprocess as _sp

        project_root = tmp_path / "project"
        project_root.mkdir()
        # gl:21: provider.type='local' requires a git repo.
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(project_root)], check=True)
        artifacts_dir = tmp_path / "artifacts"

        kaji_dir = project_root / ".kaji"
        kaji_dir.mkdir()
        (kaji_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )
        config = KajiConfig._load(kaji_dir / "config.toml")

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
                issue_number="99",
                project_root=project_root,
                artifacts_dir=artifacts_dir,
                config=config,
            )
            runner.run()

        assert captured_workdir[0] == project_root


@pytest.mark.medium
class TestCLIConfigIntegration:
    """CLI cmd_run integrates with config discovery."""

    def test_cmd_run_discovers_config(self, tmp_path: Path) -> None:
        import subprocess as _sp
        from unittest.mock import MagicMock, patch

        from kaji_harness.cli_main import cmd_run, create_parser
        from kaji_harness.models import Verdict

        # gl:21: provider.type='local' requires a git repo.
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
        # Create config
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        # Create workflow file
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        with patch("kaji_harness.commands.run.WorkflowRunner") as mock_runner:
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
        assert call_kwargs["artifacts_dir"] == tmp_path / ".kaji/artifacts"

    def test_cmd_run_config_not_found_exits_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from kaji_harness.cli_main import cmd_run, create_parser

        # No .kaji/config.toml exists
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        parser = create_parser()
        args = parser.parse_args(["run", str(wf), "1", "--workdir", str(tmp_path)])
        exit_code = cmd_run(args)

        assert exit_code == 2
        captured = capsys.readouterr()
        assert ".kaji/config.toml" in captured.err

    def test_validate_without_config_fails(self, tmp_path: Path) -> None:
        """kaji validate fails without .kaji/config.toml (config is required)."""
        from kaji_harness.cli_main import cmd_validate, create_parser

        # Create a valid workflow with matching skill but NO config
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        (tmp_path / "pyproject.toml").write_text("")
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        parser = create_parser()
        args = parser.parse_args(["validate", str(wf), "--project-root", str(tmp_path)])
        exit_code = cmd_validate(args)

        assert exit_code == 1

    def test_cmd_run_broken_config_exits_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from kaji_harness.cli_main import cmd_run, create_parser

        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("this is broken [[[")

        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        parser = create_parser()
        args = parser.parse_args(["run", str(wf), "1", "--workdir", str(tmp_path)])
        exit_code = cmd_run(args)

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "invalid TOML" in captured.err

    def test_cmd_run_tilde_artifacts_dir(self, tmp_path: Path) -> None:
        import subprocess as _sp
        from unittest.mock import MagicMock, patch

        from kaji_harness.cli_main import cmd_run, create_parser
        from kaji_harness.models import Verdict

        # gl:21: provider.type='local' requires a git repo.
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "~/.kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        with patch("kaji_harness.commands.run.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            parser = create_parser()
            args = parser.parse_args(["run", str(wf), "1", "--workdir", str(tmp_path)])
            exit_code = cmd_run(args)

        assert exit_code == 0
        call_kwargs = mock_runner.call_args.kwargs
        assert call_kwargs["artifacts_dir"] == Path("~/.kaji/artifacts").expanduser()

    def test_cmd_run_absolute_artifacts_dir_accepted(self, tmp_path: Path) -> None:
        import subprocess as _sp
        from unittest.mock import MagicMock, patch

        from kaji_harness.cli_main import cmd_run, create_parser
        from kaji_harness.models import Verdict

        # gl:21: provider.type='local' requires a git repo.
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        abs_artifacts = tmp_path / "external-artifacts"
        (config_dir / "config.toml").write_text(
            f'[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "{abs_artifacts}"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        with patch("kaji_harness.commands.run.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            parser = create_parser()
            args = parser.parse_args(["run", str(wf), "1", "--workdir", str(tmp_path)])
            exit_code = cmd_run(args)

        assert exit_code == 0
        call_kwargs = mock_runner.call_args.kwargs
        assert call_kwargs["artifacts_dir"] == abs_artifacts

    def test_validate_broken_config_reports_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from kaji_harness.cli_main import cmd_validate, create_parser

        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("broken [[[")

        wf_dir = tmp_path / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        parser = create_parser()
        args = parser.parse_args(["validate", str(wf)])
        exit_code = cmd_validate(args)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "invalid TOML" in captured.err

    def test_validate_with_config_uses_config_root(self, tmp_path: Path) -> None:
        """kaji validate prefers .kaji/config.toml root over pyproject.toml."""
        from kaji_harness.cli_main import cmd_validate, create_parser

        # Create config
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        # Create workflow inside .kaji/workflows/
        wf_dir = tmp_path / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
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
        # gl:21: provider.type='local' requires a git repo.
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main", str(tmp_path)],
            check=True,
        )
        # Create config
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        # Create workflow
        wf_dir = tmp_path / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        # Create skill
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        # Phase 3-e: pre-create local issue 999 for IssueContext resolution.
        from tests.conftest import ensure_local_issue

        ensure_local_issue(tmp_path, "999")

        # Run with restricted PATH so agent CLI is not found (expected exit 3)
        python_dir = str(Path(sys.executable).parent)
        env = {
            **__import__("os").environ,
            "PATH": f"{python_dir}:"
            + str(Path(__import__("shutil").which("git") or "/usr/bin/git").parent),
        }

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
            "name: test\ndescription: test\nexecution_policy: auto\n"
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

    def test_kaji_run_broken_config_exits_2(self, tmp_path: Path) -> None:
        """kaji run with broken .kaji/config.toml exits 2 with clean error."""
        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("this is broken [[[")

        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
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
        assert "invalid TOML" in result.stderr
        # No traceback should appear
        assert "Traceback" not in result.stderr

    def test_kaji_run_with_absolute_artifacts_dir(self, tmp_path: Path) -> None:
        """kaji run with absolute artifacts_dir places artifacts at specified path."""
        # gl:21: provider.type='local' requires a git repo.
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main", str(tmp_path)],
            check=True,
        )
        arts_dir = tmp_path / "external-artifacts"

        config_dir = tmp_path / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            f'[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "{arts_dir}"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        wf_dir = tmp_path / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        from tests.conftest import ensure_local_issue

        ensure_local_issue(tmp_path, "999")

        python_dir = str(Path(sys.executable).parent)
        env = {
            **__import__("os").environ,
            "PATH": f"{python_dir}:"
            + str(Path(__import__("shutil").which("git") or "/usr/bin/git").parent),
        }

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

        # Exit 3 = runtime error (agent CLI not found), config parsing succeeded
        assert result.returncode == 3
        assert "not found" in result.stderr.lower()

        # Artifacts must be created at the specified external path, not under repo
        issue_dir = arts_dir / "local-pc1-999"
        runs_dirs = list((issue_dir / "runs").iterdir()) if (issue_dir / "runs").exists() else []
        assert len(runs_dirs) >= 1, f"Expected run directory under {issue_dir / 'runs'}"
        assert (runs_dirs[0] / "run.log").exists(), "run.log must exist at external artifacts path"

        # Must NOT create .kaji-artifacts under repo root
        assert not (tmp_path / ".kaji-artifacts").exists(), (
            "Artifacts must not be created under repo root"
        )

    def test_kaji_run_artifacts_survive_workdir_deletion(self, tmp_path: Path) -> None:
        """After kaji run, artifacts survive workdir (worktree) deletion."""
        import shutil

        workdir = tmp_path / "worktree"
        workdir.mkdir()
        # gl:21: provider.type='local' requires a git repo.
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main", str(workdir)],
            check=True,
        )
        arts_dir = tmp_path / "external-artifacts"

        config_dir = workdir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            f'[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "{arts_dir}"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        wf_dir = workdir / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        skill_dir = workdir / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        from tests.conftest import ensure_local_issue

        ensure_local_issue(workdir, "42")

        python_dir = str(Path(sys.executable).parent)
        env = {
            **__import__("os").environ,
            "PATH": f"{python_dir}:"
            + str(Path(__import__("shutil").which("git") or "/usr/bin/git").parent),
        }

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "42",
                "--workdir",
                str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # Verify runner executed (exit 3 = agent not found, not config error)
        assert result.returncode == 3

        # Verify artifacts were created at external location before deletion
        issue_dir = arts_dir / "local-pc1-42"
        runs_dir = issue_dir / "runs"
        assert runs_dir.exists(), f"runs directory must exist at {runs_dir}"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) >= 1, "At least one run directory must exist"
        run_log = run_dirs[0] / "run.log"
        assert run_log.exists(), f"run.log must exist at {run_log}"

        # The runner creates run.log before agent execution, but session-state.json
        # is only persisted after a step completes (record_step). In this test env
        # the agent binary is absent so execute_cli raises before record_step.
        # Simulate what a real run does: persist session state via the public API.
        from kaji_harness.models import Verdict
        from kaji_harness.state import SessionState

        state = SessionState.load_or_create("local-pc1-42", arts_dir)
        state.record_step(
            "s1",
            Verdict(
                status="PASS",
                reason="test",
                evidence="test",
                suggestion="",
            ),
        )
        session_state = issue_dir / "session-state.json"
        assert session_state.exists(), f"session-state.json must exist at {session_state}"

        # Read content before deletion for post-deletion comparison
        run_log_content = run_log.read_text(encoding="utf-8")
        assert len(run_log_content) > 0, "run.log must not be empty"
        session_state_content = session_state.read_text(encoding="utf-8")
        assert len(session_state_content) > 0, "session-state.json must not be empty"

        # Delete the workdir (simulating worktree deletion)
        shutil.rmtree(workdir)
        assert not workdir.exists()

        # Artifacts must survive workdir deletion
        assert issue_dir.exists(), "Artifacts directory must survive workdir deletion"
        assert run_log.exists(), "run.log must survive workdir deletion"
        assert session_state.exists(), "session-state.json must survive workdir deletion"
        # Verify artifacts are still readable after workdir deletion (no ENOENT)
        assert run_log.read_text(encoding="utf-8") == run_log_content
        assert session_state.read_text(encoding="utf-8") == session_state_content

    def test_kaji_run_tilde_artifacts_dir_outside_repo(self, tmp_path: Path) -> None:
        """Tilde artifacts_dir places artifacts outside repo root (~/.kaji/artifacts)."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        # gl:21: provider.type='local' requires a git repo.
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main", str(repo_dir)],
            check=True,
        )

        config_dir = repo_dir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = "~/.kaji/artifacts"\n\n[execution]\ndefault_timeout = 1800\n\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )

        wf_dir = repo_dir / ".kaji" / "workflows"
        wf_dir.mkdir()
        wf = wf_dir / "test.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
            "steps:\n  - id: s1\n    skill: test-skill\n"
            "    agent: claude\n    on:\n      PASS: end\n"
        )

        skill_dir = repo_dir / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test\n")

        from tests.conftest import ensure_local_issue

        ensure_local_issue(repo_dir, "999")

        python_dir = str(Path(sys.executable).parent)
        git_dir = str(Path(__import__("shutil").which("git") or "/usr/bin/git").parent)
        env = {
            **__import__("os").environ,
            "PATH": f"{python_dir}:{git_dir}",
            "HOME": str(fake_home),
        }

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "999",
                "--workdir",
                str(repo_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # Config parsed successfully (exit 3 = agent not found, not exit 2 = config error)
        assert result.returncode == 3

        # Artifacts must be created under fake HOME's ~/.kaji/artifacts
        default_arts = fake_home / ".kaji" / "artifacts" / "local-pc1-999"
        assert default_arts.exists(), f"Default artifacts must be at {default_arts}, not under repo"
        runs_dir = default_arts / "runs"
        assert runs_dir.exists(), f"runs directory must exist at {runs_dir}"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) >= 1
        assert (run_dirs[0] / "run.log").exists(), "run.log must exist at default artifacts path"

        # Must NOT create .kaji-artifacts under repo root
        assert not (repo_dir / ".kaji-artifacts").exists(), (
            "Artifacts must not be created under repo root"
        )

    def test_kaji_validate_without_config_fails(self, tmp_path: Path) -> None:
        """kaji validate fails without .kaji/config.toml (config is required)."""
        (tmp_path / "pyproject.toml").write_text("")
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: test\ndescription: test\nexecution_policy: auto\n"
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

        assert result.returncode == 1


# ============================================================
# Small tests — [execution] runner backend (Issue #224)
# ============================================================


def _write_config(tmp_path: Path, *, execution_body: str, local_body: str | None = None) -> Path:
    """Write a minimal .kaji/config.toml (and optional config.local.toml)."""
    config_dir = tmp_path / ".kaji"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        f"[execution]\n{execution_body}\n\n"
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if local_body is not None:
        (config_dir / "config.local.toml").write_text(local_body)
    return config_file


@pytest.mark.small
class TestExecutionRunnerConfig:
    """ExecutionConfig parses agent_runner / interactive_terminal_close_on_verdict."""

    def test_defaults_when_runner_keys_absent(self, tmp_path: Path) -> None:
        config = KajiConfig._load(_write_config(tmp_path, execution_body="default_timeout = 1800"))
        assert config.execution.agent_runner == "headless"
        assert config.execution.interactive_terminal_close_on_verdict is True

    def test_explicit_interactive_terminal(self, tmp_path: Path) -> None:
        config = KajiConfig._load(
            _write_config(
                tmp_path,
                execution_body=(
                    "default_timeout = 2400\n"
                    'agent_runner = "interactive_terminal"\n'
                    "interactive_terminal_close_on_verdict = false"
                ),
            )
        )
        assert config.execution.agent_runner == "interactive_terminal"
        assert config.execution.interactive_terminal_close_on_verdict is False

    def test_invalid_agent_runner_fails_fast(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigLoadError, match="agent_runner must be"):
            KajiConfig._load(
                _write_config(
                    tmp_path,
                    execution_body='default_timeout = 1800\nagent_runner = "foo"',
                )
            )

    def test_non_bool_close_on_verdict_fails_fast(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigLoadError, match="interactive_terminal_close_on_verdict must be"):
            KajiConfig._load(
                _write_config(
                    tmp_path,
                    execution_body=(
                        'default_timeout = 1800\ninteractive_terminal_close_on_verdict = "yes"'
                    ),
                )
            )

    def test_non_string_agent_runner_fails_fast(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigLoadError, match="agent_runner must be a string"):
            KajiConfig._load(
                _write_config(
                    tmp_path,
                    execution_body="default_timeout = 1800\nagent_runner = 42",
                )
            )


@pytest.mark.medium
class TestExecutionOverlay:
    """config.local.toml [execution] overlays config.toml per key."""

    def test_overlay_overrides_runner_key_only(self, tmp_path: Path) -> None:
        # Tracked config uses headless; overlay only flips agent_runner.
        config = KajiConfig._load(
            _write_config(
                tmp_path,
                execution_body='default_timeout = 1800\nagent_runner = "headless"',
                local_body='[execution]\nagent_runner = "interactive_terminal"\n',
            )
        )
        # default_timeout is kept from tracked; agent_runner comes from overlay.
        assert config.execution.default_timeout == 1800
        assert config.execution.agent_runner == "interactive_terminal"
        assert config.execution.interactive_terminal_close_on_verdict is True

    def test_overlay_overrides_close_on_verdict(self, tmp_path: Path) -> None:
        config = KajiConfig._load(
            _write_config(
                tmp_path,
                execution_body=(
                    "default_timeout = 1800\n"
                    'agent_runner = "interactive_terminal"\n'
                    "interactive_terminal_close_on_verdict = true"
                ),
                local_body="[execution]\ninteractive_terminal_close_on_verdict = false\n",
            )
        )
        assert config.execution.agent_runner == "interactive_terminal"
        assert config.execution.interactive_terminal_close_on_verdict is False

    def test_overlay_invalid_value_reports_overlay_path(self, tmp_path: Path) -> None:
        # An invalid runner value coming from the overlay should point at the
        # overlay file in the error message.
        with pytest.raises(ConfigLoadError, match="config.local.toml") as exc_info:
            KajiConfig._load(
                _write_config(
                    tmp_path,
                    execution_body="default_timeout = 1800",
                    local_body='[execution]\nagent_runner = "bogus"\n',
                )
            )
        assert "agent_runner must be" in str(exc_info.value)

    def test_overlay_without_execution_section_keeps_tracked(self, tmp_path: Path) -> None:
        # Overlay defines only [provider]; tracked [execution] is untouched.
        config = KajiConfig._load(
            _write_config(
                tmp_path,
                execution_body='default_timeout = 1800\nagent_runner = "interactive_terminal"',
                local_body='[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc2"\n',
            )
        )
        assert config.execution.agent_runner == "interactive_terminal"
