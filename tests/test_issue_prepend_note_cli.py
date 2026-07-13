"""Tests for ``kaji issue prepend-note`` (Issue #200).

``/issue-start`` Step 4 が Issue 本文先頭へ ``> [!NOTE]`` メタ情報ブロックを追記
する際、heredoc によるエージェント multi-line 合成に依存していたため、Haiku 等
一部モデルで blockquote と本文の境界 blank line が脱落し、
``> **Branch**: `fix/199`## 概要`` のように本文 heading が blockquote 行へ吸着した
（Issue #199 実観測 OB）。

本ファイルは合成を kaji 内部の決定的経路（純粋関数 ``build_worktree_note_body`` +
``kaji issue prepend-note`` dispatch）へ移し、モデル非依存に blank line を保証する
ことの Red → Green 回帰証跡。

設計書: ``draft/design/issue-200-fix-issue-start-skill-issue-blank-line.md``
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.cli_main import _handle_issue
from kaji_harness.providers import LocalProvider
from kaji_harness.providers.context import build_worktree_note_body
from kaji_harness.providers.models import Issue

# ============================================================
# Small: build_worktree_note_body（核となる回帰テスト）
# ============================================================


@pytest.mark.small
class TestBuildWorktreeNoteBody:
    """純粋関数の不変条件（blank line 保証）を検証する。"""

    def test_blank_line_guaranteed_between_note_and_heading(self) -> None:
        """OB の直接 assert: blockquote 行と本文 heading の間が空行ちょうど 1 行。

        Issue #199 OB の ``> **Branch**: `fix/199`## 概要`` 連結（改行 1 + 空行 1
        の計 2 つが消えた状態）が起こらないことを保証する。
        """
        result = build_worktree_note_body(
            "## 概要\n\n本文",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        # blockquote と heading の間は空行ちょうど 1 行
        assert "> **Branch**: `fix/200`\n\n## 概要" in result
        # OB の吸着（backtick 直後に heading が連結）が起きていない
        assert "`fix/200`## 概要" not in result

    def test_full_note_block_layout(self) -> None:
        """NOTE ブロックのレイアウト全体が EB（#190 相当）と一致する。"""
        result = build_worktree_note_body(
            "## 概要",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result == (
            "> [!NOTE]\n> **Worktree**: `../kaji-fix-200`\n> **Branch**: `fix/200`\n\n## 概要"
        )

    def test_leading_blank_lines_normalized_to_one(self) -> None:
        """本文先頭の余分な空行は 1 行へ収束する（冪等性）。"""
        result = build_worktree_note_body(
            "\n\n\n## 概要",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert "> **Branch**: `fix/200`\n\n## 概要" in result
        # 空行が 2 行以上残らない
        assert "`fix/200`\n\n\n" not in result

    def test_empty_body_produces_note_only(self) -> None:
        """空 body → NOTE ブロックのみ。末尾に余分な blank line を付けない。"""
        result = build_worktree_note_body(
            "",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result == ("> [!NOTE]\n> **Worktree**: `../kaji-fix-200`\n> **Branch**: `fix/200`\n")

    def test_whitespace_only_body_produces_note_only(self) -> None:
        """改行のみの body も空扱いで NOTE ブロックのみになる。"""
        result = build_worktree_note_body(
            "\n\n",
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result == ("> [!NOTE]\n> **Worktree**: `../kaji-fix-200`\n> **Branch**: `fix/200`\n")

    def test_special_chars_in_body_preserved(self) -> None:
        """backtick / ``$`` / 複数行を含む body をそのまま保持する（shell 評価なし）。"""
        body = "## 概要\n\n`code` と $VAR を含む `inline`\n- 行 2"
        result = build_worktree_note_body(
            body,
            worktree="kaji-fix-200",
            branch="fix/200",
        )
        assert result.endswith(body)
        assert "$VAR" in result
        assert "`code`" in result


# ============================================================
# Medium: kaji issue prepend-note dispatch 結合
# ============================================================


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_local_repo(tmp_path: Path, *, machine_id: str = "pc1") -> Path:
    """``provider.type='local'`` 用に config + git init された repo を用意する。"""
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n'
        f'[provider.local]\nmachine_id = "{machine_id}"\n'
    )
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


@pytest.mark.medium
class TestPrependNoteLocalDispatch:
    """``kaji issue prepend-note`` の local provider end-to-end。"""

    def test_prepend_note_inserts_block_with_blank_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """issue.md 本文先頭が NOTE ブロック + blank line + 元本文の形になる。"""
        repo = _write_local_repo(tmp_path)
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        issue = provider.create_issue(
            title="Hello",
            body="## 概要\n\n元本文",
            labels=["type:bug"],
            slug="hello-test",
        )
        monkeypatch.chdir(repo)

        rc = _handle_issue(
            [
                "prepend-note",
                issue.id,
                "--worktree",
                "kaji-fix-200",
                "--branch",
                "fix/200",
            ]
        )
        assert rc == 0

        new_body = provider.view_issue(issue.id).body
        assert new_body == (
            "> [!NOTE]\n"
            "> **Worktree**: `../kaji-fix-200`\n"
            "> **Branch**: `fix/200`\n"
            "\n"
            "## 概要\n\n元本文"
        )
        # OB の吸着が起きていない
        assert "`fix/200`## 概要" not in new_body

    def test_prepend_note_with_commit_creates_atomic_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--commit`` で working tree が clean になり HEAD に issue.md が含まれる。"""
        repo = _write_local_repo(tmp_path)
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        issue = provider.create_issue(
            title="Hello",
            body="## 概要\n\n元本文",
            labels=["type:bug"],
            slug="hello-test",
        )
        # seed issue を commit し HEAD を clean にする
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "seed")
        monkeypatch.chdir(repo)

        rc = _handle_issue(
            [
                "prepend-note",
                issue.id,
                "--worktree",
                "kaji-fix-200",
                "--branch",
                "fix/200",
                "--commit",
            ]
        )
        assert rc == 0

        status = _git(repo, "status", "--porcelain").stdout
        assert status == "", f"unexpected dirty status: {status!r}"

        files = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip().splitlines()
        assert any(f.endswith("/issue.md") for f in files), f"issue.md not in HEAD: {files}"

    def test_prepend_note_caught_before_local_subcommand_guard(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``prepend-note`` は ``context`` 同様 provider 共通で先回り捕捉され、
        ``_LOCAL_ISSUE_SUBS`` の unknown subcommand ガードへ落ちない。"""
        repo = _write_local_repo(tmp_path)
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        issue = provider.create_issue(
            title="Hello", body="## 概要", labels=["type:bug"], slug="hello-test"
        )
        monkeypatch.chdir(repo)

        rc = _handle_issue(
            ["prepend-note", issue.id, "--worktree", "kaji-fix-200", "--branch", "fix/200"]
        )
        assert rc == 0
        # unknown subcommand エラーが出ていない
        assert "is not supported" not in capsys.readouterr().err


@pytest.mark.medium
class TestPrependNoteGitHubDispatch:
    """``provider.type='github'`` での provider 共通 dispatch 検証。"""

    def test_github_routes_via_provider_methods_not_gh_passthrough(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """github でも ``gh issue prepend-note`` へ forward せず
        ``view_issue`` / ``edit_issue`` 経路で合成本文を edit する。"""
        repo = tmp_path / "repo"
        (repo / ".kaji").mkdir(parents=True)
        (repo / ".kaji" / "config.toml").write_text(
            '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
            "[execution]\ndefault_timeout = 1800\n\n"
            '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'
        )
        monkeypatch.chdir(repo)

        current = Issue(id="200", title="t", body="## 概要", state="open")
        edited = Issue(id="200", title="t", body="(edited)", state="open")
        with (
            patch(
                "kaji_harness.providers.GitHubProvider.view_issue",
                return_value=current,
            ) as mock_view,
            patch(
                "kaji_harness.providers.GitHubProvider.edit_issue",
                return_value=edited,
            ) as mock_edit,
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            rc = _handle_issue(
                [
                    "prepend-note",
                    "200",
                    "--worktree",
                    "kaji-fix-200",
                    "--branch",
                    "fix/200",
                    "--commit",
                ]
            )
        assert rc == 0
        mock_view.assert_called_once()
        # edit_issue に渡る body が決定的合成（blank line 保証）であること
        _, kwargs = mock_edit.call_args
        sent_body = kwargs["body"]
        assert "> **Branch**: `fix/200`\n\n## 概要" in sent_body
        assert "`fix/200`## 概要" not in sent_body
        # gh / git subprocess へ forward していない（github の --commit は silent 無視）
        mock_run.assert_not_called()
