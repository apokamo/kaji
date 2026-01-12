"""Configuration management using pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file.

    Attributes:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        artifacts_dir: Directory for output artifacts.
    """

    log_level: LogLevel = "INFO"
    artifacts_dir: Path = Path("./artifacts")

    model_config = SettingsConfigDict(
        env_prefix="DAO_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


_settings_cache: Settings | None = None


def get_settings(use_cache: bool = True) -> Settings:
    """Get application settings.

    Args:
        use_cache: If True, return cached instance. If False, create new instance.

    Returns:
        Settings instance.
    """
    global _settings_cache

    if use_cache and _settings_cache is not None:
        return _settings_cache

    settings = Settings()

    if use_cache:
        _settings_cache = settings

    return settings
