"""Issue #408: 実 Codex で ``$<skill>`` mention が skill 注入として効くことを確認する。

production の ``skill_invocation_line`` / ``build_cli_args`` が作る prompt と argv を使い、
project / git の外に置いた一時 repo で ``codex exec`` と ``codex exec resume`` を実行する。
``codex`` が無い、または未認証なら skip する。引数エラーなどの CLI 失敗は skip にせず FAIL とする。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.cli import build_cli_args
from kaji_harness.models import Step
from kaji_harness.prompt import skill_invocation_line

pytestmark = pytest.mark.large

SKILL = "kaji-fixture-token"
TOKEN = "TOKEN-ZEBRA-4821"
TIMEOUT_SECONDS = 180
# 旧形式（バッククォート）では Codex が skill を探して ``find`` / ``rg`` を走らせる。
# ``$<skill>`` で注入された skill の SKILL.md をモデルが ``cat`` で読み直すことはあるため、
# 「skill 注入ではなく探索で動いた」ことの証跡は探索コマンドの有無で判定する。
_DISCOVERY_COMMAND = re.compile(r"\b(find|rg)\b")


def _codex_ready() -> bool:
    if shutil.which("codex") is None:
        return False
    status = subprocess.run(
        ["codex", "login", "status"], capture_output=True, text=True, check=False, timeout=60
    )
    return status.returncode == 0


@pytest.fixture()
def fixture_repo(outside_project_tmp_path: Path) -> Path:
    """skill を 1 つだけ置いた使い捨て git repo（project / git の外）。"""
    if not _codex_ready():
        pytest.skip("codex CLI is not installed or not logged in")
    repo = outside_project_tmp_path / "repo"
    skill_dir = repo / ".agents" / "skills" / SKILL
    (skill_dir / "agents").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: Return the fixed fixture token.\n---\n\n"
        f"Reply with exactly: {TOKEN}\n",
        encoding="utf-8",
    )
    (skill_dir / "agents" / "openai.yaml").write_text(
        "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e["item"] for e in events if e.get("type") == "item.completed" and "item" in e]


def _final_message(events: list[dict[str, Any]]) -> str:
    messages = [i.get("text", "") for i in _items(events) if i.get("type") == "agent_message"]
    return messages[-1] if messages else ""


def _run(argv: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def test_dollar_mention_invokes_skill_on_new_and_resume(fixture_repo: Path) -> None:
    step = Step(id="fixture", skill=SKILL, agent="codex", on={"PASS": "end"})
    mention = skill_invocation_line(step)
    assert mention == f"${SKILL} を実行してください。"

    # sandbox 引数は付けない（codex-cli 0.159.2 の ``exec resume`` は ``-s`` を受け付けない）。
    new_prompt = f"{mention}\nトークンだけを返してください。"
    new_argv = build_cli_args(step, new_prompt, fixture_repo, None, "interactive")
    new_result = _run(new_argv, fixture_repo)

    assert new_result.returncode == 0, new_result.stderr
    new_events = _events(new_result.stdout)
    assert TOKEN in _final_message(new_events)
    # 探索コマンドが無い = skill が注入された（探索経由だった旧形式との観測点の違い）。
    discovery = [
        i["command"]
        for i in _items(new_events)
        if i.get("type") == "command_execution"
        and _DISCOVERY_COMMAND.search(str(i.get("command", "")))
    ]
    assert discovery == []

    thread_ids = [e["thread_id"] for e in new_events if e.get("type") == "thread.started"]
    assert thread_ids, new_result.stdout

    resume_prompt = f"もう一度 {mention}\nトークンだけを返してください。"
    resume_argv = build_cli_args(step, resume_prompt, fixture_repo, thread_ids[0], "interactive")
    resume_result = _run(resume_argv, fixture_repo)

    assert resume_result.returncode == 0, resume_result.stderr
    assert TOKEN in _final_message(_events(resume_result.stdout))
