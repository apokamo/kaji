"""Resolve the main worktree (``default_branch`` checkout).

feature worktree 配下で動く処理（artifacts の main 側解決、provider overlay の
divergence 検査、baseline precheck）が main worktree を参照するために使う。
本 module は ``git worktree list --porcelain`` を解析し、``default_branch`` を
checkout している worktree（= main worktree）の絶対パスを返す。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..errors import MainWorktreeResolutionError


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
        default_branch: provider 設定の ``default_branch`` の値。

    Returns:
        ``default_branch`` を checkout している worktree の絶対パス。

    Raises:
        MainWorktreeResolutionError: 以下のいずれか。
            - ``git`` CLI が PATH 上に無い (``FileNotFoundError``)。
            - ``git worktree list --porcelain`` が exit != 0 を返した（非 git repo 等）。
            - ``default_branch`` に一致する worktree が無い（= 作業者が main worktree を
              作っていない）。porcelain 出力が parse 不能だった場合も「一致 0 件」経路に
              合流して同 error を raise する。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(start_dir), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MainWorktreeResolutionError(
            "git CLI not found on PATH; install git and ensure it is on PATH."
        ) from exc
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "(empty)"
        raise MainWorktreeResolutionError(
            f"'git -C {start_dir} worktree list' failed (exit {proc.returncode}). "
            f"run from a git worktree (or 'git init' first). "
            f"stderr: {stderr}"
        )
    blocks = parse_worktree_porcelain(proc.stdout)
    target = f"refs/heads/{default_branch}"
    matches: list[Path] = [
        Path(b["worktree"]) for b in blocks if b.get("branch") == target and "worktree" in b
    ]
    if not matches:
        raise MainWorktreeResolutionError(
            f"no worktree found for branch {default_branch!r}. "
            f"Run 'git worktree add ../{default_branch} {default_branch}' "
            f"(or adjust the provider's default_branch)."
        )
    if len(matches) > 1:
        sys.stderr.write(
            f"warning: multiple worktrees checking out {default_branch!r}; using {matches[0]}\n"
        )
    return matches[0].resolve()
