"""Large-local E2E: attempt deadline の単一計算元が実 dispatch 経路で一致する (Issue #421)。

- 実 ``kaji run`` + PATH 上の fake ``claude``（headless）: prompt の表示値の到達と
  hard deadline の一致

subprocess を使うがネットワーク疎通は無い。Herdr の実起動は含まない（実 Herdr pane 内からの
起動が必要で、CI / 本 test 環境では物理的に作成できない。設計書 § テスト戦略）。Herdr backend の
attempt deadline は ``tests/test_interactive_terminal_herdr.py`` が fake herdr CLI で検証する。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

# prompt から deadline 変数を読み取って記録し、PASS verdict を返す fake headless agent。
_FAKE_CLAUDE_RECORDING = """\
#!/usr/bin/env python3
import json, os, re, sys
text = sys.argv[-1]
record = {}
for key in ("step_timeout_seconds", "attempt_started_at_utc", "attempt_deadline_utc"):
    m = re.search(r"^- " + key + r": (.+)$", text, re.M)
    record[key] = m.group(1).strip() if m else None
verdict = re.search(r"^- verdict_path: (.+)$", text, re.M).group(1).strip()
with open(os.path.join(os.path.dirname(verdict), "deadline-record.json"), "w") as out:
    json.dump(record, out)
with open(verdict, "w", encoding="utf-8") as out:
    out.write("status: PASS\\nreason: deadline e2e\\nevidence: recorded\\nsuggestion: ''\\n")
print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake-sess"}))
print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.0}), flush=True)
"""

# verdict を出さず生存し続ける fake agent（hard deadline で kill されるまで）。
_FAKE_CLAUDE_HANGING = "#!/bin/sh\nexec sleep 600\n"


def _install_fake_claude(root: Path, body: str) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "claude"
    fake.write_text(body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


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


def _make_repo(tmp_path: Path, step_timeout: int) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 600\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
    )
    skill_dir = repo / ".claude" / "skills" / "e2e-impl"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# e2e-impl\n\nfixture agent skill for E2E.\n")
    (repo / "workflow.yaml").write_text(
        "name: e2e-deadline\n"
        "description: attempt deadline\n"
        "requires_provider: any\n"
        "execution_policy: auto\n\n"
        "steps:\n"
        "  - id: implement\n"
        "    skill: e2e-impl\n"
        "    agent: claude\n"
        f"    timeout: {step_timeout}\n"
        "    on:\n"
        "      PASS: end\n"
        "      ABORT: end\n"
    )
    return repo


def _run_kaji(repo: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    _install_fake_gh(bin_dir)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    worktree_root = Path(__file__).resolve().parents[1]
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{worktree_root}{os.pathsep}{existing_pp}" if existing_pp else str(worktree_root)
    )
    return subprocess.run(
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
        timeout=90,
    )


def _attempt_dir(repo: Path) -> Path:
    runs = repo / ".kaji-artifacts" / "99" / "runs"
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, f"expected 1 run dir, got {run_dirs}"
    return run_dirs[0] / "steps" / "implement" / "attempt-001"


def _truncate(moment: datetime) -> datetime:
    return moment.replace(microsecond=0)


def test_headless_prompt_shows_timeout_and_deadline_matching_result_start(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, step_timeout=120)
    bin_dir = _install_fake_claude(tmp_path, _FAKE_CLAUDE_RECORDING)

    proc = _run_kaji(repo, bin_dir)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    attempt = _attempt_dir(repo)
    record = json.loads((attempt / "deadline-record.json").read_text())
    result = json.loads((attempt / "result.json").read_text())
    started = datetime.fromisoformat(result["started_at"])
    assert record["step_timeout_seconds"] == "120"
    assert record["attempt_started_at_utc"] == _truncate(started).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert record["attempt_deadline_utc"] == (_truncate(started) + timedelta(seconds=120)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    # prompt.txt（監査用 artifact）にも同じ値が残る。
    prompt = (attempt / "prompt.txt").read_text(encoding="utf-8")
    assert f"- attempt_deadline_utc: {record['attempt_deadline_utc']}" in prompt
    assert "## 実行期限" in prompt


def test_headless_hard_deadline_matches_displayed_deadline(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, step_timeout=3)
    bin_dir = _install_fake_claude(tmp_path, _FAKE_CLAUDE_HANGING)

    proc = _run_kaji(repo, bin_dir)

    assert proc.returncode != 0
    attempt = _attempt_dir(repo)
    result = json.loads((attempt / "result.json").read_text())
    assert result["status"] == "ABORT"
    assert "timed out after 3s" in result["error"]
    started = datetime.fromisoformat(result["started_at"])
    ended = datetime.fromisoformat(result["ended_at"])
    prompt = (attempt / "prompt.txt").read_text(encoding="utf-8")
    shown = next(
        line.split(": ", 1)[1]
        for line in prompt.splitlines()
        if line.startswith("- attempt_deadline_utc: ")
    )
    shown_deadline = datetime.strptime(shown, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=started.tzinfo)
    assert shown_deadline == _truncate(started) + timedelta(seconds=3)
    # kill は表示 deadline 以降、timer 精度・プロセス終了待ち分の誤差内で起きる。
    # 表示は秒未満切り捨てのため、実 deadline は表示値から最大 1 秒後。
    elapsed_past_shown = (ended - shown_deadline).total_seconds()
    assert -0.5 <= elapsed_past_shown < 6.0, elapsed_past_shown
