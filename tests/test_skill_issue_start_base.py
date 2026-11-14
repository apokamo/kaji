"""issue-start の worktree 基点と、差分範囲の基点を remote 側にそろえる契約を検証する（Issue #549）。

- medium: ``kaji_harness.issue_start`` が実行する git コマンドの順序（fetch → ``FETCH_HEAD`` と
  remote-tracking ref の照合 → ``--no-track`` で ``<remote>/<base>`` から作成）と、ローカル
  ``<base>`` を基点にしないこと（Issue #569 で SKILL.md の bash block から script へ移行）
- small: 差分範囲をローカル ``main`` / ``[default_branch]`` で決めている同根箇所が残っていないこと

実 git での fetched base からの作成・fetch 失敗・tracking ref 不一致は
``tests/test_issue_start_large_local.py`` が検証する。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.issue_start import run_issue_start
from kaji_harness.providers.models import Comment, Issue, IssueContext

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _REPO_ROOT / ".claude" / "skills"

# 同根箇所（設計書「同根箇所の調査結果」#2〜#9）。差分範囲の基点に `[git_remote]/` が付いていること。
_DIFF_RANGE_USERS = [
    "issue-review-code/SKILL.md",
    "i-doc-review/SKILL.md",
    "issue-implement/references/pre-handoff-review.md",
    "_shared/report-unrelated-issues.md",
    "issue-small-change-review/SKILL.md",
    "issue-small-change-execute/SKILL.md",
    "issue-design/SKILL.md",
]
_CODE_REVIEWER_AGENT = _REPO_ROOT / ".claude" / "agents" / "kaji-code-reviewer.md"
# `[git_remote]/` が前に付かない `main..HEAD` / `main...HEAD` / `[default_branch]..HEAD` 等
_LOCAL_BASE_RANGE_RE = re.compile(r"(?<!\[git_remote\]/)(?:\bmain|\[default_branch\])\.\.\.?HEAD")


def _local_base_ranges(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _LOCAL_BASE_RANGE_RE.finditer(line):
            found.append((lineno, match.group(0)))
    return found


# ============================================================
# Medium: script が実行する git コマンドの順序（旧 SKILL.md block の静的契約の置き換え）
# ============================================================

_SHA = "a" * 40


class _RecordingGit:
    """``git`` 呼び出しを記録し、正常系の応答を返す（worktree add で実ディレクトリを作る）。"""

    def __init__(self, main_repo: Path, worktree: Path) -> None:
        self.main_repo = main_repo
        self.worktree = worktree
        self.calls: list[list[str]] = []
        self.created = False

    def __call__(self, argv: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        assert argv[:3] == ["git", "-C", str(argv[2])]
        args = list(argv[3:])
        self.calls.append(args)
        stdout = ""
        returncode = 0
        if args == ["rev-parse", "--show-toplevel"]:
            stdout = f"{self.main_repo}\n"
        elif args[:1] == ["rev-parse"] and "--verify" in args:
            stdout = f"{_SHA}\n"
        elif args[:2] == ["worktree", "list"]:
            stdout = f"worktree {self.main_repo}\nHEAD {_SHA}\nbranch refs/heads/main\n"
            if self.created:
                stdout += f"\nworktree {self.worktree}\nHEAD {_SHA}\nbranch refs/heads/fix/1\n"
        elif args[:1] == ["show-ref"]:
            returncode = 1
        elif args[:2] == ["worktree", "add"]:
            self.worktree.mkdir()
            self.created = True
        elif args[:2] == ["rev-parse", "--short"]:
            stdout = "aaaaaaa\n"
        elif args[:2] == ["rev-list", "--count"]:
            stdout = "0\n"
        return subprocess.CompletedProcess(list(argv), returncode, stdout, "")


class _Provider:
    def __init__(self, ctx: IssueContext) -> None:
        self.ctx = ctx
        self.comments: list[Comment] = []

    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        return self.ctx

    def view_issue(self, issue_id: str) -> Issue:
        return Issue(id=issue_id, title="t", body="b", state="open", comments=self.comments)

    def edit_issue(self, issue_id: str, **_: object) -> Issue:
        return self.view_issue(issue_id)

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        comment = Comment(author="k", body=body, created_at="")
        self.comments.append(comment)
        return comment


@pytest.mark.medium
@pytest.mark.parametrize(("remote", "base"), [("origin", "main"), ("github", "trunk")])
def test_script_fetches_verifies_then_creates_from_remote_base(
    tmp_path: Path, remote: str, base: str
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    worktree = tmp_path / "kaji-fix-1"
    ctx = IssueContext(
        issue_id="1",
        issue_ref="#1",
        issue_input="1",
        slug="x",
        branch_prefix="fix",
        branch_name="fix/1",
        worktree_dir=str(worktree),
        design_path="d.md",
        provider_type="github",
        default_branch=base,
        git_remote=remote,
    )
    git = _RecordingGit(main_repo, worktree)
    with patch("kaji_harness.issue_start.subprocess.run", side_effect=git) as mock_run:
        outcome = run_issue_start("1", _Provider(ctx), cwd=main_repo, env={})  # type: ignore[arg-type]

    assert mock_run.call_count == len(git.calls)
    assert outcome.verdict.status == "PASS", outcome.verdict
    fetch = git.calls.index(["fetch", remote, base])
    fetch_head = git.calls.index(["rev-parse", "--verify", "--quiet", "FETCH_HEAD^{commit}"])
    tracking = git.calls.index(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{base}^{{commit}}"]
    )
    add = git.calls.index(
        [
            "worktree",
            "add",
            "--no-track",
            "-b",
            "fix/1",
            str(worktree.resolve()),
            f"{remote}/{base}",
        ]
    )
    assert fetch < fetch_head < add
    assert fetch < tracking < add
    # ローカル <base> は基点にも更新対象にもしない（報告用の未 push 件数の参照のみ）
    for args in git.calls:
        assert args[:1] not in (["pull"], ["reset"], ["checkout"], ["clean"], ["branch"]), args
        if args[:1] != ["rev-list"]:
            assert base not in args or args == ["fetch", remote, base], args


# ============================================================
# Small: 同根箇所の差分範囲
# ============================================================


@pytest.mark.small
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("git diff main...HEAD", ["main...HEAD"]),
        ("git log main..HEAD", ["main..HEAD"]),
        ("git diff [default_branch]...HEAD", ["[default_branch]...HEAD"]),
        ("git log --oneline [default_branch]..HEAD", ["[default_branch]..HEAD"]),
        ("git diff [git_remote]/[default_branch]...HEAD", []),
        ("git log [git_remote]/[default_branch]..HEAD", []),
        ("git diff origin/main...HEAD", ["main...HEAD"]),
        ("domain...HEAD", []),
    ],
)
def test_local_base_range_detection(text: str, expected: list[str]) -> None:
    assert [found for _, found in _local_base_ranges(text)] == expected


@pytest.mark.small
@pytest.mark.parametrize("rel", _DIFF_RANGE_USERS)
def test_diff_range_users_use_remote_default_branch(rel: str) -> None:
    text = (_SKILL_DIR / rel).read_text(encoding="utf-8")
    assert _local_base_ranges(text) == [], rel
    assert "[git_remote]/[default_branch]" in text, rel


@pytest.mark.small
def test_code_reviewer_diff_contract_uses_remote_default_branch() -> None:
    text = _CODE_REVIEWER_AGENT.read_text(encoding="utf-8")
    assert _local_base_ranges(text) == []
    assert "git diff <git_remote>/<default_branch>...HEAD" in text
