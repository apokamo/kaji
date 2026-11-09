"""issue-start の worktree 基点と、差分範囲の基点を remote 側にそろえる契約を検証する（Issue #549）。

- small: ``issue-start/SKILL.md`` の ``# kaji-issue-start-worktree`` block の静的契約と、
  差分範囲をローカル ``main`` / ``[default_branch]`` で決めている同根箇所が残っていないこと
- large (large_local): block を実 ``bash`` で実行し、bare remote + clone 2 つの fixture で
  「remote の main だけが進んだ」状態から remote 先端を基点に worktree を作ること、
  fetch 失敗・tracking ref 不一致では何も作らずに止まることを検証する

fixture は ``outside_project_tmp_path`` に作る。workflow 内の ``tmp_path`` は kaji repo 内になり、
fixture 構築に失敗すると subprocess の git が実 repo を操作しうるため。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _REPO_ROOT / ".claude" / "skills"
_ISSUE_START = _SKILL_DIR / "issue-start" / "SKILL.md"
_BLOCK_RE = re.compile(r"```bash\n(# kaji-issue-start-worktree\n.*?)\n```", re.DOTALL)

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


def _worktree_block() -> str:
    blocks = _BLOCK_RE.findall(_ISSUE_START.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"expected exactly one kaji-issue-start-worktree block, got {blocks}"
    return str(blocks[0])


def _local_base_ranges(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _LOCAL_BASE_RANGE_RE.finditer(line):
            found.append((lineno, match.group(0)))
    return found


# ============================================================
# Small: issue-start の静的契約
# ============================================================


@pytest.mark.small
def test_step1_resolves_remote_and_base_from_issue_context() -> None:
    text = _ISSUE_START.read_text(encoding="utf-8")
    assert "REMOTE=$(kaji issue context [issue_id] -q '.git_remote')" in text
    assert "BASE=$(kaji issue context [issue_id] -q '.default_branch')" in text


@pytest.mark.small
def test_worktree_block_fetches_before_creating_worktree() -> None:
    block = _worktree_block()
    fetch = block.find('fetch "$REMOTE" "$BASE"')
    add = block.find("worktree add")
    assert fetch != -1, block
    assert add != -1, block
    assert fetch < add
    fetch_line = next(line for line in block.splitlines() if 'fetch "$REMOTE" "$BASE"' in line)
    assert "|| {" in fetch_line or "|| exit" in fetch_line, fetch_line


@pytest.mark.small
def test_worktree_block_uses_fetched_remote_base_without_upstream() -> None:
    block = _worktree_block()
    add_lines = [line for line in block.splitlines() if "worktree add" in line]
    assert len(add_lines) == 1, add_lines
    add_line = add_lines[0]
    assert "--no-track" in add_line
    assert add_line.rstrip().split(" || ")[0].endswith('"$REMOTE/$BASE"'), add_line
    assert not re.search(r"\smain(\s|$)", add_line), add_line
    assert "FETCH_HEAD" in block


@pytest.mark.small
def test_failure_of_worktree_block_is_an_abort_condition() -> None:
    text = _ISSUE_START.read_text(encoding="utf-8")
    abort_rows = [line for line in text.splitlines() if line.startswith("| ABORT |")]
    assert len(abort_rows) == 1, abort_rows
    assert "fetch" in abort_rows[0]
    assert "kaji-issue-start-worktree" in text.split("### Step 2.5")[0]
    assert "ABORT" in text.split("### Step 2.5")[0].split("### Step 2:")[1]


@pytest.mark.small
def test_report_shows_remote_base_instead_of_fixed_main() -> None:
    text = _ISSUE_START.read_text(encoding="utf-8")
    assert "| 基点ブランチ | main |" not in text
    assert "| 基点 | $REMOTE/$BASE" in text


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


# ============================================================
# Large (large_local): 実 bash / git での worktree 作成
# ============================================================


@dataclass(frozen=True)
class _Fixture:
    """bare remote・保守者 checkout（古い base のまま）・別 clone からなる fixture。"""

    root: Path
    bare: Path
    maintainer: Path
    other: Path
    remote: str
    base: str

    @property
    def worktree(self) -> Path:
        return self.root / "wt-fix-1"


_BRANCH = "fix/1"


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = "kaji-test"
        env[f"GIT_{role}_EMAIL"] = "kaji-test@example.com"
    return env


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_git_env(), capture_output=True, text=True, check=check
    )


def _out(cwd: Path, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


def _commit_file(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def _make_fixture(root: Path, *, remote: str = "origin", base: str = "main") -> _Fixture:
    """Issue 本文の実行証跡と同じ構成を作る（remote の base だけが 1 commit 進んだ状態）。"""
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", base)
    _commit_file(seed, "f.txt", "v1\n", "init")
    bare = root / "remote.git"
    _git(root, "clone", "-q", "--bare", str(seed), str(bare))
    maintainer = root / "maintainer"
    _git(root, "clone", "-q", "-o", remote, str(bare), str(maintainer))
    other = root / "other"
    _git(root, "clone", "-q", str(bare), str(other))
    _commit_file(other, "f.txt", "v2\n", "outside kaji")
    _git(other, "push", "-q", "origin", f"HEAD:{base}")
    return _Fixture(
        root=root,
        bare=bare,
        maintainer=maintainer,
        other=other,
        remote=remote,
        base=base,
    )


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: str
    stderr: str


def _run_block(fx: _Fixture) -> _Result:
    env = _git_env()
    env.update({"REMOTE": fx.remote, "BASE": fx.base, "BRANCH": _BRANCH, "WT": str(fx.worktree)})
    proc = subprocess.run(
        ["bash", "-c", _worktree_block()],
        cwd=fx.maintainer,
        env=env,
        capture_output=True,
        text=True,
    )
    return _Result(proc.returncode, proc.stdout, proc.stderr)


def _branch_exists(repo: Path, branch: str) -> bool:
    return (
        _git(
            repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        ).returncode
        == 0
    )


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize(("remote", "base"), [("origin", "main"), ("github", "trunk")])
def test_worktree_starts_from_fetched_remote_base(
    outside_project_tmp_path: Path, remote: str, base: str
) -> None:
    fx = _make_fixture(outside_project_tmp_path, remote=remote, base=base)
    stale_local = _out(fx.maintainer, "rev-parse", f"refs/heads/{base}")
    remote_tip = _out(fx.bare, "rev-parse", base)
    assert stale_local != remote_tip

    result = _run_block(fx)

    assert result.returncode == 0, result.stderr
    assert _out(fx.worktree, "rev-parse", "HEAD") == remote_tip
    assert _out(fx.worktree, "rev-list", "--count", f"HEAD..{remote}/{base}") == "0"
    assert (fx.worktree / "f.txt").read_text(encoding="utf-8") == "v2\n"
    assert _out(fx.worktree, "branch", "--show-current") == _BRANCH
    # upstream を設定しない（i-pr の push -u で設定する）
    assert _git(fx.worktree, "rev-parse", "--abbrev-ref", "@{u}", check=False).returncode != 0
    # 保守者のローカル base は更新しない
    assert _out(fx.maintainer, "rev-parse", f"refs/heads/{base}") == stale_local

    # remote 側の base を基点にした差分範囲は feature 固有の変更だけを返す
    _commit_file(fx.worktree, "feature.txt", "x\n", "feature")
    changed = _out(fx.worktree, "diff", "--name-only", f"{remote}/{base}...HEAD")
    assert changed.splitlines() == ["feature.txt"]


@pytest.mark.large
@pytest.mark.large_local
def test_unreachable_remote_stops_without_creating_anything(
    outside_project_tmp_path: Path,
) -> None:
    fx = _make_fixture(outside_project_tmp_path)
    _git(fx.maintainer, "remote", "set-url", fx.remote, str(fx.root / "does-not-exist.git"))

    result = _run_block(fx)

    assert result.returncode != 0
    assert result.stderr != ""
    assert not fx.worktree.exists()
    assert not _branch_exists(fx.maintainer, _BRANCH)


@pytest.mark.large
@pytest.mark.large_local
def test_stale_tracking_ref_after_fetch_stops_without_creating_anything(
    outside_project_tmp_path: Path,
) -> None:
    fx = _make_fixture(outside_project_tmp_path)
    # fetch refspec が base を remote-tracking ref へ写さない設定では origin/main が古いまま残る
    _git(
        fx.maintainer,
        "config",
        "remote.origin.fetch",
        "+refs/heads/other:refs/remotes/origin/other",
    )

    result = _run_block(fx)

    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert not fx.worktree.exists()
    assert not _branch_exists(fx.maintainer, _BRANCH)
