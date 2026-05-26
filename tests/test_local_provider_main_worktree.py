"""Integration tests for LocalProvider main-worktree redirection (Issue gl:11).

feature worktree を Python cwd にした状態で ``kaji issue`` 系の操作を行っても、
ファイル書き込みと commit が main worktree に向くことを検証する。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from kaji_harness.cli_main import (
    _local_issue_close,
    _local_issue_comment,
    _local_issue_create,
    _local_issue_edit,
)
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.providers import get_provider
from kaji_harness.providers.local import LocalProvider, LocalProviderError

pytestmark = pytest.mark.medium


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def main_and_feature_worktrees(tmp_path: Path) -> tuple[Path, Path, Path]:
    """bare repo + main worktree + feature worktree を組み、main 側に .kaji を seed する。"""
    bare = tmp_path / "repo.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
        check=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "commit.gpgsign", "false")
    (seed / "README.md").write_text("r\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", "origin", "main")

    main_wt = tmp_path / "main"
    feat_wt = tmp_path / "feat"
    _git(bare, "worktree", "add", "-q", str(main_wt), "main")
    _git(bare, "worktree", "add", "-q", "-b", "fix/x", str(feat_wt), "main")
    # ensure local user.* on the worktrees too (worktree inherits config but be explicit)
    for wt in (main_wt, feat_wt):
        _git(wt, "config", "user.email", "t@t")
        _git(wt, "config", "user.name", "t")
        _git(wt, "config", "commit.gpgsign", "false")

    return bare, main_wt, feat_wt


@pytest.fixture()
def chdir_feature(
    main_and_feature_worktrees: tuple[Path, Path, Path],
) -> Iterator[tuple[Path, Path]]:
    """Python の cwd を feature worktree に切り替える。"""
    _, main_wt, feat_wt = main_and_feature_worktrees
    orig = Path.cwd()
    os.chdir(feat_wt)
    try:
        yield main_wt, feat_wt
    finally:
        os.chdir(orig)


def _seed_issue_on_main(main_wt: Path) -> str:
    """main worktree 上に LocalProvider 経由で issue を 1 件作成して commit する。"""
    provider = LocalProvider(repo_root=main_wt, machine_id="pc1")
    issue = provider.create_issue(title="t", body="b", slug="x", labels=["type:bug"])
    _git(main_wt, "add", ".kaji")
    _git(main_wt, "commit", "-q", "-m", "seed issue")
    return issue.id


def _provider_for_main(main_wt: Path) -> LocalProvider:
    return LocalProvider(repo_root=main_wt, machine_id="pc1")


class TestCommentRedirection:
    """gl:11 の再現テスト: feature cwd でも main に commit される."""

    def test_comment_commit_lands_on_main_not_feature(
        self, chdir_feature: tuple[Path, Path]
    ) -> None:
        main_wt, feat_wt = chdir_feature
        issue_id = _seed_issue_on_main(main_wt)
        provider = _provider_for_main(main_wt)

        main_head_before = _git(main_wt, "rev-parse", "HEAD").stdout.strip()
        feat_head_before = _git(feat_wt, "rev-parse", "HEAD").stdout.strip()

        rc = _local_issue_comment(provider, [issue_id, "--body", "hi", "--commit"])
        assert rc == 0

        main_head_after = _git(main_wt, "rev-parse", "HEAD").stdout.strip()
        feat_head_after = _git(feat_wt, "rev-parse", "HEAD").stdout.strip()

        # main HEAD advanced
        assert main_head_after != main_head_before
        # feature HEAD unchanged
        assert feat_head_after == feat_head_before

        # comment file exists in main worktree, not in feature
        main_comments = list((main_wt / ".kaji" / "issues").rglob("comments/*.md"))
        feat_comments = list((feat_wt / ".kaji" / "issues").rglob("comments/*.md"))
        assert len(main_comments) == 1
        assert feat_comments == []

    def test_view_and_list_consistent_from_feature_cwd(
        self, chdir_feature: tuple[Path, Path]
    ) -> None:
        """feature cwd でも view/list が main 側のファイルを参照する."""
        main_wt, _feat_wt = chdir_feature
        issue_id = _seed_issue_on_main(main_wt)
        provider = _provider_for_main(main_wt)

        # add a comment via provider (not CLI commit) so file lands under main
        provider.comment_issue(issue_id, "from main")

        # read-side via LocalProvider bound to main
        issue = provider.view_issue(issue_id)
        assert issue.id == issue_id
        assert any(c.body == "from main" for c in issue.comments)

        listed = provider.list_issues(state="open")
        assert any(i.id == issue_id for i in listed)


class TestCreateEditCloseRedirection:
    """同根挙動: create / edit / close も main worktree に書き込まれる."""

    def test_create_writes_to_main_worktree(self, chdir_feature: tuple[Path, Path]) -> None:
        main_wt, feat_wt = chdir_feature
        provider = _provider_for_main(main_wt)
        rc = _local_issue_create(provider, ["--title", "t2", "--body", "b2", "--slug", "s2"])
        assert rc == 0
        # New issue dir exists under main, not feature
        main_dirs = [p for p in (main_wt / ".kaji" / "issues").iterdir() if p.is_dir()]
        assert any("s2" in p.name for p in main_dirs)
        feat_issues = feat_wt / ".kaji" / "issues"
        if feat_issues.exists():
            assert not any("s2" in p.name for p in feat_issues.iterdir())

    def test_edit_with_commit_lands_on_main(self, chdir_feature: tuple[Path, Path]) -> None:
        main_wt, feat_wt = chdir_feature
        issue_id = _seed_issue_on_main(main_wt)
        provider = _provider_for_main(main_wt)

        feat_head_before = _git(feat_wt, "rev-parse", "HEAD").stdout.strip()
        rc = _local_issue_edit(provider, [issue_id, "--title", "new-title", "--commit"])
        assert rc == 0
        feat_head_after = _git(feat_wt, "rev-parse", "HEAD").stdout.strip()
        # feature HEAD unchanged
        assert feat_head_after == feat_head_before
        # latest main commit is the edit
        log = _git(main_wt, "log", "-1", "--format=%s").stdout.strip()
        assert "edit" in log or "chore(local)" in log

    def test_close_writes_to_main(self, chdir_feature: tuple[Path, Path]) -> None:
        main_wt, _feat_wt = chdir_feature
        issue_id = _seed_issue_on_main(main_wt)
        provider = _provider_for_main(main_wt)
        rc = _local_issue_close(provider, [issue_id])
        assert rc == 0
        # state=closed in main worktree's issue.md
        issue_md = next((main_wt / ".kaji" / "issues").rglob("issue.md"))
        body = issue_md.read_text(encoding="utf-8")
        assert "state: closed" in body


class TestGetProviderResolution:
    """``get_provider()`` が LocalProvider に main worktree を渡すこと."""

    def _make_local_config(self, repo_root: Path, default_branch: str) -> KajiConfig:
        return KajiConfig(
            repo_root=repo_root,
            paths=PathsConfig(),
            execution=ExecutionConfig(default_timeout=300),
            provider=ProviderConfig(
                type="local",
                local=LocalProviderConfig(machine_id="pc1", default_branch=default_branch),
                github=GitHubProviderConfig(),
            ),
        )

    def test_get_provider_uses_main_worktree(self, chdir_feature: tuple[Path, Path]) -> None:
        main_wt, feat_wt = chdir_feature
        config = self._make_local_config(feat_wt, "main")
        provider = get_provider(config)
        assert isinstance(provider, LocalProvider)
        assert provider.repo_root == main_wt.resolve()

    def test_get_provider_raises_when_default_branch_worktree_missing(
        self, chdir_feature: tuple[Path, Path]
    ) -> None:
        _main_wt, feat_wt = chdir_feature
        config = self._make_local_config(feat_wt, "release")
        with pytest.raises(LocalProviderError, match="no worktree found for branch 'release'"):
            get_provider(config)

    def test_get_provider_custom_default_branch(self, chdir_feature: tuple[Path, Path]) -> None:
        _main_wt, feat_wt = chdir_feature
        config = self._make_local_config(feat_wt, "fix/x")
        provider = get_provider(config)
        assert isinstance(provider, LocalProvider)
        assert provider.repo_root == feat_wt.resolve()

    def test_get_provider_e2e_redirects_commit_to_main(
        self, chdir_feature: tuple[Path, Path]
    ) -> None:
        """e2e: feature cwd で get_provider() → CLI comment → commit が main に着地する。

        ``TestCommentRedirection.test_comment_commit_lands_on_main_not_feature`` は
        ``LocalProvider(repo_root=main_wt)`` を直接組み立てて検証している。本ケースは
        ``get_provider(config)`` 経由の解決まで含めた一連の経路を 1 本で押さえる。
        """
        main_wt, feat_wt = chdir_feature
        issue_id = _seed_issue_on_main(main_wt)

        config = self._make_local_config(feat_wt, "main")
        provider = get_provider(config)
        assert isinstance(provider, LocalProvider)
        assert provider.repo_root == main_wt.resolve()

        main_head_before = _git(main_wt, "rev-parse", "HEAD").stdout.strip()
        feat_head_before = _git(feat_wt, "rev-parse", "HEAD").stdout.strip()

        rc = _local_issue_comment(provider, [issue_id, "--body", "e2e", "--commit"])
        assert rc == 0

        main_head_after = _git(main_wt, "rev-parse", "HEAD").stdout.strip()
        feat_head_after = _git(feat_wt, "rev-parse", "HEAD").stdout.strip()

        assert main_head_after != main_head_before
        assert feat_head_after == feat_head_before

        main_comments = list((main_wt / ".kaji" / "issues").rglob("comments/*.md"))
        feat_comments = list((feat_wt / ".kaji" / "issues").rglob("comments/*.md"))
        assert len(main_comments) == 1
        assert feat_comments == []


class TestForgeProviderRegression:
    """GitHub provider の repo_root は cwd 起点のまま（変更なし）."""

    def test_github_provider_repo_root_unchanged(self, tmp_path: Path) -> None:
        from kaji_harness.providers.github import GitHubProvider

        config = KajiConfig(
            repo_root=tmp_path,
            paths=PathsConfig(),
            execution=ExecutionConfig(default_timeout=300),
            provider=ProviderConfig(
                type="github",
                local=LocalProviderConfig(),
                github=GitHubProviderConfig(repo="o/r"),
            ),
        )
        provider = get_provider(config)
        assert isinstance(provider, GitHubProvider)
        assert provider.repo_root == tmp_path
