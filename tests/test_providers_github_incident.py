"""Small tests: GitHubProvider の incident 全件検索（Issue #304）.

``subprocess.run`` を mock し、``gh api --paginate --slurp`` の呼び出し契約 /
複数 page flatten / PR 除外 / REST payload 写像を検証する。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.providers.github import GitHubProvider, GitHubProviderError

pytestmark = pytest.mark.small


@pytest.fixture
def provider(tmp_path: Path) -> GitHubProvider:
    return GitHubProvider(repo="owner/name", repo_root=tmp_path / "main")


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _patched(captured: list[list[str]], stdout: str):
    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        captured.append(cmd)
        return _ok(stdout)

    return (
        patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
        patch("kaji_harness.providers.github.subprocess.run", side_effect=fake_run),
    )


def test_search_issues_all_uses_paginate_slurp_and_excludes_pr(provider: GitHubProvider) -> None:
    # --slurp の外側配列に 2 page を格納。1 page 目に PR 混入。
    page1 = [
        {
            "number": 800,
            "title": "incident A",
            "body": "b",
            "state": "open",
            "labels": [{"name": "incident"}],
        },
        {
            "number": 42,
            "title": "a pull request",
            "body": "",
            "state": "open",
            "labels": [],
            "pull_request": {"url": "x"},
        },
    ]
    page2 = [
        {
            "number": 805,
            "title": "incident B",
            "body": "b2",
            "state": "closed",
            "labels": [{"name": "incident"}],
        }
    ]
    stdout = json.dumps([page1, page2])
    captured: list[list[str]] = []
    w, r = _patched(captured, stdout)
    with w, r:
        issues = provider.search_issues_all(labels=["incident"], state="all")

    # PR は除外され、2 page 分の issue が全件保持される。
    ids = [i.id for i in issues]
    assert ids == ["800", "805"]
    assert issues[1].state == "closed"
    # 呼び出し契約: gh api --paginate --slurp、limit フラグ非依存。
    cmd = captured[0]
    assert cmd[:4] == ["gh", "api", "--paginate", "--slurp"]
    assert any("labels=incident" in a for a in cmd)
    assert any("state=all" in a for a in cmd)
    assert "--limit" not in cmd


def test_list_issue_comments_all_flattens_pages(provider: GitHubProvider) -> None:
    # 100 件超（2 page）のコメントを想定した flatten と写像。
    page1 = [{"body": f"c{i}", "user": {"login": "kamo"}, "created_at": "t"} for i in range(100)]
    page2 = [{"body": "c100", "user": {"login": "bot"}, "created_at": "t2"}]
    stdout = json.dumps([page1, page2])
    captured: list[list[str]] = []
    w, r = _patched(captured, stdout)
    with w, r:
        comments = provider.list_issue_comments_all("800")

    assert len(comments) == 101
    assert comments[0].body == "c0"
    assert comments[100].author == "bot"
    cmd = captured[0]
    assert cmd[:4] == ["gh", "api", "--paginate", "--slurp"]
    assert any("repos/owner/name/issues/800/comments" in a for a in cmd)


def test_slurp_invalid_json_raises(provider: GitHubProvider) -> None:
    captured: list[list[str]] = []
    w, r = _patched(captured, "not json at all")
    with w, r, pytest.raises(GitHubProviderError, match="invalid JSON"):
        provider.search_issues_all(labels=["incident"])
