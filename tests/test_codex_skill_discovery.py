"""Issue #408: codex step の skill が Codex の探索パスで解決できることを検証する。

preflight は ``<step の実効 workdir>/.agents/skills/<skill>/SKILL.md`` を検証し、
欠けていれば Codex を起動する前にエラーで止める（D2）。

- small: ``resolve_step_workdir`` の優先順位
- medium: ``preflight_workflow`` / ``WorkflowRunner.run()`` / ``kaji validate`` の実ファイル検証
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.commands.parser import create_parser
from kaji_harness.commands.validate import cmd_validate
from kaji_harness.config import KajiConfig
from kaji_harness.errors import WorkflowValidationError
from kaji_harness.models import Step, Workflow
from kaji_harness.preflight import preflight_workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.workflow import resolve_step_workdir

CANONICAL_SKILL_DIR = ".claude/skills"


def _write_canonical_skill(root: Path, name: str, frontmatter: str = "") -> Path:
    skill_dir = root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"{frontmatter}# {name}\n", encoding="utf-8")
    return skill_dir


def _link_codex_skill(root: Path, name: str) -> Path:
    """``.agents/skills/<name>`` を canonical への相対 symlink として置く。"""
    link = root / ".agents" / "skills" / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(Path("../../.claude/skills") / name)
    return link


def _workflow(*, agent: str = "codex", skill: str = "review", step_workdir: str | None = None):
    return Workflow(
        name="t",
        description="",
        execution_policy="auto",
        steps=[
            Step(
                id="review",
                skill=skill,
                agent=agent,
                workdir=step_workdir,
                on={"PASS": "end", "ABORT": "end"},
            )
        ],
    )


def _preflight(workflow: Workflow, root: Path) -> list[str]:
    return preflight_workflow(workflow, project_root=root, skill_dir=CANONICAL_SKILL_DIR).errors


@pytest.mark.small
class TestResolveStepWorkdir:
    """step.workdir > workflow.workdir > project_root。"""

    def _wf(self, workflow_workdir: str | None) -> Workflow:
        wf = _workflow()
        wf.workdir = workflow_workdir
        return wf

    def test_step_workdir_wins(self) -> None:
        wf = self._wf("/wf")
        step = Step(id="s", skill="x", agent="codex", workdir="/step", on={})

        assert resolve_step_workdir(step, wf, Path("/root")) == Path("/step")

    def test_workflow_workdir_when_step_has_none(self) -> None:
        wf = self._wf("/wf")
        step = Step(id="s", skill="x", agent="codex", on={})

        assert resolve_step_workdir(step, wf, Path("/root")) == Path("/wf")

    def test_project_root_when_no_workdir(self) -> None:
        wf = self._wf(None)
        step = Step(id="s", skill="x", agent="codex", on={})

        assert resolve_step_workdir(step, wf, Path("/root")) == Path("/root")


@pytest.mark.medium
class TestCodexSkillPreflight:
    def test_missing_agents_skill_is_error(self, tmp_path: Path) -> None:
        """再現テスト: canonical にしか skill がない codex step を preflight が通してはならない。"""
        _write_canonical_skill(tmp_path, "review")

        errors = _preflight(_workflow(), tmp_path)

        assert len(errors) == 1
        message = errors[0]
        assert "Step 'review'" in message
        assert "'codex'" in message
        assert "skill 'review'" in message
        assert "not discoverable by Codex" in message
        assert str(tmp_path / ".agents" / "skills" / "review" / "SKILL.md") in message
        assert ".agents/skills/review" in message

    def test_symlink_to_canonical_passes(self, tmp_path: Path) -> None:
        _write_canonical_skill(tmp_path, "review")
        _link_codex_skill(tmp_path, "review")

        assert _preflight(_workflow(), tmp_path) == []

    @pytest.mark.parametrize("agent", ["claude", "antigravity"])
    def test_other_agents_do_not_require_agents_skills(self, tmp_path: Path, agent: str) -> None:
        _write_canonical_skill(tmp_path, "review")

        assert _preflight(_workflow(agent=agent), tmp_path) == []

    def test_exec_script_skill_is_exempt(self, tmp_path: Path) -> None:
        _write_canonical_skill(
            tmp_path, "poll", "---\nname: poll\ndescription: d\nexec_script: package.poll\n---\n"
        )

        errors = _preflight(_workflow(skill="poll"), tmp_path)

        assert errors == []

    def test_canonical_failure_is_not_doubled(self, tmp_path: Path) -> None:
        """canonical が無い step は従来の 1 エラーだけ。codex 側のエラーを重ねない。"""
        errors = _preflight(_workflow(skill="missing"), tmp_path)

        assert len(errors) == 1
        assert "not discoverable by Codex" not in errors[0]

    def test_traversal_skill_name_is_rejected_by_canonical_check(self, tmp_path: Path) -> None:
        errors = _preflight(_workflow(skill="../escape"), tmp_path)

        assert len(errors) == 1
        assert "path traversal" in errors[0]

    def test_symlink_escaping_workdir_is_error(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        outside = tmp_path / "outside" / "review"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("# review\n", encoding="utf-8")
        _write_canonical_skill(root, "review")
        link = root / ".agents" / "skills" / "review"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)

        errors = _preflight(_workflow(), root)

        assert len(errors) == 1
        assert "not discoverable by Codex" in errors[0]
        assert "escapes workdir" in errors[0]

    def test_step_workdir_is_the_discovery_root(self, tmp_path: Path) -> None:
        """step.workdir を指定した codex step は、その workdir の .agents/skills を見る。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        _write_canonical_skill(tmp_path, "review")
        _link_codex_skill(tmp_path, "review")  # project_root 側にはある

        missing = _preflight(_workflow(step_workdir=str(sub)), tmp_path)

        assert len(missing) == 1
        assert str(sub / ".agents" / "skills" / "review" / "SKILL.md") in missing[0]

        # workdir 側に実体を置けば通る（canonical は project_root 基準のまま）
        local = sub / ".agents" / "skills" / "review"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text("# review\n", encoding="utf-8")
        assert _preflight(_workflow(step_workdir=str(sub)), tmp_path) == []


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
class TestRunnerStopsBeforeCodex:
    def test_run_raises_validation_error_without_launching_codex(self, tmp_path: Path) -> None:
        """実ファイルで検証する。validate_skill_exists は patch しない。"""
        _write_canonical_skill(tmp_path, "review")
        runner = WorkflowRunner(
            workflow=_workflow(),
            issue_number=99,
            project_root=tmp_path,
            artifacts_dir=tmp_path / ".kaji-artifacts",
            config=_make_config(tmp_path),
        )

        with (
            patch("kaji_harness.runner.execute_cli") as mock_cli,
            patch("kaji_harness.runner.execute_interactive_terminal") as mock_it,
            pytest.raises(WorkflowValidationError) as excinfo,
        ):
            runner.run()

        assert any("not discoverable by Codex" in e for e in excinfo.value.errors)
        mock_cli.assert_not_called()
        mock_it.assert_not_called()


_CODEX_WORKFLOW_YAML = """\
name: t
description: t
execution_policy: auto
steps:
  - id: review
    skill: review
    agent: codex
    on:
      PASS: end
      ABORT: end
"""


@pytest.mark.medium
class TestValidateCommandReportsCodexDiscovery:
    def test_kaji_validate_reports_missing_agents_skill(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_canonical_skill(tmp_path, "review")
        _make_config(tmp_path)
        workflow = tmp_path / "wf.yaml"
        workflow.write_text(_CODEX_WORKFLOW_YAML, encoding="utf-8")

        args = create_parser().parse_args(
            ["validate", str(workflow), "--project-root", str(tmp_path)]
        )
        exit_code = cmd_validate(args)

        captured = capsys.readouterr()
        assert exit_code != 0
        assert "not discoverable by Codex" in captured.out + captured.err

    def test_kaji_validate_passes_with_agents_symlink(self, tmp_path: Path) -> None:
        _write_canonical_skill(tmp_path, "review")
        _link_codex_skill(tmp_path, "review")
        _make_config(tmp_path)
        workflow = tmp_path / "wf.yaml"
        workflow.write_text(_CODEX_WORKFLOW_YAML, encoding="utf-8")

        args = create_parser().parse_args(
            ["validate", str(workflow), "--project-root", str(tmp_path)]
        )

        assert cmd_validate(args) == 0
