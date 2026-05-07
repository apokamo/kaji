"""Phase 4 Large-local: subprocess E2E tests for the 3-layer guard.

CLI 層 / Skill 層 / Workflow 層の bare-provider ガードを実 subprocess 経由で
確認する。実 agent CLI（claude / codex / gemini）は起動しない（API コスト
発生のため、別途 large_forge で扱う）。

phase4-design.md § 受け入れ条件 § 機械検証 / § テスト戦略 § Large-local 参照。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_KAJI_CMD = [sys.executable, "-m", "kaji_harness.cli_main"]


def _run_kaji(repo: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_KAJI_CMD, *args],
        cwd=repo,
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
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_base_config(repo)
    rc = _run_kaji(repo, "local", "init", "--machine-id", "pc1", "--non-interactive")
    assert rc.returncode == 0, rc.stderr
    return repo


# ============================================================
# CLI 層: kaji pr が provider=local 配下で fail-fast
# ============================================================


def test_pr_create_under_local_exits_2(local_repo: Path) -> None:
    result = _run_kaji(local_repo, "pr", "create", "-t", "x", "-b", "y")
    assert result.returncode == 2
    assert "forge-only" in result.stderr
    assert "provider.type='local'" in result.stderr
    assert "/issue-review-code" in result.stderr


def test_pr_review_comments_under_local_exits_2(local_repo: Path) -> None:
    result = _run_kaji(local_repo, "pr", "review-comments", "1")
    assert result.returncode == 2
    assert "forge-only" in result.stderr


def test_pr_list_under_local_exits_2(local_repo: Path) -> None:
    result = _run_kaji(local_repo, "pr", "list")
    assert result.returncode == 2
    assert "forge-only" in result.stderr


# ============================================================
# Workflow 層: requires_provider 不整合で fail-fast
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
        "    agent: echo\n"
        "    on:\n"
        "      PASS: end\n"
    )
    return wf


def test_run_github_workflow_under_local_exits_2(local_repo: Path) -> None:
    wf = _write_workflow(local_repo, "wf-gh", "github")
    result = _run_kaji(local_repo, "run", str(wf), "local-pc1-1")
    assert result.returncode == 2
    assert "requires provider.type='github'" in result.stderr


def test_run_local_workflow_under_github_exits_2(github_repo: Path) -> None:
    wf = _write_workflow(github_repo, "wf-local", "local")
    result = _run_kaji(github_repo, "run", str(wf), "1")
    assert result.returncode == 2
    assert "requires provider.type='local'" in result.stderr


def test_run_any_workflow_under_local_passes_provider_match(local_repo: Path) -> None:
    """``requires_provider: any`` は両 provider で provider 整合を通過する
    （その先 skill 不在で別エラーになるが、provider mismatch ではない）。"""
    wf = _write_workflow(local_repo, "wf-any", "any")
    result = _run_kaji(local_repo, "run", str(wf), "local-pc1-1")
    assert "requires provider.type" not in result.stderr


# ============================================================
# kaji config provider-type
# ============================================================


def test_config_provider_type_github(github_repo: Path) -> None:
    result = _run_kaji(github_repo, "config", "provider-type")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "github\n"
    assert result.stderr == ""


def test_config_provider_type_local(local_repo: Path) -> None:
    result = _run_kaji(local_repo, "config", "provider-type")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "local\n"


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
        "requires_provider: gitlab\n"
        "steps:\n"
        "  - id: only\n    skill: noop\n    agent: echo\n    on:\n      PASS: end\n"
    )
    result = _run_kaji(github_repo, "validate", str(wf))
    assert result.returncode != 0
    assert "requires_provider" in result.stderr
