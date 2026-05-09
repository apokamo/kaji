"""Tests for GitLabProvider — subprocess mock pass-through.

Issue ``local-pc5090-5`` の Small / Medium テスト。``glab`` CLI を実呼びせず
``subprocess.run`` を mock してロジック（payload parse、IssueContext 解決、
引数組み立て、URL encode、state 正規化、system note 除外）のみ検証する。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    GitLabProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.errors import ConfigLoadError
from kaji_harness.providers import GitLabProvider, get_provider
from kaji_harness.providers.gitlab import GitLabProviderError


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)


@pytest.fixture
def provider(tmp_path: Path) -> GitLabProvider:
    return GitLabProvider(repo="group/project", repo_root=tmp_path / "main")


# ---------------------------------------------------------------------------
# config / dispatcher 系（Small）
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestGitLabProviderConfigDefaults:
    def test_default_values(self) -> None:
        cfg = GitLabProviderConfig()
        assert cfg.repo == ""
        assert cfg.default_branch == "main"

    def test_explicit_values(self) -> None:
        cfg = GitLabProviderConfig(repo="g/p", default_branch="trunk")
        assert cfg.repo == "g/p"
        assert cfg.default_branch == "trunk"


@pytest.mark.small
class TestParseProviderGitLab:
    def _write(self, tmp_path: Path, body: str) -> Path:
        (tmp_path / ".kaji").mkdir()
        (tmp_path / ".kaji" / "config.toml").write_text(
            dedent(
                """
                [paths]
                artifacts_dir = ".kaji-artifacts"
                skill_dir = ".claude/skills"

                [execution]
                default_timeout = 1800
                """
            ).strip()
            + "\n"
            + body
        )
        return tmp_path

    def test_accepts_gitlab_provider(self, tmp_path: Path) -> None:
        repo = self._write(
            tmp_path,
            '\n[provider]\ntype = "gitlab"\n\n[provider.gitlab]\nrepo = "g/p"\n',
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.type == "gitlab"
        assert cfg.provider.gitlab.repo == "g/p"
        assert cfg.provider.gitlab.default_branch == "main"

    def test_accepts_gitlab_default_branch(self, tmp_path: Path) -> None:
        repo = self._write(
            tmp_path,
            '\n[provider]\ntype = "gitlab"\n\n[provider.gitlab]\n'
            'repo = "g/p"\ndefault_branch = "trunk"\n',
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.gitlab.default_branch == "trunk"

    def test_rejects_unknown_provider_type(self, tmp_path: Path) -> None:
        repo = self._write(tmp_path, '\n[provider]\ntype = "bitbucket"\n')
        with pytest.raises(ConfigLoadError, match="provider.type"):
            KajiConfig.discover(start_dir=repo)

    def test_error_message_lists_gitlab(self, tmp_path: Path) -> None:
        repo = self._write(tmp_path, '\n[provider]\ntype = "foo"\n')
        with pytest.raises(ConfigLoadError) as ei:
            KajiConfig.discover(start_dir=repo)
        assert "gitlab" in str(ei.value)

    def test_overlay_merges_gitlab_subtable(self, tmp_path: Path) -> None:
        repo = tmp_path
        (repo / ".kaji").mkdir()
        (repo / ".kaji" / "config.toml").write_text(
            dedent(
                """
                [paths]
                artifacts_dir = ".kaji-artifacts"
                skill_dir = ".claude/skills"

                [execution]
                default_timeout = 1800

                [provider]
                type = "gitlab"

                [provider.gitlab]
                repo = "g/p"
                """
            ).strip()
            + "\n"
        )
        # overlay only sets default_branch — repo from tracked must survive
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider.gitlab]\ndefault_branch = "develop"\n'
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.gitlab.repo == "g/p"
        assert cfg.provider.gitlab.default_branch == "develop"

    def test_gitlab_repo_must_be_string(self, tmp_path: Path) -> None:
        repo = self._write(
            tmp_path,
            '\n[provider]\ntype = "gitlab"\n\n[provider.gitlab]\nrepo = 42\n',
        )
        with pytest.raises(ConfigLoadError, match="gitlab.repo"):
            KajiConfig.discover(start_dir=repo)


@pytest.mark.small
class TestGetProviderGitLab:
    def _cfg(self, tmp_path: Path, *, repo: str = "g/p", branch: str = "main") -> KajiConfig:
        return KajiConfig(
            repo_root=tmp_path,
            paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
            execution=ExecutionConfig(default_timeout=1800),
            provider=ProviderConfig(
                type="gitlab",
                local=LocalProviderConfig(),
                github=GitHubProviderConfig(),
                gitlab=GitLabProviderConfig(repo=repo, default_branch=branch),
            ),
        )

    def test_returns_gitlab_provider(self, tmp_path: Path) -> None:
        provider = get_provider(self._cfg(tmp_path))
        assert isinstance(provider, GitLabProvider)
        assert provider.repo == "g/p"
        assert provider.default_branch == "main"

    def test_propagates_default_branch(self, tmp_path: Path) -> None:
        provider = get_provider(self._cfg(tmp_path, branch="trunk"))
        assert isinstance(provider, GitLabProvider)
        assert provider.default_branch == "trunk"

    def test_rejects_empty_repo(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="provider.gitlab.repo"):
            get_provider(self._cfg(tmp_path, repo=""))


# ---------------------------------------------------------------------------
# parse / encode pure logic (Small)
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestParseIssuePayload:
    def test_normalizes_state_opened(self) -> None:
        issue = GitLabProvider._parse_issue_payload(
            {"iid": 42, "title": "T", "description": "B", "state": "opened", "labels": []}
        )
        assert issue.id == "42"
        assert issue.body == "B"
        assert issue.state == "open"

    def test_keeps_closed_state(self) -> None:
        issue = GitLabProvider._parse_issue_payload(
            {"iid": 7, "title": "T", "description": "", "state": "closed", "labels": []}
        )
        assert issue.state == "closed"

    def test_string_labels(self) -> None:
        issue = GitLabProvider._parse_issue_payload(
            {
                "iid": 1,
                "title": "T",
                "description": "",
                "state": "opened",
                "labels": ["type:feature", "priority:high"],
            }
        )
        assert [label.name for label in issue.labels] == ["type:feature", "priority:high"]

    def test_uses_iid_not_global_id(self) -> None:
        issue = GitLabProvider._parse_issue_payload(
            {
                "id": 99999,
                "iid": 5,
                "title": "T",
                "description": "",
                "state": "opened",
                "labels": [],
            }
        )
        assert issue.id == "5"

    def test_slug_derived_from_title(self) -> None:
        issue = GitLabProvider._parse_issue_payload(
            {
                "iid": 1,
                "title": "Add cool feature",
                "description": "",
                "state": "opened",
                "labels": [],
            }
        )
        assert issue.slug == "add-cool-feature"


@pytest.mark.small
class TestParseCommentsPayload:
    def test_excludes_system_notes(self) -> None:
        payload = [
            {
                "body": "user",
                "system": False,
                "author": {"username": "alice"},
                "created_at": "2025-01-01T00:00:00Z",
            },
            {
                "body": "state change",
                "system": True,
                "author": {"username": "ghost"},
                "created_at": "2025-01-02T00:00:00Z",
            },
            {
                "body": "another",
                "system": False,
                "author": {"username": "bob"},
                "created_at": "2025-01-03T00:00:00Z",
            },
        ]
        comments = GitLabProvider._parse_comments_payload(payload)
        assert len(comments) == 2
        assert [c.author for c in comments] == ["alice", "bob"]
        assert [c.body for c in comments] == ["user", "another"]


@pytest.mark.small
class TestEncodedRepo:
    def test_slash_is_encoded(self, provider: GitLabProvider) -> None:
        assert provider._encoded_repo() == "group%2Fproject"

    def test_nested_namespace_encoded(self, tmp_path: Path) -> None:
        p = GitLabProvider(repo="parent/sub/project", repo_root=tmp_path)
        assert p._encoded_repo() == "parent%2Fsub%2Fproject"


# ---------------------------------------------------------------------------
# subprocess-mocked behavior (Small)
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestRunGlab:
    def test_glab_not_installed(self, provider: GitLabProvider) -> None:
        with patch("kaji_harness.providers.gitlab.shutil.which", return_value=None):
            with pytest.raises(GitLabProviderError, match="not found in PATH"):
                provider.view_issue("1")


@pytest.mark.small
class TestViewIssue:
    def test_combines_issue_and_notes(self, provider: GitLabProvider) -> None:
        issue_payload = {
            "iid": 42,
            "title": "Add feature",
            "description": "details",
            "state": "opened",
            "labels": ["type:feature"],
        }
        notes_payload = [
            {
                "body": "comment a",
                "system": False,
                "author": {"username": "alice"},
                "created_at": "2025-01-01T00:00:00Z",
            },
            {"body": "system event", "system": True, "author": {"username": "g"}},
        ]
        outputs = iter(
            [_ok(stdout=json.dumps(issue_payload)), _ok(stdout=json.dumps(notes_payload))]
        )
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            issue = provider.view_issue("42")
        assert issue.id == "42"
        assert issue.title == "Add feature"
        assert issue.state == "open"
        assert [label.name for label in issue.labels] == ["type:feature"]
        # system note は除外される
        assert len(issue.comments) == 1
        assert issue.comments[0].author == "alice"

    def test_uses_encoded_repo_in_endpoint(self, provider: GitLabProvider) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            if "issues/42/notes" in (cmd[-1] if cmd else ""):
                return _ok(stdout="[]")
            return _ok(
                stdout=json.dumps(
                    {"iid": 42, "title": "t", "description": "", "state": "opened", "labels": []}
                )
            )

        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", side_effect=fake_run),
        ):
            provider.view_issue("42")
        # 1st call: issue body
        assert captured[0][0] == "glab"
        assert captured[0][1] == "api"
        assert captured[0][2] == "projects/group%2Fproject/issues/42"
        # 2nd call: notes
        assert captured[1][2].startswith("projects/group%2Fproject/issues/42/notes")

    def test_glab_failure_raises(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", return_value=_fail()),
        ):
            with pytest.raises(GitLabProviderError, match="glab api failed"):
                provider.view_issue("42")

    def test_invalid_json_raises(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout="not json"),
            ),
        ):
            with pytest.raises(GitLabProviderError, match="invalid JSON"):
                provider.view_issue("42")


@pytest.mark.small
class TestCreateIssue:
    def test_extracts_iid_from_url(self, provider: GitLabProvider) -> None:
        view_payload = {
            "iid": 200,
            "title": "new",
            "description": "b",
            "state": "opened",
            "labels": [],
        }
        outputs = iter(
            [
                _ok(stdout="https://gitlab.com/group/project/-/issues/200\n"),
                _ok(stdout=json.dumps(view_payload)),
                _ok(stdout="[]"),  # notes
            ]
        )
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            issue = provider.create_issue(title="new", body="b", labels=["type:feature"])
        assert issue.id == "200"

    def test_passes_repo_and_yes_flag(self, provider: GitLabProvider) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            if cmd[1:3] == ["issue", "create"]:
                return _ok(stdout="https://gitlab.com/group/project/-/issues/9\n")
            return _ok(
                stdout=json.dumps(
                    {"iid": 9, "title": "t", "description": "", "state": "opened", "labels": []}
                )
                if "/notes" not in cmd[-1]
                else "[]"
            )

        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", side_effect=fake_run),
        ):
            provider.create_issue(title="t", body="b", labels=["a", "b"])
        create_cmd = captured[0]
        assert create_cmd[0] == "glab"
        assert create_cmd[1:3] == ["issue", "create"]
        assert "--repo" in create_cmd
        assert create_cmd[create_cmd.index("--repo") + 1] == "group/project"
        # combined label flag
        assert create_cmd[create_cmd.index("--label") + 1] == "a,b"
        assert "--yes" in create_cmd


@pytest.mark.small
class TestCommentIssue:
    def test_returns_minimal_comment(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout="ok"),
            ),
        ):
            comment = provider.comment_issue("42", "hello")
        assert comment.body == "hello"
        assert comment.author == ""
        assert comment.created_at == ""


@pytest.mark.small
class TestListIssues:
    def test_translates_state_open_to_opened(self, provider: GitLabProvider) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return _ok(stdout="[]")

        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", side_effect=fake_run),
        ):
            provider.list_issues(state="open")
        assert captured[0][2].startswith("projects/group%2Fproject/issues?")
        assert "state=opened" in captured[0][2]

    def test_caps_per_page_at_100(self, provider: GitLabProvider) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return _ok(stdout="[]")

        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", side_effect=fake_run),
        ):
            provider.list_issues(limit=500)
        assert "per_page=100" in captured[0][2]


@pytest.mark.small
class TestListLabels:
    def test_returns_label_objects(self, provider: GitLabProvider) -> None:
        payload = [
            {"name": "type:feature", "description": "F", "color": "00ff00"},
            {"name": "priority:high", "description": "", "color": "ff0000"},
        ]
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout=json.dumps(payload)),
            ),
        ):
            labels = provider.list_labels()
        assert [label.name for label in labels] == ["type:feature", "priority:high"]
        assert labels[0].color == "00ff00"


# ---------------------------------------------------------------------------
# IssueContext (Small)
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestIssueContext:
    def test_resolve_uses_labels(self, provider: GitLabProvider) -> None:
        issue_payload = {
            "iid": 42,
            "title": "Add cool feature",
            "description": "",
            "state": "opened",
            "labels": ["type:feature"],
        }
        outputs = iter([_ok(stdout=json.dumps(issue_payload)), _ok(stdout="[]")])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            ctx = provider.resolve_issue_context("42")
        assert ctx.issue_id == "42"
        assert ctx.issue_ref == "gl:42"
        assert ctx.issue_input == "42"
        assert ctx.branch_prefix == "feat"
        assert ctx.branch_name == "feat/42"
        assert ctx.slug == "add-cool-feature"
        assert ctx.design_path == "draft/design/issue-42-add-cool-feature.md"
        assert ctx.provider_type == "gitlab"
        assert ctx.branch_prefix_fallback is False

    def test_resolve_falls_back_when_no_type_label(self, provider: GitLabProvider) -> None:
        issue_payload = {
            "iid": 7,
            "title": "Stuff",
            "description": "",
            "state": "opened",
            "labels": ["priority:high"],
        }
        outputs = iter([_ok(stdout=json.dumps(issue_payload)), _ok(stdout="[]")])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                side_effect=lambda *a, **kw: next(outputs),
            ),
        ):
            ctx = provider.resolve_issue_context("7")
        assert ctx.branch_prefix == "chore"
        assert ctx.branch_prefix_fallback is True
        assert ctx.provider_type == "gitlab"
