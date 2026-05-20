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

    def test_round_trip_title_with_double_quotes(self) -> None:
        """``"`` を含む title が round-trip で破損しない（Finding 1）。"""
        meta = {"title": 'Add "foo" support'}
        text = f"---\n{_serialize_frontmatter(meta)}---\nbody\n"
        parsed_meta, _ = _parse_frontmatter(text)
        assert parsed_meta["title"] == 'Add "foo" support'

    def test_round_trip_value_with_colon_and_quotes(self) -> None:
        meta = {"title": 'A: "tricky" value'}
        text = f"---\n{_serialize_frontmatter(meta)}---\nbody\n"
        parsed_meta, _ = _parse_frontmatter(text)
        assert parsed_meta["title"] == 'A: "tricky" value'


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

    def test_create_without_slug_derives_from_title(self, provider: LocalProvider) -> None:
        """Phase 3-d preflight § 4: ``slug`` 未指定なら title から導出する。"""
        issue = provider.create_issue(title="Hello World", body="y")
        assert issue.slug == "hello-world"
        # directory も <id>-<derived-slug> 形式で作られる
        issue_dir = provider._resolve_issue_dir("local-pc1-1")
        assert issue_dir.name == "local-pc1-1-hello-world"

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

    def test_comment_filename_is_timestamp(self, provider: LocalProvider) -> None:
        """Issue local-pc5090-21: comment filename は <YYYYMMDDTHHMMSSZ>-<machine>.md。"""
        import re as _re

        provider.create_issue(title="t", body="b", slug="x")
        c1 = provider.comment_issue("local-pc1-1", "first")
        c2 = provider.comment_issue("local-pc1-1", "second")
        assert _re.match(r"^\d{8}T\d{6}Z$", c1.seq), f"unexpected seq: {c1.seq!r}"
        assert _re.match(r"^\d{8}T\d{6}Z$", c2.seq), f"unexpected seq: {c2.seq!r}"
        assert c1.machine_id == "pc1"
        view = provider.view_issue("local-pc1-1")
        # ordering の正本は frontmatter created_at（書込順 = 時系列順）
        assert [c.body.rstrip() for c in view.comments] == ["first", "second"]

    def test_close(self, provider: LocalProvider) -> None:
        provider.create_issue(title="t", body="b", slug="x")
        closed = provider.close_issue("local-pc1-1")
        assert closed.state == "closed"

    def test_close_persists_reason_and_closed_by(self, provider: LocalProvider) -> None:
        """Finding 3: close 時に reason / closed_by を frontmatter に残す。"""
        provider.create_issue(title="t", body="b", slug="x")
        provider.close_issue("local-pc1-1", reason="merged into main")
        # frontmatter 直接読みで永続化を確認
        from kaji_harness.providers.local import _parse_frontmatter

        issue_dir = provider._resolve_issue_dir("local-pc1-1")
        meta, _ = _parse_frontmatter((issue_dir / "issue.md").read_text())
        assert meta["state"] == "closed"
        assert meta["close_reason"] == "merged into main"
        assert meta["closed_by"] == "pc1"
        assert isinstance(meta.get("closed_at"), str) and meta["closed_at"]

    def test_close_without_reason_defaults_to_completed(self, provider: LocalProvider) -> None:
        """Phase 3-d: --reason 未指定時の default は ``completed``（design.md L985）。"""
        provider.create_issue(title="t", body="b", slug="x")
        provider.close_issue("local-pc1-1")
        from kaji_harness.providers.local import _parse_frontmatter

        issue_dir = provider._resolve_issue_dir("local-pc1-1")
        meta, _ = _parse_frontmatter((issue_dir / "issue.md").read_text())
        assert meta["close_reason"] == "completed"
        assert meta["closed_by"] == "pc1"

    def test_close_with_empty_reason_defaults_to_completed(self, provider: LocalProvider) -> None:
        """空文字 reason も default の ``completed`` にフォールバックさせる。"""
        provider.create_issue(title="t", body="b", slug="x")
        provider.close_issue("local-pc1-1", reason="")
        from kaji_harness.providers.local import _parse_frontmatter

        issue_dir = provider._resolve_issue_dir("local-pc1-1")
        meta, _ = _parse_frontmatter((issue_dir / "issue.md").read_text())
        assert meta["close_reason"] == "completed"

    def test_close_with_not_planned_preserves_value(self, provider: LocalProvider) -> None:
        """明示値 ``not-planned`` は default に上書きされない。"""
        provider.create_issue(title="t", body="b", slug="x")
        provider.close_issue("local-pc1-1", reason="not-planned")
        from kaji_harness.providers.local import _parse_frontmatter

        issue_dir = provider._resolve_issue_dir("local-pc1-1")
        meta, _ = _parse_frontmatter((issue_dir / "issue.md").read_text())
        assert meta["close_reason"] == "not-planned"

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

    def test_counter_is_per_machine(self, tmp_path: Path) -> None:
        """Finding 2: machine_id が違えば counter は独立。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".kaji").mkdir()

        # pc1 が n=3 まで作成
        pc1 = LocalProvider(repo_root=repo, machine_id="pc1")
        for i in range(3):
            pc1.create_issue(title="x", body="", slug=f"slug-{i}")

        # 同じ repo を pc2 で開く（pc1 の commit を pull した状況を模す）
        pc2 = LocalProvider(repo_root=repo, machine_id="pc2")
        first = pc2.create_issue(title="y", body="", slug="y")
        # pc2 の最初の Issue は 1 でなければならない（pc1 の counter に引きずられない）
        assert first.id == "local-pc2-1"

        # counter file は machine ごとに分離
        assert (repo / ".kaji" / "counters" / "pc1.txt").exists()
        assert (repo / ".kaji" / "counters" / "pc2.txt").exists()


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
        cache_dir = provider.repo_root / ".kaji" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "gh-153.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "github",
                    "fetched_at": "2026-05-21T00:00:00Z",
                    "kaji_local": {
                        "is_stale": False,
                        "last_seen_at": "2026-05-21T00:00:00Z",
                        "staled_at": None,
                    },
                    "issue": {
                        "number": 153,
                        "title": "GitHub issue",
                        "body": "remote body",
                        "state": "open",
                        "labels": [{"name": "type:feature"}],
                    },
                }
            )
        )
        issue = provider.view_cached_issue("153")
        assert issue.id == "153"
        assert issue.title == "GitHub issue"
        assert issue.state == "open"
        assert issue.labels[0].name == "type:feature"

    def test_view_cached_issue_missing(self, provider: LocalProvider) -> None:
        with pytest.raises(IssueNotFoundError, match="no cached GitHub issue"):
            provider.view_cached_issue("999")

    def test_view_cached_issue_legacy_layout_not_supported(self, provider: LocalProvider) -> None:
        """旧 ``.kaji/cache/issues/<n>.json`` layout は廃止 (issue gl:34)。"""
        legacy = provider.repo_root / ".kaji" / "cache" / "issues"
        legacy.mkdir(parents=True)
        (legacy / "153.json").write_text('{"number": 153, "title": "old"}')
        with pytest.raises(IssueNotFoundError, match="no cached GitHub issue"):
            provider.view_cached_issue("153")

    def test_view_cached_issue_stale_normalized_to_closed(self, provider: LocalProvider) -> None:
        cache_dir = provider.repo_root / ".kaji" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "gh-200.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "github",
                    "fetched_at": "2026-05-21T00:00:00Z",
                    "kaji_local": {
                        "is_stale": True,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": "2026-05-01T00:00:00Z",
                    },
                    "issue": {"number": 200, "title": "Gone", "state": "open"},
                }
            )
        )
        issue = provider.view_cached_issue("200")
        assert issue.state == "closed"

    def test_is_readonly_id_only_for_remote_cache(self, provider: LocalProvider) -> None:
        assert provider.is_readonly_id("remote_cache") is True
        assert provider.is_readonly_id("local") is False
        assert provider.is_readonly_id("github") is False

    def test_is_readonly_provider_flag_false(self, provider: LocalProvider) -> None:
        # provider 全体は read-write。経路ごとの read-only は別判定。
        assert provider.is_readonly is False


class TestResolvePrContext:
    """Issue local-pc5090-7: LocalProvider has no PR concept; returns None."""

    def test_returns_none_for_any_branch(self, provider: LocalProvider) -> None:
        assert provider.resolve_pr_context("feat/local-pc1-1") is None
        assert provider.resolve_pr_context("does-not-matter") is None


# ============================================================
# Issue local-pc5090-21: comment filename timestamp 化 + ordering 正本
# ============================================================


@pytest.mark.small
class TestCommentFilenameRegex:
    """``_COMMENT_FILENAME_RE`` の正例 / 反例。"""

    def test_matches_new_format(self) -> None:
        from kaji_harness.providers.local import _COMMENT_FILENAME_RE

        m = _COMMENT_FILENAME_RE.match("20260510T142536Z-pc1")
        assert m is not None
        assert m["ts"] == "20260510T142536Z"
        assert m["machine"] == "pc1"

    def test_matches_pc5090_machine(self) -> None:
        from kaji_harness.providers.local import _COMMENT_FILENAME_RE

        m = _COMMENT_FILENAME_RE.match("20260510T142536Z-pc5090")
        assert m is not None and m["machine"] == "pc5090"

    def test_rejects_old_seq_format(self) -> None:
        from kaji_harness.providers.local import _COMMENT_FILENAME_RE

        assert _COMMENT_FILENAME_RE.match("0001-pc1") is None
        assert _COMMENT_FILENAME_RE.match("0042-pc5090") is None

    def test_rejects_short_or_malformed_ts(self) -> None:
        from kaji_harness.providers.local import _COMMENT_FILENAME_RE

        assert _COMMENT_FILENAME_RE.match("2026-05-10T14:25:36Z-pc1") is None
        assert _COMMENT_FILENAME_RE.match("20260510T142536-pc1") is None  # missing Z
        assert _COMMENT_FILENAME_RE.match("20260510142536Z-pc1") is None  # missing T

    def test_rejects_invalid_machine(self) -> None:
        from kaji_harness.providers.local import _COMMENT_FILENAME_RE

        assert _COMMENT_FILENAME_RE.match("20260510T142536Z-PC1") is None  # uppercase
        assert _COMMENT_FILENAME_RE.match("20260510T142536Z-pc-1") is None  # hyphen


@pytest.mark.medium
class TestReadCommentsNewFormat:
    """``_read_comments`` が新形式を解釈し、frontmatter created_at 順で返す。"""

    @pytest.fixture
    def issue_dir(self, provider: LocalProvider) -> Path:
        provider.create_issue(title="t", body="b", slug="x")
        d = provider._resolve_issue_dir("local-pc1-1")
        (d / "comments").mkdir(exist_ok=True)
        return d

    def _write(self, issue_dir: Path, filename: str, created_at: str, body: str) -> None:
        (issue_dir / "comments" / filename).write_text(
            f"---\nauthor: pc1\ncreated_at: {created_at}\n---\n{body}\n"
        )

    def test_reads_pc5090_machine_in_new_format(
        self, provider: LocalProvider, issue_dir: Path
    ) -> None:
        """21 配下の rename 結果（machine 部分が pc5090）も解釈できる。"""
        self._write(issue_dir, "20260510T142536Z-pc5090.md", "2026-05-10T14:25:36Z", "old")
        view = provider.view_issue("local-pc1-1")
        assert len(view.comments) == 1
        assert view.comments[0].machine_id == "pc5090"
        assert view.comments[0].seq == "20260510T142536Z"

    def test_orders_by_frontmatter_created_at(
        self, provider: LocalProvider, issue_dir: Path
    ) -> None:
        """ordering の正本は frontmatter created_at。filename ASCII 順とずらしても OK。"""
        # filename は a (古) < b (新) だが created_at は a (新) > b (古)
        self._write(issue_dir, "20260510T142536Z-pc1.md", "2026-05-10T20:00:00Z", "later-by-fm")
        self._write(issue_dir, "20260510T142537Z-pc1.md", "2026-05-10T10:00:00Z", "earlier-by-fm")
        view = provider.view_issue("local-pc1-1")
        bodies = [c.body.rstrip() for c in view.comments]
        assert bodies == ["earlier-by-fm", "later-by-fm"]

    def test_tiebreaker_is_filename(self, provider: LocalProvider, issue_dir: Path) -> None:
        """同 created_at では filename (= seq) ASCII でタイブレーク。"""
        self._write(issue_dir, "20260510T142537Z-pc1.md", "2026-05-10T10:00:00Z", "second")
        self._write(issue_dir, "20260510T142536Z-pc1.md", "2026-05-10T10:00:00Z", "first")
        view = provider.view_issue("local-pc1-1")
        bodies = [c.body.rstrip() for c in view.comments]
        assert bodies == ["first", "second"]

    def test_old_seq_format_fails_fast(self, provider: LocalProvider, issue_dir: Path) -> None:
        """旧 seq 形式は LocalProviderError で fail-fast（fallback なし）。"""
        (issue_dir / "comments" / "0001-pc1.md").write_text(
            "---\nauthor: pc1\ncreated_at: 2026-05-10T10:00:00Z\n---\nx\n"
        )
        with pytest.raises(LocalProviderError, match="unrecognized comment filename"):
            provider.view_issue("local-pc1-1")

    def test_unknown_filename_fails_fast(self, provider: LocalProvider, issue_dir: Path) -> None:
        (issue_dir / "comments" / "foo-bar.md").write_text(
            "---\nauthor: pc1\ncreated_at: 2026-05-10T10:00:00Z\n---\nx\n"
        )
        with pytest.raises(LocalProviderError, match="unrecognized comment filename"):
            provider.view_issue("local-pc1-1")

    def test_missing_created_at_fails_fast(self, provider: LocalProvider, issue_dir: Path) -> None:
        """ordering の正本（created_at）欠落は fail-fast。"""
        (issue_dir / "comments" / "20260510T142536Z-pc1.md").write_text(
            "---\nauthor: pc1\n---\nx\n"
        )
        with pytest.raises(LocalProviderError, match="missing 'created_at'"):
            provider.view_issue("local-pc1-1")
