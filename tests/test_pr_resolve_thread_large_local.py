"""Large-local: ``kaji pr resolve-thread`` を実 CLI プロセス + stub ``gh`` で駆動する（Issue #528）。

PATH 先頭に stub ``gh``（Python スクリプト）を置き、``.kaji/config.toml`` を持つ一時 project で
``python -m kaji_harness.cli_main pr resolve-thread ...`` をサブプロセス起動する。
config 発見 → provider 検証 → builtin dispatch → ``gh api graphql`` 境界までを結合で検証する。
ネットワークには一切アクセスしない。

large_forge（実 GitHub API 疎通）は追加しない: ``resolveReviewThread`` は実 PR のスレッド状態を
書き換える破壊的 mutation で、書き込みに使える隔離 repo が無いため（設計書 § テスト戦略 § Large）。
stub の応答は apokamo/kamo2#2047 の実応答の形に合わせている。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_WORKTREE_ROOT = Path(__file__).resolve().parents[1]

_STUB_GH = """\
#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["STUB_GH_ARGV_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\\n")

with open(os.environ["STUB_GH_SCENARIO"], encoding="utf-8") as f:
    scenario = json.load(f)

query = ""
for i, tok in enumerate(args):
    if tok == "-f" and args[i + 1].startswith("query="):
        query = args[i + 1][len("query="):]

if args[:2] != ["api", "graphql"]:
    sys.stderr.write("stub gh: unexpected argv\\n")
    sys.exit(9)
if "resolveReviewThread" in query:
    sys.stdout.write(json.dumps(scenario["mutation"]))
elif "reviewThreads" in query:
    sys.stdout.write(json.dumps(scenario["threads"]))
else:
    sys.stderr.write("stub gh: unexpected query\\n")
    sys.exit(9)
sys.exit(0)
"""


def _threads(*, resolved: bool) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "Y3Vyc29y"},
                        "nodes": [
                            {
                                "id": "PRRT_kwDOPlQLMc6yTJlI",
                                "isResolved": resolved,
                                "viewerCanResolve": True,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": "Y29t"},
                                    "nodes": [
                                        {"fullDatabaseId": "4432690863"},
                                        {"fullDatabaseId": "4434206283"},
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        }
    }


_MUTATION = {
    "data": {"resolveReviewThread": {"thread": {"id": "PRRT_kwDOPlQLMc6yTJlI", "isResolved": True}}}
}


def _setup(tmp_path: Path, *, resolved: bool) -> tuple[Path, dict[str, str], Path]:
    project = tmp_path / "project"
    (project / ".kaji").mkdir(parents=True)
    (project / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "apokamo/kamo2"\n',
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(_STUB_GH, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps({"threads": _threads(resolved=resolved), "mutation": _MUTATION}),
        encoding="utf-8",
    )
    argv_log = tmp_path / "argv.jsonl"

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["STUB_GH_SCENARIO"] = str(scenario)
    env["STUB_GH_ARGV_LOG"] = str(argv_log)
    # 共用 venv の editable install が別 worktree（main）の kaji_harness を読まないよう、
    # 本 worktree を PYTHONPATH 先頭に置いて package 解決を固定する。
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_WORKTREE_ROOT}{os.pathsep}{existing}" if existing else str(_WORKTREE_ROOT)
    )
    return project, env, argv_log


def _kaji(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", "pr", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _logged(argv_log: Path) -> list[list[str]]:
    if not argv_log.exists():
        return []
    return [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]


def _vars(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, tok in enumerate(argv):
        if tok in ("-f", "-F"):
            key, _, value = argv[i + 1].partition("=")
            out[f"{tok}:{key}"] = value
    return out


def test_resolves_thread_with_config_repo(tmp_path: Path) -> None:
    project, env, argv_log = _setup(tmp_path, resolved=False)
    result = _kaji(project, env, "resolve-thread", "2047", "--comment", "4434206283")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "comment_id": "4434206283",
        "thread_id": "PRRT_kwDOPlQLMc6yTJlI",
        "result": "resolved",
    }
    calls = _logged(argv_log)
    assert len(calls) == 2
    lookup, mutation = (_vars(c) for c in calls)
    assert "reviewThreads" in lookup["-f:query"]
    assert lookup["-f:owner"] == "apokamo"
    assert lookup["-f:name"] == "kamo2"
    assert lookup["-F:number"] == "2047"
    assert "resolveReviewThread" in mutation["-f:query"]
    assert mutation["-f:threadId"] == "PRRT_kwDOPlQLMc6yTJlI"


def test_already_resolved_does_not_call_mutation(tmp_path: Path) -> None:
    project, env, argv_log = _setup(tmp_path, resolved=True)
    result = _kaji(project, env, "resolve-thread", "2047", "--comment", "4432690863")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"] == "already_resolved"
    calls = _logged(argv_log)
    assert len(calls) == 1
    assert not any("resolveReviewThread" in tok for tok in calls[0])


def test_not_found_exits_2(tmp_path: Path) -> None:
    project, env, argv_log = _setup(tmp_path, resolved=False)
    result = _kaji(project, env, "resolve-thread", "2047", "--comment", "1")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        "Error: review comment 1 was not found in any review thread of PR #2047 (apokamo/kamo2).\n"
    )
    assert len(_logged(argv_log)) == 1


def test_help_shows_usage(tmp_path: Path) -> None:
    project, env, argv_log = _setup(tmp_path, resolved=False)
    result = _kaji(project, env, "resolve-thread", "--help")
    assert result.returncode == 0, result.stderr
    assert "usage: kaji pr resolve-thread" in result.stdout
    assert "--comment" in result.stdout
    assert _logged(argv_log) == []
