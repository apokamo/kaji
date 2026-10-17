"""Large-local: subprocess E2E tests for provider guards.

local provider 削除（#448）後の設定エラー（exit 2）、``kaji local`` / ``kaji sync``
サブコマンド不在、github provider の既存ガード（``config provider-type``、
``[provider]`` 欠落、``requires_provider`` 検証）を実 subprocess 経由で確認する。
実 agent CLI（claude / codex / antigravity）は起動しない。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_KAJI_CMD = [sys.executable, "-m", "kaji_harness.cli_main"]
# editable install が別 checkout を指していても、検証対象のこの checkout を import させる。
_WORKTREE_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_HINT = 'Set provider.type = "github"'


def _run_kaji(repo: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_KAJI_CMD, *args],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(_WORKTREE_ROOT)},
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_base_config(repo: Path) -> None:
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir(parents=True, exist_ok=True)
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        "\n"
        "[execution]\n"
        "default_timeout = 1800\n"
    )


@pytest.fixture
def github_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_base_config(repo)
    (repo / ".kaji" / "config.toml").write_text(
        (repo / ".kaji" / "config.toml").read_text()
        + '\n[provider]\ntype = "github"\n[provider.github]\nrepo = "owner/name"\n'
    )
    return repo


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    """tracked config に ``provider.type = "local"`` を持つ（削除済み provider）repo。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_base_config(repo)
    (repo / ".kaji" / "config.toml").write_text(
        (repo / ".kaji" / "config.toml").read_text() + '\n[provider]\ntype = "local"\n'
    )
    return repo


# ============================================================
# local provider 削除: provider.type = "local" は設定エラー（exit 2）
# ============================================================


@pytest.mark.parametrize(
    "args",
    [
        ("issue", "view", "1"),
        ("pr", "list"),
        ("config", "provider-type"),
    ],
)
def test_local_provider_config_exits_2(local_repo: Path, args: tuple[str, ...]) -> None:
    result = _run_kaji(local_repo, *args)
    assert result.returncode == 2
    assert "local provider was removed" in result.stderr
    assert _MIGRATION_HINT in result.stderr
    assert str(local_repo / ".kaji" / "config.toml") in result.stderr


def test_validate_under_local_provider_config_exits_2(local_repo: Path) -> None:
    wf = _write_workflow(local_repo, "wf-gh", "github")
    result = _run_kaji(local_repo, "validate", str(wf))
    assert result.returncode == 2
    assert f"✗ {wf}" in result.stderr
    assert _MIGRATION_HINT in result.stderr


def test_help_does_not_list_local_or_sync(github_repo: Path) -> None:
    result = _run_kaji(github_repo, "--help")
    assert result.returncode == 0
    choices_line = next(line for line in result.stdout.splitlines() if line.strip().startswith("{"))
    choices = choices_line.strip().strip("{}").split(",")
    assert "local" not in choices
    assert "sync" not in choices


@pytest.mark.parametrize("args", [("local", "init"), ("sync", "status")])
def test_removed_subcommands_exit_2(github_repo: Path, args: tuple[str, ...]) -> None:
    result = _run_kaji(github_repo, *args)
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


# ============================================================
# Workflow 層: requires_provider 検証
# ============================================================


def _write_workflow(repo: Path, name: str, requires: str) -> Path:
    """Skill 解決を回避するため、validate を通さない最小 YAML を書く（runner
    起動前の整合検証だけを発火させる）。"""
    wf = repo / f"{name}.yaml"
    wf.write_text(
        f"name: {name}\n"
        'description: ""\n'
        "execution_policy: auto\n"
        f"requires_provider: {requires}\n"
        "steps:\n"
        "  - id: only\n"
        "    skill: noop\n"
        "    agent: claude\n"
        "    on:\n"
        "      PASS: end\n"
    )
    return wf


def test_validate_rejects_requires_provider_local(github_repo: Path) -> None:
    wf = _write_workflow(github_repo, "wf-local", "local")
    result = _run_kaji(github_repo, "validate", str(wf))
    assert result.returncode == 1
    assert "'requires_provider' must be one of ['any', 'github'], got 'local'" in result.stderr


# ============================================================
# kaji config provider-type
# ============================================================


def test_config_provider_type_github(github_repo: Path) -> None:
    result = _run_kaji(github_repo, "config", "provider-type")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "github\n"
    assert result.stderr == ""


def test_config_provider_type_missing_provider_section(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_base_config(repo)  # no [provider]
    result = _run_kaji(repo, "config", "provider-type")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "[provider]" in result.stderr


# ============================================================
# kaji validate: requires_provider enum 検証
# ============================================================


def test_validate_accepts_requires_provider_github(github_repo: Path) -> None:
    wf = _write_workflow(github_repo, "wf-gh", "github")
    # validate には skill 解決が必要だが、本テストは parser-level の
    # requires_provider enum 検証のみを確認する。skill が存在しないため
    # validate は失敗するが、その前段の YAML 文法検証で
    # ``requires_provider: github`` が rejected されないことを確認したい。
    # ここでは parser を直接呼ぶより、kaji validate の stderr を inspect する。
    result = _run_kaji(github_repo, "validate", str(wf))
    # skill not found のエラーは出ても、requires_provider enum エラーは出ない
    assert "requires_provider" not in result.stderr or ("must be one of" not in result.stderr)


def test_validate_rejects_unknown_requires_provider(github_repo: Path) -> None:
    wf = github_repo / "bad.yaml"
    wf.write_text(
        'name: bad\ndescription: ""\nexecution_policy: auto\n'
        "requires_provider: nonexistent\n"
        "steps:\n"
        "  - id: only\n    skill: noop\n    agent: claude\n    on:\n      PASS: end\n"
    )
    result = _run_kaji(github_repo, "validate", str(wf))
    assert result.returncode != 0
    assert "requires_provider" in result.stderr
