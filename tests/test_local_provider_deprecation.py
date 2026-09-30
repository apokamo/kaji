"""local provider 非推奨警告のテスト（#438）。

警告は stderr にのみ、1 プロセス 1 回だけ出し、stdout と終了コードは変えない。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kaji_harness.commands.exit_codes import EXIT_INVALID_INPUT
from kaji_harness.commands.sync import cmd_sync_from_github, cmd_sync_status
from kaji_harness.config import (
    LOCAL_PROVIDER_DEPRECATION_WARNING,
    KajiConfig,
    warn_local_provider_deprecated,
)
from kaji_harness.local_init import EXIT_OVERLAY_EXISTS, cmd_local_init

_BASE_CONFIG = (
    '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
    "[execution]\ndefault_timeout = 1800\n\n"
)
_LOCAL_PROVIDER = '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
_GITHUB_PROVIDER = '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'


def _write_config(repo: Path, provider: str) -> Path:
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir(parents=True, exist_ok=True)
    path = kaji_dir / "config.toml"
    path.write_text(_BASE_CONFIG + provider, encoding="utf-8")
    return path


@pytest.mark.small
def test_warning_names_removal_and_migration_target() -> None:
    assert "deprecated" in LOCAL_PROVIDER_DEPRECATION_WARNING
    assert "deprecated starting with this release" in LOCAL_PROVIDER_DEPRECATION_WARNING
    assert (
        "removal is planned for a subsequent kaji release after one release of "
        "deprecation warnings" in LOCAL_PROVIDER_DEPRECATION_WARNING
    )
    assert "next kaji release" not in LOCAL_PROVIDER_DEPRECATION_WARNING
    assert 'provider.type = "github"' in LOCAL_PROVIDER_DEPRECATION_WARNING


@pytest.mark.small
def test_warn_emits_once_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    warn_local_provider_deprecated()
    warn_local_provider_deprecated()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count(LOCAL_PROVIDER_DEPRECATION_WARNING) == 1


@pytest.mark.medium
def test_local_config_load_warns_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write_config(tmp_path, _LOCAL_PROVIDER)
    first = KajiConfig._load(path)
    second = KajiConfig._load(path)
    assert first.provider is not None and first.provider.type == "local"
    assert second.provider is not None and second.provider.type == "local"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count(LOCAL_PROVIDER_DEPRECATION_WARNING) == 1


@pytest.mark.medium
def test_local_overlay_on_github_base_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_config(tmp_path, _GITHUB_PROVIDER)
    (tmp_path / ".kaji" / "config.local.toml").write_text(
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n',
        encoding="utf-8",
    )
    KajiConfig._load(path)
    assert LOCAL_PROVIDER_DEPRECATION_WARNING in capsys.readouterr().err


@pytest.mark.medium
def test_github_config_load_does_not_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_config(tmp_path, _GITHUB_PROVIDER)
    KajiConfig._load(path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.medium
def test_local_init_warns_and_keeps_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.local.toml").write_text("", encoding="utf-8")
    args = argparse.Namespace(
        local_command="init",
        machine_id="pc1",
        default_branch="main",
        non_interactive=True,
        repo_root=tmp_path,
    )
    assert cmd_local_init(args) == EXIT_OVERLAY_EXISTS
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count(LOCAL_PROVIDER_DEPRECATION_WARNING) == 1


@pytest.mark.medium
def test_sync_from_github_warns_and_keeps_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(include_closed=True, state=None, since=None, repo=None, quiet=True)
    assert cmd_sync_from_github(args) == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count(LOCAL_PROVIDER_DEPRECATION_WARNING) == 1


@pytest.mark.medium
def test_sync_status_warns_once_with_local_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """command 入口と設定読み込みの両方を通っても警告は 1 回。"""
    _write_config(tmp_path, _LOCAL_PROVIDER)
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(json_mode=True)
    assert cmd_sync_status(args) == 0
    captured = capsys.readouterr()
    assert LOCAL_PROVIDER_DEPRECATION_WARNING not in captured.out
    assert captured.err.count(LOCAL_PROVIDER_DEPRECATION_WARNING) == 1


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_provider_type(repo: Path) -> subprocess.CompletedProcess[str]:
    # subprocess でも本 checkout の kaji_harness を import させる。
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "config", "provider-type"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.large
@pytest.mark.large_local
def test_cli_local_provider_warns_on_stderr_only(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    _write_config(tmp_path, _LOCAL_PROVIDER)
    result = _run_provider_type(tmp_path)
    assert result.returncode == 0
    assert result.stdout == "local\n"
    assert result.stderr.count(LOCAL_PROVIDER_DEPRECATION_WARNING) == 1


@pytest.mark.large
@pytest.mark.large_local
def test_cli_github_provider_does_not_warn(tmp_path: Path) -> None:
    _write_config(tmp_path, _GITHUB_PROVIDER)
    result = _run_provider_type(tmp_path)
    assert result.returncode == 0
    assert result.stdout == "github\n"
    assert LOCAL_PROVIDER_DEPRECATION_WARNING not in result.stderr
