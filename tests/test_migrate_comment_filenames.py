"""Tests for ``scripts/migrate_comment_filenames_and_machine.py``.

Issue local-pc5090-21 設計書 § テスト戦略 § Medium / § Large の要請を満たす:

- Medium: tmp_path 上に旧形式 fixture を組み立てて pure-Python rename を検証
- Large (large_local): subprocess 経由で migration script を実行し、生成された
  filename / dir / counter / config が正しいことを assert する E2E 検証
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "migrate_comment_filenames_and_machine.py"


def _subprocess_env() -> dict[str, str]:
    """worktree の kaji_harness を確実に import させるための env。

    editable 1: は ``/home/.../main/kaji_harness`` を指すが、本 worktree 用の
    pytest では当然 worktree 側のコードを読みたい。subprocess の cwd は
    test の tmp_path であり、``python -m`` の sys.path[0] 機構では
    worktree を拾えないため、PYTHONPATH を明示する。
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{_REPO_ROOT}{os.pathsep}{existing}" if existing else str(_REPO_ROOT)
    return env


def _write_fm(path: Path, body: str, *, author: str, created_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nauthor: {author}\ncreated_at: '{created_at}'\n---\n{body}\n",
        encoding="utf-8",
    )


def _write_issue_md(path: Path, *, issue_id: str, slug: str, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {issue_id}\ntitle: t\nstate: open\nslug: {slug}\n---\n{body}\n",
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")


# ============================================================
# Medium: pure-Python migration logic
# ============================================================


@pytest.mark.medium
class TestMigrationPlan:
    """``_plan_comment_renames`` の決定論的 rename plan 生成。"""

    def _seed(self, repo: Path) -> None:
        # issue 1 (移行対象)
        i1 = repo / ".kaji" / "issues" / "local-pc5090-1-foo"
        _write_issue_md(i1 / "issue.md", issue_id="local-pc5090-1", slug="foo")
        _write_fm(
            i1 / "comments" / "0001-pc5090.md",
            "first",
            author="pc5090",
            created_at="2026-05-10T10:00:00Z",
        )
        _write_fm(
            i1 / "comments" / "0002-pc5090.md",
            "second",
            author="pc5090",
            created_at="2026-05-10T10:00:00Z",  # 同秒衝突
        )
        # issue 21 (boundary)
        i21 = repo / ".kaji" / "issues" / "local-pc5090-21-boundary"
        _write_issue_md(i21 / "issue.md", issue_id="local-pc5090-21", slug="boundary")
        _write_fm(
            i21 / "comments" / "0001-pc5090.md",
            "boundary",
            author="pc5090",
            created_at="2026-05-10T11:00:00Z",
        )

    def test_plan_assigns_p1_for_1_to_20_and_pc5090_for_21(self, tmp_path: Path) -> None:
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        try:
            import migrate_comment_filenames_and_machine as mig
        finally:
            sys.path.pop(0)
        repo = tmp_path / "repo"
        repo.mkdir()
        self._seed(repo)
        plans = mig._plan_comment_renames(repo)
        by_issue: dict[int, list[Path]] = {}
        for p in plans:
            by_issue.setdefault(p.issue_n, []).append(p.new)

        # issue 1: machine 部は p1、同秒衝突は +1s
        i1_news = sorted(p.name for p in by_issue[1])
        assert i1_news == ["20260510T100000Z-p1.md", "20260510T100001Z-p1.md"]
        # issue 21: machine 部は pc5090 維持
        assert [p.name for p in by_issue[21]] == ["20260510T110000Z-pc5090.md"]


@pytest.mark.medium
class TestRenameDirsCrossRef:
    """``_phase_rename_dirs`` の cross-ref 書換が N=1..20 のみで 21 を保護する。"""

    def test_rewrites_1_to_20_protects_21(self, tmp_path: Path) -> None:
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        try:
            import migrate_comment_filenames_and_machine as mig
        finally:
            sys.path.pop(0)
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        # issue 21 の本文に 1..20 と 21 自身への参照を入れる
        body = (
            "refs: local-pc5090-1, local-pc5090-10, local-pc5090-20, "
            "local-pc5090-21 (self), local-pc5090-100 (out of range)"
        )
        i21 = repo / ".kaji" / "issues" / "local-pc5090-21-boundary"
        _write_issue_md(
            i21 / "issue.md",
            issue_id="local-pc5090-21",
            slug="boundary",
            body=body,
        )
        # 移行対象 issue 1
        i1 = repo / ".kaji" / "issues" / "local-pc5090-1-foo"
        _write_issue_md(i1 / "issue.md", issue_id="local-pc5090-1", slug="foo")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "seed")

        mig._phase_rename_dirs(repo, dry_run=False)

        new_body = (i21 / "issue.md").read_text(encoding="utf-8")
        # N=1..20 は書き換え
        assert "local-p1-1" in new_body
        assert "local-p1-10" in new_body
        assert "local-p1-20" in new_body
        # 21 自身は保護
        assert "local-pc5090-21" in new_body
        # N=100 のような範囲外も保護
        assert "local-pc5090-100" in new_body
        # 旧 N=1..20 は残らない
        assert "local-pc5090-1," not in new_body
        assert "local-pc5090-10," not in new_body
        assert "local-pc5090-20," not in new_body

        # issue 1 の dir は rename される
        assert (repo / ".kaji" / "issues" / "local-p1-1-foo").is_dir()
        assert not (repo / ".kaji" / "issues" / "local-pc5090-1-foo").exists()
        # issue 21 の dir は据置
        assert i21.is_dir()


# ============================================================
# Large (large_local): subprocess E2E
# ============================================================


@pytest.mark.large
@pytest.mark.large_local
class TestMigrationE2E:
    """migration script を subprocess で実行し、出力結果を assert する。"""

    def _seed_full(self, repo: Path, repo_main: Path) -> None:
        """1..20 + 21 の minimal fixture。"""
        # 1〜20
        for n in (1, 5, 20):
            d = repo / ".kaji" / "issues" / f"local-pc5090-{n}-x"
            _write_issue_md(d / "issue.md", issue_id=f"local-pc5090-{n}", slug="x")
            _write_fm(
                d / "comments" / "0001-pc5090.md",
                f"body-{n}",
                author="pc5090",
                created_at=f"2026-05-{n:02d}T10:00:00Z",
            )
        # 21
        d21 = repo / ".kaji" / "issues" / "local-pc5090-21-boundary"
        _write_issue_md(
            d21 / "issue.md",
            issue_id="local-pc5090-21",
            slug="boundary",
            body="ref: local-pc5090-5",
        )
        _write_fm(
            d21 / "comments" / "0001-pc5090.md",
            "ok",
            author="pc5090",
            created_at="2026-05-30T10:00:00Z",
        )
        # gitignored: counter + config (in repo_main)
        (repo_main / ".kaji" / "counters").mkdir(parents=True, exist_ok=True)
        (repo_main / ".kaji" / "counters" / "pc5090.txt").write_text("21")
        (repo_main / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "local"\n'
            '[provider.local]\nmachine_id = "pc5090"\ndefault_branch = "main"\n'
        )

    def test_dry_run_produces_plan_without_changes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        self._seed_full(repo, repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "seed")

        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "--phase=all",
                f"--repo={repo}",
                f"--repo-main={repo}",
                "--dry-run",
            ],
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        # plan が出力されている
        assert "[rename-comments]" in result.stdout
        assert "[rename-dirs]" in result.stdout
        assert "[config]" in result.stdout
        # 実 rename は走らない
        assert (
            repo / ".kaji" / "issues" / "local-pc5090-1-x" / "comments" / "0001-pc5090.md"
        ).is_file()
        assert (repo / ".kaji" / "counters" / "pc5090.txt").is_file()

    def test_full_run_renames_files_dirs_counter_config(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        self._seed_full(repo, repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "seed")

        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "--phase=all",
                f"--repo={repo}",
                f"--repo-main={repo}",
            ],
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

        # 1..20 の dir は rename されている
        for n in (1, 5, 20):
            assert (repo / ".kaji" / "issues" / f"local-p1-{n}-x").is_dir()
            assert not (repo / ".kaji" / "issues" / f"local-pc5090-{n}-x").exists()
        # 21 dir は据置
        assert (repo / ".kaji" / "issues" / "local-pc5090-21-boundary").is_dir()
        # comment filename 形式チェック
        i1_comments = list((repo / ".kaji" / "issues" / "local-p1-1-x" / "comments").iterdir())
        assert len(i1_comments) == 1
        assert i1_comments[0].name.endswith("-p1.md")
        assert i1_comments[0].name.startswith("2026")  # timestamp
        # 21 配下は -pc5090.md 維持
        i21_comments = list(
            (repo / ".kaji" / "issues" / "local-pc5090-21-boundary" / "comments").iterdir()
        )
        assert len(i21_comments) == 1
        assert i21_comments[0].name.endswith("-pc5090.md")
        # 21 issue.md の cross-ref が書き換わっている
        i21_text = (repo / ".kaji" / "issues" / "local-pc5090-21-boundary" / "issue.md").read_text(
            encoding="utf-8"
        )
        assert "local-p1-5" in i21_text
        assert "local-pc5090-21" in i21_text  # 自身は保護
        # counter / config
        assert (repo / ".kaji" / "counters" / "p1.txt").is_file()
        assert not (repo / ".kaji" / "counters" / "pc5090.txt").exists()
        assert 'machine_id = "p1"' in (repo / ".kaji" / "config.local.toml").read_text(
            encoding="utf-8"
        )

    def test_config_phase_fails_fast_when_no_counter(self, tmp_path: Path) -> None:
        """--repo-main で counter が見つからない場合 fail-fast。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        # config.local.toml は存在するが counter file が無い
        (repo / ".kaji").mkdir()
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider.local]\nmachine_id = "pc5090"\n'
        )
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "--phase=config",
                f"--repo={repo}",
                f"--repo-main={repo}",
            ],
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "no counter file" in result.stderr

    def test_config_phase_auto_resolves_repo_main_via_symlink(self, tmp_path: Path) -> None:
        """worktree から `--repo-main` 省略時に symlink 経由で main を解決する。"""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        (main_repo / ".kaji" / "counters").mkdir(parents=True)
        (main_repo / ".kaji" / "counters" / "pc5090.txt").write_text("21")
        (main_repo / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "local"\n[provider.local]\nmachine_id = "pc5090"\n'
        )

        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / ".kaji").mkdir()
        # symlink: worktree/.kaji/config.local.toml -> main/.kaji/config.local.toml
        (worktree / ".kaji" / "config.local.toml").symlink_to(
            main_repo / ".kaji" / "config.local.toml"
        )

        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "--phase=config",
                f"--repo={worktree}",
            ],
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        # main repo 上の counter が rename された
        assert (main_repo / ".kaji" / "counters" / "p1.txt").is_file()
        assert not (main_repo / ".kaji" / "counters" / "pc5090.txt").exists()
        assert 'machine_id = "p1"' in (main_repo / ".kaji" / "config.local.toml").read_text(
            encoding="utf-8"
        )


@pytest.mark.large
@pytest.mark.large_local
class TestKajiCliPostMigration:
    """migration script 適用後に kaji CLI が想定通り動くことの subprocess smoke。"""

    def _setup_migrated_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        # base config.toml
        (repo / ".kaji").mkdir(exist_ok=True)
        (repo / ".kaji" / "config.toml").write_text(
            "[paths]\nartifacts_dir = '.kaji-artifacts'\nskill_dir = '.claude/skills'\n"
            "[execution]\ndefault_timeout = 1800\n"
            "[provider]\ntype = 'local'\n"
        )
        # config.local.toml に p1
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "local"\n'
            '[provider.local]\nmachine_id = "p1"\ndefault_branch = "main"\n'
        )
        # counter p1.txt
        (repo / ".kaji" / "counters").mkdir(parents=True)
        (repo / ".kaji" / "counters" / "p1.txt").write_text("21")
        # 既存 issue (1, 21 のみ)
        d1 = repo / ".kaji" / "issues" / "local-p1-1-foo"
        _write_issue_md(d1 / "issue.md", issue_id="local-p1-1", slug="foo")
        _write_fm(
            d1 / "comments" / "20260510T100000Z-p1.md",
            "first",
            author="p1",
            created_at="2026-05-10T10:00:00Z",
        )
        d21 = repo / ".kaji" / "issues" / "local-pc5090-21-boundary"
        _write_issue_md(d21 / "issue.md", issue_id="local-pc5090-21", slug="boundary")
        _write_fm(
            d21 / "comments" / "20260530T100000Z-pc5090.md",
            "boundary",
            author="pc5090",
            created_at="2026-05-30T10:00:00Z",
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "migrated state")
        return repo

    def test_kaji_issue_list_returns_p1_and_pc5090_21(self, tmp_path: Path) -> None:
        repo = self._setup_migrated_repo(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "kaji_harness.cli_main", "issue", "list"],
            cwd=repo,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "local-p1-1" in out
        assert "local-pc5090-21" in out

    def test_kaji_issue_create_yields_local_p1_22(self, tmp_path: Path) -> None:
        """完了条件: 移行後に新規 issue が ``local-p1-22`` で採番される。"""
        repo = self._setup_migrated_repo(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "issue",
                "create",
                "--title",
                "smoke",
                "--body",
                "smoke body",
                "--label",
                "type:chore",
            ],
            cwd=repo,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        # stdout includes issue id; either as bare token or in path
        combined = result.stdout + result.stderr
        assert "local-p1-22" in combined, f"expected local-p1-22 in output, got: {combined!r}"

    def test_kaji_issue_comment_writes_timestamp_filename(self, tmp_path: Path) -> None:
        repo = self._setup_migrated_repo(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "issue",
                "comment",
                "local-p1-1",
                "--body",
                "added",
            ],
            cwd=repo,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        # stdout: <ts>-p1
        line = result.stdout.strip().splitlines()[-1]
        assert line.endswith("-p1")
        ts = line.rsplit("-", 1)[0]
        assert len(ts) == 16 and ts.endswith("Z"), f"bad ts: {ts!r}"
        # ファイルが生成されている
        cdir = repo / ".kaji" / "issues" / "local-p1-1-foo" / "comments"
        files = sorted(cdir.iterdir())
        assert any(f.name == f"{ts}-p1.md" for f in files)


# Cleanup the migration module cache between tests so per-test sys.path
# manipulations don't leak.
@pytest.fixture(autouse=True)
def _clear_migration_module_cache() -> Iterator[None]:
    yield
    sys.modules.pop("migrate_comment_filenames_and_machine", None)


_ = shutil  # 予約: 将来 fixture 削除時の rmdir 等で使う想定
