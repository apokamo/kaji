"""Tests for ``kaji issue prepend-note`` (Issue #200).

``/issue-start`` Step 4 が Issue 本文先頭へ ``> [!NOTE]`` メタ情報ブロックを追記
する際、heredoc によるエージェント multi-line 合成に依存していたため、Haiku 等
一部モデルで blockquote と本文の境界 blank line が脱落し、
``> **Branch**: `fix/199`## 概要`` のように本文 heading が blockquote 行へ吸着した
（Issue #199 実観測 OB）。

本ファイルは ``kaji issue prepend-note`` dispatch の bridging test を担う。
純粋関数 ``build_worktree_note_body`` の command 非依存テストは
``tests/test_providers_context.py`` が担う。

設計書: ``draft/design/issue-200-fix-issue-start-skill-issue-blank-line.md``
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.commands.issue import _handle_issue
from kaji_harness.providers.context import build_worktree_note_body
from kaji_harness.providers.context import build_worktree_note_body as shim_note_body
from kaji_harness.providers.models import Issue


@pytest.mark.small
def test_legacy_shim_reexports_worktree_note_builder() -> None:
    assert shim_note_body is build_worktree_note_body


# ============================================================
# Medium: kaji issue prepend-note dispatch 結合
# ============================================================


def _write_github_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'
    )
    return repo


@pytest.mark.medium
class TestPrependNoteGitHubDispatch:
    """``provider.type='github'`` での provider 共通 dispatch 検証。"""

    def test_prepend_note_inserts_block_with_blank_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本文先頭が NOTE ブロック + blank line + 元本文の形になる。"""
        monkeypatch.chdir(_write_github_repo(tmp_path))

        current = Issue(id="200", title="Hello", body="## 概要\n\n元本文", state="open")
        with (
            patch(
                "kaji_harness.providers.GitHubProvider.view_issue",
                return_value=current,
            ),
            patch(
                "kaji_harness.providers.GitHubProvider.edit_issue",
                return_value=current,
            ) as mock_edit,
        ):
            rc = _handle_issue(
                [
                    "prepend-note",
                    "200",
                    "--worktree",
                    "kaji-fix-200",
                    "--branch",
                    "fix/200",
                ]
            )
        assert rc == 0

        args, kwargs = mock_edit.call_args
        assert args == ("200",)
        new_body = kwargs["body"]
        assert new_body == (
            "> [!NOTE]\n"
            "> **Worktree**: `../kaji-fix-200`\n"
            "> **Branch**: `fix/200`\n"
            "\n"
            "## 概要\n\n元本文"
        )
        # OB の吸着が起きていない
        assert "`fix/200`## 概要" not in new_body

    def test_github_routes_via_provider_methods_not_gh_passthrough(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """github でも ``gh issue prepend-note`` へ forward せず
        ``view_issue`` / ``edit_issue`` 経路で合成本文を edit する。"""
        monkeypatch.chdir(_write_github_repo(tmp_path))

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
            patch("subprocess.run") as mock_run,
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
