"""Issue storage and identifier allocation for the local provider."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ._local_comments import LocalCommentStore
from ._local_common import (
    _LOCAL_ID_RE,
    IssueNotFoundError,
    LocalProviderError,
    build_issue_md,
    expected_id_from_dirname,
    parse_frontmatter,
    validate_issue_meta,
)
from .models import Issue, Label

_SUPPRESS_WIN_WARNING_ENV = "KAJI_SUPPRESS_WIN_WARNING"
_WIN_WARNING_EMITTED = False


def emit_windows_warning() -> None:
    """Warn once when advisory locking is unavailable on native Windows."""
    global _WIN_WARNING_EMITTED
    if _WIN_WARNING_EMITTED:
        return
    if os.environ.get(_SUPPRESS_WIN_WARNING_ENV) == "1":
        _WIN_WARNING_EMITTED = True
        return
    _WIN_WARNING_EMITTED = True
    print(
        "WARNING: kaji local mode is running on Windows without process-level "
        "locking. Windows native is not a supported local-mode environment. "
        "Use WSL for supported local-mode operation.",
        file=sys.stderr,
    )


@contextmanager
def counter_lock(counter_path: Path) -> Iterator[IO[str]]:
    """Take the platform-appropriate advisory lock for a counter file."""
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path.touch(exist_ok=True)
    handle = counter_path.open("r+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            emit_windows_warning()
        else:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                handle.close()
                raise LocalProviderError(
                    "flock unavailable on this filesystem (NFS / FUSE?). "
                    "Set provider.local.machine_id to a unique value per process and retry."
                ) from exc
        yield handle
    finally:
        try:
            handle.close()
        except OSError:
            pass


@dataclass(frozen=True)
class LocalIssueStore:
    """Own the ``.kaji/issues`` and per-machine counter filesystem layout."""

    repo_root: Path
    machine_id: str
    comments: LocalCommentStore

    @property
    def issues_dir(self) -> Path:
        """Return the local Issue directory root."""
        return self.repo_root / ".kaji" / "issues"

    @property
    def counter_path(self) -> Path:
        """Return this machine's independent counter path."""
        return self.repo_root / ".kaji" / "counters" / f"{self.machine_id}.txt"

    def resolve_issue_dir(self, issue_id: str) -> Path:
        """Resolve a canonical local Issue identifier to exactly one directory."""
        if not _LOCAL_ID_RE.match(issue_id):
            raise ValueError(f"not a local issue id: {issue_id!r}")
        if not self.issues_dir.exists():
            raise IssueNotFoundError(f"no .kaji/issues directory under {self.repo_root}")
        candidates = sorted(self.issues_dir.glob(f"{issue_id}-*"))
        if not candidates:
            bare = self.issues_dir / issue_id
            if bare.is_dir():
                return bare
            raise IssueNotFoundError(f"no issue directory for {issue_id!r} under {self.issues_dir}")
        if len(candidates) > 1:
            names = ", ".join(candidate.name for candidate in candidates)
            raise LocalProviderError(
                f"multiple issue directories matched {issue_id!r}: {names}. "
                f"Resolve the duplicate before continuing."
            )
        return candidates[0]

    def next_local_id(self) -> int:
        """Allocate the next per-machine integer under an advisory lock."""
        with counter_lock(self.counter_path) as handle:
            handle.seek(0)
            raw = handle.read().strip()
            counter_n = int(raw) if raw.isdigit() else 0
            number = max(counter_n, self._existing_local_max()) + 1
            handle.seek(0)
            handle.truncate()
            handle.write(str(number))
            handle.flush()
        return number

    def read_issue(self, issue_dir: Path) -> Issue:
        """Read and validate an Issue with its comments."""
        issue_path = issue_dir / "issue.md"
        if not issue_path.is_file():
            raise IssueNotFoundError(f"missing issue.md in {issue_dir}")
        meta, body = parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        validate_issue_meta(
            meta,
            strict_slug=False,
            expected_id=expected_id_from_dirname(issue_dir.name),
        )
        slug_value = meta.get("slug", "")
        return Issue(
            id=str(meta["id"]),
            title=str(meta.get("title", "") or ""),
            body=body,
            state=str(meta.get("state", "open")),
            labels=labels_from_meta(meta.get("labels")),
            comments=self.comments.read_comments(issue_dir),
            slug=str(slug_value or ""),
        )

    @staticmethod
    def build_issue_md(meta: dict[str, object], body: str) -> str:
        """Build the persisted markdown representation."""
        return build_issue_md(meta, body)

    def _existing_local_max(self) -> int:
        """Return the maximum existing number for this machine identifier."""
        if not self.issues_dir.exists():
            return 0
        prefix = f"local-{self.machine_id}-"
        maximum = 0
        for entry in self.issues_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith(prefix):
                continue
            number = entry.name[len(prefix) :].split("-", 1)[0]
            if number.isdigit():
                maximum = max(maximum, int(number))
        return maximum


def labels_from_meta(value: object) -> list[Label]:
    """Convert persisted string or mapping labels to provider models."""
    if not isinstance(value, list):
        return []
    labels: list[Label] = []
    for entry in value:
        if isinstance(entry, str):
            labels.append(Label(name=entry))
        elif isinstance(entry, dict):
            labels.append(
                Label(
                    name=str(entry.get("name", "") or ""),
                    description=str(entry.get("description", "") or ""),
                    color=str(entry.get("color", "") or ""),
                )
            )
    return labels
