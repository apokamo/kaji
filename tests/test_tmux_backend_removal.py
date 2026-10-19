"""Issue #515: ``kaji run`` の command 経路で tmux backend 削除の契約を確認する。

実 config ファイルと argparse 引数から ``cmd_run`` を呼び、旧 ``tmux`` 設定が値を定義した
ファイル名付きのエラーになること、``herdr`` 設定では deprecation 警告が出ないことを検証する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kaji_harness.commands.exit_codes import EXIT_CONFIG_NOT_FOUND, EXIT_DEFINITION_ERROR
from kaji_harness.commands.parser import create_parser
from kaji_harness.commands.run import cmd_run

pytestmark = pytest.mark.medium

_BASE_CONFIG = (
    '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
    "[execution]\ndefault_timeout = 1800\n{execution}\n\n"
    '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
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


@pytest.mark.parametrize(
    ("tracked", "local", "cli", "defined_in"),
    [
        ('interactive_terminal_backend = "tmux"', None, [], "config.toml"),
        (
            'interactive_terminal_backend = "herdr"',
            'interactive_terminal_backend = "tmux"',
            [],
            "config.local.toml",
        ),
        (
            'agent_runner = "headless"\ninteractive_terminal_backend = "tmux"',
            None,
            ["--agent-runner", "headless"],
            "config.toml",
        ),
        (
            'interactive_terminal_backend = "tmux"',
            None,
            ["--agent-runner", "interactive-terminal", "--interactive-terminal-backend", "herdr"],
            "config.toml",
        ),
    ],
    ids=["tracked", "local-overlay", "headless", "cli-herdr-cannot-override"],
)
def test_tmux_config_fails_with_defining_file(
    outside_project_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    tracked: str,
    local: str | None,
    cli: list[str],
    defined_in: str,
) -> None:
    _init_repo(outside_project_tmp_path, tracked=tracked, local=local)

    exit_code = _run_missing_workflow(outside_project_tmp_path, cli)

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIG_NOT_FOUND
    assert captured.out == ""
    assert str(outside_project_tmp_path / ".kaji" / defined_in) in captured.err
    assert "interactive_terminal_backend = 'tmux' is no longer supported" in captured.err


@pytest.mark.parametrize(
    ("tracked", "cli"),
    [
        ('interactive_terminal_backend = "herdr"', []),
        ('agent_runner = "interactive_terminal"', []),
        ("", ["--agent-runner", "interactive-terminal", "--interactive-terminal-backend", "herdr"]),
    ],
    ids=["explicit-herdr", "default-backend", "cli-herdr"],
)
def test_herdr_config_emits_no_deprecation_warning(
    outside_project_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    tracked: str,
    cli: list[str],
) -> None:
    _init_repo(outside_project_tmp_path, tracked=tracked)

    exit_code = _run_missing_workflow(outside_project_tmp_path, cli)

    captured = capsys.readouterr()
    assert exit_code == EXIT_DEFINITION_ERROR
    assert "Workflow file not found" in captured.err
    assert "deprecated" not in captured.err
    assert "tmux" not in captured.err
