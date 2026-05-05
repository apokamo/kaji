"""Tests for LocalProvider — Issue CRUD + frontmatter + IssueContext.

phase3-design.md § Medium / LocalProvider CRUD 全経路 / atomic / cache reader。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaji_harness.providers.local import (
    IssueNotFoundError,
    LocalProvider,
    LocalProviderError,
    _atomic_write,
    _parse_frontmatter,
    _serialize_frontmatter,
    validate_machine_id,
)

pytestmark = pytest.mark.medium


@pytest.fixture
def provider(tmp_path: Path) -> LocalProvider:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".kaji").mkdir()
    return LocalProvider(repo_root=repo, machine_id="pc1")


class TestMachineIdValidation:
    def test_valid(self) -> None:
        validate_machine_id("pc1")
        validate_machine_id("a" * 16)
        validate_machine_id("0")

    def test_invalid(self) -> None:
        for bad in ["", "PC1", "pc-1", "pc_1", "a" * 17, "host.local"]:
            with pytest.raises(ValueError):
                validate_machine_id(bad)


class TestFrontmatter:
    def test_round_trip_simple(self) -> None:
        meta = {"id": "local-pc1-1", "title": "hello", "state": "open"}
        body = "# body\n\ncontent\n"
        text = f"---\n{_serialize_frontmatter(meta)}---\n{body}"
        parsed_meta, parsed_body = _parse_frontmatter(text)
        assert parsed_meta["id"] == "local-pc1-1"
        assert parsed_meta["title"] == "hello"
        assert parsed_body == body

    def test_list_labels(self) -> None:
        meta = {"labels": ["type:feature", "priority:high"]}
        text = f"---\n{_serialize_frontmatter(meta)}---\nbody\n"
        parsed_meta, _ = _parse_frontmatter(text)
        assert parsed_meta["labels"] == ["type:feature", "priority:high"]

    def test_empty_list(self) -> None:
        meta = {"labels": []}
        text = f"---\n{_serialize_frontmatter(meta)}---\nbody\n"
        parsed_meta, _ = _parse_frontmatter(text)
        assert parsed_meta["labels"] == []

    def test_missing_frontmatter(self) -> None:
        meta, body = _parse_frontmatter("just body\n")
        assert meta == {}
        assert body == "just body\n"


class TestAtomicWrite:
    def test_writes_and_no_tmp_left(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "file.md"
        _atomic_write(target, "hello")
        assert target.read_text() == "hello"
        assert not target.with_suffix(".md.tmp").exists()


class TestCRUD:
    def test_create_and_view(self, provider: LocalProvider) -> None:
        issue = provider.create_issue(
            title="add foo",
            body="details",
            labels=["type:feature"],
            slug="foo",
        )
        assert issue.id == "local-pc1-1"
        assert issue.title == "add foo"
        assert issue.state == "open"
        assert issue.slug == "foo"
        assert [label.name for label in issue.labels] == ["type:feature"]

        view = provider.view_issue("local-pc1-1")
        assert view.title == "add foo"
        assert view.body.startswith("details")

    def test_create_requires_slug(self, provider: LocalProvider) -> None:
        with pytest.raises(ValueError, match="requires explicit 'slug'"):
            provider.create_issue(title="x", body="y")

    def test_create_validates_slug(self, provider: LocalProvider) -> None:
        with pytest.raises(ValueError, match="invalid slug"):
            provider.create_issue(title="x", body="y", slug="Bad Slug")

    def test_id_increments(self, provider: LocalProvider) -> None:
        a = provider.create_issue(title="a", body="", slug="aaa")
        b = provider.create_issue(title="b", body="", slug="bbb")
        assert a.id == "local-pc1-1"
        assert b.id == "local-pc1-2"

    def test_id_respects_existing_dir_max(self, provider: LocalProvider) -> None:
        # 既存 dir が n=5 まで存在すると counter を超えて 6 を採番
        (provider.repo_root / ".kaji" / "issues" / "local-pc1-5-old").mkdir(parents=True)
        (provider.repo_root / ".kaji" / "issues" / "local-pc1-5-old" / "issue.md").write_text(
            "---\nid: local-pc1-5\ntitle: old\nstate: open\nslug: old\n---\nbody\n"
        )
        new = provider.create_issue(title="new", body="", slug="new")
        assert new.id == "local-pc1-6"

    def test_edit_title_and_body(self, provider: LocalProvider) -> None:
        provider.create_issue(title="orig", body="b1", slug="x")
        edited = provider.edit_issue("local-pc1-1", title="new", body="b2")
        assert edited.title == "new"
        assert edited.body == "b2"

    def test_edit_labels_add_remove(self, provider: LocalProvider) -> None:
        provider.create_issue(title="t", body="b", slug="x", labels=["a", "b"])
        edited = provider.edit_issue("local-pc1-1", add_labels=["c"], remove_labels=["a"])
        names = [label.name for label in edited.labels]
        assert names == ["b", "c"]

    def test_comment_seq(self, provider: LocalProvider) -> None:
        provider.create_issue(title="t", body="b", slug="x")
        c1 = provider.comment_issue("local-pc1-1", "first")
        c2 = provider.comment_issue("local-pc1-1", "second")
        assert c1.seq == "0001"
        assert c2.seq == "0002"
        assert c1.machine_id == "pc1"
        view = provider.view_issue("local-pc1-1")
        assert [c.body.rstrip() for c in view.comments] == ["first", "second"]

    def test_close(self, provider: LocalProvider) -> None:
        provider.create_issue(title="t", body="b", slug="x")
        closed = provider.close_issue("local-pc1-1")
        assert closed.state == "closed"

    def test_list_filters_state_and_labels(self, provider: LocalProvider) -> None:
        provider.create_issue(title="a", body="", slug="a", labels=["type:feature"])
        provider.create_issue(title="b", body="", slug="b", labels=["type:bug"])
        provider.close_issue("local-pc1-2")

        opened = provider.list_issues(state="open")
        assert [i.id for i in opened] == ["local-pc1-1"]

        all_ = provider.list_issues(state="all")
        assert {i.id for i in all_} == {"local-pc1-1", "local-pc1-2"}

        feat = provider.list_issues(state="all", labels=["type:feature"])
        assert [i.id for i in feat] == ["local-pc1-1"]

    def test_view_missing_issue_raises(self, provider: LocalProvider) -> None:
        with pytest.raises(IssueNotFoundError):
            provider.view_issue("local-pc1-99")


class TestResolveIssueDir:
    def test_duplicate_dirs_error(self, provider: LocalProvider) -> None:
        base = provider.repo_root / ".kaji" / "issues"
        base.mkdir(parents=True, exist_ok=True)
        (base / "local-pc1-1-aaa").mkdir()
        (base / "local-pc1-1-bbb").mkdir()
        with pytest.raises(LocalProviderError, match="multiple issue directories"):
            provider._resolve_issue_dir("local-pc1-1")

    def test_invalid_id(self, provider: LocalProvider) -> None:
        with pytest.raises(ValueError, match="not a local issue id"):
            provider._resolve_issue_dir("153")


class TestIssueContext:
    def test_from_frontmatter(self, provider: LocalProvider) -> None:
        provider.create_issue(
            title="add x",
            body="b",
            slug="add-x",
            labels=["type:feature"],
        )
        ctx = provider.resolve_issue_context("local-pc1-1")
        assert ctx.issue_id == "local-pc1-1"
        assert ctx.issue_ref == "local-pc1-1"
        assert ctx.issue_input == "local-pc1-1"
        assert ctx.slug == "add-x"
        assert ctx.branch_prefix == "feat"
        assert ctx.branch_prefix_fallback is False
        assert ctx.branch_name == "feat/local-pc1-1"
        assert ctx.worktree_dir.endswith("/kaji-feat-local-pc1-1")
        assert ctx.design_path == "draft/design/issue-local-pc1-1-add-x.md"
        assert ctx.provider_type == "local"

    def test_fallback_to_chore_when_no_type_label(self, provider: LocalProvider) -> None:
        provider.create_issue(title="x", body="b", slug="x", labels=["priority:high"])
        ctx = provider.resolve_issue_context("local-pc1-1")
        assert ctx.branch_prefix == "chore"
        assert ctx.branch_prefix_fallback is True

    def test_missing_slug_errors(self, provider: LocalProvider) -> None:
        # frontmatter に slug が無い古い形式の Issue を直接配置
        d = provider.repo_root / ".kaji" / "issues" / "local-pc1-9"
        d.mkdir(parents=True)
        (d / "issue.md").write_text("---\nid: local-pc1-9\ntitle: legacy\nstate: open\n---\nbody\n")
        with pytest.raises(LocalProviderError, match="has no 'slug'"):
            provider.resolve_issue_context("local-pc1-9")


class TestRemoteCacheReader:
    def test_view_cached_issue(self, provider: LocalProvider) -> None:
        cache_dir = provider.repo_root / ".kaji" / "cache" / "issues"
        cache_dir.mkdir(parents=True)
        (cache_dir / "153.json").write_text(
            json.dumps(
                {
                    "number": 153,
                    "title": "GitHub issue",
                    "body": "remote body",
                    "state": "OPEN",
                    "labels": [{"name": "type:feature"}],
                    "comments": [
                        {
                            "author": {"login": "alice"},
                            "body": "hi",
                            "createdAt": "2025-01-01T00:00:00Z",
                        }
                    ],
                }
            )
        )
        issue = provider.view_cached_issue("153")
        assert issue.id == "153"
        assert issue.title == "GitHub issue"
        assert issue.state == "open"
        assert issue.labels[0].name == "type:feature"
        assert issue.comments[0].author == "alice"

    def test_view_cached_issue_missing(self, provider: LocalProvider) -> None:
        with pytest.raises(IssueNotFoundError, match="no cached issue"):
            provider.view_cached_issue("999")

    def test_is_readonly_id_only_for_remote_cache(self, provider: LocalProvider) -> None:
        assert provider.is_readonly_id("remote_cache") is True
        assert provider.is_readonly_id("local") is False
        assert provider.is_readonly_id("github") is False

    def test_is_readonly_provider_flag_false(self, provider: LocalProvider) -> None:
        # provider 全体は read-write。経路ごとの read-only は別判定。
        assert provider.is_readonly is False
