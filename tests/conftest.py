"""Shared test helpers across kaji_harness test suite.

Phase 3-e: 既存 runner E2E テスト群が ``provider=local`` 経由で動くようになり、
``WorkflowRunner.run()`` 起動時に IssueContext 解決が走るため、tmp repo 内に
対象 Issue dir を予め作っておく必要がある。autouse fixture で
``WorkflowRunner.__post_init__`` 完了直後に Issue dir を作成し、既存テストの
書き換えなしで Phase 3-e 移行を吸収する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.providers import IssueContext, LocalProvider


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
    "test_phase3c_runner.py",
    "test_phase3d_preflight.py",
    "test_phase3e_large_local.py",
}


_RESOLVE_MAIN_WORKTREE_OPT_OUT_FILES = {
    # ``resolve_main_worktree`` 本体を検証する file は素の挙動を観測する必要がある。
    "test_resolve_main_worktree.py",
}


@pytest.fixture(autouse=True)
def _stub_resolve_main_worktree_for_non_git(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 git tmp_path 配下の test fixture 向けに ``resolve_main_worktree`` を stub する。

    gl:21 で production の test-compat fallback (非 git → ``start_dir.resolve()``) を
    撤去したが、既存テスト群は ``provider.type='local'`` config を非 git tmp_path に
    書き、``get_provider()`` 経由で worktree 解決を踏む構造になっている。本 fixture は
    その「テストが暗黙に依存していた fallback 挙動」を明示宣言された test infrastructure
    へ移管するもので、``resolve_main_worktree`` を「git でなければ ``start_dir.resolve()``
    を返す、git なら本物を呼ぶ」薄い wrapper に差し替える。

    意図的に opt out する file (``_RESOLVE_MAIN_WORKTREE_OPT_OUT_FILES``):
    - resolve_main_worktree 本体の検証 / fail-fast 挙動の検証を行う file
    """
    if Path(request.node.fspath).name in _RESOLVE_MAIN_WORKTREE_OPT_OUT_FILES:
        return

    from kaji_harness.providers import _worktree as _wt_module

    real_resolve = _wt_module.resolve_main_worktree

    def stubbed(*, start_dir: Path, default_branch: str) -> Path:
        # git repo であれば実装どおりの挙動を取らせる
        if (start_dir / ".git").exists():
            return real_resolve(start_dir=start_dir, default_branch=default_branch)
        return start_dir.resolve()

    # provider module 内で再 export されている同名シンボルも差し替える
    from kaji_harness import providers as _providers_pkg

    monkeypatch.setattr(_wt_module, "resolve_main_worktree", stubbed)
    monkeypatch.setattr(_providers_pkg, "resolve_main_worktree", stubbed)


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
