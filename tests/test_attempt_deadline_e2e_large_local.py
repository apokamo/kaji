"""Large-local E2E: attempt deadline の単一計算元が実 dispatch 経路で一致する (Issue #421)。

- 実 ``kaji run`` + PATH 上の fake ``claude``（headless）: prompt の表示値の到達と
  hard deadline の一致
- 隔離した実 tmux server 上の interactive terminal: 渡した hard deadline で打ち切られる

subprocess を使うがネットワーク疎通は無い。Herdr の実起動は含まない（実 Herdr pane 内からの
起動が必要で、CI / 本 test 環境では物理的に作成できない。設計書 § テスト戦略）。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kaji_harness.errors import StepTimeoutError
from kaji_harness.interactive_terminal import execute_interactive_terminal
from kaji_harness.models import Step
from kaji_harness.providers import LocalProvider

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


def _make_repo(tmp_path: Path, step_timeout: int) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
        "[execution]\ndefault_timeout = 600\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
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
    counter = repo / ".kaji" / "counters" / "pc1.txt"
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text("98")
    LocalProvider(repo_root=repo, machine_id="pc1").create_issue(
        title="e2e", body="body", labels=["type:feature"], slug="e2e"
    )
    return repo


def _run_kaji(repo: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
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
    runs = repo / ".kaji-artifacts" / "local-pc1-99" / "runs"
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


@pytest.fixture()
def isolated_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """利用者の tmux を一切使わない専用 server を起動し、後始末で必ず kill する。"""
    socket_name = f"kaji-test-{uuid.uuid4().hex[:12]}"
    bin_dir = _install_fake_claude(tmp_path, _FAKE_CLAUDE_HANGING)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    tmux = shutil.which("tmux")
    assert tmux is not None, "tmux is required for the interactive terminal large test"

    def run(*args: str) -> str:
        proc = subprocess.run(
            [tmux, "-L", socket_name, "-f", "/dev/null", *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    try:
        run("new-session", "-d", "-s", "kajitest", "-x", "200", "-y", "50", "sleep 600")
        socket_path = run("display-message", "-p", "-t", "kajitest", "#{socket_path}")
        pane_id = run("display-message", "-p", "-t", "kajitest", "#{pane_id}")
        server_pid = run("display-message", "-p", "-t", "kajitest", "#{pid}")
        session_idx = run("display-message", "-p", "-t", "kajitest", "#{session_id}").lstrip("$")
        monkeypatch.setenv("TMUX", f"{socket_path},{server_pid},{session_idx}")
        monkeypatch.setenv("TMUX_PANE", pane_id)
        yield socket_name
    finally:
        subprocess.run(
            [tmux, "-L", socket_name, "kill-server"],
            capture_output=True,
            text=True,
            check=False,
        )


def test_tmux_times_out_at_attempt_deadline_not_pane_launch_plus_timeout(
    tmp_path: Path, isolated_tmux: str
) -> None:
    attempt_dir = tmp_path / "artifacts" / "attempt-001"
    attempt_dir.mkdir(parents=True)
    prompt = attempt_dir / "prompt.txt"
    prompt.write_text("do the step", encoding="utf-8")
    verdict = attempt_dir / "verdict.yaml"

    attempt_start = time.monotonic()
    with pytest.raises(StepTimeoutError) as exc_info:
        execute_interactive_terminal(
            step=Step(id="design", skill="x", agent="claude"),
            prompt_path=prompt,
            verdict_path=verdict,
            workdir=tmp_path,
            timeout=600,  # 設定 timeout は大きく、deadline_monotonic が優先されることを示す。
            backend="tmux",
            deadline_monotonic=attempt_start + 4.0,
        )
    elapsed = time.monotonic() - attempt_start

    assert exc_info.value.timeout == 600
    # attempt 開始基準の 4 秒で打ち切られ、pane 起動所要時間は加算されない。
    assert 4.0 <= elapsed < 4.0 + 3.0, elapsed
