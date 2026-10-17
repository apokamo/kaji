"""Shared test helpers across kaji_harness test suite.

runner / CLI テスト群が共有する fixture と helper。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest

from kaji_harness.console_log import ROOT_LOGGER_NAME
from kaji_harness.providers import Issue, IssueContext, Label
from kaji_harness.providers.github import _MIN_GH_VERSION, GitHubProvider


class KajiRootLoggingState(NamedTuple):
    """``kaji`` root logger の復元に必要な global state スナップショット。

    Issue #250: ``configure_console_logging()`` は ``kaji`` root logger に
    ``setLevel`` / ``propagate=False`` を設定するため、復元しないと同一 pytest
    worker 内の後続 ``caplog`` 依存テストへ漏れる。``handlers`` / ``level`` /
    ``propagate`` の 3 要素を保存復元対象とする（``tests/test_console_log.py``
    の ``_clean_root`` 契約と同一）。
    """

    handlers: list[logging.Handler]
    level: int
    propagate: bool


def snapshot_kaji_root_logging() -> KajiRootLoggingState:
    """``kaji`` root logger の handlers/level/propagate を取得する。"""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    return KajiRootLoggingState(
        handlers=list(root.handlers),
        level=root.level,
        propagate=root.propagate,
    )


def restore_kaji_root_logging(state: KajiRootLoggingState) -> None:
    """``kaji`` root logger を ``state`` の handlers/level/propagate へ復元する。

    現在の handler を全て除去してから ``state`` の handler 集合・level・
    propagate を復元する。``clean_kaji_console_root`` fixture と Issue #250 の
    回帰テストが同一の復元実装を共有するための SSOT。
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in state.handlers:
        root.addHandler(h)
    root.setLevel(state.level)
    root.propagate = state.propagate


@pytest.fixture(autouse=True)
def clean_kaji_console_root() -> Iterator[None]:
    """全テストで ``kaji`` root logger の handlers/level/propagate を復元する。

    Issue #250: ``configure_console_logging()`` は ``kaji`` root logger に
    ``setLevel`` / ``propagate=False`` / handler を設定するため、復元しないと
    同一 pytest worker 内の後続 ``caplog`` 依存テスト
    （``test_pane_launched_progress_includes_step_agent_timeout``）へ漏れ、
    伝播停止により ``caplog.records`` が空になって flaky に FAILED していた。

    汚染源は ``configure_console_logging()`` を直接呼ぶテストだけではない。
    ``cmd_run()`` が ``kaji_harness/commands/main.py`` で無条件に
    ``configure_console_logging()`` を呼ぶため、``main(["run", ...])`` を叩く
    in-process run-entry テスト（例: ``tests/test_cli_main.py`` の
    ``TestMainMedium``）も同じ global state を汚す。opt-in fixture では
    これらを取りこぼすため、``autouse=True`` で全テストの teardown 時に必ず
    pre-test state へ戻し、どの caller 経由の汚染も後続テストへ漏らさない。

    各テストは直前のテストが復元した clean な baseline から開始するため、
    setup 時の handler 除去は不要（``configure_console_logging()`` は冪等で
    handler を重複登録しない）。snapshot した state を teardown で戻すだけで
    isolation 契約（``tests/test_console_log.py`` の ``_clean_root``）を満たす。
    """
    state = snapshot_kaji_root_logging()
    yield
    restore_kaji_root_logging(state)


@pytest.fixture()
def bare_with_two_worktrees(tmp_path: Path) -> tuple[Path, Path, Path]:
    """bare repo + main worktree + feature worktree を作成して返す。

    Issue #177: ``test_resolve_main_worktree.py`` と ``test_artifacts_dir.py`` の
    両方から共有するため conftest に集約する（SF1）。
    """
    bare = tmp_path / "repo.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
        check=True,
    )
    main_wt = tmp_path / "main"
    feat_wt = tmp_path / "feat"
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
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", str(main_wt), "main"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", "-b", "fix/x", str(feat_wt), "main"],
        check=True,
    )
    return bare, main_wt, feat_wt


def make_issue_context(
    *,
    provider_type: str = "github",
    issue_id: str = "42",
    slug: str = "test",
    branch_prefix: str = "feat",
    repo_root: Path | None = None,
    default_branch: str = "main",
    branch_prefix_fallback: bool = False,
) -> IssueContext:
    """Phase 4: ``build_prompt`` を直接呼び出すテスト向け IssueContext factory.

    Phase 3-e で provider 経由解決が fail-fast 化されたが、``build_prompt`` を
    直接呼び出すテストは provider 経由を回避して `IssueContext` を組み立てる
    必要がある。本 helper は github provider 用の最小限な ``IssueContext`` を返す。

    Args:
        provider_type: ``"github"``。
        issue_id: 数値文字列。
        slug: Issue 末尾 slug（``test`` 等）。
        branch_prefix: ``feat`` / ``fix`` / ``docs`` 等。
        repo_root: worktree_dir の親パス。``None`` の場合は ``"/tmp/repo"``。
        default_branch: ``main`` 等。
        branch_prefix_fallback: ``type:*`` label 不在で ``chore`` fallback された
            場合 True。
    """
    base = repo_root if repo_root is not None else Path("/tmp/repo")
    if provider_type == "github":
        issue_ref = f"#{issue_id}"
        issue_input = issue_id
    else:
        raise ValueError(f"unknown provider_type: {provider_type!r}")

    return IssueContext(
        issue_id=issue_id,
        issue_ref=issue_ref,
        issue_input=issue_input,
        slug=slug,
        branch_prefix=branch_prefix,
        branch_name=f"{branch_prefix}/{issue_id}",
        worktree_dir=str(base / f"kaji-{branch_prefix}-{issue_id}"),
        design_path=f"draft/design/issue-{issue_id}-{slug}.md",
        provider_type=provider_type,
        branch_prefix_fallback=branch_prefix_fallback,
        default_branch=default_branch,
    )


def capacity_terminal_log_text() -> str:
    """ANSI/TUI ``terminal.log`` fixture matching the Issue #296 OB artifact shape.

    The provider capacity phrase and ``Token usage`` are connected on a single
    physical line via per-character truecolor cursor-movement escapes, and that
    line is buried well before the 2000-char tail window that pre-#296
    tail-only extraction scanned. Shared by ``test_recovery_classify.py`` and
    ``test_recovery_handler.py`` so both call the real ``_terminal_exit_detail``
    against the same fixture shape instead of hand-typing its output.
    """
    raw = (
        "⚠ Selected model is at capacity. Please try a different model. "
        "› Shutting down... Token usage: total=32,386 input=31,159 output=1,227"
    )
    chars = []
    for i, ch in enumerate(raw):
        r, g, b = (i * 7) % 256, (i * 13) % 256, (i * 19) % 256
        chars.append(f"\x1b[38;2;{r};{g};{b};49m{ch}")
    chars.append("\x1b[39m\n")
    capacity_line = "".join(chars)
    header = "assistant is thinking...\n" * 60
    footer = ("\x1b[2K\x1b[1A" * 400) + "\n"
    return header + capacity_line + footer


@pytest.fixture(autouse=True)
def _default_skill_metadata_for_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #204: ``WorkflowRunner.run()`` の L2 preflight が全 step について
    ``load_skill_metadata`` を呼ぶようになった。既存テストの多くは
    ``validate_skill_exists`` のみ mock していたため、本 fixture で
    runner namespace の ``load_skill_metadata`` を benign default
    （``exec_script=None``、agent 経路を継続）に置き換えて autouse する。

    テスト側で ``patch("kaji_harness.runner.load_skill_metadata", ...)`` を
    明示的に張ると本 monkeypatch の上にスタックされ、``with`` ブロック内で
    そちらが優先される（monkeypatch は fixture teardown まで残る）。
    """
    from kaji_harness import runner as _runner
    from kaji_harness.skill import SkillMetadata

    def _fake(skill_name: str, *args: object, **kwargs: object) -> SkillMetadata:
        return SkillMetadata(name=skill_name, description="", exec_script=None)

    monkeypatch.setattr(_runner, "load_skill_metadata", _fake)


@pytest.fixture
def stub_github_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GitHubProvider.view_issue`` を gh 不要の決定的 Issue に置き換える（#448）。

    local provider 削除前は runner / CLI テストが tmp repo の local Issue で
    IssueContext を解決していた。github provider 設定へ移したテストは本 fixture で
    ``title="test issue <n>"`` / ``slug="test-<n>"`` / ``type:feature`` の Issue を
    返させ、``resolve_issue_context`` の実ロジックはそのまま通す。
    """

    def _fake_view_issue(self: GitHubProvider, issue_id: str) -> Issue:
        return Issue(
            id=issue_id,
            title=f"test issue {issue_id}",
            body="body",
            state="open",
            labels=[Label(name="type:feature")],
            slug=f"test-{issue_id}",
        )

    monkeypatch.setattr(GitHubProvider, "view_issue", _fake_view_issue)


@pytest.fixture(autouse=True)
def _stub_gh_version(request: pytest.FixtureRequest) -> Iterator[None]:
    """既定で `gh` version probe を supported 値に固定する（Issue #372）。

    `@pytest.mark.gh_version_probe` を付けたテストのみ opt-out し、probe 経路
    そのものを検証する。opt-out がなければ `GitHubProvider._detect_gh_version`
    が固定値に置換され、preflight の分岐を検証できない。
    """
    if request.node.get_closest_marker("gh_version_probe"):
        yield
        return
    with patch.object(GitHubProvider, "_detect_gh_version", return_value=_MIN_GH_VERSION):
        yield


def _has_project_marker_ancestor(path: Path) -> bool:
    """``path`` 自身または祖先に ``.kaji/config.toml`` か ``.git`` があれば True。"""
    return any(
        (candidate / ".kaji" / "config.toml").exists() or (candidate / ".git").exists()
        for candidate in (path, *path.parents)
    )


@pytest.fixture()
def outside_project_tmp_path() -> Iterator[Path]:
    """kaji project / git repo のどちらの配下でもない一意な一時ディレクトリを返す。

    Issue #407: workflow 内の ``tmp_path`` は ``TMPDIR``（= ``KAJI_TMP_DIR``）に従い
    リポジトリ内になる。そのため「project 外 / git 外」を前提とする test
    （``KajiConfig.discover`` の not-found、``git rev-parse`` の失敗など）は上位探索で実リポジトリを
    見つけてしまう。この fixture は ``TMPDIR`` / ``TMP`` / ``TEMP`` を参照しない platform 既定
    候補（POSIX は ``/tmp``）配下に作り、作成後に祖先検査を行って満たせなければ fail loud する。
    """
    candidates = [Path(tempfile.gettempdir())]
    candidates += [Path(p) for p in ("/tmp", "/var/tmp", "/usr/tmp")]
    base = next(
        (c for c in candidates if c.is_dir() and not _has_project_marker_ancestor(c)),
        None,
    )
    if base is None:
        pytest.fail(
            "outside_project_tmp_path: no temp base outside any kaji project / git repo "
            f"(tried: {[str(c) for c in candidates]})"
        )
    path = Path(tempfile.mkdtemp(prefix="kaji-outside-", dir=base)).resolve()
    try:
        if _has_project_marker_ancestor(path):
            pytest.fail(f"outside_project_tmp_path: {path} is inside a kaji project or git repo")
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
