"""Tests for config.toml support in src.core.config module.

TDD tests for Issue #40: config.toml model/tool settings migration.
"""

import tomllib
from pathlib import Path

import pytest


class TestFindConfigFile:
    """Test find_config_file function in core config."""

    def test_returns_none_when_no_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None when no config.toml exists anywhere."""
        from src.core.config import find_config_file

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        result = find_config_file()
        assert result is None

    def test_finds_config_in_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should find config.toml in current working directory."""
        from src.core.config import find_config_file

        config_file = tmp_path / "config.toml"
        config_file.write_text("[tools.claude]\nmodel = 'opus'\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        result = find_config_file()
        assert result == config_file

    def test_env_var_takes_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DAO_CONFIG env var should take highest priority."""
        from src.core.config import find_config_file

        # Create two config files
        env_config = tmp_path / "env-config.toml"
        env_config.write_text("[tools.claude]\nmodel = 'env-model'\n")
        cwd_config = tmp_path / "config.toml"
        cwd_config.write_text("[tools.claude]\nmodel = 'cwd-model'\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DAO_CONFIG", str(env_config))

        result = find_config_file()
        assert result == env_config

    def test_explicit_path_parameter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit config_path parameter should be used."""
        from src.core.config import find_config_file

        config_file = tmp_path / "custom-config.toml"
        config_file.write_text("[tools.claude]\nmodel = 'custom'\n")
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        result = find_config_file(config_path=config_file)
        assert result == config_file

    def test_explicit_path_raises_if_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise FileNotFoundError if explicit config_path doesn't exist."""
        from src.core.config import find_config_file

        nonexistent = tmp_path / "nonexistent.toml"
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        with pytest.raises(FileNotFoundError, match="Config file not found"):
            find_config_file(config_path=nonexistent)

    def test_workdir_takes_priority_over_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """workdir/config.toml should be found before CWD/config.toml."""
        from src.core.config import find_config_file

        # Create workdir config
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        workdir_config = workdir / "config.toml"
        workdir_config.write_text("[tools.claude]\nmodel = 'workdir'\n")

        # Create CWD config (different directory)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        cwd_config = cwd / "config.toml"
        cwd_config.write_text("[tools.claude]\nmodel = 'cwd'\n")

        monkeypatch.chdir(cwd)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        result = find_config_file(workdir=workdir)
        assert result == workdir_config


class TestLoadConfig:
    """Test load_config function."""

    def test_returns_empty_dict_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return empty dict when no config.toml exists."""
        from src.core.config import load_config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        # Clear cache by calling with use_cache=False
        result = load_config(use_cache=False)
        assert result == {}

    def test_loads_toml_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should load and parse TOML content."""
        from src.core.config import load_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
timeout = 1800

[tools.codex]
model = "gpt-5.1-codex-max"
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        result = load_config(use_cache=False)
        assert result["tools"]["claude"]["model"] == "opus"
        assert result["tools"]["claude"]["timeout"] == 1800
        assert result["tools"]["codex"]["model"] == "gpt-5.1-codex-max"

    def test_caches_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should cache result by default."""
        from src.core.config import load_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("[tools.claude]\nmodel = 'cached'\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        # First call clears cache and loads
        load_config(use_cache=False)  # Clear cache first
        result2 = load_config()  # Use cache

        # Modify file (cache should still return old value)
        config_file.write_text("[tools.claude]\nmodel = 'modified'\n")
        result3 = load_config()

        assert result2["tools"]["claude"]["model"] == "cached"
        assert result3["tools"]["claude"]["model"] == "cached"

    def test_raises_on_invalid_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise error on invalid TOML syntax."""
        from src.core.config import load_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid [ toml syntax")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)

        with pytest.raises(tomllib.TOMLDecodeError):
            load_config(use_cache=False)


class TestGetConfigValue:
    """Test get_config_value function."""

    def test_returns_default_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return default when no config.toml exists."""
        from src.core import config as core_config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        result = core_config.get_config_value("tools.claude.model", default="sonnet")
        assert result == "sonnet"

    def test_returns_value_from_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return value from config.toml."""
        from src.core import config as core_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("[tools.claude]\nmodel = 'opus'\ntimeout = 1800\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        assert core_config.get_config_value("tools.claude.model") == "opus"
        assert core_config.get_config_value("tools.claude.timeout") == 1800

    def test_returns_default_for_missing_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return default for missing key."""
        from src.core import config as core_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("[tools.claude]\nmodel = 'opus'\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        result = core_config.get_config_value("tools.claude.nonexistent", default="fallback")
        assert result == "fallback"

    def test_handles_empty_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should handle empty config.toml."""
        from src.core import config as core_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        result = core_config.get_config_value("tools.claude.model", default="sonnet")
        assert result == "sonnet"

    def test_handles_partial_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should handle config with [tools] but no [tools.claude]."""
        from src.core import config as core_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("[tools.codex]\nmodel = 'codex-model'\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        result = core_config.get_config_value("tools.claude.model", default="sonnet")
        assert result == "sonnet"


class TestClaudeToolConfigIntegration:
    """Test ClaudeTool integration with config.toml."""

    def test_uses_hardcoded_defaults_without_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should use hardcoded defaults when no config.toml exists."""
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        tool = ClaudeTool()
        assert tool.model == "sonnet"  # Hardcoded default
        assert tool.timeout == 600
        assert tool.permission_mode == "default"

    def test_uses_config_toml_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should use config.toml values as defaults."""
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

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
        core_config._config_cache = None  # Clear cache

        tool = ClaudeTool()
        assert tool.model == "opus"
        assert tool.timeout == 1800
        assert tool.permission_mode == "bypassPermissions"

    def test_constructor_args_override_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Constructor args should override config.toml values."""
        from src.core import config as core_config
        from src.core.tools.claude import ClaudeTool

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[tools.claude]
model = "opus"
timeout = 1800
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        core_config._config_cache = None  # Clear cache

        tool = ClaudeTool(model="haiku", timeout=300)
        assert tool.model == "haiku"  # Constructor arg wins
        assert tool.timeout == 300


class TestBugfixAgentConfigBackwardCompatibility:
    """Test backward compatibility with src.bugfix_agent.config."""

    def test_find_config_file_reexported(self) -> None:
        """find_config_file should be importable from bugfix_agent.config."""
        from src.bugfix_agent.config import find_config_file

        assert callable(find_config_file)

    def test_load_config_reexported(self) -> None:
        """load_config should be importable from bugfix_agent.config."""
        from src.bugfix_agent.config import load_config

        assert callable(load_config)

    def test_get_config_value_reexported(self) -> None:
        """get_config_value should be importable from bugfix_agent.config."""
        from src.bugfix_agent.config import get_config_value

        assert callable(get_config_value)

    def test_existing_get_config_value_behavior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing get_config_value behavior should be preserved."""
        from src.bugfix_agent import config as bugfix_config
        from src.bugfix_agent.config import get_config_value

        config_file = tmp_path / "config.toml"
        config_file.write_text("[agent]\nmax_loop_count = 5\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DAO_CONFIG", raising=False)
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        # Clear bugfix_agent's lru_cache
        bugfix_config.load_config.cache_clear()

        result = get_config_value("agent.max_loop_count", default=3)
        assert result == 5
