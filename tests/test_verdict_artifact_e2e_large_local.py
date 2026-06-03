"""Large-local E2E: artifact verdict.yaml resolution through the real CLI entrypoint.

実 ``kaji run`` を subprocess で起動し、PATH 上に置いた fake ``claude`` stub が
``verdict.yaml`` を書き + stdout に **乖離した** verdict block (RETRY) を出す状況で、
harness が artifact (PASS) を primary 採用し、layout が
``steps/<step_id>/attempt-001/verdict.yaml`` になることを end-to-end 検証する。

real LLM は使わない（Issue #220 設計 § テスト戦略: 本機能は file I/O + 解決ロジック
であり real 推論経路は新規回帰情報を加えない）。fake stub で CLI 起動 → prompt への
``verdict_path`` 注入 → agent による artifact 書き込み → harness 解決の全配線を通す。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kaji_harness.providers import LocalProvider

pytestmark = [pytest.mark.large, pytest.mark.large_local]


_FAKE_CLAUDE = '''#!/usr/bin/env python3
"""Fake `claude` CLI: writes verdict.yaml from prompt, emits stream-json JSONL.

prompt 末尾引数から `- verdict_path: <path>` を抽出し、その path へ
artifact verdict.yaml (status: PASS) を書く。stdout には意図的に乖離した
RETRY verdict block を出し、harness が artifact(PASS) を優先することを検証可能にする。
"""
import json
import re
import sys

prompt = sys.argv[-1]
m = re.search(r"^- verdict_path: (.+)$", prompt, re.M)
if m:
    vpath = m.group(1).strip()
    with open(vpath, "w", encoding="utf-8") as f:
        f.write(
            "status: PASS\\n"
            "reason: e2e artifact primary\\n"
            "evidence: fake agent wrote verdict.yaml\\n"
            "suggestion: ''\\n"
        )

# stream-json JSONL（ClaudeAdapter 互換）。assistant text に乖離 RETRY block を仕込む。
print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake-sess-001"}))
divergent = (
    "作業ログ\\n\\n"
    "---VERDICT---\\n"
    "status: RETRY\\n"
    'reason: "stdout divergent (should be ignored)"\\n'
    'evidence: "stdout"\\n'
    'suggestion: ""\\n'
    "---END_VERDICT---"
)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": divergent}]}}))
print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.0}))
'''


_WORKFLOW_YAML = """name: e2e-verdict
description: single agent step for artifact verdict E2E
requires_provider: any
execution_policy: auto

steps:
  - id: implement
    skill: e2e-impl
    agent: claude
    on:
      PASS: end
      RETRY: end
      ABORT: end
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
    # minimal agent skill（frontmatter 無し → exec_script=None で agent step として通る）
    skill_dir = repo / ".claude" / "skills" / "e2e-impl"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# e2e-impl\n\nfixture agent skill for E2E.\n")
    (repo / "workflow.yaml").write_text(_WORKFLOW_YAML)
    return repo


def _make_fake_claude(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def test_fake_agent_artifact_primary_e2e(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    bin_dir = _make_fake_claude(tmp_path)

    # local issue 99 (type:feature) を作成
    counter = repo / ".kaji" / "counters" / "pc1.txt"
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text("98")
    provider = LocalProvider(repo_root=repo, machine_id="pc1")
    provider.create_issue(title="e2e", body="body", labels=["type:feature"], slug="e2e")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    # subprocess の ``python -m kaji_harness.cli_main`` が editable install 経由で
    # 別 worktree（main repo）の古い kaji_harness を読まないよう、本 worktree を
    # PYTHONPATH 先頭に置いて解決を固定する。tests/ は worktree root 直下。
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

    # layout: steps/<step_id>/attempt-001/verdict.yaml
    runs = repo / ".kaji-artifacts" / "local-pc1-99" / "runs"
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, f"expected 1 run dir, got {run_dirs}"
    attempt = run_dirs[0] / "steps" / "implement" / "attempt-001"
    vfile = attempt / "verdict.yaml"
    assert vfile.exists(), f"verdict.yaml missing; attempt contents: {list(attempt.iterdir())}"

    # artifact が primary 採用される（stdout の RETRY ではなく PASS）
    text = vfile.read_text(encoding="utf-8")
    assert "status: PASS" in text
    assert "e2e artifact primary" in text

    # run.log の verdict_source が artifact
    sources = [
        json.loads(line)
        for line in (run_dirs[0] / "run.log").read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "verdict_source"
    ]
    assert sources and sources[-1]["source"] == "artifact"
    assert sources[-1]["attempt"] == "attempt-001"
    # prompt.txt も attempt に保存されている
    assert (attempt / "prompt.txt").exists()
