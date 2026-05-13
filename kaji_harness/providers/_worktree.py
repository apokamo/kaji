"""Resolve the main worktree (``default_branch`` checkout) for LocalProvider.

Issue gl:11: cwd 起点で ``.kaji/config.toml`` を discover すると feature worktree
配下では ``repo_root`` が feature worktree のルートになり、LocalProvider の
``.kaji/issues/`` 書き込みと ``git commit`` が feature branch に向かってしまう。
本 module は ``git worktree list --porcelain`` を解析し、
``provider.local.default_branch`` を checkout している worktree（= main worktree）
の絶対パスを返す。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .local import LocalProviderError


def parse_worktree_porcelain(output: str) -> list[dict[str, str]]:
    """``git worktree list --porcelain`` の出力をブロック列に変換する純粋関数。

    porcelain フォーマット (`git-worktree(1)`): 各属性は ``key value`` (または ``key`` 単独)
    の 1 行、空行で worktree レコードを区切る。
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw in output.splitlines():
        if raw == "":
            if current:
                blocks.append(current)
                current = {}
            continue
        key, sep, value = raw.partition(" ")
        current[key] = value if sep else ""
    if current:
        blocks.append(current)
    return blocks


def resolve_main_worktree(*, start_dir: Path, default_branch: str) -> Path:
    """``default_branch`` を checkout している worktree の絶対パスを返す。

    Args:
        start_dir: ``git -C`` の作業ディレクトリ（``config.repo_root`` を渡す）。
        default_branch: ``provider.local.default_branch`` の値。

    Raises:
        LocalProviderError: ``git worktree list`` が失敗した / 一致 worktree が無い /
            porcelain 出力が parse 不能だった場合。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(start_dir), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # git CLI not on PATH → fallback per design § 失敗ケース表 (gl:11 設計書)。
        # Downstream git ops surface a clearer "git: command not found" if invoked.
        return start_dir.resolve()
    if proc.returncode != 0:
        # 非 git repo → fallback per design § 失敗ケース表 (gl:11 設計書)。
        # production の provider.type='local' は git repo を前提 (§ 制約・前提条件)
        # とするため到達しない。kaji harness の medium テスト fixture (config 解析 /
        # dispatch / preflight など) が非 git tmp_path に対し get_provider() を呼ぶ
        # 経路を維持するための明示仕様。
        return start_dir.resolve()
    blocks = parse_worktree_porcelain(proc.stdout)
    target = f"refs/heads/{default_branch}"
    matches: list[Path] = [
        Path(b["worktree"]) for b in blocks if b.get("branch") == target and "worktree" in b
    ]
    if not matches:
        raise LocalProviderError(
            f"no worktree found for branch {default_branch!r}. "
            f"Run 'git worktree add ../{default_branch} {default_branch}' "
            f"(or adjust provider.local.default_branch)."
        )
    if len(matches) > 1:
        sys.stderr.write(
            f"warning: multiple worktrees checking out {default_branch!r}; using {matches[0]}\n"
        )
    return matches[0].resolve()
