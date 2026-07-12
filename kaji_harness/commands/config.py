"""``kaji config`` + dispatch 用 config 読込（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..artifacts import resolve_artifacts_dir
from ..config import KajiConfig
from ..errors import ConfigLoadError, ConfigNotFoundError
from ..providers import (
    actual_provider_type,
    get_provider,
    provider_overlay_divergence_warning,
)
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK


def _emit_provider_overlay_divergence_warning(config: KajiConfig) -> None:
    """provider overlay の worktree 間ズレを検出したら stderr に WARN を出す。

    overlay が無い feature worktree から provider 解決が tracked 値へ沈黙で
    フォールバックし、かつ main worktree の overlay と食い違う場合のみ発火する。
    exit code・標準出力には影響しない。
    """
    warning = provider_overlay_divergence_warning(config)
    if warning is not None:
        sys.stderr.write(warning + "\n")


def _load_config_for_dispatch() -> KajiConfig:
    """Config を読み込む（``kaji issue`` / ``kaji pr`` dispatch 用）。

    Phase 3-e: ``ConfigNotFoundError`` も propagate する（fail-fast 化）。
    Phase 3-c までの「config 不在 → legacy gh passthrough」は廃止。
    呼出側 dispatcher で ``ConfigNotFoundError`` / ``ConfigLoadError`` を
    catch して exit 2 を返す契約。
    """
    config = KajiConfig.discover(start_dir=Path.cwd())
    _emit_provider_overlay_divergence_warning(config)
    return config


def cmd_config_provider_type(args: argparse.Namespace) -> int:
    """Print resolved ``provider.type`` ("github" / "local") to stdout.

    Phase 4 で導入。Skill / 自動化スクリプトが overlay 込みの provider type を
    副作用なく取得するための read-only エントリ。``KajiConfig.discover()``
    と ``get_provider()`` の検証を経由するため、`_handle_pr` / `_handle_issue`
    / `cmd_run` と同じ config resolution path を共有する。

    Exit codes:
        0: 解決成功（stdout に ``"github\\n"`` / ``"local\\n"``）
        2: config 不在 or 不正（stderr に診断メッセージ）
    """
    start_dir = args.workdir.resolve()
    if not start_dir.is_dir():
        print(
            f"Error: --workdir '{args.workdir}' is not a valid directory",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        get_provider(config)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    _emit_provider_overlay_divergence_warning(config)
    sys.stdout.write(f"{actual_provider_type(config)}\n")
    return EXIT_OK


def cmd_config_artifacts_dir(args: argparse.Namespace) -> int:
    """Print the resolved artifacts dir (main-worktree-based) to stdout.

    Issue #305 で導入。incident-* skill 群が feature worktree の cwd に依存せず、
    ``kaji run`` が run/state を書き込む同一の絶対 artifact root を副作用なく
    取得するための read-only エントリ。``resolve_artifacts_dir(config)`` を
    共有するため、``cmd_run`` と同じ解決契約（相対 ``artifacts_dir`` を main
    worktree 基準へ解決、絶対パスはそのまま）に従う。

    Exit codes:
        0: 解決成功（stdout に絶対パス + ``"\\n"``）
        2: config 不在 or 不正（stderr に診断メッセージ）
    """
    start_dir = args.workdir.resolve()
    if not start_dir.is_dir():
        print(
            f"Error: --workdir '{args.workdir}' is not a valid directory",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except (ConfigNotFoundError, ConfigLoadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    sys.stdout.write(f"{resolve_artifacts_dir(config)}\n")
    return EXIT_OK
