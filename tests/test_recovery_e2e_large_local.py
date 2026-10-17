"""Large-local E2E: failure triage through the real ``kaji run`` / ``kaji recover`` entrypoints.

Issue #288: 必ず失敗する ``exec:`` step を持つ workflow を実 subprocess で ``kaji run`` し、
(a) ``recovery.json`` が保存され、(b) github provider（PATH 上の fake ``gh``）経由で triage コメントが
投稿され、(c) stderr に triage サマリが出ることを検証する。続けて ``kaji recover --run-id``
が同一 handler を失敗 artifact から再起動できることを確認する。

10 分の実ウェイトは CI で再現不能なため、本 E2E は auto recovery を起動しない
（``auto_recover`` は default 無効）。ウェイト順序と child 起動は
``tests/test_recovery_handler.py`` の ``wait_seconds`` 注入で検証する。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_ISSUE_DIR = "99"

# 常に非ゼロ終了する exec step（verdict.yaml を書かない）。
_FAILING_SCRIPT = """\
import sys

print("boom: deterministic step failure", file=sys.stderr)
sys.exit(7)
"""


# Issue #448: local provider 削除に伴い、subprocess の ``kaji run`` は github provider で
# 実行する。PATH 先頭に置く fake ``gh`` が ``gh issue view`` / ``gh issue comment`` /
# ``gh pr list`` / ``gh api`` に決定的に応答する（コメントは同ディレクトリの jsonl に記録）。
_FAKE_GH = """\
import json, os, sys

args = sys.argv[1:]
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh-comments.jsonl")


def comments():
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


if args[:1] == ["--version"]:
    print("gh version 2.60.0 (fake)")
elif args[:2] == ["issue", "view"]:
    n = args[2]
    print(json.dumps({
        "number": int(n), "title": "test issue " + n, "body": "body", "state": "OPEN",
        "stateReason": "", "labels": [{"name": "type:feature"}],
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
    query = args[-1]
    if "/comments" in query:
        print(json.dumps([[{"body": c["body"], "user": {"login": "kaji"}, "created_at": ""}
                           for c in comments()]]))
    else:
        print("[[]]")
else:
    print("fake gh: unsupported args: " + " ".join(args), file=sys.stderr)
    sys.exit(1)
"""


def _install_fake_gh(bin_dir: Path) -> Path:
    """``bin_dir/gh`` に fake gh を置き、記録コメントの jsonl path を返す。"""
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir / "gh-comments.jsonl"


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
    )
    script = repo / "boom.py"
    script.write_text(_FAILING_SCRIPT, encoding="utf-8")
    exec_list = json.dumps([sys.executable, str(script)])
    (repo / "workflow.yaml").write_text(
        "name: e2e-fail\n"
        "description: always failing exec step\n"
        "requires_provider: any\n"
        "execution_policy: auto\n\n"
        "steps:\n"
        "  - id: collect\n"
        f"    exec: {exec_list}\n"
        "    on:\n"
        "      PASS: end\n"
        "      ABORT: end\n"
    )
    _install_fake_gh(_gh_bin(repo))
    return repo


def _gh_bin(repo: Path) -> Path:
    return repo.parent / "gh-bin"


def _env(repo: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{_gh_bin(repo)}{os.pathsep}{env['PATH']}"
    worktree_root = Path(__file__).resolve().parents[1]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{worktree_root}{os.pathsep}{existing}" if existing else str(worktree_root)
    return env


def _kaji(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", *args],
        cwd=repo,
        env=_env(repo),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _comment_bodies(repo: Path) -> list[str]:
    log = _gh_bin(repo) / "gh-comments.jsonl"
    if not log.is_file():
        return []
    lines = log.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["body"] for line in lines if line.strip()]


def test_failure_triage_e2e_and_manual_recover(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    result = _kaji(repo, "run", str(repo / "workflow.yaml"), "99", "--workdir", str(repo))

    # (c) stderr に triage サマリ。exit code は既存 map（ランタイムエラー = 3）のまま。
    assert result.returncode == 3, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "--- failure triage ---" in result.stderr
    assert "failed_step:    collect" in result.stderr
    assert "classification: dispatch_failure" in result.stderr

    # (a) recovery.json が保存される。
    runs = repo / ".kaji-artifacts" / _ISSUE_DIR / "runs"
    run_dirs = sorted(p for p in runs.iterdir() if p.is_dir())
    assert len(run_dirs) == 1
    recovery = json.loads((run_dirs[0] / "recovery.json").read_text(encoding="utf-8"))
    assert recovery["schema_version"] == 1
    assert recovery["classification"]["cause"] == "dispatch_failure"
    assert recovery["classification"]["synthetic"] is True
    assert recovery["failed_step"] == "collect"
    assert recovery["auto_recovery_attempted"] is False
    assert recovery["triage_comment_ref"].startswith(
        "https://github.com/owner/name/issues/99#issuecomment-"
    )

    # run.log に failure_event / recovery_decision が残る。
    events = [
        json.loads(line)
        for line in (run_dirs[0] / "run.log").read_text(encoding="utf-8").splitlines()
    ]
    kinds = {e["event"] for e in events}
    assert "failure_event" in kinds
    assert "recovery_decision" in kinds

    # (b) triage コメントが github provider（fake gh）経由で Issue に投稿される。
    bodies = _comment_bodies(repo)
    assert any("## Workflow failure triage" in b for b in bodies)
    assert any("dispatch_failure" in b for b in bodies)

    # Issue #304: 失敗 run E2E 後にローカル occurrence 記録が生成される（全 provider・全失敗）。
    occurrences = repo / ".kaji-artifacts" / "incidents" / "occurrences.jsonl"
    assert occurrences.is_file()
    rec = json.loads(occurrences.read_text(encoding="utf-8").splitlines()[0])
    assert rec["signature"]["cause"] == "dispatch_failure"
    assert rec["source_issue"] == _ISSUE_DIR

    # `kaji recover` が失敗 artifact から handler を再起動できる（triage のみ、exit 0）。
    recovered = _kaji(
        repo,
        "recover",
        str(repo / "workflow.yaml"),
        "99",
        "--run-id",
        run_dirs[0].name,
        "--workdir",
        str(repo),
    )
    assert recovered.returncode == 0, f"stdout:\n{recovered.stdout}\nstderr:\n{recovered.stderr}"
    assert "--- failure triage ---" in recovered.stderr
    assert len(_comment_bodies(repo)) == len(bodies) + 1


def test_recover_rejects_run_without_workflow_end(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    runs = repo / ".kaji-artifacts" / _ISSUE_DIR / "runs" / "260710120000"
    runs.mkdir(parents=True)
    (runs / "run.log").write_text(
        json.dumps({"event": "workflow_start", "issue": _ISSUE_DIR, "workflow": "e2e-fail"}) + "\n",
        encoding="utf-8",
    )

    result = _kaji(repo, "recover", str(repo / "workflow.yaml"), "99", "--workdir", str(repo))

    assert result.returncode == 2
    assert "still in progress" in result.stderr
