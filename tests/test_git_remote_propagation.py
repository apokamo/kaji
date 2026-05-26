"""gl:6: ``git_remote`` の config -> IssueContext -> prompt 伝播経路の保護テスト。

`tests/test_skill_remote_placeholder.py` は SKILL markdown 側の placeholder
出現のみを assert しているが、`provider.<type>.git_remote` config 値が
``IssueContext.git_remote`` を経由して prompt の ``[git_remote]`` に届く本体
経路は CI で固定されていなかった。本ファイルでその空白を埋める:

- Small: ``IssueContext.git_remote`` field default / explicit、2 ProviderConfig
  の TOML default、2 Provider class の field default / explicit。
- Medium: TOML から ``provider.<type>.git_remote`` を parse、``get_provider``
  が ``git_remote`` を Provider に渡す、``resolve_issue_context`` が
  ``IssueContext.git_remote`` を埋める、``build_prompt`` が ``[git_remote]``
  variables に注入する。

参照: `draft/design/issue-6-skill-i-pr-issue-close-git-remote-origin.md`
§ テスト戦略, レビュー指摘 (note_3334889228 Must Fix #1)。
"""

from __future__ import annotations

import subprocess
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
from kaji_harness.models import Step, Workflow
from kaji_harness.prompt import build_prompt
from kaji_harness.providers import (
    GitHubProvider,
    LocalProvider,
    get_provider,
)
from kaji_harness.providers.models import IssueContext
from kaji_harness.state import SessionState

# --------------------------------------------------------------------------
# Small: dataclass / field defaults
# --------------------------------------------------------------------------


@pytest.mark.small
def test_issue_context_git_remote_default_value() -> None:
    """``IssueContext.git_remote`` が未指定なら ``"origin"`` になる。"""
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
    assert ctx.git_remote == "origin"


@pytest.mark.small
def test_issue_context_git_remote_explicit() -> None:
    """``IssueContext.git_remote`` に値を渡すとそのまま保持される。"""
    ctx = IssueContext(
        issue_id="42",
        issue_ref="#42",
        issue_input="42",
        slug="x",
        branch_prefix="feat",
        branch_name="feat/42",
        worktree_dir="/tmp/wt",
        design_path="draft/design/issue-42-x.md",
        provider_type="github",
        git_remote="upstream",
    )
    assert ctx.git_remote == "upstream"


@pytest.mark.small
def test_github_provider_config_git_remote_default() -> None:
    cfg = GitHubProviderConfig()
    assert cfg.git_remote == "origin"


@pytest.mark.small
def test_github_provider_config_git_remote_explicit() -> None:
    cfg = GitHubProviderConfig(repo="owner/name", git_remote="upstream")
    assert cfg.git_remote == "upstream"


@pytest.mark.small
def test_local_provider_config_git_remote_default() -> None:
    cfg = LocalProviderConfig()
    assert cfg.git_remote == "origin"


@pytest.mark.small
def test_local_provider_config_git_remote_explicit() -> None:
    cfg = LocalProviderConfig(machine_id="pc1", git_remote="backup")
    assert cfg.git_remote == "backup"


@pytest.mark.small
def test_github_provider_git_remote_default(tmp_path: Path) -> None:
    p = GitHubProvider(repo="owner/name", repo_root=tmp_path)
    assert p.git_remote == "origin"


@pytest.mark.small
def test_github_provider_git_remote_explicit(tmp_path: Path) -> None:
    p = GitHubProvider(repo="owner/name", repo_root=tmp_path, git_remote="upstream")
    assert p.git_remote == "upstream"


@pytest.mark.small
def test_local_provider_git_remote_default(tmp_path: Path) -> None:
    p = LocalProvider(repo_root=tmp_path, machine_id="pc1")
    assert p.git_remote == "origin"


@pytest.mark.small
def test_local_provider_git_remote_explicit(tmp_path: Path) -> None:
    p = LocalProvider(repo_root=tmp_path, machine_id="pc1", git_remote="backup")
    assert p.git_remote == "backup"


# --------------------------------------------------------------------------
# Medium: TOML parse
# --------------------------------------------------------------------------


def _write_config(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".kaji").mkdir()
    config_path = tmp_path / ".kaji" / "config.toml"
    config_path.write_text(dedent(body).strip() + "\n", encoding="utf-8")
    return config_path


@pytest.mark.medium
def test_config_parses_provider_github_git_remote(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
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
        git_remote = "upstream"
        """,
    )
    cfg = KajiConfig.discover(start_dir=tmp_path)
    assert cfg.provider is not None
    assert cfg.provider.github.git_remote == "upstream"


@pytest.mark.medium
def test_config_parses_provider_local_git_remote(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        [paths]
        artifacts_dir = ".kaji-artifacts"
        skill_dir = ".claude/skills"

        [execution]
        default_timeout = 1800

        [provider]
        type = "local"

        [provider.local]
        machine_id = "pc1"
        git_remote = "backup"
        """,
    )
    cfg = KajiConfig.discover(start_dir=tmp_path)
    assert cfg.provider is not None
    assert cfg.provider.local.git_remote == "backup"


@pytest.mark.medium
def test_config_provider_git_remote_default_when_omitted(tmp_path: Path) -> None:
    """``git_remote`` を TOML で省略すると ``origin`` がデフォルト適用される。"""
    _write_config(
        tmp_path,
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
        """,
    )
    cfg = KajiConfig.discover(start_dir=tmp_path)
    assert cfg.provider is not None
    assert cfg.provider.github.git_remote == "origin"
    assert cfg.provider.local.git_remote == "origin"


@pytest.mark.medium
def test_config_rejects_non_string_git_remote(tmp_path: Path) -> None:
    """``provider.github.git_remote`` が non-string なら ConfigLoadError。"""
    from kaji_harness.config import ConfigLoadError

    _write_config(
        tmp_path,
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
        git_remote = 42
        """,
    )
    with pytest.raises(ConfigLoadError, match="git_remote must be a string"):
        KajiConfig.discover(start_dir=tmp_path)


# --------------------------------------------------------------------------
# Medium: get_provider() flows git_remote into Provider instance
# --------------------------------------------------------------------------


def _base_paths(tmp_path: Path) -> tuple[PathsConfig, ExecutionConfig]:
    return (
        PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        ExecutionConfig(default_timeout=1800),
    )


@pytest.mark.medium
def test_get_provider_flows_git_remote_to_github_provider(tmp_path: Path) -> None:
    paths, exec_cfg = _base_paths(tmp_path)
    cfg = KajiConfig(
        repo_root=tmp_path,
        paths=paths,
        execution=exec_cfg,
        provider=ProviderConfig(
            type="github",
            local=LocalProviderConfig(),
            github=GitHubProviderConfig(repo="owner/name", git_remote="upstream"),
        ),
    )
    provider = get_provider(cfg)
    assert isinstance(provider, GitHubProvider)
    assert provider.git_remote == "upstream"


@pytest.mark.medium
def test_get_provider_flows_git_remote_to_local_provider(tmp_path: Path) -> None:
    # gl:21: ``get_provider()`` の local 経路は ``resolve_main_worktree()`` を踏むため、
    # tmp_path を本物の git repo として初期化しておく。
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(tmp_path)],
        check=True,
    )
    paths, exec_cfg = _base_paths(tmp_path)
    cfg = KajiConfig(
        repo_root=tmp_path,
        paths=paths,
        execution=exec_cfg,
        provider=ProviderConfig(
            type="local",
            local=LocalProviderConfig(machine_id="pc1", git_remote="backup"),
            github=GitHubProviderConfig(),
        ),
    )
    provider = get_provider(cfg)
    assert isinstance(provider, LocalProvider)
    assert provider.git_remote == "backup"


# --------------------------------------------------------------------------
# Medium: resolve_issue_context() flows git_remote into IssueContext
# --------------------------------------------------------------------------


@pytest.mark.medium
def test_github_provider_resolve_issue_context_flows_git_remote(tmp_path: Path) -> None:
    """GitHubProvider の `IssueContext` に `git_remote` が流れる。"""
    provider = GitHubProvider(repo="owner/name", repo_root=tmp_path, git_remote="upstream")
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
    assert ctx.git_remote == "upstream"
    assert ctx.provider_type == "github"


@pytest.mark.medium
def test_local_provider_resolve_issue_context_flows_git_remote(tmp_path: Path) -> None:
    """LocalProvider の `IssueContext` に `git_remote` が流れる。"""
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
    provider = LocalProvider(repo_root=tmp_path, machine_id="pc1", git_remote="backup")
    ctx = provider.resolve_issue_context("local-pc1-1")
    assert ctx.git_remote == "backup"
    assert ctx.provider_type == "local"


# --------------------------------------------------------------------------
# Medium: build_prompt() injects git_remote
# --------------------------------------------------------------------------


def _step() -> Step:
    return Step(id="implement", skill="x", agent="claude", on={"PASS": "end"})


def _workflow() -> Workflow:
    return Workflow(
        name="w",
        description="d",
        execution_policy="sequential",
        steps=[_step()],
        cycles=[],
    )


def _make_state() -> SessionState:
    with patch.object(SessionState, "_persist"):
        return SessionState(
            issue_number="42",
            artifacts_dir=Path("/tmp/fake"),
            sessions={},
            step_history=[],
            cycle_counts={},
            last_completed_step=None,
            last_transition_verdict=None,
        )


@pytest.mark.medium
def test_build_prompt_injects_git_remote_default() -> None:
    """``IssueContext.git_remote`` が default ``"origin"`` のとき prompt にも ``origin`` が入る。"""
    ctx = IssueContext(
        issue_id="153",
        issue_ref="#153",
        issue_input="153",
        slug="x",
        branch_prefix="feat",
        branch_name="feat/153",
        worktree_dir="/p/kaji-feat-153",
        design_path="draft/design/issue-153-x.md",
        provider_type="github",
    )
    prompt = build_prompt(
        _step(),
        issue="153",
        state=_make_state(),
        workflow=_workflow(),
        issue_context=ctx,
    )
    assert "- git_remote: origin" in prompt


@pytest.mark.medium
def test_build_prompt_injects_git_remote_explicit() -> None:
    """`IssueContext.git_remote` の explicit 値が prompt 行に反映される。"""
    ctx = IssueContext(
        issue_id="42",
        issue_ref="#42",
        issue_input="42",
        slug="x",
        branch_prefix="feat",
        branch_name="feat/42",
        worktree_dir="/p/kaji-feat-42",
        design_path="draft/design/issue-42-x.md",
        provider_type="github",
        git_remote="upstream",
    )
    prompt = build_prompt(
        _step(),
        issue="42",
        state=_make_state(),
        workflow=_workflow(),
        issue_context=ctx,
    )
    assert "- git_remote: upstream" in prompt
