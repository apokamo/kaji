"""Network-free subprocess coverage for starter CLI dispatch."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]


def _run_kaji(
    repo: Path,
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the installed module entry point in a temporary repository."""
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", *args],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )


def test_starter_release_plan_cli_dispatch() -> None:
    payload = {
        "target_kaji_release": "v0.16.0",
        "candidate_sha": "abc123",
        "tags": [],
        "releases": [],
        "state_table_row_exists": True,
        "state_table_status": "PENDING",
        "tracking_issue_state": "open",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "release-plan"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["tag"] == "kaji-v0.16.0"


def test_starter_release_plan_invalid_json_exits_two() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "release-plan"],
        input="not-json",
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2


def test_issue_resolve_verdict_cli_dispatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n\n'
        "[execution]\n"
        "default_timeout = 1800\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("", encoding="utf-8")
    initialized = _run_kaji(
        repo,
        "local",
        "init",
        "--machine-id",
        "pc1",
        "--non-interactive",
    )
    assert initialized.returncode == 0, initialized.stderr
    created = _run_kaji(
        repo,
        "issue",
        "create",
        "--title",
        "starter review",
        "--body",
        "tracking",
        "--slug",
        "starter-review",
    )
    assert created.returncode == 0, created.stderr
    commented = _run_kaji(
        repo,
        "issue",
        "comment",
        "local-pc1-1",
        "--body",
        "reviewed",
        "--verdict-step",
        "review-starter-update",
        "--verdict-status",
        "PASS",
        "--verdict-meta",
        "target=v0.16.0",
        "--verdict-meta",
        "base=aaa",
        "--verdict-meta",
        "candidate=bbb",
    )
    assert commented.returncode == 0, commented.stderr

    resolved = _run_kaji(
        repo,
        "issue",
        "resolve-verdict",
        "local-pc1-1",
        "--step",
        "review-starter-update",
        "--require-meta",
        "target",
        "--require-meta",
        "base",
        "--require-meta",
        "candidate",
    )

    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["meta"]["candidate"] == "bbb"


def test_issue_resolve_verdict_not_found_exit_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n\n'
        "[execution]\n"
        "default_timeout = 1800\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("", encoding="utf-8")
    assert (
        _run_kaji(repo, "local", "init", "--machine-id", "pc1", "--non-interactive").returncode == 0
    )
    assert (
        _run_kaji(
            repo,
            "issue",
            "create",
            "--title",
            "starter review",
            "--body",
            "tracking",
            "--slug",
            "starter-review",
        ).returncode
        == 0
    )

    resolved = _run_kaji(
        repo,
        "issue",
        "resolve-verdict",
        "local-pc1-1",
        "--step",
        "review-starter-update",
    )

    assert resolved.returncode == 4
