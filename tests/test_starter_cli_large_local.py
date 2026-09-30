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


def test_starter_tracking_plan_cli_dispatch() -> None:
    payload = {
        "starter_repo": "apokamo/kaji-starter-python",
        "new_target": "v0.20.2",
        "open_tracking_issues": [],
        "selected_issue_id": None,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "tracking-plan"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body["decision"] == "CREATE"
    assert body["route"] == 1


def test_starter_tracking_plan_invalid_json_exits_two() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "tracking-plan"],
        input="not-json",
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2


def test_starter_tracking_plan_body_parse_failure_still_exits_zero() -> None:
    """観測矛盾（本文 parse 失敗）は decision: ABORT の plan を exit 0 で返す。"""
    payload = {
        "starter_repo": "apokamo/kaji-starter-python",
        "new_target": "v0.20.2",
        "open_tracking_issues": [
            {"issue_id": 424, "body": "starter_repo: apokamo/kaji-starter-python\nbroken\n"}
        ],
        "selected_issue_id": None,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "tracking-plan"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["decision"] == "ABORT"


def test_starter_task_plan_cli_dispatch() -> None:
    body = (
        "<!-- kaji-starter-sync: v1 -->\n"
        "starter_repo: apokamo/kaji-starter-python\n\n"
        "## Sync tasks\n\n"
        "| target_kaji_release | status | batch | result |\n"
        "|---|---|---|---|\n"
        "| v0.20.0 | open | - | - |\n"
    )
    payload = {
        "issue_id": 424,
        "issue_state": "open",
        "body": body,
        "completion": None,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "task-plan"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    assert plan["decision"] == "SYNC"
    assert plan["route"] == 2
    assert plan["batch"] == "b1"


def test_starter_task_plan_invalid_json_exits_two() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "task-plan"],
        input="not-json",
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2


def test_starter_unknown_subcommand_prints_help_and_exits_nonzero() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "starter", "bogus-command"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0


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


def test_issue_resolve_verdict_artifact_fallback_via_run_option(tmp_path: Path) -> None:
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
            "artifact fallback",
            "--body",
            "tracking",
            "--slug",
            "artifact-fallback",
        ).returncode
        == 0
    )
    assert (
        _run_kaji(
            repo, "issue", "comment", "local-pc1-1", "--body", "report without marker"
        ).returncode
        == 0
    )
    attempt = (
        repo
        / ".kaji-artifacts"
        / "local-pc1-1"
        / "runs"
        / "260903210335"
        / "steps"
        / "implement-precheck"
        / "attempt-001"
    )
    attempt.mkdir(parents=True)
    (attempt / "verdict.yaml").write_text(
        "status: PASS\nreason: r\nevidence: e\nsuggestion: ''\n", encoding="utf-8"
    )

    resolved = _run_kaji(
        repo,
        "issue",
        "resolve-verdict",
        "local-pc1-1",
        "--step",
        "implement-precheck",
        "--run",
        "260903210335",
    )

    assert resolved.returncode == 0, resolved.stderr
    payload = json.loads(resolved.stdout)
    assert payload["source"] == "artifact"
    assert payload["status"] == "PASS"
    assert payload["run_id"] == "260903210335"
    assert payload["verdict_path"].endswith("attempt-001/verdict.yaml")
