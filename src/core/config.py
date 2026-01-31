"""Configuration management using pydantic-settings and config.toml."""

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

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


# ============================================================================
# config.toml support
# ============================================================================


def find_config_file(
    config_path: Path | None = None,
    workdir: Path | None = None,
) -> Path | None:
    """Search for config.toml in priority order.

    Priority:
    1. Environment variable DAO_CONFIG (highest)
    2. CLI --config option (config_path parameter)
    3. {workdir}/config.toml
    4. {CWD}/config.toml
    5. ~/.config/dao/config.toml (user config)
    6. None (use defaults only)

    Args:
        config_path: CLI --config option path (explicit path).
        workdir: Working directory (CLI --workdir option).

    Returns:
        Found config file path, or None if not found.

    Raises:
        FileNotFoundError: If config_path is specified but doesn't exist.
    """
    # 1. Environment variable (highest priority)
    if env_config := os.environ.get("DAO_CONFIG"):
        path = Path(env_config)
        if path.exists():
            return path

    # 2. CLI --config option
    if config_path is not None:
        if config_path.exists():
            return config_path
        # Explicit path must exist
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # 3. workdir/config.toml
    if workdir:
        path = workdir / "config.toml"
        if path.exists():
            return path

    # 4. CWD/config.toml
    path = Path.cwd() / "config.toml"
    if path.exists():
        return path

    # 5. User config
    path = Path.home() / ".config" / "dao" / "config.toml"
    if path.exists():
        return path

    return None


_config_cache: dict[str, Any] | None = None


def load_config(use_cache: bool = True) -> dict[str, Any]:
    """Load config.toml with caching.

    Searches for config file using find_config_file() logic.
    Returns empty dict if no config file found.

    Args:
        use_cache: If True, return cached config. If False, reload from file.

    Returns:
        Parsed config as dictionary.
    """
    global _config_cache

    if use_cache and _config_cache is not None:
        return _config_cache

    config_path = find_config_file()
    if config_path and config_path.exists():
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}

    if use_cache:
        _config_cache = config

    return config


def get_config_value(key_path: str, default: Any = None) -> Any:
    """Get config value from config.toml.

    Args:
        key_path: Dot-notation key path (e.g., "tools.claude.model")
        default: Default value if key not found

    Returns:
        Configuration value, or default if not found.
    """
    config = load_config()

    keys = key_path.split(".")
    value: Any = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value
