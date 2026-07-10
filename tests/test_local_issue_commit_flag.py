"""Tests for `kaji issue {comment,edit}` `--commit` flag (Issue local-pc5090-16 B).

`_local_issue_comment` / `_local_issue_edit` の atomic commit 動線を検証する。
`git commit --only/-o` を使って事前 staged な無関係 file を HEAD に混入させない
atomicity が肝心。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from kaji_harness.cli_main import _local_issue_comment, _local_issue_edit
from kaji_harness.providers.local import LocalProvider

pytestmark = pytest.mark.medium


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Init a tmp git repo with .kaji/ scaffolding and an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".kaji").mkdir()
    (repo / "README.md").write_text("readme\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture()
def provider_with_issue(git_repo: Path) -> tuple[LocalProvider, str]:
    """Create a LocalProvider and seed one Issue, commit it so HEAD is clean."""
    provider = LocalProvider(repo_root=git_repo, machine_id="pc1")
    issue = provider.create_issue(title="t", body="b", slug="x", labels=["type:feature"])
    # commit the seed issue so subsequent --commit calls operate on a clean HEAD
    _git(git_repo, "add", ".kaji")
    _git(git_repo, "commit", "-q", "-m", "seed issue")
    return provider, issue.id


class TestCommentCommitFlag:
    """`kaji issue comment --commit` の atomic commit 動線."""

    def test_comment_with_commit_creates_atomic_commit(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """`--commit` 指定で working tree が clean になり HEAD に comment が含まれる."""
        provider, issue_id = provider_with_issue
        rc = _local_issue_comment(provider, [issue_id, "--body", "hello", "--commit"])
        assert rc == 0

        # working tree clean
        status = _git(provider.repo_root, "status", "--porcelain").stdout
        assert status == "", f"unexpected dirty status: {status!r}"

        # HEAD includes the new comment file
        files = (
            _git(provider.repo_root, "show", "--name-only", "--format=", "HEAD")
            .stdout.strip()
            .splitlines()
        )
        comment_files = [f for f in files if f.startswith(".kaji/issues/") and "/comments/" in f]
        assert len(comment_files) == 1
        # Issue local-pc5090-21: filename を <YYYYMMDDTHHMMSSZ>-<machine>.md に変更
        assert re.match(
            r".*/comments/\d{8}T\d{6}Z-pc1\.md$",
            comment_files[0],
        ), f"unexpected comment filename: {comment_files[0]!r}"

    def test_comment_with_commit_excludes_unrelated_staged_files(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """事前 staged な無関係 file が HEAD に混入せず staged のまま残る (Must Fix 1)."""
        provider, issue_id = provider_with_issue
        # Pre-stage an unrelated file
        other = provider.repo_root / "other.txt"
        other.write_text("other\n")
        _git(provider.repo_root, "add", "other.txt")

        rc = _local_issue_comment(provider, [issue_id, "--body", "hello", "--commit"])
        assert rc == 0

        # HEAD must not contain other.txt
        head_files = (
            _git(provider.repo_root, "show", "--name-only", "--format=", "HEAD")
            .stdout.strip()
            .splitlines()
        )
        assert "other.txt" not in head_files
        # at least one comment markdown must be in HEAD
        assert any(
            f.startswith(".kaji/issues/") and "/comments/" in f and f.endswith(".md")
            for f in head_files
        )

        # other.txt remains staged in the index
        staged = (
            _git(provider.repo_root, "diff", "--cached", "--name-only").stdout.strip().splitlines()
        )
        assert "other.txt" in staged

    def test_comment_without_commit_leaves_working_tree_dirty(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """`--commit` 不指定の既存挙動: working tree dirty のまま (後方互換)."""
        provider, issue_id = provider_with_issue
        rc = _local_issue_comment(provider, [issue_id, "--body", "hello"])
        assert rc == 0

        status = _git(provider.repo_root, "status", "--porcelain").stdout
        # comment file should be untracked
        assert any(
            ".kaji/issues/" in line and "/comments/" in line for line in status.splitlines()
        ), f"expected untracked comment in status: {status!r}"


class TestEditCommitFlag:
    """`kaji issue edit --commit` の atomic commit 動線."""

    def test_edit_with_commit_creates_atomic_commit(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """`kaji issue edit --commit` で issue.md 更新が atomic に commit される."""
        provider, issue_id = provider_with_issue
        rc = _local_issue_edit(provider, [issue_id, "--body", "new body", "--commit"])
        assert rc == 0

        status = _git(provider.repo_root, "status", "--porcelain").stdout
        assert status == "", f"unexpected dirty status: {status!r}"

        files = (
            _git(provider.repo_root, "show", "--name-only", "--format=", "HEAD")
            .stdout.strip()
            .splitlines()
        )
        assert any(f.endswith("/issue.md") for f in files), f"issue.md not in HEAD: {files}"

    def test_edit_with_commit_excludes_unrelated_staged_files(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """事前 staged な無関係 file が HEAD に混入しない (Must Fix 1)."""
        provider, issue_id = provider_with_issue
        other = provider.repo_root / "other.txt"
        other.write_text("other\n")
        _git(provider.repo_root, "add", "other.txt")

        rc = _local_issue_edit(provider, [issue_id, "--body", "new body", "--commit"])
        assert rc == 0

        head_files = (
            _git(provider.repo_root, "show", "--name-only", "--format=", "HEAD")
            .stdout.strip()
            .splitlines()
        )
        assert "other.txt" not in head_files

        staged = (
            _git(provider.repo_root, "diff", "--cached", "--name-only").stdout.strip().splitlines()
        )
        assert "other.txt" in staged

    def test_edit_without_commit_leaves_working_tree_dirty(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """後方互換: `--commit` 不指定で挙動が変わらない."""
        provider, issue_id = provider_with_issue
        rc = _local_issue_edit(provider, [issue_id, "--body", "x"])
        assert rc == 0

        status = _git(provider.repo_root, "status", "--porcelain").stdout
        assert any("issue.md" in line for line in status.splitlines()), (
            f"expected issue.md to be dirty: {status!r}"
        )

    def test_edit_with_commit_is_noop_when_body_unchanged(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """no-op edit (body 不変) で `--commit` が `nothing to commit` で fail しない.

        `LocalProvider.edit_issue` は同一 body でも issue.md を再書込するため、
        単純に `git commit --only` を呼ぶと `nothing to commit, working tree clean`
        で exit 1 になる。`/issue-fix-ready` や `/i-dev-final-check` が同一本文を
        再送する動線でこれを踏むため、no-op は成功扱いで commit を skip すること。
        """
        provider, issue_id = provider_with_issue
        # initial body is "b" (set in provider_with_issue fixture)
        head_before = _git(provider.repo_root, "rev-parse", "HEAD").stdout.strip()

        # Re-send the same body via --commit
        rc = _local_issue_edit(provider, [issue_id, "--body", "b", "--commit"])
        assert rc == 0

        # working tree must be clean (no-op was absorbed)
        status = _git(provider.repo_root, "status", "--porcelain").stdout
        assert status == "", f"unexpected dirty status after no-op edit: {status!r}"

        # HEAD must not have moved (no new commit was created)
        head_after = _git(provider.repo_root, "rev-parse", "HEAD").stdout.strip()
        assert head_before == head_after, f"HEAD moved on no-op edit: {head_before} -> {head_after}"

    def test_edit_with_commit_noop_preserves_unrelated_staged_files(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        """no-op edit で skip した場合でも user の他の staged file を破壊しない."""
        provider, issue_id = provider_with_issue
        # Pre-stage an unrelated file
        other = provider.repo_root / "other.txt"
        other.write_text("other\n")
        _git(provider.repo_root, "add", "other.txt")

        rc = _local_issue_edit(provider, [issue_id, "--body", "b", "--commit"])
        assert rc == 0

        # other.txt remains staged in the index untouched
        staged = (
            _git(provider.repo_root, "diff", "--cached", "--name-only").stdout.strip().splitlines()
        )
        assert "other.txt" in staged


class TestCommitMessageFormat:
    """commit message が `chore(local): <action> for <issue_ref>` 形式であること."""

    def test_local_issue_comment_commit_message(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        provider, issue_id = provider_with_issue
        rc = _local_issue_comment(provider, [issue_id, "--body", "x", "--commit"])
        assert rc == 0
        msg = _git(provider.repo_root, "log", "-1", "--format=%s").stdout.strip()
        # local provider なので bare ID（# prefix なし）
        assert msg == f"chore(local): comment for {issue_id}", f"got {msg!r}"

    def test_local_issue_edit_commit_message(
        self, provider_with_issue: tuple[LocalProvider, str]
    ) -> None:
        provider, issue_id = provider_with_issue
        rc = _local_issue_edit(provider, [issue_id, "--body", "x", "--commit"])
        assert rc == 0
        msg = _git(provider.repo_root, "log", "-1", "--format=%s").stdout.strip()
        assert msg == f"chore(local): edit for {issue_id}", f"got {msg!r}"
