"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture(autouse=True)
def clear_config_cache() -> None:
    """Clear config cache before each test.

    This ensures tests don't pick up config.toml from the project root
    unless explicitly intended.
    """
    from src.bugfix_agent import config as bugfix_config
    from src.core import config as core_config

    # Clear core config cache
    core_config._config_cache = None
    core_config._settings_cache = None
    # Clear bugfix_agent lru_cache and settings cache
    bugfix_config.load_config.cache_clear()
    bugfix_config._settings_cache = None

    yield

    # Clear again after test for isolation
    core_config._config_cache = None
    core_config._settings_cache = None
    bugfix_config.load_config.cache_clear()
    bugfix_config._settings_cache = None
