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
    fake_bin: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the installed module entry point in a temporary repository.

    ``fake_bin`` が与えられた場合は PATH 先頭に置き、fake ``gh`` を優先させる。
    """
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    if fake_bin is not None:
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", *args],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
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


# ------------------------------------------------------------
# kaji issue resolve-verdict（github provider + fake gh）
# ------------------------------------------------------------

# subprocess 起動の kaji には monkeypatch が効かないため、PATH 先頭に置く fake
# ``gh`` が Issue comment を同 dir の ``comments.json`` に保存 / 返却する。
_FAKE_GH = """\
#!{python}
import json
import sys
from pathlib import Path

store = Path(__file__).with_name("comments.json")
args = sys.argv[1:]


def _load():
    return json.loads(store.read_text()) if store.exists() else []


if args[:1] == ["--version"]:
    print("gh version 2.83.0 (2025-01-01)")
    sys.exit(0)
if args[:2] == ["issue", "comment"]:
    comments = _load()
    body = args[args.index("--body") + 1]
    comments.append({{
        "body": body, "user": {{"login": "kamo"}},
        "created_at": f"2026-01-01T00:00:{{len(comments):02d}}Z",
    }})
    store.write_text(json.dumps(comments))
    print(f"https://github.com/owner/name/issues/{{args[2]}}#issuecomment-{{len(comments)}}")
    sys.exit(0)
if args[:3] == ["api", "--paginate", "--slurp"] and "/issues/1/comments" in args[-1]:
    print(json.dumps([_load()]))
    sys.exit(0)
sys.stderr.write(f"fake gh: unsupported args {{args}}\\n")
sys.exit(1)
"""


def _make_github_repo(tmp_path: Path) -> tuple[Path, Path]:
    """github provider 設定の tmp repo と fake ``gh`` 入りの bin dir を作る。"""
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
        "default_timeout = 1800\n\n"
        "[provider]\n"
        'type = "github"\n\n'
        "[provider.github]\n"
        'repo = "owner/name"\n',
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text(_FAKE_GH.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    return repo, fake_bin


def test_issue_resolve_verdict_cli_dispatch(tmp_path: Path) -> None:
    repo, fake_bin = _make_github_repo(tmp_path)
    commented = _run_kaji(
        repo,
        "issue",
        "comment",
        "1",
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
        fake_bin=fake_bin,
    )
    assert commented.returncode == 0, commented.stderr

    resolved = _run_kaji(
        repo,
        "issue",
        "resolve-verdict",
        "1",
        "--step",
        "review-starter-update",
        "--require-meta",
        "target",
        "--require-meta",
        "base",
        "--require-meta",
        "candidate",
        fake_bin=fake_bin,
    )

    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["meta"]["candidate"] == "bbb"


def test_issue_resolve_verdict_not_found_exit_code(tmp_path: Path) -> None:
    repo, fake_bin = _make_github_repo(tmp_path)

    resolved = _run_kaji(
        repo,
        "issue",
        "resolve-verdict",
        "1",
        "--step",
        "review-starter-update",
        fake_bin=fake_bin,
    )

    assert resolved.returncode == 4, resolved.stderr


def test_issue_resolve_verdict_artifact_fallback_via_run_option(tmp_path: Path) -> None:
    repo, fake_bin = _make_github_repo(tmp_path)
    commented = _run_kaji(
        repo, "issue", "comment", "1", "--body", "report without marker", fake_bin=fake_bin
    )
    assert commented.returncode == 0, commented.stderr
    attempt = (
        repo
        / ".kaji-artifacts"
        / "1"
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
        "1",
        "--step",
        "implement-precheck",
        "--run",
        "260903210335",
        fake_bin=fake_bin,
    )

    assert resolved.returncode == 0, resolved.stderr
    payload = json.loads(resolved.stdout)
    assert payload["source"] == "artifact"
    assert payload["status"] == "PASS"
    assert payload["run_id"] == "260903210335"
    assert payload["verdict_path"].endswith("attempt-001/verdict.yaml")
