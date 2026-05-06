"""Phase 3-c: WorkflowRunner と prompt.build_prompt の検証。

PR-3c のスコープのうち、`kaji run` 経路（IssueContext 解決と Skill への
prompt 注入）に対応するテスト。`cli_main` 側の dispatcher テストは
`tests/test_phase3c_dispatcher.py` を参照。

phase3-design.md § 4 ロールアウト戦略 PR-3c に対応。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.models import Step, Workflow
from kaji_harness.prompt import build_prompt
from kaji_harness.providers import IssueContext, LocalProvider
from kaji_harness.state import SessionState

# ============================================================
# Helpers
# ============================================================


def _write_repo(tmp_path: Path, *, provider_section: str = "") -> Path:
    """``.kaji/config.toml`` を持つ最小 repo を tmp_path 下に作る。"""
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n" + provider_section
    )
    return repo


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


# ============================================================
# prompt.build_prompt: IssueContext 注入
# ============================================================


@pytest.mark.small
class TestPromptIssueContextInjection:
    def _step(self) -> Step:
        return Step(id="implement", skill="x", agent="claude", on={"PASS": "end"})

    def _workflow(self) -> Workflow:
        return Workflow(
            name="w",
            description="d",
            execution_policy="sequential",
            steps=[self._step()],
            cycles=[],
        )

    def test_no_context_uses_legacy_two_variables(self) -> None:
        prompt = build_prompt(
            self._step(),
            issue="42",
            state=_make_state(),
            workflow=self._workflow(),
            issue_context=None,
        )
        assert "- issue_id: 42" in prompt
        assert "- issue_ref: #42" in prompt
        # IssueContext 由来の 5 変数は注入されない
        assert "- branch_prefix:" not in prompt
        assert "- branch_name:" not in prompt
        assert "- worktree_dir:" not in prompt
        assert "- design_path:" not in prompt
        assert "- issue_input:" not in prompt

    def test_with_context_injects_five_extra_variables(self) -> None:
        ctx = IssueContext(
            issue_id="local-pc1-3",
            issue_ref="local-pc1-3",
            issue_input="local-pc1-3",
            slug="my-slug",
            branch_prefix="feat",
            branch_name="feat/local-pc1-3",
            worktree_dir="/path/kaji-feat-local-pc1-3",
            design_path="draft/design/issue-local-pc1-3-my-slug.md",
            provider_type="local",
        )
        prompt = build_prompt(
            self._step(),
            issue="local-pc1-3",
            state=_make_state(),
            workflow=self._workflow(),
            issue_context=ctx,
        )
        assert "- issue_id: local-pc1-3" in prompt
        assert "- issue_ref: local-pc1-3" in prompt
        assert "- issue_input: local-pc1-3" in prompt
        assert "- branch_prefix: feat" in prompt
        assert "- branch_name: feat/local-pc1-3" in prompt
        assert "- worktree_dir: /path/kaji-feat-local-pc1-3" in prompt
        assert "- design_path: draft/design/issue-local-pc1-3-my-slug.md" in prompt


# ============================================================
# rev #3: kaji run の IssueContext 解決を normalize_id に通す
# ============================================================


def _make_runner(repo: Path, issue: str):
    from kaji_harness.runner import WorkflowRunner
    from kaji_harness.workflow import load_workflow

    wf_path = repo / "wf.yaml"
    wf_path.write_text(
        "name: t\ndescription: t\nexecution_policy: auto\n"
        "steps:\n  - id: s\n    skill: x\n    agent: claude\n"
        "    on:\n      PASS: end\n      ABORT: end\n"
    )
    cfg = KajiConfig.discover(start_dir=repo)
    return WorkflowRunner(
        workflow=load_workflow(wf_path),
        issue_number=issue,
        project_root=cfg.repo_root,
        artifacts_dir=cfg.artifacts_dir,
        config=cfg,
    )


@pytest.mark.medium
class TestRunnerIssueContextNormalization:
    """``WorkflowRunner._resolve_issue_context`` が ``normalize_id`` を経由する。

    review #1: ``kaji run ... 1`` / ``kaji run ... pc1-1`` で 5 変数注入が
    成立しなければ Phase 3-c の主目的が壊れる。
    """

    @pytest.mark.parametrize("input_id", ["1", "pc1-1", "local-pc1-1"])
    def test_local_id_forms_resolve_to_same_context(self, tmp_path: Path, input_id: str) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        provider.create_issue(title="Hello", body="b", labels=["type:feature"], slug="hello-test")
        runner = _make_runner(repo, input_id)
        ctx = runner._resolve_issue_context()
        assert ctx is not None, f"context resolution returned None for {input_id!r}"
        assert ctx.issue_id == "local-pc1-1"
        assert ctx.branch_prefix == "feat"
        assert ctx.branch_name == "feat/local-pc1-1"
        assert ctx.design_path == "draft/design/issue-local-pc1-1-hello-test.md"

    def test_github_numeric_id_resolves(self, tmp_path: Path) -> None:
        """``provider=github`` で数値 ID が GitHubProvider に渡る。"""
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        runner = _make_runner(repo, "153")
        # gh subprocess を mock し、IssueContext 構築まで通す
        from kaji_harness.providers.models import Issue as _Issue

        with patch(
            "kaji_harness.providers.github.GitHubProvider.view_issue",
            return_value=_Issue(
                id="153",
                title="GH issue",
                body="b",
                state="open",
                labels=[],
                comments=[],
                slug="",
            ),
        ):
            ctx = runner._resolve_issue_context()
        assert ctx is not None
        assert ctx.issue_id == "153"
        assert ctx.issue_ref == "#153"

    def test_no_provider_returns_none_legacy_fallback(self, tmp_path: Path) -> None:
        """``[provider]`` 未設定時のみ legacy 2 変数 fallback。"""
        import kaji_harness.providers as providers_pkg

        providers_pkg._PROVIDER_FALLBACK_WARNED = False
        repo = _write_repo(tmp_path)  # provider 未設定
        runner = _make_runner(repo, "42")
        ctx = runner._resolve_issue_context()
        assert ctx is None  # legacy 互換


@pytest.mark.medium
class TestRunnerFailFastOnExplicitProvider:
    """review #2: 明示 provider 設定下では context 解決失敗で fail-fast。"""

    def test_local_missing_issue_dir_raises_resolution_error(self, tmp_path: Path) -> None:
        from kaji_harness.errors import IssueContextResolutionError

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        # `local-pc1-9999` は存在しない → fail-fast で raise
        runner = _make_runner(repo, "9999")
        with pytest.raises(IssueContextResolutionError):
            runner._resolve_issue_context()

    def test_invalid_id_raises_resolution_error(self, tmp_path: Path) -> None:
        from kaji_harness.errors import IssueContextResolutionError

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        runner = _make_runner(repo, "Bogus-ID")
        with pytest.raises(IssueContextResolutionError, match="invalid issue id"):
            runner._resolve_issue_context()

    def test_remote_cache_id_rejected_under_local(self, tmp_path: Path) -> None:
        """``kaji run gh:N`` は read-only で意味的に矛盾 → fail-fast。"""
        from kaji_harness.errors import IssueContextResolutionError

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        runner = _make_runner(repo, "gh:153")
        with pytest.raises(IssueContextResolutionError, match="read-only"):
            runner._resolve_issue_context()

    def test_provider_init_failure_raises_resolution_error(self, tmp_path: Path) -> None:
        """machine_id 不在のような provider 構築失敗も IssueContextResolutionError。"""
        from kaji_harness.errors import IssueContextResolutionError

        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "local"\n',
        )
        runner = _make_runner(repo, "1")
        with pytest.raises(IssueContextResolutionError):
            runner._resolve_issue_context()
