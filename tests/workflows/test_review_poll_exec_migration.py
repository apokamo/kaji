"""review-poll step の exec 移行に対する回帰テスト（Issue #234）.

builtin workflow（`.kaji/wf/{review-cycle,review-close,full-cycle,full-cycle-xhigh}.yaml`）の
`review-poll` step が `skill: review-poll`（exec_script 経路）から `exec` step type へ
置換されたことを固定する。

- **static regression**（Small）: 4 workflow をロードし、`review-poll` step の `exec` argv が
  期待値であること・`skill`/`agent`/`model`/`effort` が None であることを検証する。
- **runtime-ish verification**（Medium）: `WorkflowRunner` 経路で `review-poll` が
  `dispatch_kind == "exec"`（= `execute_exec` に到達し `execute_script`/`execute_cli` に
  到達しない）となることを patch で検証する。

`kaji validate` は schema/skill 解決のみで dispatch 経路を検証しないため、dispatch 不変・
WARNING 除去の必須検証として本ファイルを置く。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.workflow import load_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Issue #234 対象の 4 workflow。
TARGET_WORKFLOWS = [
    "review-cycle.yaml",
    "review-close.yaml",
    "full-cycle.yaml",
    "full-cycle-xhigh.yaml",
]

PUBLIC_WORKFLOWS = [
    "dev.yaml",
    "dev-thorough.yaml",
    "docs.yaml",
]

EXPECTED_EXEC = [
    "uv",
    "run",
    "--no-sync",
    "python",
    "-m",
    "kaji_harness.scripts.review_poll_entry",
]

PUBLIC_EXPECTED_EXEC = ["kaji", "pr", "review-poll"]

TARGET_PATHS = [REPO_ROOT / ".kaji" / "wf" / name for name in TARGET_WORKFLOWS]
TARGET_IDS = TARGET_WORKFLOWS
PUBLIC_PATHS = [REPO_ROOT / ".kaji" / "wf" / name for name in PUBLIC_WORKFLOWS]
PUBLIC_IDS = PUBLIC_WORKFLOWS


def _review_poll_step(path: Path) -> Step:
    wf = load_workflow(path)
    return next(s for s in wf.steps if s.id == "review-poll")


def _verdict(status: str) -> str:
    return (
        f"---VERDICT---\nstatus: {status}\nreason: |\n  ok\n"
        f"evidence: |\n  ok\nsuggestion: |\n  none\n---END_VERDICT---\n"
    )


# ============================================================
# static regression（Small）
# ============================================================


@pytest.mark.small
class TestReviewPollExecStatic:
    def test_all_target_workflows_exist(self) -> None:
        """検証対象の 4 ファイルが存在する（glob/typo で silently skip しない）。"""
        missing = [p.name for p in TARGET_PATHS if not p.exists()]
        assert not missing, f"対象 workflow が見つからない: {missing}"

    @pytest.mark.parametrize("path", TARGET_PATHS, ids=TARGET_IDS)
    def test_review_poll_is_exec_step(self, path: Path) -> None:
        """review-poll step の exec argv が期待値で固定される。"""
        step = _review_poll_step(path)
        assert step.exec == EXPECTED_EXEC, (
            f"{path.name}: review-poll.exec が期待値と異なる。"
            f"got={step.exec!r} expected={EXPECTED_EXEC!r}"
        )

    @pytest.mark.parametrize("path", TARGET_PATHS, ids=TARGET_IDS)
    def test_review_poll_has_no_agent_fields(self, path: Path) -> None:
        """exec 化により skill / agent / model / effort はすべて None。"""
        step = _review_poll_step(path)
        assert step.skill is None, f"{path.name}: review-poll.skill が残存している"
        assert step.agent is None, f"{path.name}: review-poll.agent が残存している"
        assert step.model is None, f"{path.name}: review-poll.model が残存している"
        assert step.effort is None, f"{path.name}: review-poll.effort が残存している"

    @pytest.mark.parametrize("path", TARGET_PATHS, ids=TARGET_IDS)
    def test_review_poll_on_block_preserved(self, path: Path) -> None:
        """on: ブロックの遷移キーは exec 化後も維持される。"""
        step = _review_poll_step(path)
        # PASS は yaml ごとに end/close と異なるが、他 3 キーは全 yaml 共通で維持。
        assert step.on.get("RETRY") == "pr-fix", f"{path.name}: RETRY routing 喪失"
        assert step.on.get("BACK_FALLBACK") == "review", f"{path.name}: BACK_FALLBACK routing 喪失"
        assert step.on.get("ABORT") == "end", f"{path.name}: ABORT routing 喪失"
        expected_pass = "end" if path.name == "review-cycle.yaml" else "close"
        assert step.on.get("PASS") == expected_pass, (
            f"{path.name}: PASS routing が変化した（期待 {expected_pass}）"
        )


@pytest.mark.small
class TestPublicReviewPollExecStatic:
    def test_all_public_workflows_exist(self) -> None:
        """READMEで案内する新標準workflowが存在する。"""
        missing = [p.name for p in PUBLIC_PATHS if not p.exists()]
        assert not missing, f"対象 workflow が見つからない: {missing}"

    @pytest.mark.parametrize("path", PUBLIC_PATHS, ids=PUBLIC_IDS)
    def test_review_poll_uses_installed_kaji_cli(self, path: Path) -> None:
        """公開workflowは対象repoのPython環境ではなくinstalled kaji CLI経由でpollする。"""
        step = _review_poll_step(path)
        assert step.exec == PUBLIC_EXPECTED_EXEC, (
            f"{path.name}: review-poll.exec がportableなCLI経由ではない。"
            f"got={step.exec!r} expected={PUBLIC_EXPECTED_EXEC!r}"
        )

    @pytest.mark.parametrize("path", PUBLIC_PATHS, ids=PUBLIC_IDS)
    def test_review_poll_has_no_agent_fields(self, path: Path) -> None:
        """公開workflowのreview-pollもexec stepとしてdispatchされる。"""
        step = _review_poll_step(path)
        assert step.skill is None, f"{path.name}: review-poll.skill が残存している"
        assert step.agent is None, f"{path.name}: review-poll.agent が残存している"
        assert step.model is None, f"{path.name}: review-poll.model が残存している"
        assert step.effort is None, f"{path.name}: review-poll.effort が残存している"


# ============================================================
# runtime-ish verification（Medium）
# ============================================================


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


@pytest.mark.medium
class TestReviewPollExecDispatch:
    @pytest.mark.parametrize("path", TARGET_PATHS, ids=TARGET_IDS)
    def test_review_poll_dispatches_to_execute_exec(self, path: Path, tmp_path: Path) -> None:
        """実 workflow の review-poll step が exec dispatch（execute_exec）に到達する。

        routing は本検証の対象外なので on を最小化した単一 step workflow に組み替え、
        dispatch 種別の確定のみを検証する。
        """
        real_step = _review_poll_step(path)
        step = Step(id="review-poll", exec=real_step.exec, on={"PASS": "end", "ABORT": "end"})
        workflow = Workflow(name="t", description="", execution_policy="auto", steps=[step])
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=_make_config(tmp_path),
        )

        def fake_execute_exec(**kwargs: object) -> CLIResult:
            assert kwargs["argv"] == EXPECTED_EXEC
            return CLIResult(full_output=_verdict("PASS"))

        with (
            patch("kaji_harness.runner.execute_exec", side_effect=fake_execute_exec) as mock_exec,
            patch("kaji_harness.runner.execute_script") as mock_script,
            patch("kaji_harness.runner.execute_cli") as mock_cli,
        ):
            state = runner.run()

        mock_exec.assert_called_once()
        mock_script.assert_not_called()
        mock_cli.assert_not_called()
        assert state.last_completed_step == "review-poll"
