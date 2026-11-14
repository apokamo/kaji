"""Medium tests: ``issue-start`` の exec_script dispatch と ``kaji issue start`` CLI（Issue #569）。

- runner が start step を agent ではなく ``kaji_harness.scripts.issue_start`` の subprocess として
  起動し、PASS / ABORT で workflow の ``on:`` どおりに遷移する（headless / Herdr の両設定）
- ``kaji issue start <id>`` が同じ entry ``main`` に合流し、第 2 引数は副作用なしで usage error
- 変更後の全 workflow が preflight（agent 無し + exec_script 有り）を通る
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.commands.issue import _handle_issue
from kaji_harness.config import KajiConfig
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.preflight import preflight_workflow_path
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.runner import WorkflowRunner
from kaji_harness.skill import load_skill_metadata

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENTRY_MODULE = "kaji_harness.scripts.issue_start"
_WORKFLOWS = [
    ".kaji/wf/official/dev.yaml",
    ".kaji/wf/official/docs.yaml",
    ".kaji/wf/custom/dev/dev-small.yaml",
    ".kaji/wf/custom/dev/dev-thorough.yaml",
    ".kaji/wf/custom/dev/dev-thorough-codex.yaml",
    ".kaji/wf/custom/docs/docs-codex.yaml",
    ".kaji/wf/custom/docs/docs-thorough-codex.yaml",
]


def _verdict(status: str) -> str:
    return (
        f"---VERDICT---\nstatus: {status}\nreason: |\n  ok\n"
        f"evidence: |\n  ok\nsuggestion: |\n  rerun\n---END_VERDICT---\n"
    )


def _project(tmp_path: Path, *, agent_runner: str) -> KajiConfig:
    """実 issue-start SKILL.md と後続 exec_script skill を持つ project を作る。"""
    skills = tmp_path / ".claude" / "skills"
    (skills / "issue-start").mkdir(parents=True)
    shutil.copy(
        _REPO_ROOT / ".claude" / "skills" / "issue-start" / "SKILL.md",
        skills / "issue-start" / "SKILL.md",
    )
    (skills / "after").mkdir()
    (skills / "after" / "SKILL.md").write_text(
        "---\nname: after\ndescription: d\nexec_script: after.entry\n---\n# after\n",
        encoding="utf-8",
    )
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir()
    config = kaji_dir / "config.toml"
    config.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        f'[execution]\ndefault_timeout = 60\nagent_runner = "{agent_runner}"\n\n'
        '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(config)


@pytest.mark.medium
@pytest.mark.usefixtures("stub_github_issue")
class TestStartStepDispatch:
    @pytest.fixture(autouse=True)
    def _no_pr_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(GitHubProvider, "resolve_pr_context", lambda self, branch: None)

    @pytest.mark.parametrize("agent_runner", ["headless", "interactive_terminal"])
    @pytest.mark.parametrize(
        ("status", "expected_modules"),
        [("PASS", [_ENTRY_MODULE, "after.entry"]), ("ABORT", [_ENTRY_MODULE])],
    )
    def test_start_runs_entry_module_and_follows_transition(
        self,
        tmp_path: Path,
        agent_runner: str,
        status: str,
        expected_modules: list[str],
    ) -> None:
        config = _project(tmp_path, agent_runner=agent_runner)
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[
                Step(id="start", skill="issue-start", on={"PASS": "after", "ABORT": "end"}),
                Step(id="after", skill="after", on={"PASS": "end"}),
            ],
        )
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=config,
        )
        modules: list[str] = []

        def fake_execute_script(**kwargs: object) -> CLIResult:
            module = kwargs["module"]
            assert isinstance(module, str)
            modules.append(module)
            env = kwargs["env"]
            assert isinstance(env, dict)
            if module == _ENTRY_MODULE:
                assert env["KAJI_ISSUE_ID"] == "99"
                assert env["KAJI_STEP_ID"] == "start"
                assert env["KAJI_VERDICT_PATH"]
                return CLIResult(full_output=_verdict(status))
            return CLIResult(full_output=_verdict("PASS"))

        with (
            # conftest の autouse stub を外し、実 SKILL.md の frontmatter から dispatch を決める
            patch("kaji_harness.runner.load_skill_metadata", load_skill_metadata),
            patch("kaji_harness.runner.execute_script", side_effect=fake_execute_script),
            patch("kaji_harness.runner.execute_cli") as mock_cli,
            patch("kaji_harness.runner.execute_interactive_terminal") as mock_terminal,
        ):
            state = runner.run()

        assert modules == expected_modules
        mock_cli.assert_not_called()
        mock_terminal.assert_not_called()
        assert state.last_completed_step == ("after" if status == "PASS" else "start")


@pytest.mark.medium
class TestIssueStartCli:
    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / ".kaji").mkdir(parents=True)
        (repo / ".kaji" / "config.toml").write_text(
            '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
            "[execution]\ndefault_timeout = 1800\n\n"
            '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n',
            encoding="utf-8",
        )
        return repo

    def test_start_delegates_to_entry_main_with_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(self._repo(tmp_path))
        with patch("kaji_harness.scripts.issue_start.main", return_value=0) as mock_main:
            rc = _handle_issue(["start", "569"])
        assert rc == 0
        mock_main.assert_called_once()
        args, kwargs = mock_main.call_args
        assert args == (["569"],)
        assert isinstance(kwargs["provider"], GitHubProvider)

    def test_second_argument_is_usage_error_without_side_effects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(self._repo(tmp_path))
        with (
            patch.object(GitHubProvider, "resolve_issue_context") as mock_ctx,
            patch.object(GitHubProvider, "view_issue") as mock_view,
            patch.object(GitHubProvider, "edit_issue") as mock_edit,
            patch.object(GitHubProvider, "comment_issue") as mock_comment,
            patch("kaji_harness.issue_start.subprocess.run") as mock_git,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _handle_issue(["start", "569", "fix"])
        assert exc_info.value.code == 2
        for mock in (mock_ctx, mock_view, mock_edit, mock_comment, mock_git):
            mock.assert_not_called()


@pytest.mark.medium
@pytest.mark.parametrize("rel", _WORKFLOWS)
def test_changed_workflows_pass_preflight(rel: str) -> None:
    result = preflight_workflow_path(
        _REPO_ROOT / rel, project_root=_REPO_ROOT, skill_dir=".claude/skills"
    )
    assert result.errors == []
    assert not [w for w in result.warnings if "'start'" in w]
    assert result.workflow is not None
    metadata = result.skill_metadata["start"]
    assert metadata is not None
    assert metadata.exec_script == _ENTRY_MODULE
