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
import subprocess
import sys
from pathlib import Path

import pytest

from kaji_harness.providers import LocalProvider

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


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 60\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
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

    # local issue 99 (type:feature) を作成
    counter = repo / ".kaji" / "counters" / "pc1.txt"
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text("98")
    provider = LocalProvider(repo_root=repo, machine_id="pc1")
    provider.create_issue(title="e2e exec", body="body", labels=["type:feature"], slug="e2e-exec")

    env = dict(os.environ)
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
    runs = repo / ".kaji-artifacts" / "local-pc1-99" / "runs"
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
    assert "exec-step ran; issue=local-pc1-99" in stdout_log.read_text(encoding="utf-8")

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
