"""Phase 4 commit 5: ``_handle_pr`` bare provider エラー化。

Small/Medium テスト:

- ``provider.type='local'`` 配下のすべての ``kaji pr`` サブコマンドが exit 2
  + 代替手順 stderr で停止する
- ``provider.type='github'`` の挙動は Phase 3-e と bit-exact に維持される
  （``gh`` への subprocess 引数組み立てを mock で検証）
- ``_PR_BARE_PROVIDER_ERROR`` 文面のキーワード検証
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.cli_main import _PR_BARE_PROVIDER_ERROR
from kaji_harness.cli_main import main as cli_main

_BASE_CONFIG = """
[paths]
artifacts_dir = ".kaji/artifacts"
skill_dir = ".claude/skills"

[execution]
default_timeout = 600
""".lstrip()

_PROVIDER_GH = '[provider]\ntype = "github"\n[provider.github]\nrepo = "owner/name"\n'
_PROVIDER_LOCAL = '[provider]\ntype = "local"\n[provider.local]\nmachine_id = "pc1"\n'


def _setup_repo(tmp_path: Path, *, provider: str) -> None:
    (tmp_path / ".kaji").mkdir()
    body = _PROVIDER_GH if provider == "github" else _PROVIDER_LOCAL
    (tmp_path / ".kaji" / "config.toml").write_text(_BASE_CONFIG + body)


def _run_at(tmp_path: Path, argv: list[str]) -> tuple[int, str, str]:
    """Run cli_main from ``tmp_path`` cwd; capture stdout/stderr/exit."""
    out = io.StringIO()
    err = io.StringIO()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = cli_main(argv)
            except SystemExit as e:
                rc = int(e.code) if isinstance(e.code, int) else 1
    finally:
        os.chdir(cwd)
    return rc, out.getvalue(), err.getvalue()


# -------- Small: error message --------


@pytest.mark.small
def test_pr_bare_provider_error_keywords() -> None:
    msg = _PR_BARE_PROVIDER_ERROR
    assert "forge-only" in msg
    assert "provider.type='local'" in msg
    assert "/issue-review-code" in msg
    assert "/issue-fix-code" in msg
    assert "/issue-verify-code" in msg
    assert "/issue-close" in msg


# -------- Medium: provider=local rejects every pr subcommand --------


@pytest.mark.medium
@pytest.mark.parametrize(
    "args",
    [
        ["pr", "create", "--title", "t", "--body", "b"],
        ["pr", "list"],
        ["pr", "view", "1"],
        ["pr", "review-comments", "1"],
        ["pr", "reviews", "1"],
        ["pr", "reply-to-comment", "1", "--to", "2", "--body", "x"],
        ["pr", "merge", "1"],
        ["pr"],
    ],
    ids=[
        "create",
        "list",
        "view",
        "review-comments",
        "reviews",
        "reply-to-comment",
        "merge",
        "noargs",
    ],
)
def test_pr_local_provider_blocks_all_subcommands(tmp_path: Path, args: list[str]) -> None:
    _setup_repo(tmp_path, provider="local")
    # gh / subprocess.run should NEVER be called under provider=local
    with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
        rc, _, stderr = _run_at(tmp_path, args)
    assert rc == 2
    assert "forge-only" in stderr
    assert "provider.type='local'" in stderr
    # gh subprocess must not be invoked under provider=local. ``git worktree list``
    # may be invoked by ``get_provider()`` to resolve the main worktree (gl:11);
    # only ``gh`` calls indicate a forge passthrough leak.
    gh_calls = [c for c in mock_run.call_args_list if c[0] and c[0][0] and c[0][0][0] == "gh"]
    assert gh_calls == [], f"gh subprocess must not be invoked under provider=local: {gh_calls}"


# -------- Medium: provider=github passthrough behaviour preserved --------


@pytest.mark.medium
def test_pr_github_passthrough_invokes_gh_with_repo_injection(tmp_path: Path) -> None:
    """provider=github 経路は Phase 3-e と同じく gh に ``--repo`` を末尾注入する。"""
    _setup_repo(tmp_path, provider="github")
    with (
        patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
        patch("kaji_harness.cli_main.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        rc, _, _ = _run_at(tmp_path, ["pr", "list"])
    assert rc == 0
    assert mock_run.call_count == 1
    cmd = mock_run.call_args[0][0]
    assert cmd[:2] == ["gh", "pr"]
    # --repo owner/name が末尾に注入されている
    assert "--repo" in cmd
    assert "owner/name" in cmd


@pytest.mark.medium
def test_pr_github_pr_create_forwarded(tmp_path: Path) -> None:
    _setup_repo(tmp_path, provider="github")
    with (
        patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
        patch("kaji_harness.cli_main.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        rc, _, _ = _run_at(tmp_path, ["pr", "create", "--title", "t", "--body", "b"])
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[:3] == ["gh", "pr", "create"]
