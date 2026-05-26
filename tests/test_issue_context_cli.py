"""Tests for ``kaji issue context`` helper CLI (issue local-pc5090-17).

Bug 再現: ``_LOCAL_ISSUE_SUBS`` に ``context`` がないため
``kaji issue context <id>`` は EXIT_INVALID_INPUT で失敗する。本ファイルが
Red → Green の証跡となる。

設計書: ``draft/design/issue-local-pc5090-17-issue-start-skill-prefix-type-label-bran.md``
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.cli_main import _handle_issue
from kaji_harness.providers import LocalProvider
from kaji_harness.providers.models import IssueContext


def _write_local_repo(tmp_path: Path, *, machine_id: str = "pc1") -> Path:
    """gl:21: ``provider.type='local'`` は git repo を前提とするので git init する。"""
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n'
        f'[provider.local]\nmachine_id = "{machine_id}"\n'
    )
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(repo)],
        check=True,
    )
    return repo


def _create_local_issue(repo: Path, *, machine_id: str, labels: list[str]) -> str:
    provider = LocalProvider(repo_root=repo, machine_id=machine_id)
    issue = provider.create_issue(
        title="Hello",
        body="body text",
        labels=labels,
        slug="hello-test",
    )
    return issue.id


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
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=["type:bug"])
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
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=["type:bug"])
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
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=["type:bug"])
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
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=["type:bug"])
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
        repo = _write_local_repo(tmp_path)
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", "Bogus-ID"])
        assert rc == 2


# ============================================================
# Medium: 再現テスト + 全 8 種 type label + frontmatter override + fallback + github 経路
# ============================================================


@pytest.mark.medium
class TestContextCLIMedium:
    def test_repro_type_bug_resolves_to_fix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """再現テスト: ``type:bug`` Issue が ``fix`` prefix に解決される。

        本テストは Red Phase では ``_LOCAL_ISSUE_SUBS`` に ``context`` が
        ないため ``EXIT_INVALID_INPUT`` で失敗。Green Phase で PASS する。
        """
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=["type:bug"])
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
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=[label])
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
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=[])
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["branch_prefix"] == "chore"
        assert out["branch_prefix_fallback"] is True

    def test_frontmatter_override_takes_priority_over_label(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter ``branch_prefix: docs`` + label ``type:bug`` → ``docs`` を返す。"""
        repo = _write_local_repo(tmp_path)
        issue_id = _create_local_issue(repo, machine_id="pc1", labels=["type:bug"])
        # Inject frontmatter branch_prefix manually
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        issue_dir = provider._resolve_issue_dir(issue_id)
        issue_path = issue_dir / "issue.md"
        text = issue_path.read_text(encoding="utf-8")
        # Insert branch_prefix line into frontmatter block (after first ``---``)
        lines = text.splitlines()
        assert lines[0] == "---"
        # find closing ---
        end = lines.index("---", 1)
        new_lines = lines[:end] + ["branch_prefix: docs"] + lines[end:]
        issue_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        rc = _handle_issue(["context", issue_id, "-q", ".branch_prefix"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "docs"

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
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            rc = _handle_issue(["context", "153"])
        assert rc == 0
        mock_run.assert_not_called()  # gh は呼ばれない
        out = json.loads(capsys.readouterr().out)
        assert out["branch_prefix"] == "fix"
        assert out["provider_type"] == "github"
