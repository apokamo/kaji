"""Tests for kaji_harness.cli_main — kaji run CLI entrypoint."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import cmd_run, create_parser, main
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.errors import (
    CLIExecutionError,
    CLINotFoundError,
    HarnessError,
    InvalidTransition,
    InvalidVerdictValue,
    MissingResumeSessionError,
    SecurityError,
    SkillNotFound,
    StepTimeoutError,
    VerdictNotFound,
    VerdictParseError,
    WorkflowValidationError,
)
from kaji_harness.models import Verdict


def _stub_github_config() -> KajiConfig:
    """Return a minimal github-provider KajiConfig for `_handle_pr` test isolation.

    Used by autouse fixtures in PR builtin test classes to bypass CWD-based
    config discovery (which picks up ``.kaji/config.local.toml`` overlay
    in main checkout and triggers bare-provider early-exit). See
    issue local-pc5090-15.
    """
    return KajiConfig(
        repo_root=Path("/tmp/stub"),
        paths=PathsConfig(skill_dir=".claude/skills", artifacts_dir=".kaji/artifacts"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="github",
            local=LocalProviderConfig(),
            github=GitHubProviderConfig(repo="owner/repo"),
        ),
    )


# ============================================================
# Fixtures
# ============================================================

MINIMAL_WORKFLOW_YAML = """\
name: test
description: test workflow
execution_policy: auto
steps:
  - id: step1
    skill: test-skill
    agent: claude
    on:
      PASS: end
      ABORT: end
"""


@pytest.fixture()
def workflow_file(tmp_path: Path) -> Path:
    """Create a minimal valid workflow YAML file."""
    p = tmp_path / "workflow.yaml"
    p.write_text(MINIMAL_WORKFLOW_YAML)
    return p


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """Create a temporary working directory with .kaji/config.toml."""
    d = tmp_path / "workdir"
    d.mkdir()
    config_dir = d / ".kaji"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    # gl:21: provider.type='local' requires a git repo.
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(d)], check=True)
    return d


# ============================================================
# Small tests — argument parsing
# ============================================================


class TestParserSmall:
    """Small: argparse argument parsing."""

    @pytest.mark.small
    def test_run_subcommand_basic_args(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "workflow.yaml", "42"])
        assert args.workflow == Path("workflow.yaml")
        assert args.issue == "42"
        assert args.from_step is None
        assert args.single_step is None
        assert args.quiet is False

    @pytest.mark.small
    def test_run_subcommand_all_options(self, tmp_path: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(
            [
                "run",
                "w.yaml",
                "99",
                "--from",
                "step-a",
                "--workdir",
                str(tmp_path),
                "--quiet",
            ]
        )
        assert args.from_step == "step-a"
        assert args.workdir == Path(str(tmp_path))
        assert args.quiet is True

    @pytest.mark.small
    def test_run_subcommand_step_option(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "w.yaml", "1", "--step", "impl"])
        assert args.single_step == "impl"

    @pytest.mark.small
    def test_no_subcommand_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([])
        assert exc_info.value.code != 0


# ============================================================
# Small tests — --from / --step mutual exclusion
# ============================================================


class TestMutualExclusionSmall:
    """Small: --from and --step are mutually exclusive."""

    @pytest.mark.small
    def test_from_and_step_exclusive(self, workflow_file: Path, workdir: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--from",
            "a",
            "--step",
            "b",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2

    @pytest.mark.small
    def test_from_alone_is_valid(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--from",
                "step1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0

    @pytest.mark.small
    def test_step_alone_is_valid(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--step",
                "step1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0


# ============================================================
# Small tests — --workdir validation
# ============================================================


class TestWorkdirValidationSmall:
    """Small: --workdir pre-validation."""

    @pytest.mark.small
    def test_nonexistent_workdir(self, workflow_file: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            "/nonexistent/path/abc",
        )
        assert exit_code == 2

    @pytest.mark.small
    def test_file_as_workdir(self, workflow_file: Path) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            str(workflow_file),  # file, not dir
        )
        assert exit_code == 2


# ============================================================
# Small tests — exit code mapping
# ============================================================


class TestExitCodeMappingSmall:
    """Small: exception → exit code mapping."""

    @pytest.mark.small
    @pytest.mark.parametrize(
        "exception,expected_code",
        [
            (WorkflowValidationError("bad"), 2),
            (SkillNotFound("missing"), 2),
            (SecurityError("traversal"), 2),
            (CLIExecutionError("s", 1, "err"), 3),
            (CLINotFoundError("not found"), 3),
            (StepTimeoutError("s", 30), 3),
            (MissingResumeSessionError("s", "t"), 3),
            (InvalidTransition("s", "v"), 3),
            (VerdictNotFound("no verdict"), 3),
            (VerdictParseError("bad parse"), 3),
            (InvalidVerdictValue("bad value"), 3),
        ],
        ids=[
            "WorkflowValidationError",
            "SkillNotFound",
            "SecurityError",
            "CLIExecutionError",
            "CLINotFoundError",
            "StepTimeoutError",
            "MissingResumeSessionError",
            "InvalidTransition",
            "VerdictNotFound",
            "VerdictParseError",
            "InvalidVerdictValue",
        ],
    )
    def test_harness_error_exit_codes(
        self,
        workflow_file: Path,
        workdir: Path,
        exception: HarnessError,
        expected_code: int,
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.side_effect = exception
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == expected_code

    @pytest.mark.small
    def test_unexpected_exception_exit_code(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.side_effect = RuntimeError("boom")
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 1

    @pytest.mark.small
    def test_abort_verdict_exit_code(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("ABORT", "reason", "ev", "sug")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 1


# ============================================================
# Medium tests — integration with file I/O and mocked runner
# ============================================================


class TestCmdRunMedium:
    """Medium: cmd_run with real files + mocked WorkflowRunner."""

    @pytest.mark.medium
    def test_successful_run(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "done", "all good", "")
            )
            # Phase 3-d preflight § 1: canonical_issue_ref 未設定で raw fallback 経路。
            mock_runner.return_value.canonical_issue_ref = None
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "42",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "42" in captured.out  # issue number in summary

    @pytest.mark.medium
    def test_invalid_yaml_exit_2(
        self, tmp_path: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("steps: not_a_list")
        exit_code = cmd_run_with_args(
            str(bad_yaml),
            "1",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.err  # error message in stderr

    @pytest.mark.medium
    def test_nonexistent_workflow_file(
        self, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cmd_run_with_args(
            "/no/such/file.yaml",
            "1",
            "--workdir",
            str(workdir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "No such file" in captured.err

    @pytest.mark.medium
    def test_cli_execution_error_exit_3(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.side_effect = CLIExecutionError("s1", 1, "fail")
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 3
        captured = capsys.readouterr()
        assert captured.err

    @pytest.mark.medium
    def test_abort_verdict_exit_1(
        self, workflow_file: Path, workdir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("ABORT", "blocked", "ev", "sug")
            )
            exit_code = cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
            )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "ABORT" in captured.err or "blocked" in captured.err

    @pytest.mark.medium
    def test_workdir_nonexistent_exit_2_with_message(
        self, workflow_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            "/nonexistent/dir",
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "workdir" in captured.err.lower() or "directory" in captured.err.lower()

    @pytest.mark.medium
    def test_quiet_flag_passed_to_runner(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            cmd_run_with_args(
                str(workflow_file),
                "1",
                "--workdir",
                str(workdir),
                "--quiet",
            )
            call_kwargs = mock_runner.call_args
            assert call_kwargs.kwargs.get("verbose") is False or (
                len(call_kwargs.args) > 0 and not call_kwargs.kwargs.get("verbose", True)
            )

    @pytest.mark.medium
    def test_config_not_found_exit_2(
        self, workflow_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_run exits 2 when .kaji/config.toml is missing."""
        no_config_dir = tmp_path / "no-config"
        no_config_dir.mkdir()
        exit_code = cmd_run_with_args(
            str(workflow_file),
            "1",
            "--workdir",
            str(no_config_dir),
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert ".kaji/config.toml" in captured.err


class TestMainMedium:
    """Medium: main() function integration."""

    @pytest.mark.medium
    def test_main_returns_exit_code(self, workflow_file: Path, workdir: Path) -> None:
        with patch("kaji_harness.cli_main.WorkflowRunner") as mock_runner:
            mock_runner.return_value.run.return_value = MagicMock(
                last_transition_verdict=Verdict("PASS", "", "", "")
            )
            exit_code = main(
                [
                    "run",
                    str(workflow_file),
                    "1",
                    "--workdir",
                    str(workdir),
                ]
            )
        assert exit_code == 0

    @pytest.mark.medium
    def test_help_output(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "run", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "workflow" in result.stdout.lower()
        assert "issue" in result.stdout.lower()


# ============================================================
# Large tests — real subprocess execution
# ============================================================


class TestCLILarge:
    """Large: real subprocess execution of kaji CLI."""

    @pytest.mark.large
    def test_kaji_entrypoint_help(self) -> None:
        """The `kaji` console script entrypoint should be functional."""
        import shutil

        kaji_path = shutil.which("kaji")
        if kaji_path is None:
            pytest.skip("kaji entrypoint not installed (run pip install -e .)")
        result = subprocess.run(
            ["kaji", "run", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "--from" in result.stdout
        assert "--step" in result.stdout
        assert "--workdir" in result.stdout
        assert "--quiet" in result.stdout

    @pytest.mark.large
    def test_kaji_run_with_valid_workflow_missing_agent_cli(
        self,
        tmp_path: Path,
    ) -> None:
        """With a valid workflow, skill, and config, missing agent CLI should yield exit 3."""
        # Create workflow YAML referencing a skill
        wf = tmp_path / "workflow.yaml"
        wf.write_text(MINIMAL_WORKFLOW_YAML)

        # Create project dir with config and skill
        workdir = tmp_path / "project"
        workdir.mkdir()
        # gl:21: provider.type='local' requires a git repo.
        subprocess.run(
            ["git", "init", "-q", "--initial-branch=main", str(workdir)],
            check=True,
        )
        config_dir = workdir / ".kaji"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
            "[execution]\ndefault_timeout = 1800\n\n"
            '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
        )
        skill_dir = workdir / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test Skill\n")

        # Phase 3-e: provider=local の subprocess kaji run は対象 issue dir 要。
        from tests.conftest import ensure_local_issue

        ensure_local_issue(workdir, "999")

        # Restrict PATH to only the Python executable's directory so that
        # agent CLIs (claude, codex, gemini) are guaranteed not to be found.
        python_dir = str(Path(sys.executable).parent)
        git_dir = str(Path(__import__("shutil").which("git") or "/usr/bin/git").parent)
        env = {**__import__("os").environ, "PATH": f"{python_dir}:{git_dir}"}

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "run",
                str(wf),
                "999",
                "--workdir",
                str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        # Should fail with exit 3 (runtime error) because the agent CLI
        # (claude) cannot be found on the restricted PATH.
        assert result.returncode == 3
        assert "not found" in result.stderr.lower()


# ============================================================
# Phase 1: kaji issue / kaji pr passthrough wrappers
# ============================================================


@pytest.mark.small
class TestIssuePrPassthrough:
    """`kaji issue` / `kaji pr` は `gh` に引数を転送する Phase 1 wrapper。"""

    def test_run_issue_accepts_local_id(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "wf.yaml", "local-pc1-1"])
        assert args.issue == "local-pc1-1"

    def test_issue_subcommand_forwards_to_gh(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["issue", "view", "153", "--json", "title"])
        assert args.command == "issue"
        # REMAINDER は先頭の `--` を除き、user 入力をそのまま保つ
        assert args.args == ["view", "153", "--json", "title"]

    def test_pr_subcommand_forwards_to_gh(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["pr", "create", "--base", "main", "--title", "x"])
        assert args.command == "pr"
        assert args.args == ["create", "--base", "main", "--title", "x"]

    def test_pr_merge_strips_method_flags_and_forces_no_ff(self) -> None:
        """`pr merge` は --merge / --squash / --rebase を露出せず、内部で --merge 固定。"""
        from kaji_harness.cli_main import _forward_to_gh

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _forward_to_gh("pr", ["merge", "feat/153", "--squash"])
            cmd = mock_run.call_args[0][0]
            assert cmd[:3] == ["gh", "pr", "merge"]
            assert "--squash" not in cmd
            assert cmd.count("--merge") == 1
            assert cmd[-1] == "--merge"

    def test_forward_returns_error_when_gh_missing(self) -> None:
        from kaji_harness.cli_main import _forward_to_gh

        with patch("kaji_harness.cli_main.shutil.which", return_value=None):
            rc = _forward_to_gh("issue", ["view", "1"])
            assert rc != 0


# ============================================================
# Phase 2-A: kaji pr review-comments / reviews / reply-to-comment
# ============================================================


@pytest.mark.small
class TestComposeJsonAndJq:
    """`_compose_json_and_jq` — `--json FIELDS` + `--jq EXPR` の合成。"""

    def test_fields_only_produces_projection(self) -> None:
        from kaji_harness.cli_main import _compose_json_and_jq

        assert (
            _compose_json_and_jq(["title", "body"], None) == "[.[] | {title: .title, body: .body}]"
        )

    def test_jq_only_passes_through(self) -> None:
        from kaji_harness.cli_main import _compose_json_and_jq

        assert _compose_json_and_jq(None, ".[]") == ".[]"

    def test_both_chains_projection_then_user_jq(self) -> None:
        from kaji_harness.cli_main import _compose_json_and_jq

        assert _compose_json_and_jq(["id", "body"], ".[]") == "[.[] | {id: .id, body: .body}] | .[]"

    def test_neither_returns_none(self) -> None:
        from kaji_harness.cli_main import _compose_json_and_jq

        assert _compose_json_and_jq(None, None) is None


@pytest.mark.small
class TestPrReviewCommentsBuiltin:
    """`kaji pr review-comments` の argv 組み立てと異常系。"""

    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kaji_harness.cli_main._load_config_for_dispatch",
            _stub_github_config,
        )

    def _patches(self, repo: str | None = "owner/repo"):
        which = patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh")
        detect = patch("kaji_harness.cli_main._detect_repo", return_value=repo)
        run = patch("kaji_harness.cli_main.subprocess.run")
        return which, detect, run

    def test_argv_contains_repo_path_and_composed_jq(self) -> None:
        from kaji_harness.cli_main import _handle_pr

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["review-comments", "153", "--json", "id,body", "--jq", ".[]"])
            assert rc == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[:2] == ["gh", "api"]
            assert cmd[2] == "repos/owner/repo/pulls/153/comments"
            assert "--jq" in cmd
            assert cmd[cmd.index("--jq") + 1] == "[.[] | {id: .id, body: .body}] | .[]"

    def test_argv_omits_jq_when_neither_flag(self) -> None:
        from kaji_harness.cli_main import _handle_pr

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _handle_pr(["review-comments", "153"])
            cmd = mock_run.call_args[0][0]
            assert "--jq" not in cmd
            assert cmd[-1] == "repos/owner/repo/pulls/153/comments"

    def test_non_numeric_pr_id_returns_invalid_input(self) -> None:
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _handle_pr

        rc = _handle_pr(["review-comments", "abc"])
        assert rc == EXIT_INVALID_INPUT

    def test_unicode_digit_pr_id_rejected(self) -> None:
        """Unicode 全角数字 ``１２３`` は str.isdigit() を通すが ASCII でないため拒否。"""
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _handle_pr

        rc = _handle_pr(["review-comments", "１２３"])
        assert rc == EXIT_INVALID_INPUT

    def test_empty_json_field_rejected(self) -> None:
        """``--json id,,body`` のような typo は silently strip せず拒否。"""
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _handle_pr

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value="o/r"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["review-comments", "153", "--json", "id,,body"])
            assert rc == EXIT_INVALID_INPUT
            mock_run.assert_not_called()

    def test_only_comma_json_rejected(self) -> None:
        """``--json ,`` は full response への silent fallback にせず拒否。"""
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _handle_pr

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value="o/r"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["review-comments", "153", "--json", ","])
            assert rc == EXIT_INVALID_INPUT
            mock_run.assert_not_called()

    def test_missing_gh_returns_runtime_error(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _handle_pr

        with patch("kaji_harness.cli_main.shutil.which", return_value=None):
            rc = _handle_pr(["review-comments", "153"])
            assert rc == EXIT_RUNTIME_ERROR

    def test_repo_detect_failure_returns_runtime_error(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _handle_pr

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value=None),
        ):
            rc = _handle_pr(["review-comments", "153"])
            assert rc == EXIT_RUNTIME_ERROR


@pytest.mark.small
class TestPrReviewsBuiltin:
    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kaji_harness.cli_main._load_config_for_dispatch",
            _stub_github_config,
        )

    def test_argv_uses_reviews_path(self) -> None:
        from kaji_harness.cli_main import _handle_pr

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value="o/r"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_pr(["reviews", "42", "-q", ".[].state"])
            cmd = mock_run.call_args[0][0]
            assert cmd[2] == "repos/o/r/pulls/42/reviews"
            assert cmd[cmd.index("--jq") + 1] == ".[].state"


@pytest.mark.small
class TestPrReplyToCommentBuiltin:
    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kaji_harness.cli_main._load_config_for_dispatch",
            _stub_github_config,
        )

    def test_argv_contains_post_method_and_body(self) -> None:
        from kaji_harness.cli_main import _handle_pr

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value="o/r"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_pr(["reply-to-comment", "10", "--to", "999", "--body", "thanks"])
            cmd = mock_run.call_args[0][0]
            assert cmd[:4] == ["gh", "api", "--method", "POST"]
            assert cmd[4] == "repos/o/r/pulls/10/comments/999/replies"
            assert "-f" in cmd
            assert cmd[cmd.index("-f") + 1] == "body=thanks"

    def test_non_numeric_comment_id_returns_invalid_input(self) -> None:
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _handle_pr

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value="o/r"),
        ):
            rc = _handle_pr(["reply-to-comment", "10", "--to", "abc", "--body", "x"])
            assert rc == EXIT_INVALID_INPUT

    def test_unicode_digit_comment_id_rejected(self) -> None:
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _handle_pr

        rc = _handle_pr(["reply-to-comment", "10", "--to", "９９９", "--body", "x"])
        assert rc == EXIT_INVALID_INPUT


@pytest.mark.small
class TestHasApproveFlag:
    """`_has_approve_flag` 純粋関数の単体テスト。"""

    def test_approve_long_flag(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["--approve"]) is True

    def test_approve_with_value(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["--approve=true"]) is True

    def test_comment_returns_false(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["--comment"]) is False

    def test_request_changes_returns_false(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["--request-changes"]) is False

    def test_empty_returns_false(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag([]) is False

    def test_double_dash_ignores_following_approve(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["--", "--approve"]) is False

    def test_position_independent(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["185", "--approve", "--body", "x"]) is True

    def test_substring_match_not_accepted(self) -> None:
        from kaji_harness.cli_main import _has_approve_flag

        assert _has_approve_flag(["185", "--body", "--approve-only-not-real"]) is False


@pytest.mark.small
class TestGithubPrReviewHandler:
    """`_github_pr_review` を `_handle_pr` 非経由で直接呼ぶ handler 単体テスト。

    testing-convention.md § patch スコープ表 § dispatch/provider 結合 の
    禁止対象（``_handle_pr`` 経路の ``cli_main.subprocess.run`` namespace
    patch）に該当しないため、本クラスでは subprocess.run mock を使用する。
    """

    def _patches(self, repo: str = "owner/repo"):
        which = patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh")
        detect = patch("kaji_harness.cli_main._detect_repo", return_value=repo)
        run = patch("kaji_harness.cli_main.subprocess.run")
        return which, detect, run

    def test_self_pr_approve_posts_marker_only(self) -> None:
        from kaji_harness.cli_main import _github_pr_review
        from kaji_harness.providers.gitlab import build_kaji_review_marker

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="apokamo\n", stderr=""),
                MagicMock(returncode=0, stdout="apokamo\n", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            rc = _github_pr_review(
                ["185", "--approve", "--body", "LGTM"], repo_override="owner/repo"
            )
            assert rc == 0
            assert mock_run.call_count == 3
            third_cmd = mock_run.call_args_list[2][0][0]
            assert third_cmd[:4] == ["gh", "api", "--method", "POST"]
            assert third_cmd[4] == "repos/owner/repo/issues/185/comments"
            body_arg = third_cmd[third_cmd.index("-f") + 1]
            marker = build_kaji_review_marker("APPROVED")
            assert body_arg.startswith(f"body={marker}\n")
            assert body_arg.endswith("LGTM")
            # gh pr review は呼ばれていない
            for call in mock_run.call_args_list:
                cmd = call[0][0]
                if len(cmd) >= 3:
                    assert not (cmd[1] == "pr" and cmd[2] == "review")

    def test_self_pr_approve_stdout_suppressed_via_capture_output(self) -> None:
        from kaji_harness.cli_main import _github_pr_review

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="apokamo\n", stderr=""),
                MagicMock(returncode=0, stdout="apokamo\n", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            _github_pr_review(["185", "--approve"], repo_override="owner/repo")
            third_kwargs = mock_run.call_args_list[2][1]
            assert third_kwargs.get("capture_output") is True

    def test_self_pr_approve_empty_body(self) -> None:
        from kaji_harness.cli_main import _github_pr_review
        from kaji_harness.providers.gitlab import build_kaji_review_marker

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="me\n", stderr=""),
                MagicMock(returncode=0, stdout="me\n", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            rc = _github_pr_review(["185", "--approve"], repo_override="owner/repo")
            assert rc == 0
            third_cmd = mock_run.call_args_list[2][0][0]
            body_arg = third_cmd[third_cmd.index("-f") + 1]
            marker = build_kaji_review_marker("APPROVED")
            assert body_arg == f"body={marker}\n"

    def test_non_self_pr_approve_forwards_to_gh(self) -> None:
        from kaji_harness.cli_main import _github_pr_review

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="alice\n", stderr=""),
                MagicMock(returncode=0, stdout="bob\n", stderr=""),
                MagicMock(returncode=0),
            ]
            rc = _github_pr_review(
                ["185", "--approve", "--body", "LGTM"], repo_override="owner/repo"
            )
            assert rc == 0
            assert mock_run.call_count == 3
            third_cmd = mock_run.call_args_list[2][0][0]
            assert third_cmd[:2] == ["gh", "pr"]
            assert "review" in third_cmd
            assert "--approve" in third_cmd
            assert "185" in third_cmd
            # marker comment は POST されない
            for call in mock_run.call_args_list:
                cmd = call[0][0]
                if "--method" in cmd:
                    raise AssertionError(f"unexpected POST call: {cmd}")

    def test_non_ascii_pr_id_rejected(self) -> None:
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _github_pr_review

        rc = _github_pr_review(["１２３", "--approve"], repo_override="owner/repo")
        assert rc == EXIT_INVALID_INPUT

    def test_body_and_body_file_mutually_exclusive(self, tmp_path: Path) -> None:
        from kaji_harness.cli_main import EXIT_INVALID_INPUT, _github_pr_review

        f = tmp_path / "body.txt"
        f.write_text("x")
        rc = _github_pr_review(
            ["185", "--approve", "--body", "y", "--body-file", str(f)],
            repo_override="owner/repo",
        )
        assert rc == EXIT_INVALID_INPUT

    def test_missing_gh_returns_runtime_error(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _github_pr_review

        with patch("kaji_harness.cli_main.shutil.which", return_value=None):
            rc = _github_pr_review(["185", "--approve"], repo_override="owner/repo")
            assert rc == EXIT_RUNTIME_ERROR

    def test_repo_detect_failure_returns_runtime_error(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _github_pr_review

        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value=None),
        ):
            rc = _github_pr_review(["185", "--approve"], repo_override=None)
            assert rc == EXIT_RUNTIME_ERROR

    def test_pr_view_failure_returns_runtime_error_without_post(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _github_pr_review

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="", stderr="not found\n"),
            ]
            rc = _github_pr_review(["185", "--approve"], repo_override="owner/repo")
            assert rc == EXIT_RUNTIME_ERROR
            assert mock_run.call_count == 1

    def test_api_user_failure_returns_runtime_error_without_post(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _github_pr_review

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="alice\n", stderr=""),
                MagicMock(returncode=1, stdout="", stderr="auth error\n"),
            ]
            rc = _github_pr_review(["185", "--approve"], repo_override="owner/repo")
            assert rc == EXIT_RUNTIME_ERROR
            assert mock_run.call_count == 2

    def test_comment_post_failure_returns_runtime_error(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _github_pr_review

        which, detect, run = self._patches()
        with which, detect, run as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="me\n", stderr=""),
                MagicMock(returncode=0, stdout="me\n", stderr=""),
                MagicMock(returncode=1, stdout="", stderr="API error\n"),
            ]
            rc = _github_pr_review(["185", "--approve"], repo_override="owner/repo")
            assert rc == EXIT_RUNTIME_ERROR


@pytest.mark.small
class TestGithubPrReviewRouting:
    """`_handle_pr` の ``review`` routing 振り分け。

    testing-convention.md § patch スコープ表 § dispatch/provider 結合 の
    禁止対象を避けるため、本クラスは ``cli_main.subprocess.run`` を patch
    せず、dispatch 先関数（``_github_pr_review`` / ``_forward_to_gh``）を
    直接 stub する。
    """

    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kaji_harness.cli_main._load_config_for_dispatch",
            _stub_github_config,
        )

    def test_approve_routes_to_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kaji_harness.cli_main import _handle_pr

        calls: list[tuple] = []
        monkeypatch.setattr(
            "kaji_harness.cli_main._github_pr_review",
            lambda rest, *, repo_override: calls.append(("handler", rest, repo_override)) or 0,
        )
        monkeypatch.setattr(
            "kaji_harness.cli_main._forward_to_gh",
            lambda *a, **kw: calls.append(("forward", a, kw)) or 0,
        )
        rc = _handle_pr(["review", "185", "--approve", "--body", "x"])
        assert rc == 0
        assert calls == [("handler", ["185", "--approve", "--body", "x"], "owner/repo")]

    def test_comment_routes_to_forward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kaji_harness.cli_main import _handle_pr

        calls: list[tuple] = []
        monkeypatch.setattr(
            "kaji_harness.cli_main._github_pr_review",
            lambda rest, *, repo_override: calls.append(("handler", rest, repo_override)) or 0,
        )
        monkeypatch.setattr(
            "kaji_harness.cli_main._forward_to_gh",
            lambda *a, **kw: calls.append(("forward", a, kw)) or 0,
        )
        rc = _handle_pr(["review", "185", "--comment", "--body", "x"])
        assert rc == 0
        assert len(calls) == 1
        assert calls[0][0] == "forward"
        assert calls[0][1][0] == "pr"
        assert calls[0][1][1] == ["review", "185", "--comment", "--body", "x"]

    def test_request_changes_routes_to_forward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kaji_harness.cli_main import _handle_pr

        calls: list[tuple] = []
        monkeypatch.setattr(
            "kaji_harness.cli_main._github_pr_review",
            lambda rest, *, repo_override: calls.append(("handler", rest, repo_override)) or 0,
        )
        monkeypatch.setattr(
            "kaji_harness.cli_main._forward_to_gh",
            lambda *a, **kw: calls.append(("forward", a, kw)) or 0,
        )
        rc = _handle_pr(["review", "185", "--request-changes", "--body", "y"])
        assert rc == 0
        assert len(calls) == 1
        assert calls[0][0] == "forward"

    def test_no_flag_routes_to_forward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kaji_harness.cli_main import _handle_pr

        calls: list[tuple] = []
        monkeypatch.setattr(
            "kaji_harness.cli_main._github_pr_review",
            lambda rest, *, repo_override: calls.append(("handler", rest, repo_override)) or 0,
        )
        monkeypatch.setattr(
            "kaji_harness.cli_main._forward_to_gh",
            lambda *a, **kw: calls.append(("forward", a, kw)) or 0,
        )
        rc = _handle_pr(["review", "185"])
        assert rc == 0
        assert len(calls) == 1
        assert calls[0][0] == "forward"


@pytest.mark.small
class TestPrBuiltinDispatch:
    """既存 passthrough の互換性と builtin 振り分け。"""

    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kaji_harness.cli_main._load_config_for_dispatch",
            _stub_github_config,
        )

    def test_existing_pr_view_fails_when_config_missing(self) -> None:
        """Phase 3-e: `.kaji/config.toml` 不在で `kaji pr` は exit 2 で fail-fast。"""
        from kaji_harness.cli_main import _handle_pr
        from kaji_harness.errors import ConfigNotFoundError

        with (
            patch(
                "kaji_harness.cli_main._load_config_for_dispatch",
                side_effect=ConfigNotFoundError(Path("/tmp")),
            ),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            rc = _handle_pr(["view", "153", "--comments"])
        assert rc == 2
        mock_run.assert_not_called()

    def test_review_comments_help_exits_zero(self) -> None:
        """`--help` は argparse が SystemExit(0) で usage を表示する。"""
        from kaji_harness.cli_main import _handle_pr

        with pytest.raises(SystemExit) as exc:
            _handle_pr(["review-comments", "--help"])
        assert exc.value.code == 0

    def test_review_comments_missing_args_exits_two(self) -> None:
        """argparse default: 引数不足は exit code 2。"""
        from kaji_harness.cli_main import _handle_pr

        with pytest.raises(SystemExit) as exc:
            _handle_pr(["review-comments"])
        assert exc.value.code == 2


# ============================================================
# Helpers
# ============================================================


def cmd_run_with_args(*args: str) -> int:
    """Parse args and call cmd_run, returning exit code."""
    parser = create_parser()
    parsed = parser.parse_args(["run", *args])
    return cmd_run(parsed)
