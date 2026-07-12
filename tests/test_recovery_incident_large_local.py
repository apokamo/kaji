"""Large-local: GitHubProvider の incident 検索を実 subprocess 境界（stub gh）で固定する。

PATH 上に stub ``gh`` 実行ファイルを置き、``gh api --paginate --slurp`` の呼び出し契約を
実プロセス経由で検証する。ネットワークには一切アクセスしない。

検証:
- **2 page 以上**の ``--slurp`` 出力（外側配列に複数 page を格納）を flatten・全件保持する
- PR 混入要素（``pull_request`` キー）を除外する
- **100 件超コメント**（2 page）から occurrence marker のユニーク ``run_id`` を導出する

large_forge（実 GitHub API 疎通）は破壊的副作用（実インシデント起票）を持ち隔離 repo が
無いため追加しない（設計書 § テスト戦略 § Large と整合）。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kaji_harness.providers.github import GitHubProvider
from kaji_harness.recovery.incident import posted_run_ids

pytestmark = [pytest.mark.large, pytest.mark.large_local]


_STUB_GH = """\
#!/usr/bin/env python3
import json
import sys

# argv: gh api --paginate --slurp <query>
args = sys.argv[1:]
query = args[-1] if args else ""

if "/comments" in query:
    # 100 件 + 1 件 = 2 page。2 種の run_id marker（+ crash window 二重投稿 1 件）。
    page1 = []
    for i in range(100):
        rid = "260712010000" if i == 0 else f"noise{i}"
        marker = (
            f"<!-- kaji-incident-occurrence: schema=1 hash={'a'*64} "
            f"run_id={rid} source_issue=304 -->"
        ) if i < 2 else f"just a normal comment {i}"
        page1.append({"body": marker, "user": {"login": "kamo"}, "created_at": "t"})
    # crash window: 同一 run_id の重複 marker。
    dup = f"<!-- kaji-incident-occurrence: schema=1 hash={'a'*64} run_id=260712010000 source_issue=304 -->"
    page2 = [{"body": dup, "user": {"login": "bot"}, "created_at": "t2"}]
    sys.stdout.write(json.dumps([page1, page2]))
    sys.exit(0)

# issues list: page1 に incident + PR 混入、page2 に incident。
page1 = [
    {"number": 800, "title": "incident A", "body": "b", "state": "open", "labels": [{"name": "incident"}]},
    {"number": 42, "title": "pr", "body": "", "state": "open", "labels": [], "pull_request": {"url": "x"}},
]
page2 = [{"number": 805, "title": "incident B", "body": "b2", "state": "closed", "labels": [{"name": "incident"}]}]
sys.stdout.write(json.dumps([page1, page2]))
sys.exit(0)
"""


@pytest.fixture
def stub_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(_STUB_GH, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return gh


def test_search_issues_all_flattens_pages_and_excludes_pr(stub_gh: Path, tmp_path: Path) -> None:
    provider = GitHubProvider(repo="owner/name", repo_root=tmp_path / "main")
    issues = provider.search_issues_all(labels=["incident"], state="all")
    ids = [i.id for i in issues]
    assert ids == ["800", "805"]  # PR (#42) 除外・2 page 全件保持
    assert issues[1].state == "closed"


def test_list_issue_comments_all_unique_run_id_derivation(stub_gh: Path, tmp_path: Path) -> None:
    provider = GitHubProvider(repo="owner/name", repo_root=tmp_path / "main")
    comments = provider.list_issue_comments_all("800")
    assert len(comments) == 101  # 100 + 1（2 page flatten）
    # crash window の重複 marker を含めても、hash 一致のユニーク run_id は 2 種。
    run_ids = posted_run_ids(comments, "a" * 64)
    assert run_ids == {"260712010000", "noise1"}
