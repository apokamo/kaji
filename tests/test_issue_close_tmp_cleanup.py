"""issue-close SKILL.md の Step 5.6（Issue の一時作業ディレクトリ削除）を検証する（Issue #509）。

- small: SKILL.md の静的構造（識別コメント付きブロック、Step 順序、Step 6 の報告行、BSD 互換）
- large (large_local): 抽出したブロックを実 ``bash`` で実行し、実 ``git`` で作った
  bare + linked worktree 構成の fixture に対する削除結果を検証する

fixture は ``outside_project_tmp_path`` に作る。workflow 内の ``tmp_path`` は kaji repo 内になり、
fixture 構築に失敗すると subprocess の ``git worktree list`` が実 repo を返しうるため。
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close/SKILL.md"
_BLOCK_RE = re.compile(r"```(?:bash|sh)\n(# kaji-issue-tmp-cleanup\n.*?)\n```", re.DOTALL)
_RESULT_PREFIX = "TMP_CLEANUP_RESULT="
_ISSUE = "509"
_REPORT_ROW = "| 一時ディレクトリ | [tmp_cleanup_result] |"


def _skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _cleanup_block() -> str:
    blocks = _BLOCK_RE.findall(_skill_text())
    assert len(blocks) == 1, f"expected exactly one kaji-issue-tmp-cleanup block, got {len(blocks)}"
    return str(blocks[0])


def _between(text: str, start: str, end: str) -> str:
    """``start`` 見出しから ``end`` 見出しの直前までを返す（code block 内の見出しで切らない）。"""
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


# ============================================================
# Small: SKILL.md の静的構造
# ============================================================


@pytest.mark.small
def test_cleanup_block_is_extractable_once() -> None:
    block = _cleanup_block()
    for placeholder in ("[issue_id]", "[default_branch]", "[close_via_harness]"):
        assert block.count(placeholder) == 1, placeholder


@pytest.mark.small
def test_step_5_6_is_between_issue_close_and_report() -> None:
    text = _skill_text()
    step_5_5 = text.index("### Step 5.5: Issue クローズ")
    step_5_6 = text.index("### Step 5.6: Issue の一時作業ディレクトリ削除")
    step_6 = text.index("### Step 6: 完了報告")
    assert step_5_5 < step_5_6 < step_6
    assert step_5_6 < text.index("# kaji-issue-tmp-cleanup") < step_6


@pytest.mark.small
@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("#### 6a. Issue コメント投稿", "#### 6b. stdout 報告"),
        ("#### 6b. stdout 報告", "## Verdict 出力"),
    ],
)
def test_report_tables_include_tmp_cleanup_result(start: str, end: str) -> None:
    assert _REPORT_ROW in _between(_skill_text(), start, end)


@pytest.mark.small
def test_cleanup_block_avoids_non_bsd_commands() -> None:
    block = _cleanup_block()
    assert "realpath" not in block
    assert "readlink" not in block
    assert re.search(r"\bfind\b[^\n]*-delete", block) is None


# ============================================================
# Large (large_local): 実 bash / git での削除挙動
# ============================================================


@dataclass(frozen=True)
class _Repo:
    """bare repo（``.bare``）+ main / feature の linked worktree からなる fixture。"""

    root: Path
    bare: Path
    main: Path
    feat: Path

    @property
    def kaji_tmp(self) -> Path:
        return self.main / "tmp" / "kaji"

    @property
    def target(self) -> Path:
        return self.kaji_tmp / _ISSUE


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("KAJI_TMP_DIR", None)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = "kaji-test"
        env[f"GIT_{role}_EMAIL"] = "kaji-test@example.com"
    return env


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=_git_env(), capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture()
def repo(outside_project_tmp_path: Path) -> _Repo:
    root = outside_project_tmp_path
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "init")
    bare = root / ".bare"
    _git(root, "clone", "-q", "--bare", str(seed), str(bare))
    main = root / "main"
    feat = root / "feat"
    _git(bare, "worktree", "add", "-q", str(main), "main")
    _git(bare, "worktree", "add", "-q", "-b", "feat/1", str(feat))
    return _Repo(root=root, bare=bare, main=main, feat=feat)


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _tree(path: Path) -> dict[str, str]:
    """``path`` 配下を symlink を辿らずに列挙し、相対パス → 種別/内容 の dict にする。"""
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in [*dirnames, *filenames]:
            child = Path(dirpath) / name
            rel = str(child.relative_to(path))
            if child.is_symlink():
                entries[rel] = f"-> {os.readlink(child)}"
            elif child.is_dir():
                entries[rel] = "<dir>"
            else:
                entries[rel] = child.read_text(encoding="utf-8", errors="surrogateescape")
    return entries


def _assert_fixture_resolves_main(repo: _Repo, cwd: Path) -> None:
    """subprocess の cwd から見た default branch checkout が fixture の main であることを確認する。"""
    porcelain = _git(cwd, "worktree", "list", "--porcelain")
    mains = [
        block.splitlines()[0].removeprefix("worktree ")
        for block in porcelain.strip().split("\n\n")
        if "branch refs/heads/main" in block.splitlines()
    ]
    assert mains == [str(repo.main)], porcelain


def _run(
    repo: _Repo,
    *,
    issue_id: str = _ISSUE,
    default_branch: str = "main",
    close_via_harness: str = "0",
    kaji_tmp_dir: str | Path | None = None,
    cwd: Path | None = None,
) -> str:
    run_cwd = repo.feat if cwd is None else cwd
    _assert_fixture_resolves_main(repo, run_cwd)
    script = (
        _cleanup_block()
        .replace("'[issue_id]'", shlex.quote(issue_id))
        .replace("'[default_branch]'", shlex.quote(default_branch))
        .replace("'[close_via_harness]'", shlex.quote(close_via_harness))
    )
    env = _git_env()
    if kaji_tmp_dir is not None:
        env["KAJI_TMP_DIR"] = str(kaji_tmp_dir)
    proc = subprocess.run(
        ["bash", "-c", script], cwd=run_cwd, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    results = [line for line in proc.stdout.splitlines() if line.startswith(_RESULT_PREFIX)]
    assert len(results) == 1, proc.stdout
    return results[0].removeprefix(_RESULT_PREFIX)


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize("cwd_name", ["feat", "main"])
def test_deletes_only_issue_dir_of_default_branch_checkout(repo: _Repo, cwd_name: str) -> None:
    _write(repo.target / "0001" / "a.txt")
    _write(repo.kaji_tmp / ".gitignore", "*\n")
    _write(repo.kaji_tmp / "510" / "0001" / "keep.txt", "other issue")
    _write(repo.bare / "tmp" / "kaji" / _ISSUE / "bare.txt", "bare")
    _write(repo.feat / "tmp" / "kaji" / _ISSUE / "feat.txt", "feat")

    result = _run(repo, cwd=getattr(repo, cwd_name))

    assert result == "削除済み"
    assert not repo.target.exists()
    assert (repo.kaji_tmp / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert (repo.kaji_tmp / "510" / "0001" / "keep.txt").read_text(encoding="utf-8") == (
        "other issue"
    )
    assert (repo.bare / "tmp" / "kaji" / _ISSUE / "bare.txt").exists()
    assert (repo.feat / "tmp" / "kaji" / _ISSUE / "feat.txt").exists()


@pytest.mark.large
@pytest.mark.large_local
def test_skips_when_default_branch_checkout_is_missing(repo: _Repo) -> None:
    _write(repo.target / "0001" / "a.txt")
    before = _tree(repo.root)

    result = _run(repo, default_branch="no-such-branch")

    assert result.startswith("スキップ（")
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize("linked", ["tmp", "tmp/kaji"])
def test_skips_when_ancestor_is_symlink(repo: _Repo, linked: str) -> None:
    external = repo.root / "external" / Path(linked).name
    _write((external / "kaji" if linked == "tmp" else external) / _ISSUE / "f")
    link = repo.main / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=True)
    assert (repo.target / "f").read_text(encoding="utf-8") == "x"  # symlink 経由で見える
    before = _tree(repo.root / "external")

    result = _run(repo)

    assert result.startswith("スキップ（")
    assert _tree(repo.root / "external") == before
    assert (repo.target / "f").exists()


@pytest.mark.large
@pytest.mark.large_local
def test_protects_running_attempt_dir_in_numbered_layout(repo: _Repo) -> None:
    _write(repo.target / "0001" / "a.txt")
    _write(repo.target / "0002" / "pytest-of-u" / "b.txt")
    _write(repo.target / "0003" / "c.txt", "running")
    _write(repo.target / "0003" / "scratchpad" / "d.txt", "scratch")
    _write(repo.target / "top.txt")
    keep = repo.target / "0003"

    result = _run(repo, close_via_harness="1", kaji_tmp_dir=keep)

    assert result == f"削除済み（実行中 attempt dir を保持: {keep}）"
    assert _tree(repo.target) == {
        "0003": "<dir>",
        "0003/c.txt": "running",
        "0003/scratchpad": "<dir>",
        "0003/scratchpad/d.txt": "scratch",
    }


@pytest.mark.large
@pytest.mark.large_local
def test_protects_running_attempt_dir_and_ancestors_in_run_id_layout(repo: _Repo) -> None:
    run = repo.target / "261018140038"
    _write(run / "design" / "attempt-001" / "x")
    _write(run / "close" / "attempt-002" / "y")
    _write(run / "close" / "attempt-001" / "z", "running")
    _write(run / "run.log")
    _write(repo.target / "261017000000" / "implement" / "attempt-001" / "w")
    keep = run / "close" / "attempt-001"

    result = _run(repo, close_via_harness="1", kaji_tmp_dir=keep)

    assert result == f"削除済み（実行中 attempt dir を保持: {keep}）"
    assert _tree(repo.target) == {
        "261018140038": "<dir>",
        "261018140038/close": "<dir>",
        "261018140038/close/attempt-001": "<dir>",
        "261018140038/close/attempt-001/z": "running",
    }


@pytest.mark.large
@pytest.mark.large_local
def test_manual_close_deletes_whole_issue_dir(repo: _Repo) -> None:
    _write(repo.target / "0001" / "a.txt")
    _write(repo.target / "0002" / "b.txt")

    result = _run(repo)

    assert result == "削除済み"
    assert not repo.target.exists()
    assert repo.kaji_tmp.is_dir()


@pytest.mark.large
@pytest.mark.large_local
def test_reports_absent_issue_dir(repo: _Repo) -> None:
    _write(repo.kaji_tmp / "510" / "a.txt")
    before = _tree(repo.root)

    result = _run(repo)

    assert result == "対象なし"
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize("issue_id", ["", "../x", "12a", "[issue_id]"])
def test_skips_invalid_issue_id(repo: _Repo, issue_id: str) -> None:
    _write(repo.target / "a.txt")
    _write(repo.main / "tmp" / "x" / "outside.txt")
    _write(repo.kaji_tmp / "12" / "b.txt")
    before = _tree(repo.root)

    result = _run(repo, issue_id=issue_id)

    assert result.startswith("スキップ（")
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
def test_skips_when_issue_dir_is_symlink(repo: _Repo) -> None:
    linked = _write(repo.kaji_tmp / "510" / "keep.txt", "other issue").parent
    repo.target.symlink_to(linked, target_is_directory=True)
    before = _tree(repo.root)

    result = _run(repo)

    assert result.startswith("スキップ（")
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize(
    "kaji_tmp_dir_kind", ["missing", "empty", "issue_dir", "issue_dir_ancestor", "filesystem_root"]
)
def test_skips_when_kaji_tmp_dir_cannot_be_protected(repo: _Repo, kaji_tmp_dir_kind: str) -> None:
    _write(repo.target / "0001" / "a.txt")
    kaji_tmp_dir = {
        "missing": str(repo.target / "9999"),
        "empty": "",
        "issue_dir": str(repo.target),
        "issue_dir_ancestor": str(repo.kaji_tmp),
        "filesystem_root": "/",
    }[kaji_tmp_dir_kind]
    before = _tree(repo.root)

    result = _run(repo, close_via_harness="1", kaji_tmp_dir=kaji_tmp_dir)

    assert result.startswith("スキップ（")
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize("close_via_harness", ["1", "[close_via_harness]"])
def test_skips_without_kaji_tmp_dir_unless_manual(repo: _Repo, close_via_harness: str) -> None:
    _write(repo.target / "0001" / "a.txt")
    before = _tree(repo.root)

    result = _run(repo, close_via_harness=close_via_harness)

    assert result.startswith("スキップ（")
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
def test_deletes_whole_issue_dir_when_kaji_tmp_dir_is_outside(repo: _Repo) -> None:
    _write(repo.target / "0001" / "a.txt")
    elsewhere = _write(repo.root / "elsewhere" / "0001" / "running.txt", "running").parent

    result = _run(repo, close_via_harness="1", kaji_tmp_dir=elsewhere)

    assert result == "削除済み"
    assert not repo.target.exists()
    assert (elsewhere / "running.txt").read_text(encoding="utf-8") == "running"


@pytest.mark.large
@pytest.mark.large_local
def test_does_not_follow_symlink_inside_issue_dir(repo: _Repo) -> None:
    other = _write(repo.kaji_tmp / "510" / "keep.txt", "other issue").parent
    repo.target.mkdir(parents=True)
    (repo.target / "link").symlink_to(other, target_is_directory=True)
    (repo.target / "file-link").symlink_to(other / "keep.txt")

    result = _run(repo)

    assert result == "削除済み"
    assert not repo.target.exists()
    assert (other / "keep.txt").read_text(encoding="utf-8") == "other issue"


@pytest.mark.large
@pytest.mark.large_local
def test_deletes_dotfiles(repo: _Repo) -> None:
    _write(repo.target / ".hidden")
    _write(repo.target / ".hidden-dir" / "x")
    _write(repo.target / "..odd")
    _write(repo.target / "0002" / "keep.txt", "running")
    _write(repo.target / "0002" / ".scratch", "running-dot")
    keep = repo.target / "0002"

    result = _run(repo, close_via_harness="1", kaji_tmp_dir=keep)

    assert result == f"削除済み（実行中 attempt dir を保持: {keep}）"
    assert _tree(repo.target) == {
        "0002": "<dir>",
        "0002/keep.txt": "running",
        "0002/.scratch": "running-dot",
    }


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write permission")
def test_reports_partial_failure_and_exits_zero(repo: _Repo) -> None:
    locked = repo.target / "0001" / "locked"
    _write(locked / "a.txt")
    _write(repo.target / "0002" / "b.txt")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _run(repo)
    finally:
        locked.chmod(stat.S_IRWXU)

    assert result == f"一部削除失敗（要手動対応: {repo.target / '0001'}）"
    assert (locked / "a.txt").exists()
    assert not (repo.target / "0002").exists()
