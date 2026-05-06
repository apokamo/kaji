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

from kaji_harness.providers import LocalProvider


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
