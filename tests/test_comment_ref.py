"""Tests for the provider-neutral ``Comment.ref`` field (Issue #288).

GitHub provider は ``gh issue comment`` stdout の作成コメント URL を、local provider は
作成 comment file の repo-root 相対パスを ``ref`` に格納する。consumer は形式に依存せず、
``ref == ""`` を ``n/a`` として扱う不透明文字列として読む。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.providers.github import GitHubProvider
from kaji_harness.providers.local import LocalProvider
from kaji_harness.providers.models import Comment


@pytest.mark.small
def test_comment_ref_defaults_to_empty_string() -> None:
    comment = Comment(author="a", body="b", created_at="t")
    assert comment.ref == ""


@pytest.mark.small
def test_comment_field_order_is_backward_compatible() -> None:
    # 既存の位置引数呼び出し（author, body, created_at, seq, machine_id）を壊さない。
    comment = Comment("a", "b", "t", "20260710T000000Z", "pc1")
    assert comment.seq == "20260710T000000Z"
    assert comment.machine_id == "pc1"
    assert comment.ref == ""


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=stdout, stderr="")


@pytest.mark.medium
class TestGitHubCommentRef:
    def _provider(self, tmp_path: Path) -> GitHubProvider:
        return GitHubProvider(repo="owner/name", repo_root=tmp_path)

    def test_ref_captures_comment_url_from_stdout(self, tmp_path: Path) -> None:
        url = "https://github.com/owner/name/issues/288#issuecomment-1"
        with patch.object(GitHubProvider, "_run_gh", return_value=_completed(url + "\n")):
            comment = self._provider(tmp_path).comment_issue("288", "body")
        assert comment.ref == url

    def test_ref_uses_first_line_only(self, tmp_path: Path) -> None:
        url = "https://github.com/owner/name/issues/288#issuecomment-1"
        with patch.object(GitHubProvider, "_run_gh", return_value=_completed(f"{url}\nnoise\n")):
            comment = self._provider(tmp_path).comment_issue("288", "body")
        assert comment.ref == url

    def test_ref_is_empty_when_stdout_is_blank(self, tmp_path: Path) -> None:
        with patch.object(GitHubProvider, "_run_gh", return_value=_completed("")):
            comment = self._provider(tmp_path).comment_issue("288", "body")
        assert comment.ref == ""

    def test_ref_is_empty_when_stdout_is_not_a_url(self, tmp_path: Path) -> None:
        with patch.object(GitHubProvider, "_run_gh", return_value=_completed("Comment created\n")):
            comment = self._provider(tmp_path).comment_issue("288", "body")
        assert comment.ref == ""


@pytest.mark.medium
class TestLocalCommentRef:
    def test_ref_is_repo_relative_comment_path(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
        (tmp_path / ".kaji" / "counters").mkdir(parents=True)
        (tmp_path / ".kaji" / "counters" / "pc1.txt").write_text("0")
        provider = LocalProvider(repo_root=tmp_path, machine_id="pc1")
        issue = provider.create_issue(title="t", body="b", labels=["type:feature"], slug="t")

        comment = provider.comment_issue(issue.id, "hello")

        assert comment.ref.startswith(".kaji/issues/")
        assert comment.ref.endswith("-pc1.md")
        assert (tmp_path / comment.ref).is_file()
