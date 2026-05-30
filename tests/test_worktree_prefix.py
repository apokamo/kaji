"""Issue #215: worktree_prefix を config option 化する回帰テスト。

`build_worktree_dir` の `kaji-` prefix ハードコードが原因で、worktree prefix を
別名に設定した consumer の `review-poll` exec_script が `FileNotFoundError` で
クラッシュした（kamo2 Issue #1159 / PR #1162 実ログ）。本テスト群は OB の逆 = EB
（prefix 設定時に算出値が consumer の実 worktree と一致すること）を固定する。

実装前 Red は実世界障害ログで代替し escape clause を適用（design § 実装前 Red）。
本ファイルは恒久回帰テスト（修正後 Green）であり省略しない。

設計対応:
- Small ①②: build_worktree_dir の prefix 設定時 / 無設定時（後方互換）
- Small ③④: [paths].worktree_prefix の config パース（正常系 / 不正値）
- Medium M-1: config → provider field 伝搬（get_provider 結線）
- Medium M-2: provider → build_worktree_dir 反映（resolve_issue_context）
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.errors import ConfigLoadError
from kaji_harness.providers import LocalProvider, get_provider
from kaji_harness.providers.context import build_worktree_dir
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.providers.models import Issue, Label

# --------------------------------------------------------------------------- #
# Small ①②: build_worktree_dir の prefix 分岐
# --------------------------------------------------------------------------- #


@pytest.mark.small
def test_build_worktree_dir_with_prefix(tmp_path: Path) -> None:
    """worktree_prefix 指定時はその prefix を採用する（OB の逆 = EB）。"""
    repo_root = tmp_path / "main"
    result = build_worktree_dir("refactor", "1159", repo_root, "kamo2")
    assert result == str(tmp_path / "kamo2-refactor-1159")


@pytest.mark.small
def test_build_worktree_dir_default_kaji_when_unset_positional(tmp_path: Path) -> None:
    """worktree_prefix 未指定（3 引数）は後方互換で 'kaji' を維持する。"""
    repo_root = tmp_path / "main"
    result = build_worktree_dir("feat", "153", repo_root)
    assert result == str(tmp_path / "kaji-feat-153")


@pytest.mark.small
def test_build_worktree_dir_default_kaji_when_empty(tmp_path: Path) -> None:
    """worktree_prefix が空文字でも 'kaji' にフォールバックする。"""
    repo_root = tmp_path / "main"
    result = build_worktree_dir("feat", "153", repo_root, "")
    assert result == str(tmp_path / "kaji-feat-153")


# --------------------------------------------------------------------------- #
# config 生成ヘルパ
# --------------------------------------------------------------------------- #


def _write_config(
    repo_root: Path,
    *,
    worktree_prefix_line: str = "",
    extra_paths: str = "",
) -> Path:
    """最小構成の .kaji/config.toml を tmp_path 配下に書き、repo_root を返す。

    Args:
        repo_root: ``.kaji/`` を置くディレクトリ。
        worktree_prefix_line: ``[paths]`` 内に追記する行（例 ``worktree_prefix = "kamo2"``）。
        extra_paths: ``[paths]`` 内に追記する任意の追加行。
    """
    kaji_dir = repo_root / ".kaji"
    kaji_dir.mkdir(parents=True, exist_ok=True)
    config = (
        "[paths]\n"
        'artifacts_dir = "artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        f"{worktree_prefix_line}\n"
        f"{extra_paths}\n"
        "\n"
        "[execution]\n"
        "default_timeout = 600\n"
        "\n"
        "[provider]\n"
        'type = "github"\n'
        "\n"
        "[provider.github]\n"
        'repo = "owner/name"\n'
    )
    (kaji_dir / "config.toml").write_text(config, encoding="utf-8")
    return repo_root


# --------------------------------------------------------------------------- #
# Small ③: config パース（正常系）
# --------------------------------------------------------------------------- #


@pytest.mark.small
def test_config_parse_worktree_prefix(tmp_path: Path) -> None:
    """[paths].worktree_prefix が PathsConfig に取り込まれる。"""
    repo_root = _write_config(tmp_path / "main", worktree_prefix_line='worktree_prefix = "kamo2"')
    config = KajiConfig.discover(repo_root)
    assert config.paths.worktree_prefix == "kamo2"


@pytest.mark.small
def test_config_parse_worktree_prefix_absent_defaults_empty(tmp_path: Path) -> None:
    """worktree_prefix 未記載なら空文字（デフォルト）になる。"""
    repo_root = _write_config(tmp_path / "main")
    config = KajiConfig.discover(repo_root)
    assert config.paths.worktree_prefix == ""


# --------------------------------------------------------------------------- #
# Small ④: config パース（異常系 = ConfigLoadError）
# --------------------------------------------------------------------------- #


@pytest.mark.small
def test_config_worktree_prefix_non_string(tmp_path: Path) -> None:
    """非 str（整数）は ConfigLoadError。"""
    repo_root = _write_config(tmp_path / "main", worktree_prefix_line="worktree_prefix = 123")
    with pytest.raises(ConfigLoadError, match="paths.worktree_prefix"):
        KajiConfig.discover(repo_root)


@pytest.mark.small
@pytest.mark.parametrize("bad", ["a/b", "..", "a b"])
def test_config_worktree_prefix_invalid_segment(tmp_path: Path, bad: str) -> None:
    """separator / traversal / 空白を含む値は ConfigLoadError。"""
    repo_root = _write_config(tmp_path / "main", worktree_prefix_line=f'worktree_prefix = "{bad}"')
    with pytest.raises(ConfigLoadError, match="paths.worktree_prefix"):
        KajiConfig.discover(repo_root)


# --------------------------------------------------------------------------- #
# Medium M-1: config → provider field 伝搬
# --------------------------------------------------------------------------- #


@pytest.mark.medium
def test_get_provider_propagates_worktree_prefix_github(tmp_path: Path) -> None:
    """get_provider が config.paths.worktree_prefix を GitHubProvider へ注入する。"""
    repo_root = _write_config(tmp_path / "main", worktree_prefix_line='worktree_prefix = "kamo2"')
    config = KajiConfig.discover(repo_root)
    provider = get_provider(config)
    assert isinstance(provider, GitHubProvider)
    assert provider.worktree_prefix == "kamo2"


@pytest.mark.medium
def test_get_provider_propagates_worktree_prefix_local(tmp_path: Path) -> None:
    """get_provider が config.paths.worktree_prefix を LocalProvider へ注入する。

    gl:21 / git_remote 伝搬テストと同様、local 経路は ``resolve_main_worktree()``
    を踏むため tmp_path を本物の git repo として初期化する（``subprocess.run`` の
    名前空間 patch は dispatch/provider 結合では禁止 — testing-convention § patch スコープ）。
    """
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(tmp_path)],
        check=True,
    )
    config = KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(
            artifacts_dir=".kaji-artifacts",
            skill_dir=".claude/skills",
            worktree_prefix="kamo2",
        ),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="local",
            local=LocalProviderConfig(machine_id="pc1"),
            github=GitHubProviderConfig(),
        ),
    )
    provider = get_provider(config)
    assert isinstance(provider, LocalProvider)
    assert provider.worktree_prefix == "kamo2"


# --------------------------------------------------------------------------- #
# Medium M-2: provider → build_worktree_dir 反映（GitHub）
# --------------------------------------------------------------------------- #


@pytest.mark.medium
def test_github_resolve_issue_context_uses_worktree_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resolve_issue_context の worktree_dir が worktree_prefix を反映する。

    外部 API 非疎通: view_issue を最小 mock し、build_worktree_dir 経路のみ通す。
    """
    repo_root = tmp_path / "main"
    provider = GitHubProvider(
        repo="owner/name",
        repo_root=repo_root,
        worktree_prefix="kamo2",
    )

    issue = Issue(
        id="1159",
        title="Some bug",
        body="",
        state="open",
        labels=[Label(name="type:bug")],
        comments=[],
        slug="some-bug",
    )
    monkeypatch.setattr(provider, "view_issue", lambda _issue_id: issue)

    ctx = provider.resolve_issue_context("1159")
    # branch_prefix の値域に依存せず、worktree_prefix が先頭 segment に反映される
    # ことを検証する（build_worktree_dir と同一規約で期待値を組み立てる）。
    assert ctx.worktree_dir == str(tmp_path / f"kamo2-{ctx.branch_prefix}-1159")
    assert ctx.worktree_dir.startswith(str(tmp_path / "kamo2-"))


@pytest.mark.medium
def test_local_resolve_issue_context_uses_worktree_prefix(tmp_path: Path) -> None:
    """LocalProvider.resolve_issue_context の worktree_dir が worktree_prefix を反映する。

    ファイル I/O のみ（外部 API / subprocess 非疎通）。ディスク上に issue.md を配置し、
    build_worktree_dir(..., worktree_prefix=self.worktree_prefix) 経路を通す。
    """
    issues_dir = tmp_path / ".kaji" / "issues"
    issues_dir.mkdir(parents=True)
    issue_dir = issues_dir / "local-pc1-1-some-bug"
    issue_dir.mkdir()
    (issue_dir / "issue.md").write_text(
        dedent(
            """
            ---
            id: local-pc1-1
            title: Some bug
            state: open
            slug: some-bug
            branch_prefix: feat
            labels: []
            ---
            body
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    provider = LocalProvider(
        repo_root=tmp_path,
        machine_id="pc1",
        worktree_prefix="kamo2",
    )

    ctx = provider.resolve_issue_context("local-pc1-1")
    # worktree_dir は repo_root.parent 基準（build_worktree_dir と同一規約）。
    assert ctx.worktree_dir == str(tmp_path.parent / "kamo2-feat-local-pc1-1")
    assert ctx.provider_type == "local"
