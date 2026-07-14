"""Read-only GitHub Issue cache support for the local provider."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ._local_common import _POS_INT_RE, IssueNotFoundError, LocalProviderError
from .cache_guard import detect_legacy_forge_cache
from .models import Issue, Label


@dataclass(frozen=True)
class GitHubCacheReader:
    """Read the ``.kaji/cache/gh-*.json`` compatibility cache."""

    repo_root: Path

    @property
    def cache_dir(self) -> Path:
        """Return the cache directory root."""
        return self.repo_root / ".kaji" / "cache"

    def view(self, number: str) -> Issue:
        """Read one cached GitHub Issue by positive integer number."""
        detect_legacy_forge_cache(self.cache_dir)
        if not _POS_INT_RE.match(number):
            raise ValueError(
                f"cached issue number must be a positive integer (no leading zero): {number!r}"
            )
        path = self.cache_dir / f"gh-{number}.json"
        if not path.is_file():
            raise IssueNotFoundError(
                f"no cached GitHub issue at {path}. "
                f"Run 'kaji sync from-github' to populate the cache."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalProviderError(f"cache JSON malformed at {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise LocalProviderError(f"cache JSON at {path} must be an object")
        return cached_github_issue_from_payload(payload)

    def list(self, state: str, labels: list[str] | None) -> list[Issue]:
        """List cached GitHub Issues with local state and label filtering."""
        if not self.cache_dir.exists():
            return []
        issues: list[Issue] = []
        for path in sorted(self.cache_dir.glob("gh-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                sys.stderr.write(f"warning: skipping malformed cache entry {path.name}: {exc}\n")
                continue
            if not isinstance(payload, dict):
                sys.stderr.write(
                    f"warning: skipping malformed cache entry {path.name}: not a JSON object\n"
                )
                continue
            issue = _listed_issue_from_payload(payload)
            if issue is None or (state != "all" and issue.state != state):
                continue
            label_names = {label.name for label in issue.labels}
            if labels and not all(label in label_names for label in labels):
                continue
            issues.append(issue)
        return issues


def _normalized_payload(payload: dict[str, object]) -> tuple[dict[object, object], str]:
    """Return the nested Issue payload and normalized display state."""
    issue_payload = payload.get("issue") or {}
    if not isinstance(issue_payload, dict):
        return {}, "closed"
    local_meta = payload.get("kaji_local") or {}
    is_stale = bool(local_meta.get("is_stale", False)) if isinstance(local_meta, dict) else False
    github_state = str(issue_payload.get("state", "") or "").lower()
    state = "open" if not is_stale and github_state == "open" else "closed"
    return issue_payload, state


def _payload_labels(issue_payload: dict[object, object]) -> list[Label]:
    """Normalize REST string or mapping labels."""
    raw_labels = issue_payload.get("labels") or []
    labels: list[Label] = []
    if isinstance(raw_labels, list):
        for entry in raw_labels:
            if isinstance(entry, str):
                labels.append(Label(name=entry))
            elif isinstance(entry, dict):
                labels.append(Label(name=str(entry.get("name", "") or "")))
    return labels


def _listed_issue_from_payload(payload: dict[str, object]) -> Issue | None:
    """Build the list representation, whose identifier carries the ``gh:`` prefix."""
    issue_payload, state = _normalized_payload(payload)
    if not issue_payload:
        return None
    number = issue_payload.get("number")
    return Issue(
        id=f"gh:{number}" if number is not None else "",
        title=str(issue_payload.get("title", "") or ""),
        body=str(issue_payload.get("body", "") or ""),
        state=state,
        labels=[label for label in _payload_labels(issue_payload) if label.name],
        comments=[],
    )


def cached_github_issue_from_payload(payload: dict[str, object]) -> Issue:
    """Build the direct-view representation of a cached GitHub Issue."""
    issue_payload, state = _normalized_payload(payload)
    if not issue_payload:
        return Issue(id="", title="", body="", state="closed", labels=[], comments=[])
    number = issue_payload.get("number")
    return Issue(
        id=str(number) if number is not None else "",
        title=str(issue_payload.get("title", "") or ""),
        body=str(issue_payload.get("body", "") or ""),
        state=state,
        labels=_payload_labels(issue_payload),
        comments=[],
    )
