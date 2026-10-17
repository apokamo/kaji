"""tmux interactive terminal backend の非推奨警告のテスト（#475）。

警告は ``kaji run`` の stderr にのみ、1 プロセス 1 回だけ出し、stdout・終了コード・
runner へ渡る config は変えない。判定表（設計書 § 判定規則）の 12 ケースを Small で網羅する。
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.commands import run as run_module
from kaji_harness.commands.exit_codes import EXIT_DEFINITION_ERROR, EXIT_OK
from kaji_harness.commands.parser import create_parser
from kaji_harness.commands.run import (
    _apply_execution_overrides,
    _emit_tmux_backend_deprecation_warning,
    cmd_run,
)
from kaji_harness.config import (
    TMUX_BACKEND_DEPRECATION_PREFIX,
    ExecutionConfig,
    KajiConfig,
    tmux_backend_deprecation_warning,
)
from kaji_harness.models import Verdict

_REPO = Path("/repo")
_TRACKED = _REPO / ".kaji" / "config.toml"
_LOCAL = _REPO / ".kaji" / "config.local.toml"
_DOCS_LINK = "docs/cli-guides/interactive-terminal-runner.md#tmux-backend-deprecation"
_AFTER_REMOVAL_CONFIG_ERROR = 'after removal, an explicit "tmux" in config fails config loading'
_AFTER_REMOVAL_DEFAULT = "After removal, the default backend becomes Herdr."

_SOURCE_TEXT = {
    "tracked": f'interactive_terminal_backend = "tmux" in {_TRACKED}',
    "local": f'interactive_terminal_backend = "tmux" in {_LOCAL}',
    "cli": "--interactive-terminal-backend tmux",
    "default": "the built-in default (interactive_terminal_backend is not set)",
}
_SOURCE_PATH = {"tracked": _TRACKED, "local": _LOCAL, None: None}


@pytest.fixture(autouse=True)
def _reset_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_module, "_TMUX_BACKEND_DEPRECATION_EMITTED", False)


def _evaluate(
    *,
    runner: str,
    backend: str,
    source: str | None,
    cli_runner: str | None = None,
    cli_backend: str | None = None,
) -> str | None:
    """C（config 解決値）と CLI 指定から E を作り、純粋関数の結果を返す。"""
    configured = ExecutionConfig(
        default_timeout=1800,
        agent_runner=runner,  # type: ignore[arg-type]
        interactive_terminal_backend=backend,  # type: ignore[arg-type]
        interactive_terminal_backend_source=_SOURCE_PATH[source],
    )
    effective = configured
    if cli_runner is not None:
        effective = dataclasses.replace(effective, agent_runner=cli_runner)  # type: ignore[arg-type]
    if cli_backend is not None:
        effective = dataclasses.replace(
            effective,
            interactive_terminal_backend=cli_backend,  # type: ignore[arg-type]
        )
    return tmux_backend_deprecation_warning(
        configured=configured, effective=effective, cli_backend=cli_backend
    )


# 判定表（設計書 § 判定規則）: (#, C.runner, C.backend, C.source, CLI runner, CLI backend, 出所)
_DECISION_TABLE = [
    (1, "headless", "tmux", None, None, None, None),
    (2, "interactive_terminal", "tmux", None, None, None, ["default"]),
    (3, "headless", "tmux", "tracked", None, None, ["tracked"]),
    (4, "headless", "herdr", "local", None, None, None),
    (5, "headless", "herdr", "local", "interactive_terminal", None, None),
    (6, "headless", "tmux", "local", None, None, ["local"]),
    (7, "headless", "tmux", "tracked", "interactive_terminal", "herdr", ["tracked"]),
    (8, "headless", "tmux", None, None, "tmux", ["cli"]),
    (9, "headless", "tmux", None, "interactive_terminal", "tmux", ["cli"]),
    (10, "headless", "tmux", "tracked", "interactive_terminal", "tmux", ["tracked", "cli"]),
    (11, "interactive_terminal", "herdr", "tracked", None, None, None),
    (12, "headless", "tmux", None, "interactive_terminal", "herdr", None),
]


@pytest.mark.small
class TestDecisionRules:
    """判定規則 (a)〜(c) と出所の列挙を純粋関数で検証する。"""

    @pytest.mark.parametrize(
        ("case", "runner", "backend", "source", "cli_runner", "cli_backend", "expected"),
        _DECISION_TABLE,
        ids=[f"case{row[0]}" for row in _DECISION_TABLE],
    )
    def test_decision_table(
        self,
        case: int,
        runner: str,
        backend: str,
        source: str | None,
        cli_runner: str | None,
        cli_backend: str | None,
        expected: list[str] | None,
    ) -> None:
        message = _evaluate(
            runner=runner,
            backend=backend,
            source=source,
            cli_runner=cli_runner,
            cli_backend=cli_backend,
        )
        if expected is None:
            assert message is None
            return
        assert message is not None
        joined = "; ".join(_SOURCE_TEXT[key] for key in expected)
        assert f"tmux is selected by: {joined}. " in message

    def test_overridden_tracked_tmux_does_not_warn(self) -> None:
        # #4: tracked tmux が config.local.toml の herdr で上書き → マージ後の値は herdr
        assert _evaluate(runner="headless", backend="herdr", source="local") is None

    def test_overridden_tracked_tmux_with_interactive_cli_does_not_warn(self) -> None:
        # #5: 実効 interactive / herdr。削除後も per-key precedence でエラーにならない
        assert (
            _evaluate(
                runner="headless",
                backend="herdr",
                source="local",
                cli_runner="interactive_terminal",
            )
            is None
        )

    def test_explicit_config_tmux_warns_even_with_cli_herdr(self) -> None:
        # #7: 削除後は config エラーになり CLI で救済できないため警告する
        message = _evaluate(
            runner="headless",
            backend="tmux",
            source="tracked",
            cli_runner="interactive_terminal",
            cli_backend="herdr",
        )
        assert message is not None
        assert _SOURCE_TEXT["tracked"] in message
        assert _SOURCE_TEXT["cli"] not in message


@pytest.mark.small
class TestWarningText:
    """警告文の必須要素。"""

    def test_required_elements(self) -> None:
        message = _evaluate(runner="interactive_terminal", backend="tmux", source=None)
        assert message is not None
        assert message.startswith(TMUX_BACKEND_DEPRECATION_PREFIX)
        assert TMUX_BACKEND_DEPRECATION_PREFIX == (
            "WARNING: the tmux interactive terminal backend is deprecated"
        )
        assert "will be removed in a subsequent kaji release" in message
        assert 'interactive_terminal_backend = "herdr"' in message
        assert 'agent_runner = "headless"' in message
        assert message.endswith(f"See {_DOCS_LINK}")
        assert "\n" not in message

    def test_config_source_adds_after_removal_notice(self) -> None:
        message = _evaluate(runner="headless", backend="tmux", source="local")
        assert message is not None
        assert str(_LOCAL) in message
        assert _AFTER_REMOVAL_CONFIG_ERROR in message
        assert _AFTER_REMOVAL_DEFAULT not in message

    def test_default_source_adds_default_notice_only(self) -> None:
        message = _evaluate(runner="interactive_terminal", backend="tmux", source=None)
        assert message is not None
        assert _AFTER_REMOVAL_DEFAULT in message
        assert _AFTER_REMOVAL_CONFIG_ERROR not in message
        assert str(_TRACKED) not in message

    def test_cli_only_source_has_no_extra_notice(self) -> None:
        message = _evaluate(runner="headless", backend="tmux", source=None, cli_backend="tmux")
        assert message is not None
        assert _AFTER_REMOVAL_CONFIG_ERROR not in message
        assert _AFTER_REMOVAL_DEFAULT not in message

    def test_multiple_sources_in_one_message(self) -> None:
        # #10: tracked と CLI を 1 通の警告にこの順で列挙する
        message = _evaluate(
            runner="headless",
            backend="tmux",
            source="tracked",
            cli_runner="interactive_terminal",
            cli_backend="tmux",
        )
        assert message is not None
        assert message.count(TMUX_BACKEND_DEPRECATION_PREFIX) == 1
        assert message.index(_SOURCE_TEXT["tracked"]) < message.index(_SOURCE_TEXT["cli"])
        assert _SOURCE_TEXT["default"] not in message


def _execution(*, backend: str, source: Path | None) -> ExecutionConfig:
    return ExecutionConfig(
        default_timeout=1800,
        interactive_terminal_backend=backend,  # type: ignore[arg-type]
        interactive_terminal_backend_source=source,
    )


@pytest.mark.small
class TestEmitOncePerProcess:
    """emitter は stderr に 1 プロセス 1 回だけ出し、stdout を使わない。"""

    def test_emits_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        execution = _execution(backend="tmux", source=_TRACKED)
        _emit_tmux_backend_deprecation_warning(
            configured=execution, effective=execution, cli_backend=None
        )
        _emit_tmux_backend_deprecation_warning(
            configured=execution, effective=execution, cli_backend=None
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count(TMUX_BACKEND_DEPRECATION_PREFIX) == 1

    def test_no_warning_does_not_set_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        quiet = _execution(backend="tmux", source=None)
        _emit_tmux_backend_deprecation_warning(configured=quiet, effective=quiet, cli_backend=None)
        assert capsys.readouterr().err == ""
        assert run_module._TMUX_BACKEND_DEPRECATION_EMITTED is False

        loud = _execution(backend="tmux", source=_TRACKED)
        _emit_tmux_backend_deprecation_warning(configured=loud, effective=loud, cli_backend=None)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count(TMUX_BACKEND_DEPRECATION_PREFIX) == 1


# --- Medium / Large: 実 config ファイル + cmd_run -------------------------------------------

_BASE_CONFIG = (
    '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
    "[execution]\ndefault_timeout = 1800\n{execution}\n\n"
    '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
)
_WORKFLOW = (
    "name: test\ndescription: test\nexecution_policy: auto\n"
    "steps:\n  - id: s1\n    skill: test-skill\n"
    "    agent: claude\n    on:\n      PASS: end\n"
)


def _init_repo(repo: Path, *, tracked: str = "", local: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(_BASE_CONFIG.format(execution=tracked), encoding="utf-8")
    if local is not None:
        (kaji_dir / "config.local.toml").write_text(f"[execution]\n{local}\n", encoding="utf-8")


def _run_missing_workflow(repo: Path, cli: list[str]) -> int:
    args = create_parser().parse_args(
        ["run", str(repo / "missing.yaml"), "1", "--workdir", str(repo), *cli]
    )
    return cmd_run(args)


# (#, tracked [execution], local [execution], CLI 引数, 警告するか)
_CMD_RUN_CASES = [
    (1, "", None, [], False),
    (2, 'agent_runner = "interactive_terminal"', None, [], True),
    (3, 'interactive_terminal_backend = "tmux"', None, [], True),
    (
        4,
        'interactive_terminal_backend = "tmux"',
        'interactive_terminal_backend = "herdr"',
        [],
        False,
    ),
    (
        6,
        'interactive_terminal_backend = "herdr"',
        'interactive_terminal_backend = "tmux"',
        [],
        True,
    ),
    (
        7,
        'interactive_terminal_backend = "tmux"',
        None,
        ["--agent-runner", "interactive-terminal", "--interactive-terminal-backend", "herdr"],
        True,
    ),
    (8, "", None, ["--interactive-terminal-backend", "tmux"], True),
    (
        12,
        "",
        None,
        ["--agent-runner", "interactive-terminal", "--interactive-terminal-backend", "herdr"],
        False,
    ),
]


@pytest.mark.medium
class TestCmdRunIntegration:
    """``cmd_run`` が実 config と argparse 引数から警告を判定する。"""

    @pytest.mark.parametrize(
        ("case", "tracked", "local", "cli", "warns"),
        _CMD_RUN_CASES,
        ids=[f"case{row[0]}" for row in _CMD_RUN_CASES],
    )
    def test_warning_decision_without_changing_exit_or_stdout(
        self,
        outside_project_tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        case: int,
        tracked: str,
        local: str | None,
        cli: list[str],
        warns: bool,
    ) -> None:
        _init_repo(outside_project_tmp_path, tracked=tracked, local=local)
        exit_code = _run_missing_workflow(outside_project_tmp_path, cli)
        captured = capsys.readouterr()
        assert exit_code == EXIT_DEFINITION_ERROR
        assert captured.out == ""
        assert "Workflow file not found" in captured.err
        assert captured.err.count(TMUX_BACKEND_DEPRECATION_PREFIX) == (1 if warns else 0)

    def test_overlay_source_is_named_in_warning(
        self, outside_project_tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _init_repo(
            outside_project_tmp_path,
            tracked='interactive_terminal_backend = "herdr"',
            local='interactive_terminal_backend = "tmux"',
        )
        _run_missing_workflow(outside_project_tmp_path, [])
        err = capsys.readouterr().err
        assert str(outside_project_tmp_path / ".kaji" / "config.local.toml") in err

    def test_warns_once_per_process_across_runs(
        self, outside_project_tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _init_repo(outside_project_tmp_path, tracked='interactive_terminal_backend = "tmux"')
        _run_missing_workflow(outside_project_tmp_path, [])
        _run_missing_workflow(outside_project_tmp_path, [])
        assert capsys.readouterr().err.count(TMUX_BACKEND_DEPRECATION_PREFIX) == 1


def _run_success(
    repo: Path, tracked: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str, KajiConfig, KajiConfig]:
    """成功する workflow を stub runner で実行し、(exit, stdout, stderr, 受領 config, 期待 config) を返す。"""
    _init_repo(repo, tracked=tracked)
    workflow = repo / "workflow.yaml"
    workflow.write_text(_WORKFLOW, encoding="utf-8")
    args = create_parser().parse_args(["run", str(workflow), "1", "--workdir", str(repo)])
    with patch("kaji_harness.commands.run.WorkflowRunner") as mock_runner:
        mock_runner.return_value.run.return_value = MagicMock(
            last_transition_verdict=Verdict("PASS", "", "", "")
        )
        mock_runner.return_value.canonical_issue_ref = "#1"
        exit_code = cmd_run(args)
    captured = capsys.readouterr()
    expected_config = _apply_execution_overrides(KajiConfig.discover(start_dir=repo), args)
    received_config = mock_runner.call_args.kwargs["config"]
    return exit_code, captured.out, captured.err, received_config, expected_config


@pytest.mark.medium
class TestCmdRunSuccessPathUnchanged:
    """警告が出ても成功経路の終了コード・stdout・runner へ渡る config は変わらない。"""

    def test_tmux_warning_keeps_exit_stdout_and_config(
        self, outside_project_tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        tmux_repo = outside_project_tmp_path / "tmux"
        herdr_repo = outside_project_tmp_path / "herdr"
        tmux_exit, tmux_out, tmux_err, tmux_received, tmux_expected = _run_success(
            tmux_repo, 'interactive_terminal_backend = "tmux"', capsys
        )
        herdr_exit, herdr_out, herdr_err, herdr_received, herdr_expected = _run_success(
            herdr_repo, 'interactive_terminal_backend = "herdr"', capsys
        )

        assert tmux_err.count(TMUX_BACKEND_DEPRECATION_PREFIX) == 1
        assert TMUX_BACKEND_DEPRECATION_PREFIX not in herdr_err
        assert tmux_exit == herdr_exit == EXIT_OK
        assert tmux_out == herdr_out == "Workflow 'test' completed for issue #1\n"
        assert tmux_received == tmux_expected
        assert tmux_received.execution.interactive_terminal_backend_source == (
            tmux_repo / ".kaji" / "config.toml"
        )
        assert herdr_received == herdr_expected


_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _run_cli_missing_workflow(repo: Path) -> subprocess.CompletedProcess[str]:
    # subprocess でも本 checkout の kaji_harness を import させる。
    env = {**os.environ, "PYTHONPATH": str(_SOURCE_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "run", "missing.yaml", "1"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.large
@pytest.mark.large_local
def test_cli_stderr_contract(outside_project_tmp_path: Path) -> None:
    tmux_repo = outside_project_tmp_path / "tmux"
    herdr_repo = outside_project_tmp_path / "herdr"
    _init_repo(tmux_repo, tracked='interactive_terminal_backend = "tmux"')
    _init_repo(herdr_repo, tracked='interactive_terminal_backend = "herdr"')

    tmux = _run_cli_missing_workflow(tmux_repo)
    herdr = _run_cli_missing_workflow(herdr_repo)

    assert tmux.stderr.count(TMUX_BACKEND_DEPRECATION_PREFIX) == 1
    assert TMUX_BACKEND_DEPRECATION_PREFIX not in herdr.stderr
    assert tmux.returncode == herdr.returncode == EXIT_DEFINITION_ERROR
    assert tmux.stdout == herdr.stdout
