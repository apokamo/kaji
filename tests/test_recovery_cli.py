"""Tests for the recovery-related CLI surface (Issue #288).

Small: ``kaji run`` の新 flag parsing / ``_apply_execution_overrides`` の precedence。
Medium: ``[execution] failure_triage`` / ``auto_recover`` の config validation、
``cmd_run`` 終端での handler 起動と exit code 不変、``kaji recover`` の入口検証。
"""

from __future__ import annotations

import json
import subprocess as _sp
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.cli_main import (
    EXIT_DEFINITION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    _apply_execution_overrides,
    create_parser,
    main,
)
from kaji_harness.config import ExecutionConfig, KajiConfig, PathsConfig
from kaji_harness.errors import ConfigLoadError, StepTimeoutError
from kaji_harness.recovery.models import RECOVERY_FILE

_ISSUE = "local-pc1-99"


def _parse(argv: list[str]):
    return create_parser().parse_args(["run", "wf.yaml", "1", *argv])


def _config(*, failure_triage: bool = True, auto_recover: bool = False) -> KajiConfig:
    return KajiConfig(
        repo_root=Path("/repo"),
        paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(
            default_timeout=1800,
            failure_triage=failure_triage,
            auto_recover=auto_recover,
        ),
    )


# ============================================================
# Small: flag parsing / override precedence
# ============================================================


@pytest.mark.small
class TestRecoveryFlagParsing:
    def test_defaults_are_none(self) -> None:
        args = _parse([])
        assert args.failure_triage is None
        assert args.auto_recover is None
        assert args.recovery_root is None
        assert args.recovery_parent is None

    def test_triage_flags_are_three_state(self) -> None:
        assert _parse(["--failure-triage"]).failure_triage is True
        assert _parse(["--no-failure-triage"]).failure_triage is False

    def test_auto_recover_flags_are_three_state(self) -> None:
        assert _parse(["--auto-recover"]).auto_recover is True
        assert _parse(["--no-auto-recover"]).auto_recover is False

    def test_triage_flags_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            _parse(["--failure-triage", "--no-failure-triage"])

    def test_chain_flags_are_parsed(self) -> None:
        args = _parse(["--recovery-root", "r1", "--recovery-parent", "p1"])
        assert args.recovery_root == "r1"
        assert args.recovery_parent == "p1"


@pytest.mark.small
class TestRecoveryOverridePrecedence:
    def test_cli_flag_overrides_config(self) -> None:
        merged = _apply_execution_overrides(
            _config(failure_triage=True, auto_recover=False), _parse(["--auto-recover"])
        )
        assert merged.execution.auto_recover is True
        merged = _apply_execution_overrides(
            _config(failure_triage=True, auto_recover=False), _parse(["--no-failure-triage"])
        )
        assert merged.execution.failure_triage is False

    def test_unspecified_flags_keep_config_values(self) -> None:
        args = _parse([])
        merged = _apply_execution_overrides(_config(failure_triage=True, auto_recover=True), args)
        assert merged.execution.failure_triage is True
        assert merged.execution.auto_recover is True

    def test_config_level_triage_off_also_disables_auto_recover(self) -> None:
        args = _parse([])
        merged = _apply_execution_overrides(_config(failure_triage=False, auto_recover=True), args)
        assert merged.execution.auto_recover is False

    def test_auto_recover_is_disabled_when_triage_disabled(self) -> None:
        args = _parse(["--no-failure-triage", "--auto-recover"])
        merged = _apply_execution_overrides(_config(), args)
        assert merged.execution.failure_triage is False
        # triage 無効時は auto_recover を有効にできない（handler 自体が起動しないため）。
        assert merged.execution.auto_recover is False


# ============================================================
# Medium: config validation
# ============================================================


def _write_config(tmp_path: Path, execution_extra: str = "") -> Path:
    kaji = tmp_path / ".kaji"
    kaji.mkdir(parents=True, exist_ok=True)
    path = kaji / "config.toml"
    path.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        f"[execution]\ndefault_timeout = 1800\n{execution_extra}\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    return path


@pytest.mark.medium
class TestRecoveryConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        config = KajiConfig._load(_write_config(tmp_path))
        assert config.execution.failure_triage is True
        assert config.execution.auto_recover is False

    def test_values_are_read(self, tmp_path: Path) -> None:
        config = KajiConfig._load(
            _write_config(tmp_path, "failure_triage = false\nauto_recover = true\n")
        )
        assert config.execution.failure_triage is False
        assert config.execution.auto_recover is True

    @pytest.mark.parametrize("key", ["failure_triage", "auto_recover"])
    def test_non_bool_rejected(self, tmp_path: Path, key: str) -> None:
        with pytest.raises(ConfigLoadError, match=f"execution.{key} must be a boolean"):
            KajiConfig._load(_write_config(tmp_path, f'{key} = "yes"\n'))

    def test_overlay_overrides_tracked_value(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path)
        (tmp_path / ".kaji" / "config.local.toml").write_text("[execution]\nauto_recover = true\n")
        assert KajiConfig._load(path).execution.auto_recover is True


# ============================================================
# Medium: cmd_run 終端 / kaji recover
# ============================================================


def _repo(tmp_path: Path, execution_extra: str = "") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _sp.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    _write_config(repo, execution_extra)
    (repo / "wf.yaml").write_text(
        "name: single\ndescription: d\nrequires_provider: any\nexecution_policy: auto\n\n"
        "steps:\n"
        "  - id: implement\n"
        "    skill: issue-implement\n"
        "    agent: claude\n"
        "    on:\n"
        "      PASS: end\n"
        "      ABORT: end\n"
    )
    from kaji_harness.providers import LocalProvider

    counter = repo / ".kaji" / "counters" / "pc1.txt"
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text("98")
    LocalProvider(repo_root=repo, machine_id="pc1").create_issue(
        title="t", body="b", labels=["type:feature"], slug="t"
    )
    # safety gate（worktree 実在 + branch 一致）を満たすため、repo 自身を worktree として
    # state に焼き込む。これを省くと triage が常に worktree_unavailable で止まる。
    state_dir = repo / ".kaji-artifacts" / _ISSUE
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "session-state.json").write_text(
        json.dumps(
            {
                "issue_number": _ISSUE,
                "sessions": {},
                "step_history": [],
                "cycle_counts": {},
                "last_completed_step": None,
                "last_transition_verdict": None,
                "worktree_dir": str(repo),
                "branch_name": "main",
            }
        ),
        encoding="utf-8",
    )
    return repo


def _run_dir(repo: Path) -> Path:
    runs = repo / ".kaji-artifacts" / _ISSUE / "runs"
    return sorted(p for p in runs.iterdir() if p.is_dir())[-1]


@pytest.mark.medium
class TestCmdRunTriage:
    def test_triage_runs_on_failure_and_keeps_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=StepTimeoutError("implement", 5)),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            rc = main(["run", str(repo / "wf.yaml"), "99", "--workdir", str(repo)])

        assert rc == EXIT_RUNTIME_ERROR
        err = capsys.readouterr().err
        assert "--- failure triage ---" in err
        assert (_run_dir(repo) / RECOVERY_FILE).exists()

    def test_no_failure_triage_flag_skips_handler(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=StepTimeoutError("implement", 5)),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            rc = main(
                [
                    "run",
                    str(repo / "wf.yaml"),
                    "99",
                    "--workdir",
                    str(repo),
                    "--no-failure-triage",
                ]
            )

        assert rc == EXIT_RUNTIME_ERROR
        assert "--- failure triage ---" not in capsys.readouterr().err
        assert not (_run_dir(repo) / RECOVERY_FILE).exists()

    def test_run_dir_absent_failure_skips_handler(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        rc = main(["run", str(repo / "wf.yaml"), "99", "--from", "ghost", "--workdir", str(repo)])
        assert rc == EXIT_DEFINITION_ERROR
        assert "--- failure triage ---" not in capsys.readouterr().err

    def test_successful_run_does_not_trigger_triage(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from kaji_harness.models import CLIResult, CostInfo

        repo = _repo(tmp_path)
        verdict = "---VERDICT---\nstatus: PASS\nreason: r\nevidence: e\nsuggestion: s\n---END_VERDICT---\n"
        result = CLIResult(full_output=verdict, session_id="s", cost=CostInfo(usd=0.0), exit_code=0)
        with (
            patch("kaji_harness.runner.execute_cli", return_value=result),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            rc = main(["run", str(repo / "wf.yaml"), "99", "--workdir", str(repo)])

        assert rc == EXIT_OK
        assert "--- failure triage ---" not in capsys.readouterr().err
        assert not (_run_dir(repo) / RECOVERY_FILE).exists()

    def test_recovery_parent_without_root_is_definition_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        rc = main(
            [
                "run",
                str(repo / "wf.yaml"),
                "99",
                "--workdir",
                str(repo),
                "--recovery-parent",
                "260710110000",
            ]
        )
        assert rc == EXIT_DEFINITION_ERROR
        assert "--recovery-parent requires --recovery-root" in capsys.readouterr().err


@pytest.mark.medium
class TestCmdRecover:
    def _failed_run(self, repo: Path) -> Path:
        with (
            patch("kaji_harness.runner.execute_cli", side_effect=StepTimeoutError("implement", 5)),
            patch("kaji_harness.runner.validate_skill_exists"),
        ):
            main(
                [
                    "run",
                    str(repo / "wf.yaml"),
                    "99",
                    "--workdir",
                    str(repo),
                    "--no-failure-triage",
                ]
            )
        return _run_dir(repo)

    def test_recover_uses_latest_run_when_run_id_omitted(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        run_dir = self._failed_run(repo)

        rc = main(["recover", str(repo / "wf.yaml"), "99", "--workdir", str(repo)])

        assert rc == EXIT_OK
        decision = json.loads((run_dir / RECOVERY_FILE).read_text(encoding="utf-8"))
        assert decision["classification"]["cause"] == "dispatch_failure"
        assert decision["decision"] == "comment_only"

    def test_recover_with_explicit_run_id(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        run_dir = self._failed_run(repo)

        rc = main(
            [
                "recover",
                str(repo / "wf.yaml"),
                "99",
                "--run-id",
                run_dir.name,
                "--workdir",
                str(repo),
            ]
        )
        assert rc == EXIT_OK

    def test_recover_rejects_unknown_run_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        self._failed_run(repo)
        rc = main(
            ["recover", str(repo / "wf.yaml"), "99", "--run-id", "999", "--workdir", str(repo)]
        )
        assert rc == EXIT_INVALID_INPUT
        assert "run dir not found" in capsys.readouterr().err

    def test_recover_rejects_in_progress_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        runs = repo / ".kaji-artifacts" / _ISSUE / "runs"
        runs.mkdir(parents=True)
        (runs / "260710120000").mkdir()
        (runs / "260710120000" / "run.log").write_text(
            json.dumps({"event": "workflow_start", "issue": _ISSUE, "workflow": "s"}) + "\n"
        )
        rc = main(["recover", str(repo / "wf.yaml"), "99", "--workdir", str(repo)])
        assert rc == EXIT_INVALID_INPUT
        assert "still in progress" in capsys.readouterr().err

    def test_recover_rejects_missing_runs_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _repo(tmp_path)
        rc = main(["recover", str(repo / "wf.yaml"), "99", "--workdir", str(repo)])
        assert rc == EXIT_INVALID_INPUT
        assert "no runs found" in capsys.readouterr().err
