"""Shared validation and frontmatter helpers for the local provider."""

from __future__ import annotations

import re

import yaml

from .context import validate_branch_prefix, validate_slug

_MACHINE_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
_LOCAL_ID_RE = re.compile(r"^local-([a-z0-9]{1,16})-([1-9]\d*)$")
_POS_INT_RE = re.compile(r"^[1-9]\d*$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_VALID_ISSUE_STATES: frozenset[str] = frozenset({"open", "closed"})


class LocalProviderError(RuntimeError):
    """LocalProvider-specific error."""


class IssueNotFoundError(LocalProviderError):
    """Raised when a local Issue directory does not exist."""


class IssueReadOnlyError(LocalProviderError):
    """Raised when a remote-cache Issue is mutated in local mode."""


def validate_machine_id(machine_id: str) -> None:
    """Validate the local provider machine identifier."""
    if not isinstance(machine_id, str) or not _MACHINE_ID_RE.match(machine_id):
        raise ValueError(
            f"invalid machine_id {machine_id!r}: must match [a-z0-9]{{1,16}} "
            f"(lowercase alphanumeric, hyphen disallowed, max 16 chars)"
        )


def serialize_frontmatter(meta: dict[str, object]) -> str:
    """Serialize frontmatter through PyYAML while retaining insertion order."""
    return yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    """Split and parse YAML frontmatter from an Issue or comment body."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    fm_text = match.group(1)
    body = match.group(2)
    try:
        loaded = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise LocalProviderError(f"invalid YAML frontmatter: {exc}") from exc
    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        raise LocalProviderError(f"frontmatter must be a YAML mapping, got {type(loaded).__name__}")
    return loaded, body


def build_issue_md(meta: dict[str, object], body: str) -> str:
    """Build the persisted markdown representation of an Issue or comment."""
    return f"---\n{serialize_frontmatter(meta)}---\n{body}"


def expected_id_from_dirname(dirname: str) -> str | None:
    """Extract ``local-<machine>-<n>`` from a local Issue directory name."""
    match = re.match(r"^(local-[a-z0-9]{1,16}-[1-9]\d*)(?:-|$)", dirname)
    return match.group(1) if match else None


def validate_issue_meta(
    meta: dict[str, object],
    *,
    strict_slug: bool,
    expected_id: str | None = None,
) -> None:
    """Validate the persisted local Issue frontmatter contract."""
    issue_id = meta.get("id")
    if not isinstance(issue_id, str) or not _LOCAL_ID_RE.match(issue_id):
        raise LocalProviderError(
            f"frontmatter 'id' must match local-<machine>-<n>, got {issue_id!r}"
        )
    if expected_id is not None and issue_id != expected_id:
        raise LocalProviderError(
            f"frontmatter 'id' {issue_id!r} does not match expected id "
            f"{expected_id!r} derived from issue directory; the directory may "
            f"have been renamed or the frontmatter edited in isolation"
        )
    state = meta.get("state", "open")
    if not isinstance(state, str) or state not in _VALID_ISSUE_STATES:
        raise LocalProviderError(f"frontmatter 'state' must be 'open' or 'closed', got {state!r}")
    labels = meta.get("labels")
    if labels is not None:
        if not isinstance(labels, list):
            raise LocalProviderError(
                f"frontmatter 'labels' must be a list, got {type(labels).__name__}"
            )
        for index, entry in enumerate(labels):
            if not isinstance(entry, (str, dict)):
                raise LocalProviderError(
                    f"frontmatter 'labels[{index}]' must be str or dict, got {type(entry).__name__}"
                )
    slug_value = meta.get("slug")
    if slug_value not in (None, ""):
        if not isinstance(slug_value, str):
            raise LocalProviderError(
                f"frontmatter 'slug' must be a string, got {type(slug_value).__name__}"
            )
        try:
            validate_slug(slug_value)
        except ValueError as exc:
            raise LocalProviderError(f"frontmatter 'slug' invalid: {exc}") from exc
    elif strict_slug:
        raise LocalProviderError(
            f"issue {issue_id!r} has no 'slug' in frontmatter; required for "
            f"context resolution and write operations"
        )
    prefix_value = meta.get("branch_prefix")
    if prefix_value not in (None, ""):
        if not isinstance(prefix_value, str):
            raise LocalProviderError(
                f"frontmatter 'branch_prefix' must be a string, got {type(prefix_value).__name__}"
            )
        try:
            validate_branch_prefix(prefix_value)
        except ValueError as exc:
            raise LocalProviderError(f"frontmatter 'branch_prefix' invalid: {exc}") from exc
