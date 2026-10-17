"""Large-local E2E: exec-step dispatch through the real ``kaji run`` entrypoint (Issue #205).

実 ``kaji run`` を subprocess で起動し、workflow.yaml の ``exec:`` step が実 subprocess
として実行され、artifact (``verdict.yaml``) 経路で verdict が解決され、attempt 配下の
artifact レイアウト（``stdout.log`` / ``verdict.yaml`` / ``result.json``）が生成される
ことを end-to-end で検証する。

``test_exec_script_subprocess_large.py`` / ``test_verdict_artifact_e2e_large_local.py`` と
同じ ``large_local`` 方針（実 subprocess あり・外部ネットワーク疎通なし）。CLI wiring・
config 探索・artifact レイアウトの結合は Medium の runner 単体では覆えないため、exec の
新 dispatch entrypoint に対し同種の回帰シグナルを確保する。
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


# exec-step が実行する実スクリプト。KAJI_VERDICT_PATH に PASS verdict を書き
# (artifact-primary)、context env を stdout に出して dispatch を可視化する。
_EXEC_SCRIPT = """\
import os

vpath = os.environ["KAJI_VERDICT_PATH"]
with open(vpath, "w", encoding="utf-8") as f:
    f.write(
        "status: PASS\\n"
        "reason: exec-step e2e\\n"
        "evidence: exec step wrote verdict.yaml\\n"
        "suggestion: ''\\n"
    )
print("exec-step ran; issue=" + os.environ.get("KAJI_ISSUE_ID", "?"))
print("step=" + os.environ.get("KAJI_STEP_ID", "?"))
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
    # exec-step が実行するスクリプト。skill ファイルは作らない（exec-step は不要）。
    script = repo / "exec_step.py"
    script.write_text(_EXEC_SCRIPT, encoding="utf-8")
    # exec はリスト形式で interpreter を sys.executable に固定する（PATH 非依存）。
    exec_list = json.dumps([sys.executable, str(script)])
    (repo / "workflow.yaml").write_text(
        "name: e2e-exec\n"
        "description: single exec step E2E\n"
        "requires_provider: any\n"
        "execution_policy: auto\n\n"
        "steps:\n"
        "  - id: collect\n"
        f"    exec: {exec_list}\n"
        "    on:\n"
        "      PASS: end\n"
        "      ABORT: end\n"
    )
    return repo


def test_exec_step_run_e2e(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    # github Issue 99 (type:feature) を fake gh で応答させる。
    gh_bin = tmp_path / "gh-bin"
    _install_fake_gh(gh_bin)

    env = dict(os.environ)
    env["PATH"] = f"{gh_bin}{os.pathsep}{env['PATH']}"
    # subprocess の ``python -m kaji_harness.cli_main`` が別 worktree の古い
    # kaji_harness を読まないよう、本 worktree を PYTHONPATH 先頭に固定する。
    worktree_root = Path(__file__).resolve().parents[1]
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{worktree_root}{os.pathsep}{existing_pp}" if existing_pp else str(worktree_root)
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kaji_harness.cli_main",
            "run",
            str(repo / "workflow.yaml"),
            "99",
            "--workdir",
            str(repo),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    # layout: steps/<step_id>/attempt-001/{verdict.yaml,result.json,stdout.log}
    runs = repo / ".kaji-artifacts" / "99" / "runs"
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, f"expected 1 run dir, got {run_dirs}"
    attempt = run_dirs[0] / "steps" / "collect" / "attempt-001"

    # verdict.yaml が artifact 経路で生成され PASS。
    vfile = attempt / "verdict.yaml"
    assert vfile.exists(), f"verdict.yaml missing; attempt contents: {list(attempt.iterdir())}"
    vtext = vfile.read_text(encoding="utf-8")
    assert "status: PASS" in vtext
    assert "exec-step e2e" in vtext

    # stdout.log に exec script の出力が残る。
    stdout_log = attempt / "stdout.log"
    assert stdout_log.exists()
    assert "exec-step ran; issue=99" in stdout_log.read_text(encoding="utf-8")

    # result.json の dispatch が "exec"、session_id は None。
    result_json = json.loads((attempt / "result.json").read_text(encoding="utf-8"))
    assert result_json["dispatch"] == "exec"
    assert result_json["status"] == "PASS"
    assert result_json["session_id"] is None

    # run.log の verdict_source が artifact。
    sources = [
        json.loads(line)
        for line in (run_dirs[0] / "run.log").read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "verdict_source"
    ]
    assert sources and sources[-1]["source"] == "artifact"
