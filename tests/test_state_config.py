"""Tests for Phase 2: State-based configuration.

TDD tests for Issue #40: StateConfig and create_tool_for_state.
"""

from pathlib import Path

import pytest


class TestStateConfig:
    """Test StateConfig dataclass."""

    def test_state_config_with_all_fields(self) -> None:
        """StateConfig with all fields set."""
        from src.core.config import StateConfig

        config = StateConfig(agent="claude", model="opus", timeout=1800)
        assert config.agent == "claude"
        assert config.model == "opus"
        assert config.timeout == 1800

    def test_state_config_with_required_only(self) -> None:
        """StateConfig with only required field (agent)."""
        from src.core.config import StateConfig

        config = StateConfig(agent="claude")
        assert config.agent == "claude"
        assert config.model is None
        assert config.timeout is None

    def test_state_config_agent_required(self) -> None:
        """StateConfig should require agent field."""
        from src.core.config import StateConfig

        with pytest.raises(TypeError):
            StateConfig()  # type: ignore[call-arg]


class TestGetStateConfig:
    """Test get_state_config function."""

    def test_returns_state_config_when_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return StateConfig when [states.XXX] exists."""
        from src.core import config as core_config
        from src.core.config import StateConfig, get_state_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "sonnet"
timeout = 600

[states.INIT]
agent = "claude"
model = "opus"
timeout = 1800
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_state_config("INIT")
        assert result is not None
        assert isinstance(result, StateConfig)
        assert result.agent == "claude"
        assert result.model == "opus"
        assert result.timeout == 1800

    def test_returns_none_when_state_not_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None when [states.XXX] doesn't exist."""
        from src.core import config as core_config
        from src.core.config import get_state_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "sonnet"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_state_config("NONEXISTENT")
        assert result is None

    def test_returns_state_config_with_only_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StateConfig with only agent specified (model/timeout inherit)."""
        from src.core import config as core_config
        from src.core.config import get_state_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[states.INVESTIGATE]
agent = "codex"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_state_config("INVESTIGATE")
        assert result is not None
        assert result.agent == "codex"
        assert result.model is None  # Not in config, will be inherited
        assert result.timeout is None

    def test_returns_none_when_empty_state_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None when [states.XXX] is empty (no agent)."""
        from src.core import config as core_config
        from src.core.config import get_state_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[states.EMPTY]
# No agent specified
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_state_config("EMPTY")
        assert result is None  # agent is required

    def test_returns_none_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None when no config.toml exists."""
        from src.core import config as core_config
        from src.core.config import get_state_config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_state_config("INIT")
        assert result is None


class TestCreateToolForState:
    """Test create_tool_for_state function."""

    def test_creates_claude_tool_from_state_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should create ClaudeTool with state-specific settings."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "sonnet"
timeout = 600

[states.INIT]
agent = "claude"
model = "opus"
timeout = 1800
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        tool = create_tool_for_state("INIT")
        assert isinstance(tool, ClaudeTool)
        assert tool.model == "opus"
        assert tool.timeout == 1800

    def test_inherits_from_tools_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should inherit model/timeout from [tools.XXX] when not specified."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
timeout = 1800
permission_mode = "bypassPermissions"

[states.INVESTIGATE]
agent = "claude"
# model and timeout will be inherited from [tools.claude]
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        tool = create_tool_for_state("INVESTIGATE")
        assert isinstance(tool, ClaudeTool)
        assert tool.model == "opus"  # Inherited from [tools.claude]
        assert tool.timeout == 1800  # Inherited from [tools.claude]
        assert tool.permission_mode == "bypassPermissions"

    def test_uses_hardcoded_defaults_when_no_tools_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should use hardcoded defaults when [tools.XXX] doesn't exist."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[states.INIT]
agent = "claude"
# No [tools.claude] section
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        tool = create_tool_for_state("INIT")
        assert isinstance(tool, ClaudeTool)
        assert tool.model == "sonnet"  # Hardcoded default
        assert tool.timeout == 600  # Hardcoded default

    def test_raises_value_error_for_unknown_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise ValueError for unknown agent name."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[states.INIT]
agent = "unknown_agent"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        with pytest.raises(ValueError, match="Unknown agent"):
            create_tool_for_state("INIT")

    def test_raises_value_error_when_state_not_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise ValueError when state is not configured."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "sonnet"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        with pytest.raises(ValueError, match="not configured"):
            create_tool_for_state("NONEXISTENT")

    def test_state_config_overrides_tools_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """State-specific settings should override tools settings."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
timeout = 1800

[states.PR_CREATE]
agent = "claude"
model = "sonnet"  # Override opus
timeout = 300  # Override 1800
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        tool = create_tool_for_state("PR_CREATE")
        assert isinstance(tool, ClaudeTool)
        assert tool.model == "sonnet"  # State override
        assert tool.timeout == 300  # State override


class TestGetToolConfig:
    """Test get_tool_config helper function."""

    def test_returns_tool_config_when_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return tool config dict when [tools.XXX] exists."""
        from src.core import config as core_config
        from src.core.config import get_tool_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
timeout = 1800
permission_mode = "bypassPermissions"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_tool_config("claude")
        assert result == {
            "model": "opus",
            "timeout": 1800,
            "permission_mode": "bypassPermissions",
        }

    def test_returns_empty_dict_when_tool_not_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return empty dict when [tools.XXX] doesn't exist."""
        from src.core import config as core_config
        from src.core.config import get_tool_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_tool_config("codex")
        assert result == {}

    def test_returns_empty_dict_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return empty dict when no config.toml exists."""
        from src.core import config as core_config
        from src.core.config import get_tool_config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        result = get_tool_config("claude")
        assert result == {}


class TestIntegrationScenarios:
    """Integration tests for state-based tool creation."""

    def test_full_workflow_multiple_states(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test creating tools for multiple states."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "sonnet"
timeout = 600

[states.INIT]
agent = "claude"
model = "opus"
timeout = 1800

[states.INVESTIGATE]
agent = "claude"
# Inherits model and timeout from [tools.claude]

[states.PR_CREATE]
agent = "claude"
model = "haiku"
timeout = 120
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        # INIT state - explicit overrides
        init_tool = create_tool_for_state("INIT")
        assert isinstance(init_tool, ClaudeTool)
        assert init_tool.model == "opus"
        assert init_tool.timeout == 1800

        # INVESTIGATE state - inherits from [tools.claude]
        investigate_tool = create_tool_for_state("INVESTIGATE")
        assert isinstance(investigate_tool, ClaudeTool)
        assert investigate_tool.model == "sonnet"  # From [tools.claude]
        assert investigate_tool.timeout == 600  # From [tools.claude]

        # PR_CREATE state - explicit overrides
        pr_tool = create_tool_for_state("PR_CREATE")
        assert isinstance(pr_tool, ClaudeTool)
        assert pr_tool.model == "haiku"
        assert pr_tool.timeout == 120


class TestGitRootSearch:
    """Test git repository root search in find_config_file."""

    def test_finds_config_in_git_root_from_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should find config.toml in git root when running from subdirectory."""
        from unittest.mock import patch

        from src.core import config as core_config
        from src.core.config import find_config_file

        # Simulate git root
        git_root = tmp_path / "repo"
        git_root.mkdir()
        subdir = git_root / "src" / "module"
        subdir.mkdir(parents=True)

        # Create config.toml in git root only
        config_file = git_root / "config.toml"
        config_file.write_text("[tools.claude]\nmodel = 'from-git-root'\n")

        # Run from subdirectory
        monkeypatch.chdir(subdir)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        # Mock _find_git_root to return our fake git root
        with patch("src.core.config._find_git_root", return_value=git_root):
            result = find_config_file()

        assert result == config_file

    def test_cwd_takes_priority_over_git_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CWD config should be found before git root config."""
        from unittest.mock import patch

        from src.core import config as core_config
        from src.core.config import find_config_file

        # Simulate git root with config
        git_root = tmp_path / "repo"
        git_root.mkdir()
        git_config = git_root / "config.toml"
        git_config.write_text("[tools.claude]\nmodel = 'from-git-root'\n")

        # Create subdirectory with its own config
        subdir = git_root / "subproject"
        subdir.mkdir()
        subdir_config = subdir / "config.toml"
        subdir_config.write_text("[tools.claude]\nmodel = 'from-cwd'\n")

        # Run from subdirectory
        monkeypatch.chdir(subdir)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        with patch("src.core.config._find_git_root", return_value=git_root):
            result = find_config_file()

        # CWD should take priority
        assert result == subdir_config


class TestZeroValueInheritance:
    """Test that explicit zero/empty values are preserved during inheritance."""

    def test_explicit_timeout_zero_is_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit timeout=0 in state config should not be overwritten."""
        from src.bugfix_agent.tool_factory import create_tool_for_state
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
timeout = 1800

[states.QUICK_CHECK]
agent = "claude"
timeout = 0
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None

        tool = create_tool_for_state("QUICK_CHECK")
        assert isinstance(tool, ClaudeTool)
        # timeout=0 should be preserved, not overwritten by tools.claude.timeout
        assert tool.timeout == 0
        # model should be inherited from [tools.claude]
        assert tool.model == "opus"
