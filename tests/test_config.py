"""Tests for src.core.config module."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.config import Settings, get_settings


class TestSettingsDefaults:
    """Test default values for Settings."""

    def test_default_log_level(self) -> None:
        """Default log_level should be INFO."""
        settings = Settings()
        assert settings.log_level == "INFO"

    def test_default_artifacts_dir(self) -> None:
        """Default artifacts_dir should be ./artifacts."""
        settings = Settings()
        assert settings.artifacts_dir == Path("./artifacts")


class TestSettingsFromEnv:
    """Test Settings loading from environment variables."""

    def test_log_level_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """log_level should be read from DAO_LOG_LEVEL env var."""
        monkeypatch.setenv("DAO_LOG_LEVEL", "DEBUG")
        settings = Settings()
        assert settings.log_level == "DEBUG"

    def test_artifacts_dir_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """artifacts_dir should be read from DAO_ARTIFACTS_DIR env var."""
        monkeypatch.setenv("DAO_ARTIFACTS_DIR", "/tmp/test-artifacts")
        settings = Settings()
        assert settings.artifacts_dir == Path("/tmp/test-artifacts")

    def test_all_log_levels_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All valid log levels should be accepted."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for level in valid_levels:
            monkeypatch.setenv("DAO_LOG_LEVEL", level)
            settings = Settings()
            assert settings.log_level == level


class TestSettingsValidation:
    """Test Settings validation."""

    def test_invalid_log_level_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid log_level should raise ValidationError."""
        monkeypatch.setenv("DAO_LOG_LEVEL", "INVALID")
        with pytest.raises(ValidationError):
            Settings()

    def test_empty_log_level_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty log_level should raise ValidationError."""
        monkeypatch.setenv("DAO_LOG_LEVEL", "")
        with pytest.raises(ValidationError):
            Settings()


class TestSettingsEnvFile:
    """Test Settings loading from .env file."""

    def test_env_file_not_required(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings should work without .env file."""
        monkeypatch.chdir(tmp_path)
        settings = Settings()
        assert settings.log_level == "INFO"

    def test_env_var_overrides_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variable should override .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("DAO_LOG_LEVEL=WARNING\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DAO_LOG_LEVEL", "ERROR")
        settings = Settings()
        assert settings.log_level == "ERROR"


class TestGetSettings:
    """Test get_settings function."""

    def test_returns_settings_instance(self) -> None:
        """get_settings should return a Settings instance."""
        settings = get_settings(use_cache=False)
        assert isinstance(settings, Settings)

    def test_cache_returns_same_instance(self) -> None:
        """get_settings should return cached instance by default."""
        # Clear cache first by creating new instance
        _ = get_settings(use_cache=False)
        settings1 = get_settings(use_cache=True)
        settings2 = get_settings()
        assert settings1 is settings2

    def test_no_cache_returns_new_instance(self) -> None:
        """get_settings(use_cache=False) should return new instance."""
        settings1 = get_settings(use_cache=False)
        settings2 = get_settings(use_cache=False)
        assert settings1 is not settings2

    def test_cache_can_be_refreshed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cache should be refreshable with use_cache=False."""
        # Clear env var first to ensure clean state
        monkeypatch.delenv("DAO_LOG_LEVEL", raising=False)

        # Get initial cached settings
        settings1 = get_settings(use_cache=False)
        assert settings1.log_level == "INFO"

        # Change env and get new settings without cache
        monkeypatch.setenv("DAO_LOG_LEVEL", "DEBUG")
        settings2 = get_settings(use_cache=False)
        assert settings2.log_level == "DEBUG"


class TestSettingsPathHandling:
    """Test Path handling in Settings."""

    def test_relative_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Relative path should be preserved."""
        monkeypatch.setenv("DAO_ARTIFACTS_DIR", "./output")
        settings = Settings()
        assert settings.artifacts_dir == Path("./output")

    def test_absolute_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absolute path should be preserved."""
        monkeypatch.setenv("DAO_ARTIFACTS_DIR", "/var/artifacts")
        settings = Settings()
        assert settings.artifacts_dir == Path("/var/artifacts")
