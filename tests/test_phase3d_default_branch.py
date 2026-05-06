"""Phase 3-d: ``default_branch`` placeholder の供給を検証する。

- Small: ``IssueContext.default_branch`` field の default 値、
  ``GitHubProviderConfig.default_branch`` の TOML parse、
  ``LocalProvider.default_branch`` のフィールド伝搬。
- Medium: provider 経由で `IssueContext` を解決した結果に
  ``default_branch`` が provider 別に正しく流れていることを確認。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.providers import GitHubProvider, LocalProvider, get_provider
from kaji_harness.providers.models import IssueContext


@pytest.mark.small
def test_issue_context_default_branch_default_value() -> None:
    ctx = IssueContext(
        issue_id="local-pc1-1",
        issue_ref="local-pc1-1",
        issue_input="local-pc1-1",
        slug="x",
        branch_prefix="feat",
        branch_name="feat/local-pc1-1",
        worktree_dir="/tmp/wt",
        design_path="draft/design/local-pc1-1-x.md",
        provider_type="local",
    )
    assert ctx.default_branch == "main"


@pytest.mark.small
def test_issue_context_default_branch_explicit() -> None:
    ctx = IssueContext(
        issue_id="local-pc1-1",
        issue_ref="local-pc1-1",
        issue_input="local-pc1-1",
        slug="x",
        branch_prefix="feat",
        branch_name="feat/local-pc1-1",
        worktree_dir="/tmp/wt",
        design_path="draft/design/local-pc1-1-x.md",
        provider_type="local",
        default_branch="develop",
    )
    assert ctx.default_branch == "develop"


@pytest.mark.small
def test_github_provider_config_default_branch_default() -> None:
    cfg = GitHubProviderConfig()
    assert cfg.default_branch == "main"


@pytest.mark.small
def test_github_provider_config_default_branch_explicit() -> None:
    cfg = GitHubProviderConfig(repo="owner/name", default_branch="trunk")
    assert cfg.default_branch == "trunk"


@pytest.mark.small
def test_local_provider_default_branch_default(tmp_path: Path) -> None:
    p = LocalProvider(repo_root=tmp_path, machine_id="pc1")
    assert p.default_branch == "main"


@pytest.mark.small
def test_local_provider_default_branch_explicit(tmp_path: Path) -> None:
    p = LocalProvider(repo_root=tmp_path, machine_id="pc1", default_branch="develop")
    assert p.default_branch == "develop"


@pytest.mark.medium
def test_config_parses_provider_github_default_branch(tmp_path: Path) -> None:
    """``provider.github.default_branch`` を TOML から read できる。"""
    (tmp_path / ".kaji").mkdir()
    config_path = tmp_path / ".kaji" / "config.toml"
    config_path.write_text(
        dedent(
            """
            [paths]
            artifacts_dir = ".kaji-artifacts"
            skill_dir = ".claude/skills"

            [execution]
            default_timeout = 1800

            [provider]
            type = "github"

            [provider.github]
            repo = "owner/name"
            default_branch = "develop"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    cfg = KajiConfig.discover(start_dir=tmp_path)
    assert cfg.provider is not None
    assert cfg.provider.github.default_branch == "develop"


@pytest.mark.medium
def test_config_provider_github_default_branch_default(tmp_path: Path) -> None:
    """``provider.github.default_branch`` 未設定時は ``main``。"""
    (tmp_path / ".kaji").mkdir()
    config_path = tmp_path / ".kaji" / "config.toml"
    config_path.write_text(
        dedent(
            """
            [paths]
            artifacts_dir = ".kaji-artifacts"
            skill_dir = ".claude/skills"

            [execution]
            default_timeout = 1800

            [provider]
            type = "github"

            [provider.github]
            repo = "owner/name"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    cfg = KajiConfig.discover(start_dir=tmp_path)
    assert cfg.provider is not None
    assert cfg.provider.github.default_branch == "main"


@pytest.mark.medium
def test_get_provider_flows_default_branch_to_github_provider(tmp_path: Path) -> None:
    cfg = KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="github",
            local=LocalProviderConfig(),
            github=GitHubProviderConfig(repo="owner/name", default_branch="trunk"),
        ),
    )
    provider = get_provider(cfg)
    assert isinstance(provider, GitHubProvider)
    assert provider.default_branch == "trunk"


@pytest.mark.medium
def test_get_provider_flows_default_branch_to_local_provider(tmp_path: Path) -> None:
    cfg = KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="local",
            local=LocalProviderConfig(machine_id="pc1", default_branch="develop"),
            github=GitHubProviderConfig(),
        ),
    )
    provider = get_provider(cfg)
    assert isinstance(provider, LocalProvider)
    assert provider.default_branch == "develop"


@pytest.mark.medium
def test_local_provider_resolve_issue_context_flows_default_branch(tmp_path: Path) -> None:
    """LocalProvider の `IssueContext` に `default_branch` が流れる。"""
    issues_dir = tmp_path / ".kaji" / "issues"
    issues_dir.mkdir(parents=True)
    issue_dir = issues_dir / "local-pc1-1-foo"
    issue_dir.mkdir()
    (issue_dir / "issue.md").write_text(
        dedent(
            """
            ---
            id: local-pc1-1
            title: foo
            state: open
            slug: foo
            branch_prefix: feat
            labels: []
            ---
            body
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    provider = LocalProvider(repo_root=tmp_path, machine_id="pc1", default_branch="develop")
    ctx = provider.resolve_issue_context("local-pc1-1")
    assert ctx.default_branch == "develop"
    assert ctx.provider_type == "local"


@pytest.mark.medium
def test_github_provider_resolve_issue_context_flows_default_branch(tmp_path: Path) -> None:
    """GitHubProvider の `IssueContext` に `default_branch` が流れる。"""
    provider = GitHubProvider(repo="owner/name", repo_root=tmp_path, default_branch="trunk")

    payload: dict[str, Any] = {
        "number": 153,
        "title": "Foo bar",
        "body": "...",
        "state": "OPEN",
        "labels": [{"name": "type:feature"}],
        "comments": [],
    }

    def fake_view_issue(self_: GitHubProvider, _id: str) -> Any:  # noqa: ANN401
        return GitHubProvider._parse_issue_payload(payload)

    with patch.object(GitHubProvider, "view_issue", fake_view_issue):
        ctx = provider.resolve_issue_context("153")
    assert ctx.default_branch == "trunk"
    assert ctx.provider_type == "github"
