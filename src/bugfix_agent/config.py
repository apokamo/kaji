"""Configuration management for Bugfix Agent v5

This module provides configuration loading and access functions:
- Settings: pydantic-settings based configuration with BUGFIX_AGENT_ prefix
- find_config_file: Search for config.toml in priority order
- resolve_workdir: Determine working directory with priority rules
- get_config_value: Access nested config values via dot notation (backward compatible)
- load_config: Load config.toml with caching (legacy)

Note:
    find_config_file and load_config use src.core.config as the base implementation,
    with bugfix_agent-specific environment variable (BUGFIX_AGENT_CONFIG) and
    user config path (~/.config/bugfix-agent/).
"""

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.config import find_config_file as _core_find_config_file

# Default config path (relative to this file's parent directory)
CONFIG_PATH = Path(__file__).parent.parent / "config.toml"

# Bugfix agent specific configuration
_BUGFIX_AGENT_ENV_VAR = "BUGFIX_AGENT_CONFIG"
_BUGFIX_AGENT_USER_CONFIG_DIR = "bugfix-agent"


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

    Uses src.core.config.find_config_file with bugfix_agent-specific settings:
    - Environment variable: BUGFIX_AGENT_CONFIG
    - User config path: ~/.config/bugfix-agent/config.toml

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
    return _core_find_config_file(
        config_path=config_path,
        workdir=workdir,
        env_var=_BUGFIX_AGENT_ENV_VAR,
        user_config_dir=_BUGFIX_AGENT_USER_CONFIG_DIR,
    )


def resolve_workdir(
    cli_workdir: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    """Resolve working directory with priority rules.

    Priority:
    1. CLI --workdir argument (highest)
    2. Environment variable BUGFIX_AGENT_WORKDIR
    3. config.toml agent.workdir
    4. Current working directory (fallback)

    Args:
        cli_workdir: CLI --workdir argument.
        settings: Settings instance (optional, used if workdir explicitly set via env).

    Returns:
        Resolved working directory as absolute path.
    """
    # 1. CLI argument (highest priority)
    if cli_workdir is not None:
        return cli_workdir.resolve()

    # 2. Environment variable (check directly, not via Settings)
    if env_workdir := os.environ.get("BUGFIX_AGENT_WORKDIR"):
        return Path(env_workdir).resolve()

    # 3. config.toml agent.workdir
    config = load_config()
    if toml_workdir := config.get("agent", {}).get("workdir"):
        return Path(toml_workdir).resolve()

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
    """Get config value with priority: env > config.toml > defaults.

    Backward-compatible wrapper that:
    1. For mapped keys: env (via Settings) > config.toml > Settings defaults
    2. For unmapped keys: config.toml > provided default

    Args:
        key_path: Dot-notation key path (e.g., "agent.max_loop_count")
        default: Default value if key not found

    Returns:
        Configuration value.
    """
    # Load config.toml once
    config = load_config()

    # 1. Check Settings mapping
    if key_path in _SETTINGS_KEY_MAP:
        settings = get_settings()
        attr_name = _SETTINGS_KEY_MAP[key_path]

        # If env variable explicitly set, use Settings value (priority 1)
        if attr_name in settings.model_fields_set:
            return getattr(settings, attr_name)

        # Check config.toml (priority 2)
        keys = key_path.split(".")
        toml_value: Any = config
        for key in keys:
            if isinstance(toml_value, dict) and key in toml_value:
                toml_value = toml_value[key]
            else:
                # Not in toml, return Settings default (priority 3)
                return getattr(settings, attr_name)
        return toml_value

    # 2. Unmapped keys: Fall back to config.toml (legacy)
    keys = key_path.split(".")
    toml_value = config
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
