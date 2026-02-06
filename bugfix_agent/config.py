"""Configuration management for Bugfix Agent v5

This module provides configuration loading and access functions:
- load_config: Load config.toml with caching
- get_config_value: Access nested config values via dot notation
- get_workdir: Get the working directory with fallbacks
"""

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

# Default config path (relative to this file's parent directory)
CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """設定ファイルを読み込む（キャッシュ付き）

    環境変数 BUGFIX_AGENT_CONFIG でパスを上書き可能。
    ファイルが存在しない場合は空の辞書を返す。
    """
    config_path = Path(os.environ.get("BUGFIX_AGENT_CONFIG", CONFIG_PATH))
    if config_path.exists():
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    return {}


def get_config_value(key_path: str, default: Any = None) -> Any:
    """ドット記法で設定値を取得する

    Args:
        key_path: "agent.max_loop_count" のようなドット区切りのキーパス
        default: キーが存在しない場合のデフォルト値
    """
    config = load_config()
    keys = key_path.split(".")
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


def get_workdir() -> Path:
    """作業ディレクトリを取得する

    優先順位:
    1. 環境変数 BUGFIX_AGENT_WORKDIR
    2. config.toml の agent.workdir
    3. リポジトリルートを自動検出（このファイルの4階層上）
    """
    env_workdir = os.environ.get("BUGFIX_AGENT_WORKDIR")
    if env_workdir:
        return Path(env_workdir)

    config_workdir = get_config_value("agent.workdir", "")
    if config_workdir:
        return Path(config_workdir)

    # bugfix_agent/config.py -> bugfix_agent -> repo_root
    return Path(__file__).parents[1]
