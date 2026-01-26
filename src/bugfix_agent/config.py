"""Configuration management for Bugfix Agent v5

This module provides configuration loading and access functions:
- Settings: pydantic-settings based configuration with BUGFIX_AGENT_ prefix
- find_config_file: Search for config.toml in priority order
- resolve_workdir: Determine working directory with priority rules
- get_config_value: Access nested config values via dot notation (backward compatible)
- load_config: Load config.toml with caching (legacy)
"""

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default config path (relative to this file's parent directory)
CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Environment variables use BUGFIX_AGENT_ prefix.
    Priority: env > config.toml > defaults

    Attributes:
        max_loop_count: Maximum loop iterations for design/review cycles.
        workdir: Working directory path (None = determined at runtime).
        max_comment_retries: Maximum retries for GitHub comment posting.
        retry_delay: Delay in seconds between retries.
        context_max_chars: Maximum characters for context building.
    """

    max_loop_count: int = Field(default=3, description="Maximum loop iterations")
    workdir: Path | None = Field(default=None, description="Working directory")
    max_comment_retries: int = Field(default=2, description="Max GitHub comment retries")
    retry_delay: float = Field(default=1.0, description="Retry delay in seconds")
    context_max_chars: int = Field(default=4000, description="Max context characters")

    model_config = SettingsConfigDict(
        env_prefix="BUGFIX_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Settings cache for singleton pattern
_settings_cache: Settings | None = None


def get_settings(use_cache: bool = True) -> Settings:
    """Get Settings instance with optional caching.

    Args:
        use_cache: If True, return cached instance. If False, create new instance.

    Returns:
        Settings instance.
    """
    global _settings_cache
    if use_cache and _settings_cache is not None:
        return _settings_cache
    _settings_cache = Settings()
    return _settings_cache


def find_config_file(
    config_path: Path | None = None,
    workdir: Path | None = None,
) -> Path | None:
    """Search for config.toml in priority order.

    Priority:
    1. Environment variable BUGFIX_AGENT_CONFIG (highest)
    2. CLI --config option (config_path parameter)
    3. {workdir}/config.toml
    4. {CWD}/config.toml
    5. ~/.config/bugfix-agent/config.toml (user config)
    6. None (use defaults only)

    Args:
        config_path: CLI --config option path (explicit path).
        workdir: Working directory (CLI --workdir from).

    Returns:
        Found config file path, or None if not found.

    Raises:
        FileNotFoundError: If config_path is specified but doesn't exist.
    """
    # 1. Environment variable (highest priority)
    if env_config := os.environ.get("BUGFIX_AGENT_CONFIG"):
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
    path = Path.home() / ".config" / "bugfix-agent" / "config.toml"
    if path.exists():
        return path

    return None


def resolve_workdir(
    cli_workdir: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    """Resolve working directory with priority rules.

    Priority:
    1. CLI --workdir argument (highest)
    2. Environment variable BUGFIX_AGENT_WORKDIR
    3. Settings.workdir (from config.toml via Settings)
    4. Current working directory (fallback)

    Args:
        cli_workdir: CLI --workdir argument.
        settings: Settings instance (optional).

    Returns:
        Resolved working directory as absolute path.
    """
    # 1. CLI argument (highest priority)
    if cli_workdir is not None:
        return cli_workdir.resolve()

    # 2. Environment variable
    if env_workdir := os.environ.get("BUGFIX_AGENT_WORKDIR"):
        return Path(env_workdir).resolve()

    # 3. Settings (from config.toml or env)
    if settings and settings.workdir:
        return settings.workdir.resolve()

    # 4. Current working directory
    return Path.cwd()


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load config.toml with caching (legacy function).

    Searches for config file using find_config_file() logic.
    Returns empty dict if no config file found.

    Returns:
        Parsed config as dictionary.
    """
    config_path = find_config_file()
    if config_path and config_path.exists():
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    return {}


# Mapping from config.toml key paths to Settings attribute names
_SETTINGS_KEY_MAP: dict[str, str] = {
    "agent.max_loop_count": "max_loop_count",
    "agent.workdir": "workdir",
    "github.max_comment_retries": "max_comment_retries",
    "github.retry_delay": "retry_delay",
    "tools.context_max_chars": "context_max_chars",
}


def get_config_value(key_path: str, default: Any = None) -> Any:
    """Get config value with Settings priority.

    Backward-compatible wrapper that:
    1. Checks if key_path maps to a Settings attribute (priority)
    2. Falls back to config.toml for unmapped keys (legacy)
    3. Returns default if not found anywhere

    Args:
        key_path: Dot-notation key path (e.g., "agent.max_loop_count")
        default: Default value if key not found

    Returns:
        Configuration value.
    """
    # 1. Check Settings mapping first (priority)
    if key_path in _SETTINGS_KEY_MAP:
        settings = get_settings()
        attr_name = _SETTINGS_KEY_MAP[key_path]
        settings_value = getattr(settings, attr_name, None)
        if settings_value is not None:
            return settings_value
        # If Settings value is None, fall through to toml

    # 2. Fall back to config.toml (legacy)
    config = load_config()
    keys = key_path.split(".")
    toml_value: Any = config
    for key in keys:
        if isinstance(toml_value, dict) and key in toml_value:
            toml_value = toml_value[key]
        else:
            return default
    return toml_value


def get_workdir() -> Path:
    """Get working directory (legacy function).

    Deprecated: Use resolve_workdir() for new code.

    Returns:
        Working directory path.
    """
    return resolve_workdir(settings=get_settings())
