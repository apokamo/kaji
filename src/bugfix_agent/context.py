"""Context building utilities for Bugfix Agent v5

This module provides context data construction:
- build_context: Build context from text or file paths with security checks
"""

from pathlib import Path

from .config import get_config_value, get_workdir
from .logging import warn


def build_context(
    context: str | list[str],
    max_chars: int | None = None,
    allowed_root: Path | None = None,
) -> str:
    """コンテキストデータを構築する（共通ユーティリティ）

    Args:
        context: コンテキスト情報
            - str: テキストとしてそのまま返す（max_chars で切り詰め）
            - list[str]: ファイルパスリストとして各ファイルを読み込み
        max_chars: 最大文字数（None で config から取得、0 で無制限）
        allowed_root: 読み込み許可するルートディレクトリ（Path Traversal 対策）
            None の場合は get_workdir() を使用

    Returns:
        構築されたコンテキスト文字列

    Note:
        allowed_root 配下でないパスや読み取り権限のないファイルは
        スキップされ、警告が stderr に出力されます。
    """
    # 文字列入力の場合もmax_chars適用
    if isinstance(context, str):
        if max_chars is None:
            max_chars = get_config_value("tools.context_max_chars", 4000)
        if max_chars and len(context) > max_chars:
            return context[:max_chars]
        return context

    # ファイルパスリストの場合
    if allowed_root is None:
        allowed_root = get_workdir()
    allowed_root = allowed_root.resolve()

    result_parts: list[str] = []
    for path_str in context:
        path = Path(path_str)
        if not path.exists():
            continue

        # Path Traversal 対策: 許可されたルート配下かチェック
        try:
            resolved = path.resolve()
            resolved.relative_to(allowed_root)
        except ValueError:
            # allowed_root 配下でない場合はスキップ（警告出力）
            warn(f"Skipping path outside allowed_root: {path_str} (not under {allowed_root})")
            continue

        try:
            content = resolved.read_text(encoding="utf-8")
            result_parts.append(f"\n--- {path_str} ---\n{content}\n")
        except (PermissionError, OSError) as e:
            warn(f"Failed to read {path_str}: {e}")
            continue

    result = "".join(result_parts)

    # 最大文字数制限
    if max_chars is None:
        max_chars = get_config_value("tools.context_max_chars", 4000)
    if max_chars and len(result) > max_chars:
        result = result[:max_chars]

    return result
