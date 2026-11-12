"""Large-local E2E: ``kaji status`` / ``kaji recover --retry-failed-step`` / series resume.

Issue #540: 実 ``kaji run`` / ``kaji recover`` / ``kaji status`` / ``kaji run-series`` を subprocess で
起動し、``exec:`` step だけの workflow と PATH 上の fake ``gh`` で外部依存なしに検証する。

- status → retry: 2 番目の step が初回だけ失敗する run を ``--retry-failed-step`` で再開する
- R1: Issue 解決中（fake ``gh issue view`` を flag で止める）の ``kaji run`` が alive / launching に見える
- R3: step 実行中に ``kaji run`` を SIGKILL した run が ``vanished`` / ``process_vanished`` になり再開できる
- R4: feature worktree と main worktree のどちらから ``kaji status`` を起動しても同じ結果になる
- series: 失敗 member を ``kaji run-series --resume`` が失敗 step から再開する
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_ISSUE = "99"

# exec step の実スクリプト。mode ごとに PASS verdict を KAJI_VERDICT_PATH へ書く。
_STEP_SCRIPT = """\
import os, sys, time

mode = sys.argv[1]


def passed():
    with open(os.environ["KAJI_VERDICT_PATH"], "w", encoding="utf-8") as f:
        f.write("status: PASS\\nreason: ok\\nevidence: exec step\\nsuggestion: ''\\n")


if mode == "pass":
    passed()
elif mode == "flaky":
    flag = sys.argv[2]
    if not os.path.exists(flag):
        open(flag, "w").close()
        print("boom: first attempt fails", file=sys.stderr)
        sys.exit(7)
    passed()
elif mode == "sleepy":
    flag, pid_file = sys.argv[2], sys.argv[3]
    if not os.path.exists(flag):
        open(flag, "w").close()
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        time.sleep(60)
        sys.exit(9)
    passed()
elif mode == "close":
    marker = os.path.join(sys.argv[2], "closed-" + os.environ["KAJI_ISSUE_ID"])
    open(marker, "w").close()
    passed()
"""

# fake gh: ``block-view`` があれば ``issue view`` を待たせ、``closed-<n>`` があれば closed/completed。
_FAKE_GH = """\
import json, os, sys, time

args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
log = os.path.join(here, "gh-comments.jsonl")


def comments():
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


if args[:1] == ["--version"]:
    print("gh version 2.60.0 (fake)")
elif args[:2] == ["issue", "view"]:
    deadline = time.time() + 60
    while os.path.exists(os.path.join(here, "block-view")) and time.time() < deadline:
        time.sleep(0.1)
    n = args[2]
    closed = os.path.exists(os.path.join(here, "closed-" + n))
    print(json.dumps({
        "number": int(n), "title": "test issue " + n, "body": "body",
        "state": "CLOSED" if closed else "OPEN",
        "stateReason": "COMPLETED" if closed else "",
        "labels": [{"name": "type:feature"}],
        "comments": [{"author": {"login": "kaji"}, "body": c["body"], "createdAt": ""}
                     for c in comments()],
    }))
elif args[:2] == ["issue", "comment"]:
    body = args[args.index("--body") + 1]
    seq = len(comments()) + 1
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"issue": args[2], "body": body}) + "\\n")
    print(f"https://github.com/owner/name/issues/{args[2]}#issuecomment-{seq}")
elif args[:2] == ["pr", "list"]:
    print("[]")
elif args[:1] == ["api"]:
    if "/comments" in args[-1]:
        print(json.dumps([[{"body": c["body"], "user": {"login": "kaji"}, "created_at": ""}
                           for c in comments()]]))
    else:
        print("[[]]")
else:
    print("fake gh: unsupported args: " + " ".join(args), file=sys.stderr)
    sys.exit(1)
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _gh_bin(repo: Path) -> Path:
    return repo.parent / "gh-bin"


def _workflow(repo: Path, name: str, steps: list[tuple[str, list[str]]]) -> Path:
    """``exec:`` step を順に並べた workflow を書く（最後の step の PASS で end）。"""
    lines = [
        f"name: {name}",
        "description: issue 540 e2e",
        "requires_provider: any",
        "execution_policy: auto",
        "",
        "steps:",
    ]
    for index, (step_id, argv) in enumerate(steps):
        nxt = steps[index + 1][0] if index + 1 < len(steps) else "end"
        exec_argv = json.dumps([sys.executable, str(repo / "step.py"), *argv])
        lines += [
            f"  - id: {step_id}",
            f"    exec: {exec_argv}",
            "    on:",
            f"      PASS: {nxt}",
            "      ABORT: end",
        ]
    path = repo / f"{name}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _make_repo(tmp_path: Path, *, worktree_issues: tuple[str, ...] = (_ISSUE,)) -> Path:
    """main worktree と ``kaji-feat-<issue>`` worktree（retry のゲート用）を持つ repo を作る。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.email", "e2e@example.invalid")
    _git(repo, "config", "user.name", "e2e")
    (repo / ".kaji").mkdir()
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 120\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(".kaji-artifacts/\n", encoding="utf-8")
    (repo / "step.py").write_text(_STEP_SCRIPT, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    for issue in worktree_issues:
        _git(
            repo,
            "worktree",
            "add",
            "-q",
            "-b",
            f"feat/{issue}",
            str(tmp_path / f"kaji-feat-{issue}"),
        )
    bin_dir = _gh_bin(repo)
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return repo


def _env(repo: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{_gh_bin(repo)}{os.pathsep}{env['PATH']}"
    worktree_root = Path(__file__).resolve().parents[1]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{worktree_root}{os.pathsep}{existing}" if existing else str(worktree_root)
    return env


def _argv(*args: str) -> list[str]:
    return [sys.executable, "-m", "kaji_harness.cli_main", *args]


def _kaji(repo: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _argv(*args),
        cwd=cwd or repo,
        env=_env(repo),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _status(repo: Path, cwd: Path | None = None) -> dict[str, Any]:
    result = _kaji(repo, "status", _ISSUE, "--json", cwd=cwd)
    assert result.returncode == 0, result.stderr
    issue: dict[str, Any] = json.loads(result.stdout)["issues"][0]
    return issue


def _runs(repo: Path, issue: str = _ISSUE) -> list[Path]:
    runs = repo / ".kaji-artifacts" / issue / "runs"
    return sorted(p for p in runs.iterdir() if p.is_dir())


def _chain(run_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((run_dir / "recovery-chain.json").read_text(encoding="utf-8"))
    return data


def _wait_for(predicate: Any, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError("condition was not met in time")


def test_status_then_retry_failed_step_e2e(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    flag = tmp_path / "work.flag"
    workflow = _workflow(repo, "flaky", [("prepare", ["pass"]), ("work", ["flaky", str(flag)])])

    first = _kaji(repo, "run", str(workflow), _ISSUE, "--workdir", str(repo))
    assert first.returncode == 3, first.stderr
    issue = _status(repo)
    run = issue["latest_run"]
    assert run["termination"] == "ended"
    assert run["end_status"] == "ERROR"
    assert run["failed_step"] == "work"
    assert issue["any_run_alive"] is False
    failed_run_id = run["run_id"]

    retry = _kaji(
        repo, "recover", str(workflow), _ISSUE, "--retry-failed-step", "--workdir", str(repo)
    )
    assert retry.returncode == 0, f"stdout:\n{retry.stdout}\nstderr:\n{retry.stderr}"
    runs = _runs(repo)
    assert len(runs) == 2
    child = runs[-1]
    chain = _chain(child)
    assert chain["parent_run_id"] == failed_run_id
    assert chain["trigger"] == "retry_failed_step"
    events = [json.loads(line) for line in (child / "run.log").read_text().splitlines()]
    assert [e["step_id"] for e in events if e["event"] == "step_start"] == ["work"]
    assert _status(repo)["latest_run"]["end_status"] == "COMPLETE"

    again = _kaji(
        repo,
        "recover",
        str(workflow),
        _ISSUE,
        "--retry-failed-step",
        "--run-id",
        failed_run_id,
        "--workdir",
        str(repo),
    )
    assert again.returncode == 4
    assert "retry-gate: newer_run_detected" in again.stderr
    assert len(_runs(repo)) == 2


def test_status_reports_read_errors_and_rejects_bad_arguments(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, worktree_issues=())
    run_dir = repo / ".kaji-artifacts" / _ISSUE / "runs" / "261111000000"
    run_dir.mkdir(parents=True)
    (run_dir / "run.log").write_text("{not json\n", encoding="utf-8")

    result = _kaji(repo, "status", _ISSUE, "--json")
    assert result.returncode == 0, result.stderr
    issue = json.loads(result.stdout)["issues"][0]
    assert issue["read_errors"]

    bad = _kaji(repo, "status", _ISSUE, "--series", "acceptance")
    assert bad.returncode == 2
    assert "either" in bad.stderr


def test_launching_run_is_alive_while_resolving_issue(tmp_path: Path) -> None:
    # R1: provider の Issue 解決中でも launch record により alive / launching に見える
    repo = _make_repo(tmp_path)
    workflow = _workflow(repo, "ok", [("only", ["pass"])])
    block = _gh_bin(repo) / "block-view"
    block.touch()
    proc = subprocess.Popen(
        _argv("run", str(workflow), _ISSUE, "--workdir", str(repo)),
        cwd=repo,
        env=_env(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:

        def launching() -> bool:
            issue = _status(repo)
            return bool(issue["any_run_alive"] and issue["launching"])

        _wait_for(launching)
        block.unlink()
        assert proc.wait(timeout=120) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    issue = _status(repo)
    assert issue["any_run_alive"] is False
    assert issue["launching"] is False
    launches = repo / ".kaji-artifacts" / _ISSUE / "launches"
    assert [p.name for p in launches.glob("*.json")] == []


def test_killed_run_is_vanished_and_retry_resumes_from_its_step(tmp_path: Path) -> None:
    # R3: kaji run を step 実行中に SIGKILL → vanished / process_vanished → 同じ step から再開
    repo = _make_repo(tmp_path)
    flag = tmp_path / "nap.flag"
    pid_file = tmp_path / "nap.pid"
    workflow = _workflow(
        repo, "sleepy", [("prepare", ["pass"]), ("nap", ["sleepy", str(flag), str(pid_file)])]
    )
    proc = subprocess.Popen(
        _argv("run", str(workflow), _ISSUE, "--workdir", str(repo)),
        cwd=repo,
        env=_env(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    nap_pid: int | None = None
    try:
        _wait_for(lambda: pid_file.is_file() and pid_file.read_text().strip() != "")
        nap_pid = int(pid_file.read_text().strip())
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if nap_pid is not None:
            try:
                os.kill(nap_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    run = _status(repo)["latest_run"]
    assert run["termination"] == "vanished"
    assert run["failed_step"] == "nap"
    assert run["failure"]["cause"] == "process_vanished"

    retry = _kaji(
        repo, "recover", str(workflow), _ISSUE, "--retry-failed-step", "--workdir", str(repo)
    )
    assert retry.returncode == 0, f"stdout:\n{retry.stdout}\nstderr:\n{retry.stderr}"
    child = _runs(repo)[-1]
    events = [json.loads(line) for line in (child / "run.log").read_text().splitlines()]
    assert [e["step_id"] for e in events if e["event"] == "step_start"] == ["nap"]
    assert _chain(child)["trigger"] == "retry_failed_step"


def test_status_is_the_same_from_feature_and_main_worktrees(tmp_path: Path) -> None:
    # R4: 呼び出し元 worktree によらず main 側 artifacts を読む
    repo = _make_repo(tmp_path)
    workflow = _workflow(repo, "ok", [("only", ["pass"])])
    assert _kaji(repo, "run", str(workflow), _ISSUE, "--workdir", str(repo)).returncode == 0
    from_main = _status(repo, cwd=repo)
    from_feature = _status(repo, cwd=tmp_path / f"kaji-feat-{_ISSUE}")
    assert from_main["latest_run"] is not None
    assert from_main == from_feature


def test_run_series_resume_restarts_failed_member_from_failed_step(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, worktree_issues=("501",))
    gh_dir = str(_gh_bin(repo))
    flag = tmp_path / "member.flag"
    _workflow(
        repo,
        "member-a",
        [("prepare", ["pass"]), ("work", ["flaky", str(flag)]), ("close", ["close", gh_dir])],
    )
    _workflow(repo, "member-b", [("close", ["close", gh_dir])])
    series = repo / ".kaji" / "series" / "pair.yaml"
    series.parent.mkdir(parents=True)
    series.write_text(
        "id: pair\n"
        "strategy: sequential\n"
        "members:\n"
        "  - issue: 501\n"
        "    workflow: member-a.yaml\n"
        "  - issue: 502\n"
        "    workflow: member-b.yaml\n"
        "on_failure: stop\n",
        encoding="utf-8",
    )

    first = _kaji(repo, "run-series", str(series))
    assert first.returncode == 1, f"stdout:\n{first.stdout}\nstderr:\n{first.stderr}"
    state_path = repo / ".kaji-artifacts" / "_series" / "pair" / "state.json"
    stopped = json.loads(state_path.read_text(encoding="utf-8"))
    assert [m["status"] for m in stopped["members"]] == ["failed", "pending"]

    resumed = _kaji(repo, "run-series", str(series), "--resume")
    assert resumed.returncode == 0, f"stdout:\n{resumed.stdout}\nstderr:\n{resumed.stderr}"
    final = json.loads(state_path.read_text(encoding="utf-8"))
    assert final["status"] == "completed"
    assert [m["status"] for m in final["members"]] == ["completed", "completed"]
    child = _runs(repo, "501")[-1]
    assert _chain(child)["trigger"] == "series_resume"
    events = [json.loads(line) for line in (child / "run.log").read_text().splitlines()]
    assert [e["step_id"] for e in events if e["event"] == "step_start"] == ["work", "close"]
