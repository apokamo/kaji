"""Tests for ``resolve_main_worktree`` (Issue gl:11).

cwd 依存で feature worktree に書き込まれる LocalProvider の問題を、
``provider.local.default_branch`` を checkout している worktree (= main worktree)
へ固定することで解消する。本テストはその helper の単体検証。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kaji_harness.providers._worktree import (
    parse_worktree_porcelain,
    resolve_main_worktree,
)
from kaji_harness.providers.local import LocalProviderError


@pytest.mark.small
class TestParseWorktreePorcelain:
    """porcelain 出力のパース（純粋関数）."""

    def test_single_worktree(self) -> None:
        out = "worktree /home/u/repo\nHEAD abc123\nbranch refs/heads/main\n"
        blocks = parse_worktree_porcelain(out)
        assert blocks == [
            {"worktree": "/home/u/repo", "HEAD": "abc123", "branch": "refs/heads/main"}
        ]

    def test_multiple_worktrees_blank_separator(self) -> None:
        out = (
            "worktree /home/u/main\nHEAD a1\nbranch refs/heads/main\n\n"
            "worktree /home/u/feat\nHEAD b2\nbranch refs/heads/fix/11\n"
        )
        blocks = parse_worktree_porcelain(out)
        assert len(blocks) == 2
        assert blocks[0]["worktree"] == "/home/u/main"
        assert blocks[0]["branch"] == "refs/heads/main"
        assert blocks[1]["worktree"] == "/home/u/feat"
        assert blocks[1]["branch"] == "refs/heads/fix/11"

    def test_bare_block_has_no_branch(self) -> None:
        out = "worktree /home/u/bare.git\nbare\n\nworktree /home/u/main\nHEAD a1\nbranch refs/heads/main\n"
        blocks = parse_worktree_porcelain(out)
        assert len(blocks) == 2
        assert blocks[0] == {"worktree": "/home/u/bare.git", "bare": ""}
        assert "branch" not in blocks[0]
        assert blocks[1]["branch"] == "refs/heads/main"

    def test_detached_block_has_no_branch(self) -> None:
        out = "worktree /home/u/det\nHEAD deadbeef\ndetached\n"
        blocks = parse_worktree_porcelain(out)
        assert blocks == [{"worktree": "/home/u/det", "HEAD": "deadbeef", "detached": ""}]
        assert "branch" not in blocks[0]

    def test_no_trailing_newline(self) -> None:
        out = "worktree /home/u/main\nHEAD a1\nbranch refs/heads/main"
        blocks = parse_worktree_porcelain(out)
        assert len(blocks) == 1
        assert blocks[0]["worktree"] == "/home/u/main"

    def test_trailing_blank_line(self) -> None:
        out = "worktree /home/u/main\nHEAD a1\nbranch refs/heads/main\n\n"
        blocks = parse_worktree_porcelain(out)
        assert len(blocks) == 1

    def test_empty_output(self) -> None:
        assert parse_worktree_porcelain("") == []
        assert parse_worktree_porcelain("\n") == []


@pytest.fixture()
def bare_with_two_worktrees(tmp_path: Path) -> tuple[Path, Path, Path]:
    """bare repo + main worktree + feature worktree を作成して返す。"""
    bare = tmp_path / "repo.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
        check=True,
    )
    main_wt = tmp_path / "main"
    feat_wt = tmp_path / "feat"
    # seed
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    (seed / "README.md").write_text("r\n")
    subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-q", "-m", "init"],
        check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
    # add worktrees on the bare repo
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", str(main_wt), "main"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", "-b", "fix/x", str(feat_wt), "main"],
        check=True,
    )
    return bare, main_wt, feat_wt


@pytest.mark.medium
class TestResolveMainWorktree:
    def test_resolve_from_feature_returns_main(
        self, bare_with_two_worktrees: tuple[Path, Path, Path]
    ) -> None:
        _bare, main_wt, feat_wt = bare_with_two_worktrees
        result = resolve_main_worktree(start_dir=feat_wt, default_branch="main")
        assert result == main_wt.resolve()

    def test_resolve_from_main_returns_main(
        self, bare_with_two_worktrees: tuple[Path, Path, Path]
    ) -> None:
        _bare, main_wt, _ = bare_with_two_worktrees
        result = resolve_main_worktree(start_dir=main_wt, default_branch="main")
        assert result == main_wt.resolve()

    def test_no_matching_branch_raises(
        self, bare_with_two_worktrees: tuple[Path, Path, Path]
    ) -> None:
        _bare, main_wt, _ = bare_with_two_worktrees
        with pytest.raises(LocalProviderError, match="no worktree found for branch 'release'"):
            resolve_main_worktree(start_dir=main_wt, default_branch="release")

    def test_non_git_dir_falls_back_to_start_dir(self, tmp_path: Path) -> None:
        """非 git ディレクトリでは fallback として ``start_dir`` を返す。

        production の ``provider.type='local'`` 利用者は常に git repo を持つが、
        テスト fixture が非 git tmp_path に対し ``get_provider()`` を呼ぶ経路の
        後方互換のため、git でなければ ``start_dir`` を返す。
        """
        plain = tmp_path / "plain"
        plain.mkdir()
        assert resolve_main_worktree(start_dir=plain, default_branch="main") == plain.resolve()

    def test_custom_default_branch(self, bare_with_two_worktrees: tuple[Path, Path, Path]) -> None:
        _bare, _main_wt, feat_wt = bare_with_two_worktrees
        result = resolve_main_worktree(start_dir=feat_wt, default_branch="fix/x")
        assert result == feat_wt.resolve()
