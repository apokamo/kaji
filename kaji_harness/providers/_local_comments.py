"""Comment persistence for the local provider."""

from __future__ import annotations

import errno
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ._local_common import (
    IssueNotFoundError,
    LocalProviderError,
    build_issue_md,
    expected_id_from_dirname,
    parse_frontmatter,
    validate_issue_meta,
)
from .models import Comment

_COMMENT_FILENAME_RE = re.compile(r"^(?P<ts>\d{8}T\d{6}Z)-(?P<machine>[a-z0-9]{1,16})$")
MAX_COMMENT_WRITE_RETRIES: int = 8


@dataclass(frozen=True)
class LocalCommentStore:
    """Read and atomically append comments below a local Issue directory."""

    repo_root: Path
    machine_id: str

    def read_comments(self, issue_dir: Path) -> list[Comment]:
        """Read comments ordered by persisted ``created_at`` and sequence."""
        comment_dir = issue_dir / "comments"
        if not comment_dir.is_dir():
            return []
        result: list[Comment] = []
        for path in comment_dir.iterdir():
            if path.suffix != ".md":
                continue
            match = _COMMENT_FILENAME_RE.match(path.stem)
            if match is None:
                raise LocalProviderError(
                    f"unrecognized comment filename: {path}. "
                    f"Expected '<YYYYMMDDTHHMMSSZ>-<machine>.md'."
                )
            ts, machine = match["ts"], match["machine"]
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            created_at = str(meta.get("created_at", "") or "")
            if not created_at:
                raise LocalProviderError(
                    f"missing 'created_at' in {path}; ordering source must exist."
                )
            result.append(
                Comment(
                    author=str(meta.get("author", "") or ""),
                    body=body,
                    created_at=created_at,
                    seq=ts,
                    machine_id=machine,
                )
            )
        result.sort(key=lambda comment: (comment.created_at, comment.seq))
        return result

    def comment(
        self,
        issue_dir: Path,
        body: str,
        *,
        now: datetime,
        writer: Callable[[Path, str], None],
    ) -> Comment:
        """Validate an Issue and atomically append one comment."""
        issue_path = issue_dir / "issue.md"
        if not issue_path.is_file():
            raise IssueNotFoundError(f"missing issue.md in {issue_dir}")
        meta, _ = parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        validate_issue_meta(
            meta,
            strict_slug=False,
            expected_id=expected_id_from_dirname(issue_dir.name),
        )
        comment_dir = issue_dir / "comments"
        comment_dir.mkdir(exist_ok=True)
        created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        content = build_issue_md(
            {"author": self.machine_id, "created_at": created_at},
            body,
        )
        last_attempted = ""
        for attempt in range(MAX_COMMENT_WRITE_RETRIES):
            ts = (now + timedelta(seconds=attempt)).strftime("%Y%m%dT%H%M%SZ")
            last_attempted = ts
            path = comment_dir / f"{ts}-{self.machine_id}.md"
            try:
                writer(path, content)
            except FileExistsError:
                continue
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    continue
                raise
            return Comment(
                author=self.machine_id,
                body=body,
                created_at=created_at,
                seq=ts,
                machine_id=self.machine_id,
                ref=self.comment_ref(path),
            )
        raise LocalProviderError(
            f"failed to allocate unique comment filename in {comment_dir} after "
            f"{MAX_COMMENT_WRITE_RETRIES} retries (last attempted ts={last_attempted!r}). "
            f"Another process may be writing comments concurrently; retry later "
            f"or inspect the directory."
        )

    def comment_ref(self, path: Path) -> str:
        """Return a repository-relative comment reference when possible."""
        try:
            return path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return path.as_posix()
