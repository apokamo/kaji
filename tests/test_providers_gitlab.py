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
from kaji_harness.providers.gitlab import (
    GitLabProviderError,
    _GitLabPrShape,
    build_kaji_review_marker,
)


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
class TestHostnamePinning:
    """全 ``glab`` invocation が ``--hostname gitlab.com`` を default 注入することを保証する。

    EPIC `local-pc5090-4` 確定事項 #3「self-hosted 非対応 / ``gitlab.com`` 内部固定」と
    Issue 本文「current project / login に依存しない」要求の回帰防止。``glab`` の
    host 解決（current git directory / login config）への暗黙依存を切る。
    """

    def _captured_first(
        self, provider: GitLabProvider, callable_name: str, *args: object, **kwargs: object
    ) -> list[list[str]]:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            endpoint = cmd[-1] if cmd else ""
            # array endpoints
            if "/notes" in endpoint or endpoint.endswith("/labels?per_page=100"):
                return _ok(stdout="[]")
            if "/issues?" in endpoint:
                return _ok(stdout="[]")
            if endpoint.startswith("projects/") and "/issues/" in endpoint:
                # single-issue dict
                return _ok(
                    stdout=json.dumps(
                        {
                            "iid": 1,
                            "title": "t",
                            "description": "",
                            "state": "opened",
                            "labels": [],
                        }
                    )
                )
            # mutating subcommand stdout for create — provide URL so iid extraction works
            return _ok(stdout="https://gitlab.com/group/project/-/issues/1\n")

        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", side_effect=fake_run),
        ):
            getattr(provider, callable_name)(*args, **kwargs)
        return captured

    def test_view_issue_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "view_issue", "1")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]

    def test_list_issues_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "list_issues")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]

    def test_list_labels_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "list_labels")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]

    def test_create_issue_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "create_issue", title="t", body="b")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]
        # mutating subcommand follows the global flag
        assert captured[0][3:5] == ["issue", "create"]

    def test_edit_issue_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "edit_issue", "1", title="t")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]
        assert captured[0][3:5] == ["issue", "update"]

    def test_comment_issue_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "comment_issue", "1", "msg")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]
        assert captured[0][3:5] == ["issue", "note"]

    def test_close_issue_pins_hostname(self, provider: GitLabProvider) -> None:
        captured = self._captured_first(provider, "close_issue", "1")
        assert captured[0][:3] == ["glab", "--hostname", "gitlab.com"]
        assert captured[0][3:5] == ["issue", "close"]


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
        # 1st call: issue body — "glab --hostname gitlab.com api <endpoint>"
        assert captured[0][:4] == ["glab", "--hostname", "gitlab.com", "api"]
        assert captured[0][4] == "projects/group%2Fproject/issues/42"
        # 2nd call: notes
        assert captured[1][:4] == ["glab", "--hostname", "gitlab.com", "api"]
        assert captured[1][4].startswith("projects/group%2Fproject/issues/42/notes")

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
            # mutating: "glab --hostname gitlab.com issue create ..."
            if cmd[3:5] == ["issue", "create"]:
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
        assert create_cmd[:5] == ["glab", "--hostname", "gitlab.com", "issue", "create"]
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
        # ``glab --hostname gitlab.com api <endpoint>``
        assert captured[0][:4] == ["glab", "--hostname", "gitlab.com", "api"]
        endpoint = captured[0][4]
        assert endpoint.startswith("projects/group%2Fproject/issues?")
        assert "state=opened" in endpoint

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
        assert "per_page=100" in captured[0][4]


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


# ---------------------------------------------------------------------------
# _GitLabPrShape: pure shape converter (Issue local-pc5090-6)
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestGitLabPrShapeView:
    def test_basic_view_payload(self) -> None:
        payload = {
            "iid": 42,
            "title": "Add feature",
            "description": "body text",
            "state": "opened",
            "source_branch": "feat/x",
            "target_branch": "main",
            "web_url": "https://gitlab.com/g/p/-/merge_requests/42",
            "author": {"username": "alice"},
            "labels": ["bug", "priority:high"],
        }
        out = _GitLabPrShape.to_github(payload)
        assert out == {
            "number": 42,
            "title": "Add feature",
            "body": "body text",
            "state": "OPEN",
            "headRefName": "feat/x",
            "baseRefName": "main",
            "url": "https://gitlab.com/g/p/-/merge_requests/42",
            "author": {"login": "alice"},
            "labels": [{"name": "bug"}, {"name": "priority:high"}],
        }

    def test_state_mapping(self) -> None:
        for gl, gh in [("opened", "OPEN"), ("closed", "CLOSED"), ("merged", "MERGED")]:
            out = _GitLabPrShape.to_github({"iid": 1, "state": gl})
            assert out["state"] == gh

    def test_dict_labels_form(self) -> None:
        payload = {"iid": 1, "labels": [{"name": "bug", "color": "#fff"}]}
        out = _GitLabPrShape.to_github(payload)
        assert out["labels"] == [{"name": "bug"}]

    def test_missing_optional_fields(self) -> None:
        out = _GitLabPrShape.to_github({"iid": 5})
        assert out["number"] == 5
        assert out["title"] == ""
        assert out["body"] == ""
        assert out["author"] == {}
        assert out["labels"] == []


@pytest.mark.small
class TestGitLabPrShapeList:
    def test_list_conversion(self) -> None:
        payload = [
            {"iid": 1, "state": "opened", "title": "A"},
            {"iid": 2, "state": "merged", "title": "B"},
        ]
        out = _GitLabPrShape.to_github_list(payload)
        assert [e["number"] for e in out] == [1, 2]
        assert [e["state"] for e in out] == ["OPEN", "MERGED"]

    def test_skips_non_dict_entries(self) -> None:
        out = _GitLabPrShape.to_github_list([{"iid": 1}, "ignored", None])
        assert len(out) == 1


@pytest.mark.small
class TestGitLabPrShapeReviewComments:
    def test_diff_comment_with_position(self) -> None:
        payload = [
            {
                "id": "abc123",
                "notes": [
                    {
                        "id": 99,
                        "system": False,
                        "body": "looks off",
                        "author": {"username": "bob"},
                        "position": {"new_path": "src/a.py", "new_line": 42},
                    }
                ],
            }
        ]
        out = _GitLabPrShape.to_github_review_comments(payload)
        assert out == [
            {
                "id": "abc123:99",
                "path": "src/a.py",
                "line": 42,
                "body": "looks off",
                "user": {"login": "bob"},
            }
        ]

    def test_skips_system_discussion(self) -> None:
        payload = [
            {
                "id": "sys",
                "notes": [{"id": 1, "system": True, "body": "state changed"}],
            }
        ]
        assert _GitLabPrShape.to_github_review_comments(payload) == []

    def test_skips_empty_notes(self) -> None:
        assert _GitLabPrShape.to_github_review_comments([{"id": "x", "notes": []}]) == []


@pytest.mark.small
class TestKajiReviewMarker:
    def test_build_marker(self) -> None:
        assert build_kaji_review_marker("APPROVED") == "<!-- kaji-review: state=APPROVED -->"
        assert (
            build_kaji_review_marker("CHANGES_REQUESTED")
            == "<!-- kaji-review: state=CHANGES_REQUESTED -->"
        )

    def test_invalid_state_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid review state"):
            build_kaji_review_marker("BOGUS")


@pytest.mark.small
class TestGitLabPrShapeReviews:
    def test_marker_note_approved(self) -> None:
        notes = [
            {
                "id": 1,
                "system": False,
                "body": "<!-- kaji-review: state=APPROVED -->\nLGTM!",
                "author": {"username": "alice"},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        approvals = {"approved_by": []}
        out = _GitLabPrShape.to_github_reviews(notes, approvals)
        assert out == [
            {
                "user": {"login": "alice"},
                "state": "APPROVED",
                "body": "LGTM!",
                "submitted_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_marker_note_changes_requested(self) -> None:
        notes = [
            {
                "id": 2,
                "system": False,
                "body": "<!-- kaji-review: state=CHANGES_REQUESTED -->\nplease fix",
                "author": {"username": "bob"},
                "created_at": "2026-01-02T00:00:00Z",
            }
        ]
        out = _GitLabPrShape.to_github_reviews(notes, {"approved_by": []})
        assert out[0]["state"] == "CHANGES_REQUESTED"
        assert out[0]["body"] == "please fix"

    def test_implicit_approval_from_approvals_api(self) -> None:
        # marker note を持たない approver は state=APPROVED / body="" で補完
        notes: list[object] = []
        approvals = {"approved_by": [{"user": {"username": "carol"}}]}
        out = _GitLabPrShape.to_github_reviews(notes, approvals)
        assert out == [
            {
                "user": {"login": "carol"},
                "state": "APPROVED",
                "body": "",
                "submitted_at": "",
            }
        ]

    def test_marker_note_dedupes_implicit_approval(self) -> None:
        # 同じ user が marker note を持つ場合、approvals 側の補完は抑止
        notes = [
            {
                "id": 1,
                "system": False,
                "body": "<!-- kaji-review: state=APPROVED -->\ndone",
                "author": {"username": "alice"},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        approvals = {"approved_by": [{"user": {"username": "alice"}}]}
        out = _GitLabPrShape.to_github_reviews(notes, approvals)
        assert len(out) == 1
        assert out[0]["body"] == "done"

    def test_invalid_marker_state_ignored(self) -> None:
        notes = [
            {
                "id": 1,
                "system": False,
                "body": "<!-- kaji-review: state=BOGUS -->\nx",
                "author": {"username": "a"},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        assert _GitLabPrShape.to_github_reviews(notes, {"approved_by": []}) == []

    def test_system_note_skipped(self) -> None:
        notes = [
            {
                "id": 1,
                "system": True,
                "body": "<!-- kaji-review: state=APPROVED -->\nshould skip",
                "author": {"username": "a"},
            }
        ]
        assert _GitLabPrShape.to_github_reviews(notes, {"approved_by": []}) == []

    def test_sort_by_submitted_at(self) -> None:
        notes = [
            {
                "id": 1,
                "system": False,
                "body": "<!-- kaji-review: state=APPROVED -->\nB",
                "author": {"username": "u2"},
                "created_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": 2,
                "system": False,
                "body": "<!-- kaji-review: state=APPROVED -->\nA",
                "author": {"username": "u1"},
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        out = _GitLabPrShape.to_github_reviews(notes, {"approved_by": []})
        assert [r["body"] for r in out] == ["A", "B"]


# ---------------------------------------------------------------------------
# GitLabProvider PR helper subprocess tests (Issue local-pc5090-6)
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestGetMrViewPayload:
    def test_returns_dict_payload(self, provider: GitLabProvider) -> None:
        payload = json.dumps({"iid": 1, "state": "opened"})
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout=payload),
            ) as mock_run,
        ):
            out = provider.get_mr_view_payload("1")
        assert out == {"iid": 1, "state": "opened"}
        cmd = mock_run.call_args[0][0]
        # glab api projects/g%2Fp/merge_requests/1
        assert "api" in cmd
        assert any("merge_requests/1" in str(c) for c in cmd)
        # 実 glab CLI に存在しない `mr view --output json` 経路ではない
        assert "view" not in cmd

    def test_non_object_raises(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout="[1,2]"),
            ),
        ):
            with pytest.raises(GitLabProviderError, match="non-object"):
                provider.get_mr_view_payload("1")


@pytest.mark.small
class TestListMrsPayload:
    def test_query_params_url_encoded(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout="[]"),
            ) as mock_run,
        ):
            provider.list_mrs_payload(
                state="opened",
                source_branch="feat/x",
                target_branch="main",
                search="hello world",
                per_page=50,
            )
        cmd = mock_run.call_args[0][0]
        endpoint = next(c for c in cmd if "merge_requests" in str(c))
        assert "state=opened" in endpoint
        # URL encode: feat/x → feat%2Fx, "hello world" → hello%20world
        assert "source_branch=feat%2Fx" in endpoint
        assert "target_branch=main" in endpoint
        assert "search=hello%20world" in endpoint
        assert "per_page=50" in endpoint
        # 実 glab CLI に存在しない `mr list -F json` 経路ではない
        assert "list" not in cmd

    def test_per_page_clamps_to_100(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout="[]"),
            ) as mock_run,
        ):
            provider.list_mrs_payload(per_page=999)
        cmd = mock_run.call_args[0][0]
        endpoint = next(c for c in cmd if "merge_requests" in str(c))
        assert "per_page=100" in endpoint

    def test_non_array_raises(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run",
                return_value=_ok(stdout='{"x":1}'),
            ),
        ):
            with pytest.raises(GitLabProviderError, match="non-array"):
                provider.list_mrs_payload()


@pytest.mark.small
class TestResolveMrIidFromBranch:
    def test_resolves_unique_open_mr(self, provider: GitLabProvider) -> None:
        mr_payload = json.dumps([{"iid": 42, "title": "x"}])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run", return_value=_ok(stdout=mr_payload)
            ),
        ):
            assert provider.resolve_mr_iid_from_branch("feat/x") == "42"

    def test_no_mr_raises(self, provider: GitLabProvider) -> None:
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch("kaji_harness.providers.gitlab.subprocess.run", return_value=_ok(stdout="[]")),
        ):
            with pytest.raises(GitLabProviderError, match="no open merge request"):
                provider.resolve_mr_iid_from_branch("feat/x")

    def test_multiple_mrs_raises(self, provider: GitLabProvider) -> None:
        mr_payload = json.dumps([{"iid": 1}, {"iid": 2}])
        with (
            patch("kaji_harness.providers.gitlab.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "kaji_harness.providers.gitlab.subprocess.run", return_value=_ok(stdout=mr_payload)
            ),
        ):
            with pytest.raises(GitLabProviderError, match="multiple open merge requests"):
                provider.resolve_mr_iid_from_branch("feat/x")
