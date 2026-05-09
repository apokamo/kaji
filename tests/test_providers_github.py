"""Tests for GitHubProvider — subprocess mock pass-through.

phase3-design.md § Small / GitHubProvider の subprocess mock pass-through。
buildout 中は実 gh を呼べないため、`subprocess.run` を mock してロジック
（payload parse、IssueContext 解決、引数組み立て）のみ検証する。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.providers.github import GitHubProvider, GitHubProviderError

pytestmark = pytest.mark.small


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)


@pytest.fixture
def provider(tmp_path: Path) -> GitHubProvider:
    return GitHubProvider(repo="owner/name", repo_root=tmp_path / "main")


class TestViewIssue:
    def test_parses_payload(self, provider: GitHubProvider) -> None:
        payload = {
            "number": 153,
            "title": "Add feature",
            "body": "details",
            "state": "OPEN",
            "labels": [{"name": "type:feature", "color": "00ff00", "description": ""}],
            "comments": [
                {
                    "author": {"login": "alice"},
                    "body": "hello",
                    "createdAt": "2025-01-01T00:00:00Z",
                }
            ],
        }
        with (
            patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
            patch(
                "kaji_harness.providers.github.subprocess.run",
                return_value=_ok(stdout=json.dumps(payload)),
            ),
        ):
            issue = provider.view_issue("153")
        assert issue.id == "153"
        assert issue.title == "Add feature"
        assert issue.state == "open"
        assert issue.labels[0].name == "type:feature"
        assert issue.comments[0].author == "alice"

    def test_gh_not_installed(self, provider: GitHubProvider) -> None:
        with patch("kaji_harness.providers.github.shutil.which", return_value=None):
            with pytest.raises(GitHubProviderError, match="not found in PATH"):
                provider.view_issue("153")

    def test_gh_failure(self, provider: GitHubProvider) -> None:
        with (
            patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.providers.github.subprocess.run", return_value=_fail()),
        ):
            with pytest.raises(GitHubProviderError, match="gh failed"):
                provider.view_issue("153")


class TestArgConstruction:
    def test_view_passes_repo_and_json(self, provider: GitHubProvider) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return _ok(
                stdout=json.dumps(
                    {
                        "number": 153,
                        "title": "t",
                        "body": "",
                        "state": "open",
                        "labels": [],
                        "comments": [],
                    }
                )
            )

        with (
            patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.providers.github.subprocess.run", side_effect=fake_run),
        ):
            provider.view_issue("153")
        assert captured[0][:5] == ["gh", "issue", "view", "153", "--repo"]
        assert captured[0][5] == "owner/name"
        assert "--json" in captured[0]

    def test_create_returns_view(self, provider: GitHubProvider) -> None:
        view_payload = json.dumps(
            {
                "number": 200,
                "title": "new",
                "body": "b",
                "state": "open",
                "labels": [],
                "comments": [],
            }
        )
        outputs = iter(
            [
                _ok(stdout="https://github.com/owner/name/issues/200\n"),
                _ok(stdout=view_payload),
            ]
        )
        with (
            patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
            patch(
                "kaji_harness.providers.github.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            issue = provider.create_issue(title="new", body="b", labels=["type:feature"])
        assert issue.id == "200"


class TestIssueContext:
    def test_resolve_uses_labels(self, provider: GitHubProvider) -> None:
        payload = {
            "number": 153,
            "title": "Add cool feature",
            "body": "",
            "state": "open",
            "labels": [{"name": "type:feature"}],
            "comments": [],
        }
        with (
            patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
            patch(
                "kaji_harness.providers.github.subprocess.run",
                return_value=_ok(stdout=json.dumps(payload)),
            ),
        ):
            ctx = provider.resolve_issue_context("153")
        assert ctx.issue_id == "153"
        assert ctx.issue_ref == "#153"
        assert ctx.issue_input == "153"
        assert ctx.branch_prefix == "feat"
        assert ctx.branch_name == "feat/153"
        assert ctx.slug == "add-cool-feature"
        assert ctx.design_path == "draft/design/issue-153-add-cool-feature.md"
        assert ctx.provider_type == "github"
        assert ctx.branch_prefix_fallback is False

    def test_resolve_falls_back_when_no_type_label(self, provider: GitHubProvider) -> None:
        payload = {
            "number": 153,
            "title": "Stuff",
            "body": "",
            "state": "open",
            "labels": [{"name": "priority:high"}],
            "comments": [],
        }
        with (
            patch("kaji_harness.providers.github.shutil.which", return_value="/usr/bin/gh"),
            patch(
                "kaji_harness.providers.github.subprocess.run",
                return_value=_ok(stdout=json.dumps(payload)),
            ),
        ):
            ctx = provider.resolve_issue_context("153")
        assert ctx.branch_prefix == "chore"
        assert ctx.branch_prefix_fallback is True


class TestResolvePrContext:
    """Issue local-pc5090-7: GitHub side is a no-op until forge is selected."""

    def test_returns_none_for_any_branch(self, provider: GitHubProvider) -> None:
        assert provider.resolve_pr_context("feat/153") is None
        assert provider.resolve_pr_context("does-not-matter") is None
