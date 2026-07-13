"""Tests for Issue #218: runner backfill / override / capture of worktree state.

mutable label からの再合成を防ぎ、`issue-start` 確定後の worktree/branch を
SessionState 経由で正本として使うことを検証する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.runner import WorkflowRunner
from kaji_harness.skill import SkillMetadata
from kaji_harness.state import SessionState


def _make_config(repo: Path) -> KajiConfig:
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir(parents=True, exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    return KajiConfig._load(cfg)


def _init_local_repo_with_issue(repo: Path, labels: list[str]) -> str:
    """local provider repo + 1 issue を作って issue_id を返す。"""
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init", "-q"],
        check=True,
    )
    from kaji_harness.providers import LocalProvider

    provider = LocalProvider(repo_root=repo, machine_id="pc1")
    issue = provider.create_issue(
        title="resumed workflow worktree bug",
        body="b",
        labels=labels,
        slug="bug-worktree",
    )
    return issue.id


def _verdict(status: str) -> str:
    return (
        f"---VERDICT---\nstatus: {status}\nreason: |\n  ok\n"
        f"evidence: |\n  ok\nsuggestion: |\n  none\n---END_VERDICT---\n"
    )


@pytest.mark.medium
class TestRunnerBackfillAndOverride:
    def test_state_override_wins_over_label_derived_path(self, tmp_path: Path) -> None:
        """事前保存 state.worktree_dir/branch_name が context を override する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        issue_id = _init_local_repo_with_issue(repo, labels=["type:feature"])
        config = _make_config(repo)

        # 事前に SessionState に「過去確定」した worktree を焼き込む
        artifacts_dir = repo / ".kaji" / "artifacts"
        state = SessionState.load_or_create(issue_id, artifacts_dir=artifacts_dir)
        state.capture_worktree(str(tmp_path / f"kaji-chore-{issue_id}"), f"chore/{issue_id}")

        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[Step(id="poll", skill="rp", on={"PASS": "end", "ABORT": "end"})],
        )
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=issue_id,
            project_root=repo,
            artifacts_dir=artifacts_dir,
            config=config,
        )

        captured_env: dict[str, str] = {}

        def fake_execute_script(**kwargs: object) -> CLIResult:
            captured_env.update(kwargs["env"])  # type: ignore[arg-type]
            return CLIResult(full_output=_verdict("PASS"))

        metadata = SkillMetadata(name="rp", description="", exec_script="x")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch("kaji_harness.runner.execute_script", side_effect=fake_execute_script),
        ):
            runner.run()

        # label は type:feature （= feat prefix）だが、state 由来の chore で override される
        assert captured_env["KAJI_BRANCH_NAME"] == f"chore/{issue_id}"
        assert captured_env["KAJI_WORKTREE_DIR"].endswith(f"kaji-chore-{issue_id}")

    def test_backfill_from_physical_worktree(self, tmp_path: Path) -> None:
        """旧 state file（worktree_dir/branch_name 未保存）でも physical worktree から backfill。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        issue_id = _init_local_repo_with_issue(repo, labels=["type:feature"])
        config = _make_config(repo)

        # physical worktree を chore prefix で作る
        wt = tmp_path / f"kaji-chore-{issue_id}"
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-b",
                f"chore/{issue_id}",
                str(wt),
            ],
            check=True,
        )

        # state は空（旧 kaji 版互換シナリオ）
        artifacts_dir = repo / ".kaji" / "artifacts"

        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[Step(id="poll", skill="rp", on={"PASS": "end", "ABORT": "end"})],
        )
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=issue_id,
            project_root=repo,
            artifacts_dir=artifacts_dir,
            config=config,
        )

        captured_env: dict[str, str] = {}

        def fake_execute_script(**kwargs: object) -> CLIResult:
            captured_env.update(kwargs["env"])  # type: ignore[arg-type]
            return CLIResult(full_output=_verdict("PASS"))

        metadata = SkillMetadata(name="rp", description="", exec_script="x")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch("kaji_harness.runner.execute_script", side_effect=fake_execute_script),
        ):
            runner.run()

        assert captured_env["KAJI_BRANCH_NAME"] == f"chore/{issue_id}"
        assert captured_env["KAJI_WORKTREE_DIR"] == str(wt)

        # state にも backfill されている
        state = SessionState.load_or_create(issue_id, artifacts_dir=artifacts_dir)
        assert state.branch_name == f"chore/{issue_id}"
        assert state.worktree_dir == str(wt)

    def test_ambiguous_worktree_emits_abort_verdict(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """multi-candidate 検出 → main loop に入らず ABORT verdict を emit。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        issue_id = _init_local_repo_with_issue(repo, labels=["type:feature"])
        config = _make_config(repo)

        wt1 = tmp_path / f"kaji-chore-{issue_id}"
        wt2 = tmp_path / f"kaji-feat-{issue_id}"
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-b",
                f"chore/{issue_id}",
                str(wt1),
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-b",
                f"feat/{issue_id}",
                str(wt2),
            ],
            check=True,
        )

        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[Step(id="poll", skill="rp", on={"PASS": "end", "ABORT": "end"})],
        )
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=issue_id,
            project_root=repo,
            artifacts_dir=repo / ".kaji" / "artifacts",
            config=config,
        )
        metadata = SkillMetadata(name="rp", description="", exec_script="x")

        exec_called = False

        def fake_exec(**kwargs: object) -> CLIResult:
            nonlocal exec_called
            exec_called = True
            return CLIResult(full_output=_verdict("PASS"))

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch("kaji_harness.runner.execute_script", side_effect=fake_exec),
        ):
            returned_state = runner.run()

        out = capsys.readouterr()
        assert "status: ABORT" in out.out
        assert "multiple worktrees match" in out.out
        assert f"kaji-chore-{issue_id}" in out.err or f"kaji-chore-{issue_id}" in out.out
        assert "git worktree remove" in out.err
        assert exec_called is False

        # cli_main は state.last_transition_verdict.status == "ABORT" を見て
        # EXIT_ABORT を返す。返却 state と再 load 双方で ABORT が永続化されていること。
        assert returned_state.last_transition_verdict is not None
        assert returned_state.last_transition_verdict.status == "ABORT"
        assert "multiple worktrees match" in returned_state.last_transition_verdict.reason
        reloaded = SessionState.load_or_create(issue_id, artifacts_dir=repo / ".kaji" / "artifacts")
        assert reloaded.last_transition_verdict is not None
        assert reloaded.last_transition_verdict.status == "ABORT"

    def test_capture_at_dispatch_when_worktree_appears(self, tmp_path: Path) -> None:
        """新規 run で worktree が physical 存在すれば dispatch 直前に capture される。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        issue_id = _init_local_repo_with_issue(repo, labels=["type:feature"])
        config = _make_config(repo)

        # label-derived path（=feat）に合わせて physical worktree を作る
        wt = tmp_path / f"kaji-feat-{issue_id}"
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-b",
                f"feat/{issue_id}",
                str(wt),
            ],
            check=True,
        )

        artifacts_dir = repo / ".kaji" / "artifacts"
        workflow = Workflow(
            name="t",
            description="",
            execution_policy="auto",
            steps=[Step(id="poll", skill="rp", on={"PASS": "end", "ABORT": "end"})],
        )
        runner = WorkflowRunner(
            workflow=workflow,
            issue_number=issue_id,
            project_root=repo,
            artifacts_dir=artifacts_dir,
            config=config,
        )
        metadata = SkillMetadata(name="rp", description="", exec_script="x")

        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=metadata),
            patch(
                "kaji_harness.runner.execute_script",
                return_value=CLIResult(full_output=_verdict("PASS")),
            ),
        ):
            runner.run()

        # backfill 経路が先に candidate を拾うため、capture / backfill いずれかで state 保存される
        state = SessionState.load_or_create(issue_id, artifacts_dir=artifacts_dir)
        assert state.worktree_dir == str(wt)
        assert state.branch_name == f"feat/{issue_id}"

    def test_ambiguous_worktree_causes_cli_exit_abort(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """多重候補 ABORT が cmd_run 経由で EXIT_ABORT (=1) を返すこと。"""
        from kaji_harness.commands.parser import create_parser
        from kaji_harness.commands.run import cmd_run

        repo = tmp_path / "repo"
        repo.mkdir()
        issue_id = _init_local_repo_with_issue(repo, labels=["type:feature"])
        _make_config(repo)

        # 多重候補 worktree を作る
        for prefix in ("chore", "feat"):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "worktree",
                    "add",
                    "-b",
                    f"{prefix}/{issue_id}",
                    str(tmp_path / f"kaji-{prefix}-{issue_id}"),
                ],
                check=True,
            )

        # workflow validation 通過用に最小 skill を作る（conftest autouse fixture が
        # load_skill_metadata を fake 化し exec_script=None を返すため、step に
        # agent を明示して L2 preflight を通す）
        skill_dir = repo / ".claude" / "skills" / "rp"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: rp\n---\nbody\n")

        wf_path = repo / "wf.yaml"
        wf_path.write_text(
            "name: t\n"
            "description: ''\n"
            "execution_policy: auto\n"
            "requires_provider: any\n"
            "steps:\n"
            "  - id: poll\n"
            "    skill: rp\n"
            "    agent: claude\n"
            "    on: {PASS: end, ABORT: end}\n"
        )

        parser = create_parser()
        parsed = parser.parse_args(
            ["run", str(wf_path), str(issue_id), "--workdir", str(repo), "--quiet"]
        )
        exit_code = cmd_run(parsed)

        assert exit_code == 1  # EXIT_ABORT
        captured = capsys.readouterr()
        assert "ABORT" in captured.err or "aborted" in captured.err.lower()
        assert (
            "multiple worktrees match" in captured.err or "multiple worktrees match" in captured.out
        )
