"""Tests for the provider-neutral ``Comment.ref`` field (Issue #288).

GitHub provider は ``gh issue comment`` stdout の作成コメント URL を ``ref`` に格納する。consumer は形式に依存せず、
``ref == ""`` を ``n/a`` として扱う不透明文字列として読む。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.providers.github import GitHubProvider
from kaji_harness.providers.models import Comment


@pytest.mark.small
def test_comment_ref_defaults_to_empty_string() -> None:
    comment = Comment(author="a", body="b", created_at="t")
    assert comment.ref == ""


@pytest.mark.small
def test_comment_field_order_is_backward_compatible() -> None:
    # 既存の位置引数呼び出し（author, body, created_at）を壊さない。
    comment = Comment("a", "b", "t")
    assert comment.author == "a"
    assert comment.body == "b"
    assert comment.created_at == "t"
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
