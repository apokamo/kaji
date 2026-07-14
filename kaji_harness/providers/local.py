"""Public facade for the ``.kaji/issues`` local Issue provider."""

from __future__ import annotations

# ``os`` / ``datetime`` / ``atomic_write_new`` はこの facade の namespace 束縛が
# 既存テストの patch target（例: tests/test_preflight.py の
# ``patch.object(local.os, "write", ...)``）なので、facade 内で直接使わなくても再export する。
import os as os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..fsio import atomic_write, atomic_write_new
from . import _local_comments, _local_common
from ._local_cache import GitHubCacheReader, cached_github_issue_from_payload
from ._local_comments import (
    LocalCommentStore,
)
from ._local_common import (
    IssueNotFoundError as IssueNotFoundError,
)
from ._local_common import (
    LocalProviderError as LocalProviderError,
)
from ._local_common import (
    expected_id_from_dirname,
    parse_frontmatter,
    serialize_frontmatter,
    validate_issue_meta,
)
from ._local_common import (
    validate_machine_id as validate_machine_id,
)
from ._local_store import LocalIssueStore, emit_windows_warning, labels_from_meta
from ._mappings import DEFAULT_BRANCH_PREFIX, labels_to_branch_prefix
from .cache_guard import detect_legacy_forge_cache
from .context import (
    build_branch_name,
    build_design_path,
    build_worktree_dir,
    derive_slug_from_title,
    format_issue_ref,
    validate_slug,
)
from .models import Comment, Issue, IssueContext, Label, PRContext

if TYPE_CHECKING:
    from . import ResolvedId

# Preserve the established module import surface while implementation lives in
# cohesive package-private components.
_serialize_frontmatter = serialize_frontmatter
_parse_frontmatter = parse_frontmatter
_validate_issue_meta = validate_issue_meta
_expected_id_from_dirname = expected_id_from_dirname
_cached_github_issue_from_payload = cached_github_issue_from_payload
IssueReadOnlyError = _local_common.IssueReadOnlyError
MAX_COMMENT_WRITE_RETRIES = _local_comments.MAX_COMMENT_WRITE_RETRIES
_COMMENT_FILENAME_RE = _local_comments._COMMENT_FILENAME_RE


@dataclass
class LocalProvider:
    """Use ``.kaji/issues`` as the mutable Issue source of truth."""

    repo_root: Path
    machine_id: str
    default_branch: str = "main"
    git_remote: str = "origin"
    worktree_prefix: str = ""
    _comments: LocalCommentStore = field(init=False, repr=False)
    _store: LocalIssueStore = field(init=False, repr=False)
    _cache: GitHubCacheReader = field(init=False, repr=False)

    def __post_init__(self) -> None:
        validate_machine_id(self.machine_id)
        if sys.platform == "win32":
            emit_windows_warning()
        self._comments = LocalCommentStore(self.repo_root, self.machine_id)
        self._store = LocalIssueStore(self.repo_root, self.machine_id, self._comments)
        self._cache = GitHubCacheReader(self.repo_root)

    @property
    def is_readonly(self) -> bool:
        """Return whether the provider as a whole is read-only."""
        return False

    def _resolve_issue_dir(self, issue_id: str) -> Path:
        """Retain the established private helper used by local integrations."""
        return self._store.resolve_issue_dir(issue_id)

    def commit_issue_change(
        self,
        rid: ResolvedId,
        action: str,
        paths: list[Path],
    ) -> None:
        """Commit selected Issue-relative paths while preserving the user index."""
        issue_dir = self._store.resolve_issue_dir(rid.value)
        repo_paths = [issue_dir / path for path in paths]
        relative_paths = [str(path.relative_to(self.repo_root)) for path in repo_paths]
        message = f"chore(local): {action} for {format_issue_ref(rid.value)}"
        subprocess.run(
            ["git", "add", "--", *relative_paths],
            cwd=self.repo_root,
            check=True,
        )
        diff_check = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", *relative_paths],
            cwd=self.repo_root,
        )
        if diff_check.returncode == 0:
            return
        if diff_check.returncode != 1:
            diff_check.check_returncode()
        subprocess.run(
            ["git", "commit", "--only", "-m", message, "--", *relative_paths],
            cwd=self.repo_root,
            check=True,
        )

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        slug: str | None = None,
    ) -> Issue:
        """Create and return a new local Issue."""
        if slug is None:
            slug = derive_slug_from_title(title)
        validate_slug(slug)
        number = self._store.next_local_id()
        issue_id = f"local-{self.machine_id}-{number}"
        issue_dir = self._store.issues_dir / f"{issue_id}-{slug}"
        if issue_dir.exists():
            raise LocalProviderError(
                f"issue directory already exists: {issue_dir}. "
                f"This indicates a counter / glob inconsistency."
            )
        issue_dir.mkdir(parents=True)
        meta: dict[str, object] = {
            "id": issue_id,
            "title": title,
            "state": "open",
            "slug": slug,
            "labels": list(labels or []),
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        atomic_write(issue_dir / "issue.md", self._store.build_issue_md(meta, body))
        return self._store.read_issue(issue_dir)

    def view_issue(self, issue_id: str) -> Issue:
        """Return one mutable local Issue."""
        return self._store.read_issue(self._store.resolve_issue_dir(issue_id))

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        """Edit title, body, or labels without changing the local Issue identity."""
        issue_dir = self._store.resolve_issue_dir(issue_id)
        issue_path = issue_dir / "issue.md"
        meta, current_body = parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        validate_issue_meta(
            meta,
            strict_slug=True,
            expected_id=expected_id_from_dirname(issue_dir.name),
        )
        if title is not None:
            meta["title"] = title
        if add_labels or remove_labels:
            current = [label.name for label in labels_from_meta(meta.get("labels"))]
            updated = [label for label in current if label not in (remove_labels or [])]
            for label in add_labels or []:
                if label not in updated:
                    updated.append(label)
            meta["labels"] = updated
        new_body = body if body is not None else current_body
        atomic_write(issue_path, self._store.build_issue_md(meta, new_body))
        return self._store.read_issue(issue_dir)

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        """Atomically append a comment to a local Issue."""
        return self._comments.comment(
            self._store.resolve_issue_dir(issue_id),
            body,
            now=datetime.now(UTC),
            writer=atomic_write_new,
        )

    def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
        """Close a local Issue and persist its close metadata."""
        issue_dir = self._store.resolve_issue_dir(issue_id)
        issue_path = issue_dir / "issue.md"
        meta, current_body = parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        validate_issue_meta(
            meta,
            strict_slug=True,
            expected_id=expected_id_from_dirname(issue_dir.name),
        )
        meta["state"] = "closed"
        meta["closed_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["closed_by"] = self.machine_id
        meta["close_reason"] = reason if reason else "completed"
        atomic_write(issue_path, self._store.build_issue_md(meta, current_body))
        return self._store.read_issue(issue_dir)

    def list_issues(
        self,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Issue]:
        """List local Issues followed by read-only cached GitHub Issues."""
        detect_legacy_forge_cache(self._cache.cache_dir)
        issues: list[Issue] = []
        if self._store.issues_dir.exists():
            for entry in sorted(self._store.issues_dir.iterdir()):
                if not entry.is_dir() or not entry.name.startswith("local-"):
                    continue
                try:
                    issue = self._store.read_issue(entry)
                except IssueNotFoundError:
                    continue
                if state != "all" and issue.state != state:
                    continue
                label_names = {label.name for label in issue.labels}
                if labels and not all(label in label_names for label in labels):
                    continue
                issues.append(issue)
                if limit is not None and len(issues) >= limit:
                    return issues
        issues.extend(self._cache.list(state, labels))
        return issues[:limit] if limit is not None else issues

    def list_labels(self) -> list[Label]:
        """Return the union of labels observed on local and cached Issues."""
        seen: dict[str, Label] = {}
        for issue in self.list_issues(state="all"):
            for label in issue.labels:
                seen.setdefault(label.name, label)
        return list(seen.values())

    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        """Resolve workflow paths and branch metadata for a local Issue."""
        issue_dir = self._store.resolve_issue_dir(issue_id)
        meta, _ = parse_frontmatter((issue_dir / "issue.md").read_text(encoding="utf-8"))
        validate_issue_meta(
            meta,
            strict_slug=True,
            expected_id=expected_id_from_dirname(issue_dir.name),
        )
        slug = str(meta["slug"])
        prefix_value = meta.get("branch_prefix")
        fallback = False
        if isinstance(prefix_value, str) and prefix_value:
            prefix = prefix_value
        else:
            prefix, fallback = labels_to_branch_prefix(
                [label.name for label in labels_from_meta(meta.get("labels"))]
            )
            if fallback:
                prefix = DEFAULT_BRANCH_PREFIX
        return IssueContext(
            issue_id=issue_id,
            issue_ref=format_issue_ref(issue_id),
            issue_input=issue_id,
            slug=slug,
            branch_prefix=prefix,
            branch_name=build_branch_name(prefix, issue_id),
            worktree_dir=build_worktree_dir(
                prefix,
                issue_id,
                self.repo_root,
                self.worktree_prefix,
            ),
            design_path=build_design_path(issue_id, slug),
            provider_type="local",
            branch_prefix_fallback=fallback,
            default_branch=self.default_branch,
            git_remote=self.git_remote,
        )

    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        """Return ``None`` because local mode has no pull request concept."""
        del branch_name
        return None

    def view_cached_issue(self, number: str) -> Issue:
        """Return one read-only cached GitHub Issue."""
        return self._cache.view(number)

    def is_readonly_id(self, resolved_kind: str) -> bool:
        """Return whether an identifier resolves to the remote cache."""
        return resolved_kind == "remote_cache"
