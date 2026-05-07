"""Phase 3-e Large-local: subprocess E2E tests with no external network.

`kaji local init` / `kaji issue create / list / close` を実 subprocess で起動し、
filesystem 上の生成物 / exit code / stderr を検証する。実 agent CLI（claude /
codex / gemini）は起動しない（API コスト発生のため、別途 large_forge で扱う）。

phase3e-design.md § 4 を参照。
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


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    """`kaji local init` 直前の fresh tmp repo を作る。

    - .kaji/config.toml を minimum 構成（paths + execution、provider 無し）で生成
    - .gitignore は空
    - skill ディレクトリは作らない（kaji validate を本テストでは要求しない）
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        "\n"
        "[execution]\n"
        "default_timeout = 1800\n"
    )
    (repo / ".gitignore").write_text("")
    return repo


# ============================================================
# kaji local init smoke
# ============================================================


def test_local_init_smoke_creates_overlay_and_updates_gitignore(fresh_repo: Path) -> None:
    result = _run_kaji(fresh_repo, "local", "init", "--machine-id", "pc1", "--non-interactive")
    assert result.returncode == 0, result.stderr
    overlay = fresh_repo / ".kaji" / "config.local.toml"
    assert overlay.exists()
    text = overlay.read_text()
    assert 'machine_id = "pc1"' in text
    gitignore = (fresh_repo / ".gitignore").read_text()
    assert ".kaji/config.local.toml" in gitignore


def test_local_init_double_run_exits_3(fresh_repo: Path) -> None:
    rc1 = _run_kaji(fresh_repo, "local", "init", "--machine-id", "pc1", "--non-interactive")
    assert rc1.returncode == 0
    rc2 = _run_kaji(fresh_repo, "local", "init", "--machine-id", "pc2", "--non-interactive")
    assert rc2.returncode == 3


def test_local_init_uppercase_machine_id_rejected(fresh_repo: Path) -> None:
    result = _run_kaji(fresh_repo, "local", "init", "--machine-id", "PC1", "--non-interactive")
    assert result.returncode == 2


def test_local_init_hyphen_machine_id_rejected(fresh_repo: Path) -> None:
    result = _run_kaji(fresh_repo, "local", "init", "--machine-id", "pc-1", "--non-interactive")
    assert result.returncode == 2


def test_local_init_default_branch_in_overlay(fresh_repo: Path) -> None:
    result = _run_kaji(
        fresh_repo,
        "local",
        "init",
        "--machine-id",
        "pc1",
        "--default-branch",
        "develop",
        "--non-interactive",
    )
    assert result.returncode == 0, result.stderr
    overlay = (fresh_repo / ".kaji" / "config.local.toml").read_text()
    assert 'default_branch = "develop"' in overlay


# ============================================================
# kaji issue (local) smoke
# ============================================================


def _init_local(repo: Path, machine_id: str = "pc1") -> None:
    rc = _run_kaji(repo, "local", "init", "--machine-id", machine_id, "--non-interactive")
    assert rc.returncode == 0, rc.stderr


def test_issue_create_writes_frontmatter(fresh_repo: Path) -> None:
    _init_local(fresh_repo)
    result = _run_kaji(
        fresh_repo,
        "issue",
        "create",
        "--title",
        "phase 3e smoke",
        "--body",
        "x",
    )
    assert result.returncode == 0, result.stderr
    issue_dir = fresh_repo / ".kaji" / "issues" / "local-pc1-1-phase-3e-smoke"
    assert issue_dir.is_dir()
    issue_md = (issue_dir / "issue.md").read_text()
    assert "state: open" in issue_md
    assert "slug: phase-3e-smoke" in issue_md


def test_issue_list_includes_local_id(fresh_repo: Path) -> None:
    _init_local(fresh_repo)
    _run_kaji(fresh_repo, "issue", "create", "--title", "x", "--body", "y", "--slug", "x-test")
    result = _run_kaji(fresh_repo, "issue", "list")
    assert result.returncode == 0, result.stderr
    assert "local-pc1-1" in result.stdout


def test_issue_close_writes_close_reason(fresh_repo: Path) -> None:
    _init_local(fresh_repo)
    _run_kaji(fresh_repo, "issue", "create", "--title", "x", "--body", "y", "--slug", "close-test")
    result = _run_kaji(fresh_repo, "issue", "close", "local-pc1-1")
    assert result.returncode == 0, result.stderr
    issue_md = (fresh_repo / ".kaji" / "issues" / "local-pc1-1-close-test" / "issue.md").read_text()
    assert "state: closed" in issue_md
    assert "close_reason: completed" in issue_md


# ============================================================
# fail-fast subprocess (Phase 3-e commit 5 で skip 解除)
# ============================================================


def test_failfast_issue_view_no_provider_section(fresh_repo: Path) -> None:
    result = _run_kaji(fresh_repo, "issue", "view", "1")
    assert result.returncode == 2
    assert "[provider]" in result.stderr


def test_failfast_pr_view_no_provider_section(fresh_repo: Path) -> None:
    result = _run_kaji(fresh_repo, "pr", "view", "153")
    assert result.returncode == 2
    assert "[provider]" in result.stderr


def test_failfast_run_no_provider_section_exits_2(fresh_repo: Path, tmp_path: Path) -> None:
    # workflow yaml は適当でよい — get_provider 早期 fail-fast で先に止まる
    wf = tmp_path / "wf.yaml"
    wf.write_text(
        "name: x\n"
        "execution_policy: auto\n"
        "steps:\n"
        "  - id: s1\n"
        "    skill: dummy\n"
        "    agent: claude\n"
        "    model: opus\n"
        "    on:\n"
        "      PASS: end\n"
    )
    result = _run_kaji(fresh_repo, "run", str(wf), "1")
    assert result.returncode == 2
    assert "[provider]" in result.stderr


def test_failfast_issue_view_no_config_toml(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    result = subprocess.run(
        [*_KAJI_CMD, "issue", "view", "1"],
        cwd=bare,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    err = result.stderr
    # phase3e-design.md § 9 文面: 「kaji repo が必要」+ base config 必要 +
    # GitHub / local-first 双方の導線を含むこと（local-first ガイドが
    # `kaji local init` 単独で完結しないことを明示する）
    assert ".kaji/config.toml not found" in err
    assert "[paths]" in err and "[execution]" in err
    assert "kaji local init" in err
    assert '"github"' in err and '"local"' in err


def test_local_init_alone_does_not_unblock_kaji_issue_when_base_config_missing(
    tmp_path: Path,
) -> None:
    """`kaji local init` は overlay しか作らないため、tracked `.kaji/config.toml`
    が無い bare directory では init 後も `kaji issue` が同じ fail-fast を返す。
    エラーメッセージの local-first 導線（base config 作成）が誤誘導しない
    ことを構造で担保するためのリグレッションガード。"""
    bare = tmp_path / "bare"
    bare.mkdir()
    rc = subprocess.run(
        [*_KAJI_CMD, "local", "init", "--machine-id", "pc1", "--non-interactive"],
        cwd=bare,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rc.returncode == 0, rc.stderr
    # overlay は作られたが tracked .kaji/config.toml は無い
    assert (bare / ".kaji" / "config.local.toml").exists()
    assert not (bare / ".kaji" / "config.toml").exists()
    # `kaji issue list` は config.toml not found で停止する（init 単独では足りない）
    result = subprocess.run(
        [*_KAJI_CMD, "issue", "list"],
        cwd=bare,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert ".kaji/config.toml not found" in result.stderr


def test_failfast_run_no_config_toml(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    wf = tmp_path / "wf.yaml"
    wf.write_text(
        "name: x\n"
        "execution_policy: auto\n"
        "steps:\n"
        "  - id: s1\n"
        "    skill: dummy\n"
        "    agent: claude\n"
        "    model: opus\n"
        "    on:\n"
        "      PASS: end\n"
    )
    result = subprocess.run(
        [*_KAJI_CMD, "run", str(wf), "1"],
        cwd=bare,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
