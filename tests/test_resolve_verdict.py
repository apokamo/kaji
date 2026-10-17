"""Latest verdict marker resolution tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaji_harness.commands.issue import (
    VerdictMarkerMalformedError,
    VerdictMarkerMetaMissingError,
    VerdictMarkerNotFoundError,
    resolve_latest_verdict,
)
from kaji_harness.providers import Comment
from kaji_harness.providers.github import GitHubProvider


def _comment(body: str, created_at: str) -> Comment:
    return Comment(author="tester", body=body, created_at=created_at)


@pytest.mark.small
def test_latest_verdict_invalidates_stale_pass() -> None:
    comments = [
        _comment(
            "<!-- kaji-verdict: step=review-starter-update status=PASS "
            "base=aaa candidate=bbb target=v0.16.0 -->\npass",
            "2026-07-16T00:00:00Z",
        ),
        _comment(
            "<!-- kaji-verdict: step=review-starter-update status=RETRY "
            "base=aaa candidate=bbb target=v0.16.0 -->\nretry",
            "2026-07-16T00:01:00Z",
        ),
    ]

    resolved = resolve_latest_verdict(
        comments,
        step="review-starter-update",
        required_meta=("target", "base", "candidate"),
    )

    assert resolved.status == "RETRY"
    assert resolved.created_at == "2026-07-16T00:01:00Z"


@pytest.mark.small
def test_latest_verdict_missing_is_distinct() -> None:
    with pytest.raises(VerdictMarkerNotFoundError):
        resolve_latest_verdict([], step="review-starter-update")


@pytest.mark.small
def test_latest_verdict_malformed_fails_closed() -> None:
    comments = [
        _comment(
            "<!-- kaji-verdict: step=review-starter-update status=PASS bad -->",
            "2026-07-16T00:00:00Z",
        )
    ]

    with pytest.raises(VerdictMarkerMalformedError):
        resolve_latest_verdict(comments, step="review-starter-update")


@pytest.mark.small
def test_latest_verdict_required_metadata_fails_closed() -> None:
    comments = [
        _comment(
            "<!-- kaji-verdict: step=review-starter-update status=PASS -->",
            "2026-07-16T00:00:00Z",
        )
    ]

    with pytest.raises(VerdictMarkerMetaMissingError, match="candidate"):
        resolve_latest_verdict(
            comments,
            step="review-starter-update",
            required_meta=("candidate",),
        )


class _InMemoryGitHubProvider(GitHubProvider):
    """``comment_issue`` / ``list_issue_comments_all`` を gh 不要の in-memory 実装に置換する。"""

    def __init__(self, repo_root: Path) -> None:
        super().__init__(repo="owner/name", repo_root=repo_root)
        self.posted: list[Comment] = []

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        comment = Comment(
            author="tester",
            body=body,
            created_at=f"2026-07-16T00:{len(self.posted):02d}:00Z",
        )
        self.posted.append(comment)
        return comment

    def list_issue_comments_all(self, issue_id: str) -> list[Comment]:
        return list(self.posted)


@pytest.mark.medium
def test_github_comment_meta_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from kaji_harness.commands.issue import (
        _github_issue_comment_with_verdict,
        _handle_issue_resolve_verdict,
    )

    provider = _InMemoryGitHubProvider(tmp_path)

    rc = _github_issue_comment_with_verdict(
        provider,
        [
            "1",
            "--verdict-step",
            "review-starter-update",
            "--verdict-status",
            "PASS",
            "--verdict-meta",
            "target=v0.16.0",
            "--verdict-meta",
            "base=aaa",
            "--verdict-meta",
            "candidate=bbb",
            "--body",
            "reviewed",
            "--commit",
        ],
    )
    assert rc == 0
    capsys.readouterr()

    rc = _handle_issue_resolve_verdict(
        provider,
        [
            "1",
            "--step",
            "review-starter-update",
            "--require-meta",
            "target",
            "--require-meta",
            "base",
            "--require-meta",
            "candidate",
        ],
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "PASS"
    assert payload["meta"] == {"base": "aaa", "candidate": "bbb", "target": "v0.16.0"}


@pytest.mark.medium
@pytest.mark.parametrize(
    ("body", "required_meta", "expected_code"),
    [
        ("plain comment", (), 4),
        ("<!-- kaji-verdict: step=review-starter-update status=PASS bad -->", (), 5),
        ("<!-- kaji-verdict: step=review-starter-update status=PASS -->", ("candidate",), 6),
    ],
)
def test_resolve_verdict_cli_distinguishes_fail_closed_codes(
    tmp_path: Path,
    body: str,
    required_meta: tuple[str, ...],
    expected_code: int,
) -> None:
    from kaji_harness.commands.issue import _handle_issue_resolve_verdict

    provider = _InMemoryGitHubProvider(tmp_path)
    provider.comment_issue("1", body)
    args = ["1", "--step", "review-starter-update"]
    for key in required_meta:
        args.extend(["--require-meta", key])

    assert _handle_issue_resolve_verdict(provider, args) == expected_code
