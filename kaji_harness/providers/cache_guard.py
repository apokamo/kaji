"""GitHub cache layout の guard（legacy forge cache の fail-fast 検出）。

cache layout は ``sync``（書き手）と ``LocalProvider``（読み手）が共有する契約で
あり、どちらか一方の実装詳細ではない。よって独立した public module に置く。

Issue #285: 以前は ``sync.py`` の private 関数だったため
``providers.local -> sync._detect_legacy_forge_cache`` という「下位層 → 上位層」の
逆流と、``sync <-> providers.local`` の実行時循環依存（deferred import で回避されて
いた）を生んでいた。本 module へ移設して依存の向きを一方向に是正した（ADR 009）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import SyncError

# ``.kaji/cache/`` 直下の sync メタデータファイル名。cache layout 契約の正本。
SYNC_META_FILENAME = ".sync-meta.json"

# 過去 forge のリテラル（Issue #191 で撤去された forge の名称）。
# 本 module 以外で参照しない（設計書 § ベースライン計測 § 許容除外規則 § 2）。
_LEGACY_FORGE_LITERAL = "gitlab"
_LEGACY_FORGE_DISPLAY = "GitLab"


def detect_legacy_forge_cache(cache_dir: Path) -> None:
    """legacy forge cache を検出し ``SyncError`` で fail-fast (Issue #191)。

    検出条件は **OR 結合** (設計書 § Cache artifact 移行ポリシー):

    - (a) ``.sync-meta.json`` の ``forge`` が撤去済 forge
    - (b) ``cache_dir/gl-*.json`` の存在

    いずれか成立で recovery 手順を含む ``SyncError`` を raise する。
    両条件不成立（GitHub cache のみ / cache 空）は無音で通過する。
    ``sync_status`` / ``view_cached_*`` / ``list_issues`` の cache 統合経路の
    **すべての entry point** から先頭で呼び出す。
    """
    if not cache_dir.is_dir():
        return
    meta_path = cache_dir / SYNC_META_FILENAME
    meta_legacy = False
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("forge") == _LEGACY_FORGE_LITERAL:
            meta_legacy = True
    gl_files = sorted(cache_dir.glob("gl-*.json"))
    gl_count = len(gl_files)
    if not meta_legacy and gl_count == 0:
        return
    detail_lines: list[str] = []
    if meta_legacy:
        detail_lines.append(f"  - .sync-meta.json forge='{_LEGACY_FORGE_LITERAL}'")
    if gl_count:
        detail_lines.append(f"  - gl-*.json files: {gl_count} file(s)")
    recovery_lines = ["To recover:", "  1. Remove the legacy cache:"]
    if gl_count:
        recovery_lines.append(f"       rm -f {cache_dir}/gl-*.json")
    if meta_legacy:
        recovery_lines.append(f"       rm -f {cache_dir}/{SYNC_META_FILENAME}")
    recovery_lines.append("  2. Re-sync from GitHub:")
    recovery_lines.append("       kaji sync from-github")
    message = (
        f"legacy {_LEGACY_FORGE_DISPLAY} cache detected at {cache_dir}/\n"
        + "\n".join(detail_lines)
        + "\n\n"
        + f"{_LEGACY_FORGE_DISPLAY} forge support has been removed in this version of kaji.\n"
        + "\n".join(recovery_lines)
    )
    raise SyncError(message)
