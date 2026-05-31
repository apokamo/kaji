"""Tests for worktree_discovery (Issue #218)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kaji_harness.worktree_discovery import (
    AmbiguousWorktreeError,
    _parse_worktree_list,
    discover_existing_worktree,
)


@pytest.mark.small
class TestParseWorktreeList:
    def test_parses_multiple_entries(self) -> None:
        porcelain = (
            "worktree /a/main\n"
            "HEAD abc\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /a/wt\n"
            "HEAD def\n"
            "branch refs/heads/fix/218\n"
            "\n"
        )
        result = _parse_worktree_list(porcelain)
        assert result == [
            ("/a/main", "refs/heads/main"),
            ("/a/wt", "refs/heads/fix/218"),
        ]

    def test_detached_head_has_no_branch(self) -> None:
        porcelain = "worktree /a/detached\nHEAD abc\ndetached\n\n"
        result = _parse_worktree_list(porcelain)
        assert result == [("/a/detached", None)]


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init", "-q"],
        check=True,
    )


@pytest.mark.medium
class TestDiscoverExistingWorktree:
    def test_single_candidate_returned(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "kaji-chore-218"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "chore/218", str(wt)],
            check=True,
        )
        result = discover_existing_worktree(repo, "218", "kaji")
        assert result is not None
        path, branch = result
        assert Path(path) == wt
        assert branch == "chore/218"

    def test_zero_candidates_returns_none(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        assert discover_existing_worktree(repo, "218", "kaji") is None

    def test_basename_violation_excluded(self, tmp_path: Path) -> None:
        """branch は規約合致だが path basename が規約違反 → 除外。"""
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "random-218"  # 規約外 basename
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "fix/218", str(wt)],
            check=True,
        )
        assert discover_existing_worktree(repo, "218", "kaji") is None

    def test_unknown_branch_prefix_excluded(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "kaji-foobar-218"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "foobar/218", str(wt)],
            check=True,
        )
        assert discover_existing_worktree(repo, "218", "kaji") is None

    def test_main_branch_excluded(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        # main worktree のみ → 候補なし
        assert discover_existing_worktree(repo, "218", "kaji") is None

    def test_ambiguous_raises(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt1 = tmp_path / "kaji-chore-218"
        wt2 = tmp_path / "kaji-feat-218"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "chore/218", str(wt1)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "feat/218", str(wt2)],
            check=True,
        )
        with pytest.raises(AmbiguousWorktreeError) as ei:
            discover_existing_worktree(repo, "218", "kaji")
        assert ei.value.issue_id == "218"
        assert len(ei.value.candidates) == 2

    def test_custom_worktree_prefix(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "myproj-fix-218"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "fix/218", str(wt)],
            check=True,
        )
        # worktree_prefix="kaji" では一致しない
        assert discover_existing_worktree(repo, "218", "kaji") is None
        # worktree_prefix="myproj" で一致する
        result = discover_existing_worktree(repo, "218", "myproj")
        assert result is not None
        assert result[1] == "fix/218"
