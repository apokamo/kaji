"""Worktree discovery for SessionState backfill (Issue #218).

旧 kaji 版で作られた ``session-state.json`` には ``worktree_dir`` / ``branch_name``
が無いため、resumed run では label 由来の path を毎回再合成して既存 worktree と
乖離する不具合があった。本 module は ``git worktree list --porcelain`` を走査し、
規約 ``<known-prefix>/<issue_id>`` branch + 規約準拠 path basename + physical
existence の 3 条件 AND で既存 worktree を発見する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .providers import DEFAULT_BRANCH_PREFIX, LABEL_TO_PREFIX


class AmbiguousWorktreeError(Exception):
    """同一 issue_id に対し複数の worktree 候補が検出された。"""

    def __init__(self, issue_id: str, candidates: list[tuple[str, str]]) -> None:
        self.issue_id = issue_id
        self.candidates = candidates
        super().__init__(
            f"multiple worktrees match issue {issue_id!r}: "
            + ", ".join(f"{p} ({b})" for p, b in candidates)
        )


def _parse_worktree_list(porcelain: str) -> list[tuple[str, str | None]]:
    """`git worktree list --porcelain` 出力を ``[(worktree_path, branch_ref_or_None), ...]`` に parse。

    porcelain format は entry を空行で区切る:
        worktree /path
        HEAD <sha>
        branch refs/heads/foo
        (空行)
    """
    entries: list[tuple[str, str | None]] = []
    current_path: str | None = None
    current_branch: str | None = None
    for line in porcelain.splitlines():
        if not line.strip():
            if current_path is not None:
                entries.append((current_path, current_branch))
            current_path = None
            current_branch = None
            continue
        if line.startswith("worktree "):
            current_path = line[len("worktree ") :].strip()
        elif line.startswith("branch "):
            current_branch = line[len("branch ") :].strip()
    if current_path is not None:
        entries.append((current_path, current_branch))
    return entries


def discover_existing_worktree(
    repo_root: Path,
    issue_id: str,
    worktree_prefix: str,
) -> tuple[str, str] | None:
    """既存 worktree を 3 条件 AND で探索する。

    候補条件:
        1. branch が ``refs/heads/<known-prefix>/<issue_id>`` 形式
           （known-prefix = ``LABEL_TO_PREFIX`` の values + ``DEFAULT_BRANCH_PREFIX``）
        2. worktree path がディレクトリとして実在する
        3. path basename が ``<worktree_prefix>-<prefix>-<issue_id>`` 規約
           （`build_worktree_dir` と同形）

    Returns:
        単一候補時: ``(worktree_path, branch_name)`` の tuple
            （branch_name は ``<prefix>/<issue_id>``、``refs/heads/`` 除去済み）
        0 候補時: ``None``

    Raises:
        AmbiguousWorktreeError: 2 候補以上が見つかった場合。
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    known_prefixes = set(LABEL_TO_PREFIX.values()) | {DEFAULT_BRANCH_PREFIX}
    wt_prefix = worktree_prefix or "kaji"

    candidates: list[tuple[str, str]] = []
    for path, branch_ref in _parse_worktree_list(result.stdout):
        if branch_ref is None or not branch_ref.startswith("refs/heads/"):
            continue
        branch_name = branch_ref[len("refs/heads/") :]
        # `<prefix>/<issue_id>` 形式チェック
        if "/" not in branch_name:
            continue
        prefix, _, tail = branch_name.partition("/")
        if prefix not in known_prefixes or tail != issue_id:
            continue
        if not Path(path).is_dir():
            continue
        expected_basename = f"{wt_prefix}-{prefix}-{issue_id}"
        if Path(path).name != expected_basename:
            continue
        candidates.append((path, branch_name))

    if not candidates:
        return None
    if len(candidates) >= 2:
        raise AmbiguousWorktreeError(issue_id, candidates)
    return candidates[0]
