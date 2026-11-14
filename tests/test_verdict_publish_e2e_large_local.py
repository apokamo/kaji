"""Large-local E2E: agent が prompt の ``kaji verdict publish`` コマンドで verdict を公開する。

Issue #568: 実 ``kaji run`` を subprocess で起動し、PATH 上の fake ``claude`` が prompt の
手順 3 に示された bash コマンドを（値だけ差し替えて）実 bash で実行する。最初は ``on:`` に
ない status で exit 1 を受け、同じ attempt 内で訂正して公開する。#551 型の値（複数行・
``: ``・``#``・引用符・``\\``・``$``・Unicode）が artifact source でそのまま採用されることを
end-to-end で検証する。実 LLM と実 Herdr は使わない（設計書 § テスト戦略 Large）。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kaji_harness.verdict import load_verdict_yaml

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_REASON = "Issue #551 の worktree セットアップが完了"
_EVIDENCE = (
    "fetch した origin/main: FETCH_HEAD と一致 # comment\n"
    "- 2行目 'single' \"double\" \\backslash $HOME `whoami`\n"
    "  字下げされた 3 行目 🎉"
)

_FAKE_CLAUDE = r'''
"""Fake `claude`: runs the prompt's publish command through real bash, then emits stream-json."""
import json
import os
import subprocess
import sys

REASON = __REASON__
EVIDENCE = __EVIDENCE__

prompt = sys.argv[-1]
lines = prompt.splitlines()
step3 = next(i for i, line in enumerate(lines) if line.startswith("3. "))
start = next(i for i in range(step3, len(lines)) if lines[i] == "```bash")
end = next(i for i in range(start + 1, len(lines)) if lines[i] == "```")
block = "\n".join(lines[start + 1 : end])
kaji = f"{sys.executable} -m kaji_harness.cli_main verdict publish"


def render(status):
    command = block.replace("kaji verdict publish", kaji, 1)
    command = command.replace("--status=STATUS", f"--status={status}", 1)
    command = command.replace("判定理由", REASON, 1)
    command = command.replace("具体的根拠（複数行可。抽象表現禁止）", EVIDENCE, 1)
    return command.replace("次のアクション提案", "", 1)


record = {}
for label, status in (("invalid", "BACK"), ("valid", "PASS")):
    done = subprocess.run(["bash", "-c", render(status)], capture_output=True, text=True)
    record[label] = {"rc": done.returncode, "out": done.stdout, "err": done.stderr}
with open(os.environ["FAKE_RECORD"], "w", encoding="utf-8") as f:
    json.dump(record, f, ensure_ascii=False)

print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake-sess-568"}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.0}))
'''

_FAKE_GH = '''#!/usr/bin/env python3
"""Fake `gh` CLI: subprocess の kaji run に決定的な Issue / PR 応答を返す。"""
import json
import sys

args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("gh version 2.83.0 (2025-01-01)")
    sys.exit(0)
if args[:2] == ["issue", "view"]:
    n = args[2]
    print(json.dumps({
        "number": int(n), "title": "e2e", "body": "body", "state": "OPEN",
        "stateReason": None, "labels": [{"name": "type:bug"}], "comments": [],
    }))
    sys.exit(0)
if args[:2] == ["pr", "list"]:
    print("[]")
    sys.exit(0)
sys.stderr.write(f"fake gh: unsupported args {args}\\n")
sys.exit(1)
'''

_WORKFLOW_YAML = """name: e2e-verdict-publish
description: single agent step publishing its verdict through kaji verdict publish
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
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
    )
    skill_dir = repo / ".claude" / "skills" / "e2e-impl"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# e2e-impl\n\nfixture agent skill for E2E.\n")
    (repo / "workflow.yaml").write_text(_WORKFLOW_YAML)
    return repo


def _install(bin_dir: Path, name: str, text: str) -> None:
    path = bin_dir / name
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_fakes(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # fake claude は kaji_harness を import する interpreter（本 venv）で実行する
    claude = _FAKE_CLAUDE.replace("__REASON__", repr(_REASON)).replace(
        "__EVIDENCE__", repr(_EVIDENCE)
    )
    _install(bin_dir, "claude", f"#!{sys.executable}\n{claude.lstrip()}")
    _install(bin_dir, "gh", _FAKE_GH)
    return bin_dir


def test_agent_publishes_verdict_with_prompt_command(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    bin_dir = _make_fakes(tmp_path)
    record_path = tmp_path / "fake-record.json"

    env = dict(os.environ)
    env.pop("KAJI_VERDICT_CONTRACT", None)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_RECORD"] = str(record_path)
    # subprocess（kaji run と fake agent 内の publish）が本 worktree の kaji_harness を読むよう固定する
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
        timeout=120,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    record = json.loads(record_path.read_text(encoding="utf-8"))

    # 1 回目: on: にない status は exit 1 で拒否され、artifact は作られない
    assert record["invalid"]["rc"] == 1, record
    assert "allowed statuses: PASS, RETRY, ABORT" in record["invalid"]["err"]
    # 2 回目: 同じ attempt 内で訂正して公開できる
    assert record["valid"]["rc"] == 0, record
    assert record["valid"]["out"].startswith("verdict published: step=implement attempt=001")

    run_dirs = [p for p in (repo / ".kaji-artifacts" / "99" / "runs").iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    attempt = run_dirs[0] / "steps" / "implement" / "attempt-001"
    assert (attempt / "verdict-contract.json").is_file()
    verdict = load_verdict_yaml(attempt / "verdict.yaml", {"PASS", "RETRY", "ABORT"})
    assert verdict.status == "PASS"
    assert verdict.reason == _REASON
    assert verdict.evidence == _EVIDENCE
    assert verdict.suggestion == ""
    assert [p.name for p in attempt.iterdir() if p.name.endswith(".tmp")] == []

    events = [
        json.loads(line)
        for line in (run_dirs[0] / "run.log").read_text(encoding="utf-8").splitlines()
    ]
    sources = [e for e in events if e.get("event") == "verdict_source"]
    assert sources and sources[-1]["source"] == "artifact"
    assert not [e for e in events if e.get("event") == "verdict_sanitization"]
