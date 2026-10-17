"""Tests for ``kaji issue context`` helper CLI.

``kaji issue context <id>`` は provider 共通 helper として
``provider.resolve_issue_context()`` の結果を JSON 出力する。#448 で local provider を
削除したため、github provider 設定 + ``view_issue`` stub で検証する。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.commands.issue import _handle_issue
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.providers.models import Issue, IssueContext, Label


def _write_github_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
    )
    return repo


def _stub_issue(monkeypatch: pytest.MonkeyPatch, *, labels: list[str]) -> str:
    """``GitHubProvider.view_issue`` を指定 label 付きの決定的 Issue に置き換える。"""

    def _fake_view_issue(self: GitHubProvider, issue_id: str) -> Issue:
        return Issue(
            id=issue_id,
            title="Hello",
            body="body text",
            state="open",
            labels=[Label(name=name) for name in labels],
            slug="hello-test",
        )

    monkeypatch.setattr(GitHubProvider, "view_issue", _fake_view_issue)
    return "1"


# ============================================================
# Small: argparse / --json / -q
# ============================================================


@pytest.mark.small
class TestContextCLISmall:
    def test_default_outputs_all_fields(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=["type:bug"])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        for field in (
            "issue_id",
            "issue_ref",
            "issue_input",
            "slug",
            "branch_prefix",
            "branch_name",
            "worktree_dir",
            "design_path",
            "provider_type",
            "branch_prefix_fallback",
            "default_branch",
        ):
            assert field in out
        assert out["branch_prefix"] == "fix"
        assert out["branch_name"] == f"fix/{issue_id}"

    def test_json_field_filter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=["type:bug"])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id, "--json", "branch_prefix,branch_name"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert set(out.keys()) == {"branch_prefix", "branch_name"}
        assert out["branch_prefix"] == "fix"

    def test_jq_raw_value(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=["type:bug"])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id, "--json", "branch_prefix", "-q", ".branch_prefix"])
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "fix"

    def test_unknown_json_field_returns_null_exit_0(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """設計契約: 未知 ``--json`` field は ``null`` + exit 0（``kaji issue view`` と同挙動）。"""
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=["type:bug"])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id, "--json", "branch_prefix,nonexistent"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"branch_prefix": "fix", "nonexistent": None}

    def test_invalid_id_returns_exit_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_github_repo(tmp_path)
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", "Bogus-ID"])
        assert rc == 2


# ============================================================
# Medium: 再現テスト + 全 8 種 type label + fallback + github 経路
# ============================================================


@pytest.mark.medium
class TestContextCLIMedium:
    def test_repro_type_bug_resolves_to_fix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """再現テスト: ``type:bug`` Issue が ``fix`` prefix に解決される。"""
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=["type:bug"])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id, "-q", ".branch_prefix"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "fix"

    @pytest.mark.parametrize(
        "label,expected_prefix",
        [
            ("type:feature", "feat"),
            ("type:bug", "fix"),
            ("type:refactor", "refactor"),
            ("type:docs", "docs"),
            ("type:test", "test"),
            ("type:chore", "chore"),
            ("type:perf", "perf"),
            ("type:security", "security"),
        ],
    )
    def test_all_type_labels_resolve(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        label: str,
        expected_prefix: str,
    ) -> None:
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=[label])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id, "-q", ".branch_prefix"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == expected_prefix

    def test_no_type_label_falls_back_to_chore(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_github_repo(tmp_path)
        issue_id = _stub_issue(monkeypatch, labels=[])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["branch_prefix"] == "chore"
        assert out["branch_prefix_fallback"] is True

    def test_github_provider_error_is_normalized_to_runtime_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """GitHub 経路で ``GitHubProviderError`` (gh 不在 / 非 0 終了 / 不正 JSON) が
        例外漏れせず ``EXIT_RUNTIME_ERROR`` + stderr に正規化される。"""
        from kaji_harness.providers.github import GitHubProviderError

        repo = tmp_path / "repo"
        (repo / ".kaji").mkdir(parents=True)
        (repo / ".kaji" / "config.toml").write_text(
            '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
            "[execution]\ndefault_timeout = 1800\n\n"
            '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'
        )
        monkeypatch.chdir(repo)
        with patch(
            "kaji_harness.providers.GitHubProvider.resolve_issue_context",
            side_effect=GitHubProviderError("boom"),
        ):
            rc = _handle_issue(["context", "123"])
        assert rc == 3  # EXIT_RUNTIME_ERROR
        captured = capsys.readouterr()
        assert "boom" in captured.err

    def test_github_provider_resolves_via_provider_method_not_passthrough(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``provider.type='github'`` でも ``kaji issue context`` は ``gh`` に
        passthrough せず ``provider.resolve_issue_context()`` を呼ぶ。"""
        repo = tmp_path / "repo"
        (repo / ".kaji").mkdir(parents=True)
        (repo / ".kaji" / "config.toml").write_text(
            '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
            "[execution]\ndefault_timeout = 1800\n\n"
            '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'
        )
        monkeypatch.chdir(repo)
        stub = IssueContext(
            issue_id="153",
            issue_ref="#153",
            issue_input="153",
            slug="some-slug",
            branch_prefix="fix",
            branch_name="fix/153",
            worktree_dir="/tmp/kaji-fix-153",
            design_path="draft/design/issue-153-some-slug.md",
            provider_type="github",
            branch_prefix_fallback=False,
            default_branch="main",
        )
        with (
            patch(
                "kaji_harness.providers.GitHubProvider.resolve_issue_context",
                return_value=stub,
            ),
            patch("subprocess.run") as mock_run,
        ):
            rc = _handle_issue(["context", "153"])
        assert rc == 0
        mock_run.assert_not_called()  # gh は呼ばれない
        out = json.loads(capsys.readouterr().out)
        assert out["branch_prefix"] == "fix"
        assert out["provider_type"] == "github"
