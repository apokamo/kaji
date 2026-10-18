"""Issue #408 / #489: codex step の skill が Codex の探索パスで解決できることを検証する。

preflight は、実効 workdir から、その workdir が属する Git repo の root まで（両端を含む）の
各階層の ``.agents/skills/<skill>/SKILL.md`` を検証する。Git repo の外では workdir 直下だけを見る。
どこにも無ければ Codex を起動する前にエラーで止める（#408 D2）。

- small: ``resolve_step_workdir`` の優先順位
- medium: ``preflight_workflow`` / ``WorkflowRunner.run()`` / ``kaji validate`` の実ファイル検証。
  Git repo はファイル操作で作った ``.git`` マーカーで表す
- large (large_local): 実 ``git init`` / ``git worktree add`` で作った repo での検証
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


def _link_codex_skill_to(directory: Path, name: str, target: Path) -> Path:
    """``<directory>/.agents/skills/<name>`` を ``target`` への symlink として置く。"""
    link = directory / ".agents" / "skills" / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link


def _write_codex_skill(directory: Path, name: str) -> Path:
    """``<directory>/.agents/skills/<name>/SKILL.md`` を実ファイルとして置く。"""
    skill_dir = directory / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return skill_dir


def _make_repo(root: Path) -> Path:
    """``.git`` ディレクトリを mkdir して、通常の Git repo の root を表す。"""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return root


def _make_worktree_like(root: Path) -> Path:
    """``gitdir:`` を書いた ``.git`` ファイルで、linked worktree の root を表す。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").write_text("gitdir: /nonexistent/.git/worktrees/wt\n", encoding="utf-8")
    return root


def _subdir(root: Path, relative: str = "sub/deeper") -> Path:
    path = root / relative
    path.mkdir(parents=True, exist_ok=True)
    return path


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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """``.git`` マーカーを持つ一時 repo。祖先の実リポジトリ（#407 の TMPDIR）を拾わせない。"""
    return _make_repo(tmp_path / "repo")


@pytest.mark.medium
class TestCodexSkillPreflight:
    def test_missing_agents_skill_is_error(self, repo: Path) -> None:
        """canonical にしか skill がない codex step を preflight が通してはならない。"""
        _write_canonical_skill(repo, "review")

        errors = _preflight(_workflow(), repo)

        assert len(errors) == 1
        message = errors[0]
        assert "Step 'review'" in message
        assert "'codex'" in message
        assert "skill 'review'" in message
        assert "not discoverable by Codex" in message
        assert f"up to the Git repo root {repo.resolve()}" in message
        assert ".agents/skills/review" in message

    def test_symlink_to_canonical_passes(self, repo: Path) -> None:
        _write_canonical_skill(repo, "review")
        _link_codex_skill(repo, "review")

        assert _preflight(_workflow(), repo) == []

    @pytest.mark.parametrize("target", ["step", "workflow"])
    def test_non_string_workdir_returns_structured_error(self, repo: Path, target: str) -> None:
        """手組みの Workflow で workdir が非文字列でも TypeError にせず schema error を返す。"""
        _write_canonical_skill(repo, "review")
        workflow = _workflow()
        if target == "step":
            workflow.steps[0].workdir = 123  # type: ignore[assignment]
        else:
            workflow.workdir = 123  # type: ignore[assignment]

        errors = _preflight(workflow, repo)

        assert any("'workdir' must be a" in e for e in errors)
        assert not any("not discoverable by Codex" in e for e in errors)

    @pytest.mark.parametrize("agent", ["claude", "antigravity"])
    def test_other_agents_do_not_require_agents_skills(self, repo: Path, agent: str) -> None:
        _write_canonical_skill(repo, "review")

        assert _preflight(_workflow(agent=agent), repo) == []

    def test_exec_script_skill_is_exempt(self, repo: Path) -> None:
        _write_canonical_skill(
            repo, "poll", "---\nname: poll\ndescription: d\nexec_script: package.poll\n---\n"
        )

        errors = _preflight(_workflow(skill="poll"), repo)

        assert errors == []

    def test_canonical_failure_is_not_doubled(self, repo: Path) -> None:
        """canonical が無い step は従来の 1 エラーだけ。codex 側のエラーを重ねない。"""
        errors = _preflight(_workflow(skill="missing"), repo)

        assert len(errors) == 1
        assert "not discoverable by Codex" not in errors[0]

    def test_traversal_skill_name_is_rejected_by_canonical_check(self, repo: Path) -> None:
        errors = _preflight(_workflow(skill="../escape"), repo)

        assert len(errors) == 1
        assert "path traversal" in errors[0]


@pytest.mark.medium
class TestCodexDiscoveryWithinGitRepo:
    """Issue #489: workdir から Git repo root まで（両端を含む）の各階層を探索する。"""

    def test_repo_root_skill_is_found_from_subdirectory(self, repo: Path) -> None:
        """再現テスト: repo root の skill は workdir = repo/sub/deeper から見つかる。"""
        _write_canonical_skill(repo, "review")
        _link_codex_skill(repo, "review")
        workdir = _subdir(repo)

        assert _preflight(_workflow(step_workdir=str(workdir)), repo) == []

    def test_intermediate_directory_skill_is_found(self, repo: Path) -> None:
        _write_canonical_skill(repo, "review")
        _write_codex_skill(repo / "sub", "review")
        workdir = _subdir(repo)

        assert _preflight(_workflow(step_workdir=str(workdir)), repo) == []

    def test_intermediate_symlink_to_repo_root_canonical_is_allowed(self, repo: Path) -> None:
        """symlink の境界は候補ディレクトリではなく repo root。"""
        _write_canonical_skill(repo, "review")
        _link_codex_skill_to(repo / "sub", "review", Path("../../../.claude/skills/review"))
        workdir = _subdir(repo)

        assert _preflight(_workflow(step_workdir=str(workdir)), repo) == []

    def test_worktree_shaped_root_is_the_repo_root(self, tmp_path: Path) -> None:
        """``.git`` がファイル（linked worktree / submodule）でも repo root とみなす。"""
        worktree = _make_worktree_like(tmp_path / "wt")
        _write_canonical_skill(worktree, "review")
        _link_codex_skill(worktree, "review")
        workdir = _subdir(worktree)

        assert _preflight(_workflow(step_workdir=str(workdir)), worktree) == []

    def test_other_repo_outside_project_root_uses_its_own_root(self, tmp_path: Path) -> None:
        project = _make_repo(tmp_path / "project")
        _write_canonical_skill(project, "review")
        other = _make_repo(tmp_path / "other")
        _write_codex_skill(other, "review")
        workdir = _subdir(other, "sub")

        assert _preflight(_workflow(step_workdir=str(workdir)), project) == []

    def test_skill_above_repo_root_is_error(self, outside_project_tmp_path: Path) -> None:
        """repo root より上（Git repo 外の親）は候補にしない。Codex は注入しない（fail-open）。"""
        outer = outside_project_tmp_path / "outer"
        inner = _make_repo(outer / "inner")
        _write_canonical_skill(inner, "review")
        _write_codex_skill(outer, "review")
        workdir = _subdir(inner, "sub")

        errors = _preflight(_workflow(step_workdir=str(workdir)), inner)

        assert len(errors) == 1
        assert "not discoverable by Codex" in errors[0]

    def test_skill_nowhere_in_range_is_error(self, repo: Path) -> None:
        _write_canonical_skill(repo, "review")
        workdir = _subdir(repo)

        errors = _preflight(_workflow(step_workdir=str(workdir)), repo)

        assert len(errors) == 1
        message = errors[0]
        assert "not discoverable by Codex" in message
        assert f"from {workdir.resolve()} up to the Git repo root {repo.resolve()}" in message

    def test_project_root_skill_does_not_count_for_other_repo(self, tmp_path: Path) -> None:
        """``project_root`` は境界にも候補にも使わない。"""
        project = _make_repo(tmp_path / "project")
        _write_canonical_skill(project, "review")
        _link_codex_skill(project, "review")
        other = _make_repo(tmp_path / "other")
        workdir = _subdir(other, "sub")

        errors = _preflight(_workflow(step_workdir=str(workdir)), project)

        assert len(errors) == 1
        assert f"up to the Git repo root {other.resolve()}" in errors[0]

    def test_nested_repo_stops_at_inner_root(self, tmp_path: Path) -> None:
        outer = _make_repo(tmp_path / "outer")
        inner = _make_repo(outer / "inner")
        _write_canonical_skill(inner, "review")
        _write_codex_skill(outer, "review")
        workdir = _subdir(inner, "sub")

        errors = _preflight(_workflow(step_workdir=str(workdir)), inner)

        assert len(errors) == 1
        assert f"up to the Git repo root {inner.resolve()}" in errors[0]

    def test_symlinked_workdir_uses_the_real_path_repo(self, tmp_path: Path) -> None:
        """workdir の実体が別 repo にあれば、実体側の repo root まで探索する。"""
        project = _make_repo(tmp_path / "project")
        _write_canonical_skill(project, "review")
        other = _make_repo(tmp_path / "other")
        _write_codex_skill(other, "review")
        real = _subdir(other, "sub")
        workdir = project / "linked"
        workdir.symlink_to(real)

        assert _preflight(_workflow(step_workdir=str(workdir)), project) == []

    def test_symlink_to_other_directory_inside_repo_is_allowed(self, repo: Path) -> None:
        _write_canonical_skill(repo, "review")
        tools_skill = repo / "tools" / "skills" / "review"
        tools_skill.mkdir(parents=True)
        (tools_skill / "SKILL.md").write_text("# review\n", encoding="utf-8")
        _link_codex_skill_to(repo, "review", Path("../../tools/skills/review"))
        workdir = _subdir(repo)

        assert _preflight(_workflow(step_workdir=str(workdir)), repo) == []

    def test_symlink_escaping_repo_is_error(self, tmp_path: Path) -> None:
        root = _make_repo(tmp_path / "repo")
        outside = tmp_path / "outside" / "review"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("# review\n", encoding="utf-8")
        _write_canonical_skill(root, "review")
        _link_codex_skill_to(root, "review", outside)

        errors = _preflight(_workflow(), root)

        assert len(errors) == 1
        assert "not discoverable by Codex" in errors[0]
        assert "escapes workdir" in errors[0]
        assert f"inside the Git repo root {root.resolve()}" in errors[0]

    def test_escaping_candidate_fails_even_with_a_valid_one(self, tmp_path: Path) -> None:
        """範囲内の同名候補が 1 つでも repo 外を指せば、正しい候補があっても不合格。"""
        root = _make_repo(tmp_path / "repo")
        outside = tmp_path / "outside" / "review"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("# review\n", encoding="utf-8")
        _write_canonical_skill(root, "review")
        _link_codex_skill(root, "review")
        _link_codex_skill_to(root / "sub", "review", outside)
        workdir = _subdir(root)

        errors = _preflight(_workflow(step_workdir=str(workdir)), root)

        assert len(errors) == 1
        assert "escapes workdir" in errors[0]


@pytest.mark.medium
class TestCodexDiscoveryOutsideGitRepo:
    """Git repo の外では現行どおり workdir 直下だけを見る。"""

    def test_step_workdir_is_the_discovery_root(self, outside_project_tmp_path: Path) -> None:
        root = outside_project_tmp_path
        sub = root / "sub"
        sub.mkdir()
        _write_canonical_skill(root, "review")
        _link_codex_skill(root, "review")  # 親（project_root 側）にはある

        missing = _preflight(_workflow(step_workdir=str(sub)), root)

        assert len(missing) == 1
        assert str(sub / ".agents" / "skills" / "review" / "SKILL.md") in missing[0]
        assert "<workdir>/.agents/skills" in missing[0]

        # workdir 側に実体を置けば通る（canonical は project_root 基準のまま）
        _write_codex_skill(sub, "review")
        assert _preflight(_workflow(step_workdir=str(sub)), root) == []

    def test_symlink_escaping_workdir_is_error(self, outside_project_tmp_path: Path) -> None:
        root = outside_project_tmp_path / "project"
        outside = outside_project_tmp_path / "outside" / "review"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("# review\n", encoding="utf-8")
        _write_canonical_skill(root, "review")
        _link_codex_skill_to(root, "review", outside)

        errors = _preflight(_workflow(), root)

        assert len(errors) == 1
        assert "escapes workdir" in errors[0]


def _make_config(tmp_path: Path) -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
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


def _git(*args: str | Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=kaji-test", "-c", "user.email=kaji-test@example.invalid"]
        + [str(a) for a in args],
        check=True,
        capture_output=True,
    )


def _codex_workflow_yaml(workdir: Path) -> str:
    return _CODEX_WORKFLOW_YAML.replace(
        "    agent: codex\n", f"    agent: codex\n    workdir: {workdir}\n"
    )


def _validate(workflow_path: Path, project_root: Path) -> int:
    args = create_parser().parse_args(
        ["validate", str(workflow_path), "--project-root", str(project_root)]
    )
    return cmd_validate(args)


@pytest.mark.large
@pytest.mark.large_local
class TestCodexDiscoveryWithRealGit:
    """Issue #489: 実 ``git`` で作った repo / worktree での探索範囲（外部ネットワーク・実 Codex なし）。"""

    def test_kaji_validate_finds_repo_root_skill_from_subdirectory(self, tmp_path: Path) -> None:
        """再現テスト（Issue 再現手順 4 の CLI 経路）。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_config(repo)
        _write_canonical_skill(repo, "review")
        _link_codex_skill(repo, "review")
        workdir = _subdir(repo)
        workflow = repo / "wf.yaml"
        workflow.write_text(_codex_workflow_yaml(workdir), encoding="utf-8")

        assert _validate(workflow, repo) == 0

    def test_linked_worktree_root_skill_is_found(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", "-q", "--initial-branch=main", repo)
        _write_canonical_skill(repo, "review")
        _link_codex_skill(repo, "review")
        _git("-C", repo, "add", ".")
        _git("-C", repo, "commit", "-q", "-m", "skills")
        worktree = tmp_path / "wt"
        _git("-C", repo, "worktree", "add", "-q", "-b", "wt", worktree)
        assert (worktree / ".git").is_file()
        workdir = _subdir(worktree)

        assert _preflight(_workflow(step_workdir=str(workdir)), worktree) == []

    def test_kaji_validate_uses_other_repo_root(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        _make_config(project)
        _write_canonical_skill(project, "review")
        other = tmp_path / "other"
        _git("init", "-q", "--initial-branch=main", other)
        _write_codex_skill(other, "review")
        workdir = _subdir(other, "sub")
        workflow = project / "wf.yaml"
        workflow.write_text(_codex_workflow_yaml(workdir), encoding="utf-8")

        assert _validate(workflow, project) == 0

    def test_kaji_validate_reports_skill_missing_in_range(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_config(repo)
        _write_canonical_skill(repo, "review")
        workdir = _subdir(repo)
        workflow = repo / "wf.yaml"
        workflow.write_text(_codex_workflow_yaml(workdir), encoding="utf-8")

        exit_code = _validate(workflow, repo)

        captured = capsys.readouterr()
        assert exit_code != 0
        assert "not discoverable by Codex" in captured.out + captured.err
