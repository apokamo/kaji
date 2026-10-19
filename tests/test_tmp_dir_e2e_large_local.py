"""Large-local E2E: attempt tmp dir の 4 変数が実 dispatch 経路の子プロセスに届く (Issue #407)。

- 実 ``kaji run`` + ``exec:`` step
- 実 ``kaji run`` + PATH 上の fake ``claude``（headless agent）
- Herdr launcher の実行（実 Herdr の起動は含まない。理由は設計書 § テスト戦略）

いずれも subprocess を使うがネットワーク疎通は無い。python 等の前提は環境側で満たす
（skip しない。testing-convention § 環境不備は修正対象）。
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from kaji_harness.interactive_terminal_common import _build_wrapper_command, _wrapper_path
from kaji_harness.interactive_terminal_herdr import (
    _build_recorded_command,
    _materialize_herdr_launcher,
    _recorder_path,
)
from kaji_harness.runner import build_tmp_env

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_TMP_VARS = ("KAJI_TMP_DIR", "TMPDIR", "TMP", "TEMP")

# fake agent / exec script 共通: 自プロセスの 4 変数と標準 temp API の結果を JSON に記録する。
_RECORD_SNIPPET = """\
import json, os, subprocess, tempfile
record = {k: os.environ.get(k) for k in ("KAJI_TMP_DIR", "TMPDIR", "TMP", "TEMP")}
record["gettempdir"] = tempfile.gettempdir()
with tempfile.NamedTemporaryFile(delete=False) as handle:
    record["tempfile"] = handle.name
record["mktemp"] = subprocess.run(
    ["mktemp"], capture_output=True, text=True, check=True
).stdout.strip()
"""

_FAKE_CLAUDE = (
    "#!/usr/bin/env python3\n"
    + _RECORD_SNIPPET
    + """\
import re, sys, time
text = sys.argv[-1]
m = re.search(r"^- verdict_path: (.+)$", text, re.M)
interactive = m is None
if interactive:
    m = re.search(r"exact path:\\n(.+)$", text, re.M)
verdict = m.group(1).strip()
record_path = os.path.join(os.path.dirname(verdict), "fake-agent-record.json")
with open(record_path, "w", encoding="utf-8") as out:
    json.dump(record, out)
if interactive:
    # Issue #515: recorder が alternate screen と子の環境を記録することを確かめる（verdict 前に出す）。
    hint = os.environ.get("HERDR_AGENT", "<unset>")
    print("\x1b[?1049hFAKE-ALT HERDR_AGENT=" + hint + "\x1b[?1049l", flush=True)
with open(verdict, "w", encoding="utf-8") as out:
    out.write("status: PASS\\nreason: tmp env e2e\\nevidence: recorded\\nsuggestion: ''\\n")
print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake-sess"}))
print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.0}), flush=True)
if interactive:
    # 実 agent と同様、verdict 観測後に kaji が pane を閉じるまで生存し続ける。
    time.sleep(60)
"""
)

_EXEC_SCRIPT = (
    _RECORD_SNIPPET
    + """\
with open(os.path.join(os.path.dirname(os.environ["KAJI_VERDICT_PATH"]),
                       "exec-record.json"), "w", encoding="utf-8") as out:
    json.dump(record, out)
with open(os.environ["KAJI_VERDICT_PATH"], "w", encoding="utf-8") as out:
    out.write("status: PASS\\nreason: tmp env e2e\\nevidence: recorded\\nsuggestion: ''\\n")
"""
)


def _install_fake_claude(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE)
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


def _make_repo(tmp_path: Path, workflow_yaml: str) -> Path:
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
    (repo / "exec_step.py").write_text(_EXEC_SCRIPT, encoding="utf-8")
    (repo / "workflow.yaml").write_text(workflow_yaml)
    _install_fake_gh(repo.parent / "gh-bin")
    return repo


def _run_kaji(repo: Path, outer_tmp: Path, extra_path: Path | None = None) -> Path:
    """実 ``kaji run`` を外側 TMPDIR 付きで起動し、run dir を返す。"""
    env = dict(os.environ)
    for var in ("TMPDIR", "TMP", "TEMP"):
        env[var] = str(outer_tmp)
    env.pop("KAJI_TMP_DIR", None)
    if extra_path is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env['PATH']}"
    env["PATH"] = f"{repo.parent / 'gh-bin'}{os.pathsep}{env['PATH']}"
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
        timeout=90,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    runs = repo / ".kaji-artifacts" / "99" / "runs"
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, f"expected 1 run dir, got {run_dirs}"
    return run_dirs[0]


def _assert_record_in_attempt_tmp(record: dict[str, str], expected: Path) -> None:
    for var in _TMP_VARS:
        assert record[var] == str(expected), var
    assert record["gettempdir"] == str(expected)
    assert Path(record["tempfile"]).parent == expected
    assert Path(record["mktemp"]).parent == expected


def test_exec_step_child_uses_attempt_tmp_dir(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        "name: e2e-tmp-exec\n"
        "description: exec step tmp env\n"
        "requires_provider: any\n"
        "execution_policy: auto\n\n"
        "steps:\n"
        "  - id: collect\n"
        f"    exec: {json.dumps([sys.executable, str(tmp_path / 'repo' / 'exec_step.py')])}\n"
        "    on:\n"
        "      PASS: end\n"
        "      ABORT: end\n",
    )
    outer_tmp = tmp_path / "outer"
    outer_tmp.mkdir()

    run_dir = _run_kaji(repo, outer_tmp)

    expected = repo.resolve() / "tmp" / "kaji" / "99" / "0001"
    attempt = run_dir / "steps" / "collect" / "attempt-001"
    record = json.loads((attempt / "exec-record.json").read_text())
    _assert_record_in_attempt_tmp(record, expected)
    assert expected.is_dir()
    assert (repo / "tmp" / "kaji" / ".gitignore").read_text() == "*\n"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "tmp"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == "", "tmp/kaji must be fully ignored by git"


def test_headless_agent_child_uses_attempt_tmp_dir(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        "name: e2e-tmp-agent\n"
        "description: headless agent tmp env\n"
        "requires_provider: any\n"
        "execution_policy: auto\n\n"
        "steps:\n"
        "  - id: implement\n"
        "    skill: e2e-impl\n"
        "    agent: claude\n"
        "    on:\n"
        "      PASS: end\n"
        "      ABORT: end\n",
    )
    outer_tmp = tmp_path / "outer"
    outer_tmp.mkdir()

    run_dir = _run_kaji(repo, outer_tmp, extra_path=_install_fake_claude(tmp_path))

    expected = repo.resolve() / "tmp" / "kaji" / "99" / "0001"
    attempt = run_dir / "steps" / "implement" / "attempt-001"
    record = json.loads((attempt / "fake-agent-record.json").read_text())
    _assert_record_in_attempt_tmp(record, expected)


# ---------------------------------------------------------------------------
# interactive terminal
# ---------------------------------------------------------------------------


def _write_prompt(attempt_dir: Path) -> tuple[Path, Path]:
    attempt_dir.mkdir(parents=True)
    prompt = attempt_dir / "prompt.txt"
    prompt.write_text("do the step", encoding="utf-8")
    return prompt, attempt_dir / "verdict.yaml"


def test_herdr_launcher_applies_attempt_tmp_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _install_fake_claude(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for var in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(var, str(tmp_path / "parent-env"))
    (tmp_path / "parent-env").mkdir()
    attempt_tmp = tmp_path / "project" / "tmp" / "kaji" / "1" / "r" / "design" / "attempt-001"
    attempt_tmp.mkdir(parents=True)
    prompt, verdict = _write_prompt(tmp_path / "artifacts" / "attempt-001")

    terminal_log = prompt.parent / "terminal.log"
    command = _build_recorded_command(
        _build_wrapper_command(
            _wrapper_path(),
            agent="claude",
            prompt_path=prompt,
            verdict_path=verdict,
            workdir=tmp_path,
            resume_session_id="",
            launch_session_id=str(uuid.uuid4()),
            model="",
            effort="",
            execution_policy="auto",
        ),
        agent="claude",
        recorder=_recorder_path(),
        terminal_log=terminal_log,
        env=build_tmp_env(attempt_tmp),
    )
    launcher = _materialize_herdr_launcher(prompt.parent / "herdr-launcher.sh", command)

    # fake agent は実 agent 同様に verdict を書いた後も生存するため、成果物を待って後始末する。
    record_path = verdict.parent / "fake-agent-record.json"
    proc = subprocess.Popen(
        ["/bin/sh", "-c", launcher],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not verdict.exists():
            assert proc.poll() is None, f"launcher exited early: {proc.stdout.read()}"
            time.sleep(0.1)
        assert verdict.exists(), "fake agent did not write verdict.yaml"
        # recorder が verdict 前の出力を書き終えるまで待ってから pane 相当を落とす
        while time.monotonic() < deadline and (
            not terminal_log.exists() or b"FAKE-ALT" not in terminal_log.read_bytes()
        ):
            time.sleep(0.05)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    record = json.loads(record_path.read_text())
    _assert_record_in_attempt_tmp(record, attempt_tmp)
    # Issue #515: launcher → recorder → wrapper → agent の raw byte が terminal.log に残る。
    recorded = terminal_log.read_bytes()
    assert b"kaji interactive terminal runner" in recorded
    assert b"\x1b[?1049hFAKE-ALT HERDR_AGENT=<unset>\x1b[?1049l" in recorded
