"""Tests for src.bugfix_agent.config module.

TDD tests for Issue #32: config integration with pydantic-settings.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError


class TestSettingsDefaults:
    """Test default values for Settings."""

    def test_default_max_loop_count(self) -> None:
        """Default max_loop_count should be 3."""
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.max_loop_count == 3

    def test_default_workdir_is_none(self) -> None:
        """Default workdir should be None (determined at runtime)."""
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.workdir is None

    def test_default_max_comment_retries(self) -> None:
        """Default max_comment_retries should be 2."""
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.max_comment_retries == 2

    def test_default_retry_delay(self) -> None:
        """Default retry_delay should be 1.0."""
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.retry_delay == 1.0

    def test_default_context_max_chars(self) -> None:
        """Default context_max_chars should be 4000."""
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.context_max_chars == 4000


class TestSettingsFromEnv:
    """Test Settings loading from environment variables with BUGFIX_AGENT_ prefix."""

    def test_max_loop_count_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_loop_count should be read from BUGFIX_AGENT_MAX_LOOP_COUNT env var."""
        monkeypatch.setenv("BUGFIX_AGENT_MAX_LOOP_COUNT", "5")
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.max_loop_count == 5

    def test_workdir_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """workdir should be read from BUGFIX_AGENT_WORKDIR env var."""
        monkeypatch.setenv("BUGFIX_AGENT_WORKDIR", "/tmp/test-workdir")
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.workdir == Path("/tmp/test-workdir")

    def test_max_comment_retries_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_comment_retries should be read from BUGFIX_AGENT_MAX_COMMENT_RETRIES."""
        monkeypatch.setenv("BUGFIX_AGENT_MAX_COMMENT_RETRIES", "5")
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.max_comment_retries == 5

    def test_retry_delay_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """retry_delay should be read from BUGFIX_AGENT_RETRY_DELAY."""
        monkeypatch.setenv("BUGFIX_AGENT_RETRY_DELAY", "2.5")
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.retry_delay == 2.5

    def test_context_max_chars_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """context_max_chars should be read from BUGFIX_AGENT_CONTEXT_MAX_CHARS."""
        monkeypatch.setenv("BUGFIX_AGENT_CONTEXT_MAX_CHARS", "8000")
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.context_max_chars == 8000


class TestSettingsValidation:
    """Test Settings validation."""

    def test_invalid_max_loop_count_type_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid max_loop_count type should raise ValidationError."""
        monkeypatch.setenv("BUGFIX_AGENT_MAX_LOOP_COUNT", "not_a_number")
        from src.bugfix_agent.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_invalid_retry_delay_type_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid retry_delay type should raise ValidationError."""
        monkeypatch.setenv("BUGFIX_AGENT_RETRY_DELAY", "invalid")
        from src.bugfix_agent.config import Settings

        with pytest.raises(ValidationError):
            Settings()


class TestFindConfigFile:
    """Test find_config_file() function."""

    def test_returns_none_when_no_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when no config file exists anywhere."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        from src.bugfix_agent.config import find_config_file

        result = find_config_file()
        assert result is None

    def test_env_var_takes_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """BUGFIX_AGENT_CONFIG env var should take priority."""
        config_path = tmp_path / "env_config.toml"
        config_path.write_text("[agent]\nmax_loop_count = 10\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        from src.bugfix_agent.config import find_config_file

        result = find_config_file()
        assert result == config_path

    def test_cli_config_takes_priority_over_workdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI --config should take priority over workdir config."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        cli_config = tmp_path / "cli_config.toml"
        cli_config.write_text("[agent]\n")
        workdir_config = tmp_path / "workdir" / "config.toml"
        workdir_config.parent.mkdir()
        workdir_config.write_text("[agent]\n")
        from src.bugfix_agent.config import find_config_file

        result = find_config_file(config_path=cli_config, workdir=workdir_config.parent)
        assert result == cli_config

    def test_cli_config_not_found_raises_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI --config pointing to non-existent file should raise FileNotFoundError."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        from src.bugfix_agent.config import find_config_file

        with pytest.raises(FileNotFoundError, match="Config file not found"):
            find_config_file(config_path=tmp_path / "nonexistent.toml")

    def test_workdir_config_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config in workdir should be found."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        # Create directories first, then chdir
        (tmp_path / "other").mkdir(exist_ok=True)
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        config_path = workdir / "config.toml"
        config_path.write_text("[agent]\n")
        monkeypatch.chdir(tmp_path / "other")  # Different from workdir
        from src.bugfix_agent.config import find_config_file

        result = find_config_file(workdir=workdir)
        assert result == config_path

    def test_cwd_config_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config in CWD should be found when no workdir specified."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / "config.toml"
        config_path.write_text("[agent]\n")
        from src.bugfix_agent.config import find_config_file

        result = find_config_file()
        assert result == config_path

    def test_user_config_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config in ~/.config/bugfix-agent/ should be found."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        user_config_dir = tmp_path / ".config" / "bugfix-agent"
        user_config_dir.mkdir(parents=True)
        config_path = user_config_dir / "config.toml"
        config_path.write_text("[agent]\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from src.bugfix_agent.config import find_config_file

        result = find_config_file()
        assert result == config_path


class TestResolveWorkdir:
    """Test resolve_workdir() function."""

    def test_cli_workdir_takes_priority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI --workdir should take highest priority."""
        monkeypatch.delenv("BUGFIX_AGENT_WORKDIR", raising=False)
        cli_workdir = tmp_path / "cli_workdir"
        cli_workdir.mkdir()
        from src.bugfix_agent.config import resolve_workdir

        result = resolve_workdir(cli_workdir=cli_workdir)
        assert result == cli_workdir.resolve()

    def test_env_var_takes_priority_over_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BUGFIX_AGENT_WORKDIR env var should take priority over toml."""
        env_workdir = tmp_path / "env_workdir"
        env_workdir.mkdir()
        toml_workdir = tmp_path / "toml_workdir"
        toml_workdir.mkdir()
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"[agent]\nworkdir = '{toml_workdir}'\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.setenv("BUGFIX_AGENT_WORKDIR", str(env_workdir))
        import src.bugfix_agent.config as config_module

        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import resolve_workdir

        result = resolve_workdir()
        assert result == env_workdir.resolve()

    def test_toml_workdir_used_when_no_cli_or_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.toml workdir should be used when no CLI or env."""
        monkeypatch.delenv("BUGFIX_AGENT_WORKDIR", raising=False)
        toml_workdir = tmp_path / "toml_workdir"
        toml_workdir.mkdir()
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"[agent]\nworkdir = '{toml_workdir}'\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        import src.bugfix_agent.config as config_module

        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import resolve_workdir

        result = resolve_workdir()
        assert result == toml_workdir.resolve()

    def test_cwd_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CWD should be used as fallback."""
        monkeypatch.delenv("BUGFIX_AGENT_WORKDIR", raising=False)
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        # Clear caches
        import src.bugfix_agent.config as config_module

        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import resolve_workdir

        result = resolve_workdir()
        assert result == tmp_path.resolve()


class TestGetConfigValue:
    """Test get_config_value() backward compatibility wrapper."""

    def test_env_value_takes_priority_over_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env variable should take priority over toml."""
        monkeypatch.setenv("BUGFIX_AGENT_MAX_LOOP_COUNT", "7")
        config_path = tmp_path / "config.toml"
        config_path.write_text("[agent]\nmax_loop_count = 5\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        # Clear caches
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("agent.max_loop_count")
        assert result == 7

    def test_toml_fallback_for_unmapped_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unmapped keys should fall back to toml."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[custom]\nkey = 'value'\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        # Clear caches
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("custom.key")
        assert result == "value"

    def test_default_returned_when_key_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default should be returned when key not found anywhere."""
        monkeypatch.delenv("BUGFIX_AGENT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("nonexistent.key", default="fallback")
        assert result == "fallback"

    def test_legacy_workdir_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """agent.workdir should return Settings.workdir value when set via env."""
        monkeypatch.setenv("BUGFIX_AGENT_WORKDIR", "/test/workdir")
        # Clear cache to ensure fresh Settings instance
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("agent.workdir")
        assert result == Path("/test/workdir")


class TestConfigPriorityIntegration:
    """Integration tests for config priority: env > config.toml > defaults."""

    def test_env_overrides_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variable should override config.toml value."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[agent]\nmax_loop_count = 10\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.setenv("BUGFIX_AGENT_MAX_LOOP_COUNT", "20")
        from src.bugfix_agent.config import Settings

        settings = Settings()
        assert settings.max_loop_count == 20

    def test_toml_value_used_for_unmapped_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.toml values should be used for keys not mapped to Settings."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[custom]\nspecial_value = 42\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        # Clear load_config cache
        from src.bugfix_agent.config import get_config_value, load_config

        load_config.cache_clear()
        result = get_config_value("custom.special_value")
        # This comes from toml because it's not mapped to Settings
        assert result == 42


class TestTomlOverridesDefaults:
    """Tests for config.toml overriding Settings defaults when env is not set."""

    def test_toml_overrides_default_max_loop_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.toml value should override Settings default when env not set."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[agent]\nmax_loop_count = 10\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.delenv("BUGFIX_AGENT_MAX_LOOP_COUNT", raising=False)
        # Clear caches
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("agent.max_loop_count")
        # Should be 10 from toml, not default 3
        assert result == 10

    def test_toml_overrides_default_max_comment_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.toml github.max_comment_retries should override default."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[github]\nmax_comment_retries = 5\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.delenv("BUGFIX_AGENT_MAX_COMMENT_RETRIES", raising=False)
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("github.max_comment_retries")
        assert result == 5

    def test_toml_overrides_default_retry_delay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.toml github.retry_delay should override default."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[github]\nretry_delay = 3.5\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.delenv("BUGFIX_AGENT_RETRY_DELAY", raising=False)
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("github.retry_delay")
        assert result == 3.5

    def test_toml_overrides_default_context_max_chars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.toml tools.context_max_chars should override default."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[tools]\ncontext_max_chars = 8000\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.delenv("BUGFIX_AGENT_CONTEXT_MAX_CHARS", raising=False)
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("tools.context_max_chars")
        assert result == 8000

    def test_toml_workdir_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """config.toml agent.workdir should be resolved by resolve_workdir()."""
        workdir = tmp_path / "toml_workdir"
        workdir.mkdir()
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"[agent]\nworkdir = '{workdir}'\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.delenv("BUGFIX_AGENT_WORKDIR", raising=False)
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import resolve_workdir

        result = resolve_workdir()
        assert result == workdir.resolve()

    def test_env_still_overrides_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variable should still override toml value."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("[agent]\nmax_loop_count = 10\n")
        monkeypatch.setenv("BUGFIX_AGENT_CONFIG", str(config_path))
        monkeypatch.setenv("BUGFIX_AGENT_MAX_LOOP_COUNT", "20")
        import src.bugfix_agent.config as config_module

        config_module._settings_cache = None
        config_module.load_config.cache_clear()
        from src.bugfix_agent.config import get_config_value

        result = get_config_value("agent.max_loop_count")
        # Should be 20 from env, not 10 from toml
        assert result == 20
