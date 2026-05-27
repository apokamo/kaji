"""Shared test helpers across kaji_harness test suite.

Phase 3-e: 既存 runner E2E テスト群が ``provider=local`` 経由で動くようになり、
``WorkflowRunner.run()`` 起動時に IssueContext 解決が走るため、tmp repo 内に
対象 Issue dir を予め作っておく必要がある。autouse fixture で
``WorkflowRunner.__post_init__`` 完了直後に Issue dir を作成し、既存テストの
書き換えなしで Phase 3-e 移行を吸収する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kaji_harness.providers import IssueContext, LocalProvider


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
    必要がある。本 helper は github / local 両 provider 用の最小限な
    ``IssueContext`` を返す。

    Args:
        provider_type: ``"github"`` / ``"local"``。
        issue_id: github なら数値文字列、local なら ``"local-pc1-3"`` 形式。
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
    elif provider_type == "local":
        # local では issue_id がそのまま ref / input
        issue_ref = issue_id
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


def ensure_local_issue(repo_root: Path, issue: str, machine_id: str = "pc1") -> None:
    """provider=local 配下で ``local-<machine>-<issue>`` の Issue dir を作成する。

    counter file を ``issue - 1`` に固定してから 1 度 ``create_issue`` を呼ぶ
    ことで目的の id を直接採番させる（O(1)）。``issue`` が数値以外の場合や
    Issue dir が既に存在する場合は no-op。
    """
    if not issue.isdigit() or issue == "0":
        return
    n = int(issue)
    issues_root = repo_root / ".kaji" / "issues"
    issues_root.mkdir(parents=True, exist_ok=True)
    if any(d.name.startswith(f"local-{machine_id}-{n}-") for d in issues_root.iterdir()):
        return
    counter_path = repo_root / ".kaji" / "counters" / f"{machine_id}.txt"
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path.write_text(str(n - 1))
    provider = LocalProvider(repo_root=repo_root, machine_id=machine_id)
    provider.create_issue(
        title=f"test issue {n}",
        body="body",
        labels=["type:feature"],
        slug=f"test-{n}",
    )


_AUTOCREATE_OPT_OUT_FILES = {
    "test_runner.py",
    "test_preflight.py",
    "test_local_cli_large_local.py",
}


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


@pytest.fixture(autouse=True)
def _autocreate_local_issue_for_runner(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``WorkflowRunner`` 構築時に provider=local config なら対象 Issue dir を作る。

    Phase 3-e で fail-fast 化したことで、既存 runner E2E テストが Issue dir
    不在で IssueContextResolutionError を吹くため。``__post_init__`` 完了直後に
    フックする。numeric な issue_number のみ対象（``local-pc1-1`` のような
    完全形 id は明示的に作られている前提）。fail-fast 自体を検証する test
    file は ``_AUTOCREATE_OPT_OUT_FILES`` で除外する。
    """
    if Path(request.node.fspath).name in _AUTOCREATE_OPT_OUT_FILES:
        return
    from kaji_harness import runner as _runner

    original = _runner.WorkflowRunner.__post_init__

    def patched_post_init(self: _runner.WorkflowRunner) -> None:  # type: ignore[no-redef]
        original(self)
        provider_cfg = self.config.provider
        if provider_cfg is None or provider_cfg.type != "local":
            return
        machine_id = provider_cfg.local.machine_id or "pc1"
        repo_root = self.config.repo_root
        ensure_local_issue(repo_root, str(self.issue_number), machine_id=machine_id)

    monkeypatch.setattr(_runner.WorkflowRunner, "__post_init__", patched_post_init)
