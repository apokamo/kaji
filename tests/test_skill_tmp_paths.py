"""skill の一時ファイル置き場を Issue / attempt ごとに分ける契約を検証する（Issue #548）。

- small: 共通手順 ``_shared/skill-tmp-resolve.md`` の静的構造と、利用側 skill の参照・置換
- medium: ``.claude/skills/**/*.md`` 全件を走査し、``[issue_id]`` を含まない絶対 ``/tmp/`` パスを禁止する
- large (large_local): 共通手順のブロックを実 ``sh`` で実行し、実 ``git`` で作った
  bare + linked worktree 構成の fixture に対する解決結果を検証する

fixture は ``outside_project_tmp_path`` に作る。workflow 内の ``tmp_path`` は kaji repo 内になり、
fixture 構築に失敗すると subprocess の ``git worktree list`` が実 repo を返しうるため。
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _REPO_ROOT / ".claude" / "skills"
_RESOLVE_DOC = _SKILL_DIR / "_shared" / "skill-tmp-resolve.md"
_BLOCK_RE = re.compile(r"```(?:bash|sh)\n(# kaji-skill-tmp-resolve\n.*?)\n```", re.DOTALL)
# 直前が行頭・空白・引用符・バッククォート・`(`・`=` の `/tmp/` を絶対パスとみなす。
# `$BASE/tmp/...`、`<main checkout>/tmp/...`、`${TMPDIR:-/tmp}/...` は対象外。
_ABSOLUTE_TMP_RE = re.compile(r"(?<![^\s\"'`(=])/tmp/[^\s\"'`)]*")
_TMP_USERS = [
    "issue-close/SKILL.md",
    "i-dev-final-check/SKILL.md",
    "i-doc-final-check/SKILL.md",
    "issue-design/SKILL.md",
    "_shared/implement-by-type/refactor.md",
]
_ISSUE = "548"


def _resolve_block() -> str:
    blocks = _BLOCK_RE.findall(_RESOLVE_DOC.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"expected exactly one kaji-skill-tmp-resolve block, got {len(blocks)}"
    return str(blocks[0])


def _fixed_tmp_paths(text: str) -> list[tuple[int, str]]:
    """``text`` から ``[issue_id]`` を含まない絶対 ``/tmp/`` パスを (行番号, パス) で返す。"""
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _ABSOLUTE_TMP_RE.finditer(line):
            if "[issue_id]" not in match.group(0):
                found.append((lineno, match.group(0)))
    return found


# ============================================================
# Small: 共通手順と利用側 skill の静的構造
# ============================================================


@pytest.mark.small
def test_resolve_block_is_extractable_once() -> None:
    block = _resolve_block()
    for placeholder in ("[issue_id]", "[default_branch]"):
        assert block.count(placeholder) == 1, placeholder


@pytest.mark.small
def test_resolve_block_avoids_non_bsd_commands() -> None:
    block = _resolve_block()
    assert "realpath" not in block
    assert "readlink" not in block
    assert "--tmpdir" not in block


@pytest.mark.small
@pytest.mark.parametrize("rel", _TMP_USERS)
def test_tmp_users_reference_resolver_and_placeholder(rel: str) -> None:
    text = (_SKILL_DIR / rel).read_text(encoding="utf-8")
    assert "skill-tmp-resolve.md" in text, rel
    assert "[skill_tmp_dir]/" in text, rel


@pytest.mark.small
def test_release_tracking_plan_does_not_pass_through_file() -> None:
    text = (_SKILL_DIR / "release" / "SKILL.md").read_text(encoding="utf-8")
    assert "/tmp/" not in text
    assert "[skill_tmp_dir]" not in text
    assert "open-tracking.json" not in text


@pytest.mark.small
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cat /tmp/issue-body.md", ["/tmp/issue-body.md"]),
        ("see `/tmp/kaji-follow-up-body.md`", ["/tmp/kaji-follow-up-body.md"]),
        ('x="/tmp/a"', ["/tmp/a"]),
        ("--body-file=/tmp/a.md", ["/tmp/a.md"]),
        ("(/tmp/a)", ["/tmp/a"]),
        ("git worktree add /tmp/kaji-incident-review-[issue_id] HEAD", []),
        ("TARGET=$BASE/tmp/kaji/$ISSUE_ID", []),
        ("`<main checkout>/tmp/kaji/[issue_id]/`", []),
        ('mktemp "${TMPDIR:-/tmp}/kaji-grill-me.XXXXXX"', []),
        ("`/tmp` へ fallback しない", []),
    ],
)
def test_fixed_tmp_path_detection(text: str, expected: list[str]) -> None:
    assert [path for _, path in _fixed_tmp_paths(text)] == expected


# ============================================================
# Medium: .claude/skills 全体の固定 /tmp パス禁止（Red → Green の再現テスト）
# ============================================================


@pytest.mark.medium
def test_no_fixed_tmp_paths_in_skills() -> None:
    """``.claude/skills/`` に Issue 番号を含まない絶対 ``/tmp/`` パスが残っていない。"""
    violations: list[str] = []
    for path in sorted(_SKILL_DIR.rglob("*.md")):
        rel = path.relative_to(_REPO_ROOT)
        for lineno, fixed in _fixed_tmp_paths(path.read_text(encoding="utf-8")):
            violations.append(f"{rel}:{lineno}: {fixed}")
    assert not violations, "fixed /tmp paths found:\n  " + "\n  ".join(violations)


# ============================================================
# Large (large_local): 実 sh / git での解決挙動
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


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: str
    stderr: str


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
    (root / "systmp").mkdir()
    return _Repo(root=root, bare=bare, main=main, feat=feat)


def _tree(path: Path) -> list[str]:
    """``path`` 配下の相対パス一覧を symlink を辿らずに返す。"""
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in [*dirnames, *filenames]:
            entries.append(str((Path(dirpath) / name).relative_to(path)))
    return sorted(entries)


def _run(
    repo: _Repo,
    *,
    issue_id: str = _ISSUE,
    default_branch: str = "main",
    kaji_tmp_dir: str | Path | None = None,
    cwd: Path | None = None,
) -> _Result:
    script = (
        _resolve_block()
        .replace("'[issue_id]'", shlex.quote(issue_id))
        .replace("'[default_branch]'", shlex.quote(default_branch))
    )
    env = _git_env()
    # 解決ブロックが /tmp（TMPDIR）に何も作らないことを fixture 内で観測する
    env["TMPDIR"] = str(repo.root / "systmp")
    if kaji_tmp_dir is not None:
        env["KAJI_TMP_DIR"] = str(kaji_tmp_dir)
    proc = subprocess.run(
        ["sh", "-c", script],
        cwd=repo.feat if cwd is None else cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    return _Result(proc.returncode, proc.stdout, proc.stderr)


def _single_line(result: _Result) -> Path:
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    return Path(lines[0])


@pytest.mark.large
@pytest.mark.large_local
def test_uses_kaji_tmp_dir_physical_path(repo: _Repo) -> None:
    attempt = repo.root / "attempt" / "0001"
    attempt.mkdir(parents=True)
    link = repo.root / "attempt-link"
    link.symlink_to(attempt, target_is_directory=True)

    resolved = _single_line(_run(repo, kaji_tmp_dir=link))

    assert resolved == attempt.resolve()
    assert not (repo.main / "tmp").exists()
    assert _tree(repo.root / "systmp") == []


@pytest.mark.large
@pytest.mark.large_local
def test_fails_without_fallback_when_kaji_tmp_dir_is_missing(repo: _Repo) -> None:
    before = _tree(repo.root)

    result = _run(repo, kaji_tmp_dir=repo.root / "does-not-exist")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "KAJI_TMP_DIR" in result.stderr
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize("cwd_name", ["feat", "main"])
def test_manual_creates_unique_dir_under_default_branch_checkout(
    repo: _Repo, cwd_name: str
) -> None:
    cwd = getattr(repo, cwd_name)

    first = _single_line(_run(repo, cwd=cwd))
    second = _single_line(_run(repo, cwd=cwd))

    issue_dir = repo.kaji_tmp.resolve() / _ISSUE
    for resolved in (first, second):
        assert resolved.is_absolute()
        assert resolved.parent == issue_dir
        assert resolved.name.startswith("manual-")
        assert resolved.is_dir()
        assert list(resolved.iterdir()) == []
    assert first != second
    assert (repo.kaji_tmp / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert not (repo.feat / "tmp").exists()
    assert not (repo.bare / "tmp").exists()
    assert _tree(repo.root / "systmp") == []


@pytest.mark.large
@pytest.mark.large_local
def test_manual_keeps_existing_gitignore(repo: _Repo) -> None:
    repo.kaji_tmp.mkdir(parents=True)
    (repo.kaji_tmp / ".gitignore").write_text("custom\n", encoding="utf-8")

    _single_line(_run(repo))

    assert (repo.kaji_tmp / ".gitignore").read_text(encoding="utf-8") == "custom\n"


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize(
    ("issue_id", "default_branch"),
    [
        ("", "main"),
        ("../x", "main"),
        ("12a", "main"),
        ("[issue_id]", "main"),
        (_ISSUE, "no-such-branch"),
    ],
)
def test_manual_fails_without_creating_anything(
    repo: _Repo, issue_id: str, default_branch: str
) -> None:
    before = _tree(repo.root)

    result = _run(repo, issue_id=issue_id, default_branch=default_branch)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr != ""
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
def test_manual_fails_when_default_branch_checkout_is_ambiguous(repo: _Repo) -> None:
    # 同じ branch を 2 つの worktree が持つ状態（--force で作れる）を 1 件に特定できないとして扱う
    _git(repo.bare, "worktree", "add", "-q", "--force", str(repo.root / "main2"), "main")
    before = _tree(repo.root)

    result = _run(repo)

    assert result.returncode != 0
    assert result.stdout == ""
    assert _tree(repo.root) == before


@pytest.mark.large
@pytest.mark.large_local
def test_manual_fails_outside_git_repository(repo: _Repo) -> None:
    outside = repo.root / "not-a-repo"
    outside.mkdir()
    before = _tree(repo.root)

    result = _run(repo, cwd=outside)

    assert result.returncode != 0
    assert result.stdout == ""
    assert _tree(repo.root) == before
