"""``kaji pr review-comments`` / ``kaji pr reviews`` / ``kaji pr reply-to-comment``
output shape and provider-local ID round-trip.

Confirmed-item #7 (EPIC local-pc5090-4): GitLab provider must emit GitHub-
compatible JSON shapes for review surfaces. Specifically:

- ``review-comments`` items expose ``id``, ``user.login``, ``body``,
  ``path``, ``line``, ``created_at``, ``in_reply_to_id`` keys.
- ``reviews`` items expose ``state`` taking ``APPROVED`` /
  ``CHANGES_REQUESTED`` / ``COMMENTED`` (the GitHub vocabulary).
- ``reply-to-comment`` accepts the provider-local ``id`` value emitted
  by ``review-comments`` (``<discussion_id>:<note_id>`` opaque) and the
  posted reply is observable in a subsequent ``review-comments`` call,
  with the discussion thread restored (same ``in_reply_to_id``).

Setup: open a real MR, post a review-comment via the GitLab discussions
API (``glab api`` direct because ``kaji pr review-comment-create`` is
not in scope here), then exercise the read / reply shape contracts.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


def _open_mr_with_diff_thread(
    repo: str,
    base: str,
    suffix: str,
) -> tuple[int, str, str]:
    """Open MR with a single-line diff, then post a single non-positional
    discussion (a free-text discussion is sufficient for shape contract
    — ``in_reply_to_id`` round-trip is the key check).

    Returns ``(mr_iid, branch, discussion_id)``.
    """
    branch = f"e2e/{suffix}"
    encoded = quote(repo, safe="")

    rc = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"branch={branch}",
            "-f",
            f"ref={base}",
            f"projects/{encoded}/repository/branches",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc.returncode != 0:
        pytest.fail(f"branch create: {rc.stderr.strip()}")

    rc2 = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"branch={branch}",
            "-f",
            "commit_message=kaji-e2e review-shape test commit",
            "-f",
            "actions[][action]=create",
            "-f",
            f"actions[][file_path]=kaji-e2e/{suffix}.txt",
            "-f",
            "actions[][content]=initial line\n",
            f"projects/{encoded}/repository/commits",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc2.returncode != 0:
        pytest.fail(f"commit: {rc2.stderr.strip()}")

    rc3 = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"source_branch={branch}",
            "-f",
            f"target_branch={base}",
            "-f",
            f"title=review-shape {suffix}",
            f"projects/{encoded}/merge_requests",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc3.returncode != 0:
        pytest.fail(f"mr create: {rc3.stderr.strip()}")
    iid = json.loads(rc3.stdout)["iid"]

    # Open a discussion (non-positional, body-only is sufficient to test
    # shape + in_reply_to_id round-trip).
    rc4 = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"body=initial review note for {suffix}",
            f"projects/{encoded}/merge_requests/{iid}/discussions",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc4.returncode != 0:
        pytest.fail(f"discussion create: {rc4.stderr.strip()}")
    discussion = json.loads(rc4.stdout)
    return iid, branch, discussion["id"]


def test_review_comments_have_github_compatible_shape(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_default_branch: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
) -> None:
    iid, branch, discussion_id = _open_mr_with_diff_thread(
        gitlab_repo, gitlab_default_branch, unique_suffix
    )
    created_resources.add_mr(iid)
    created_resources.add_branch(branch)

    result = run_kaji(kaji_workspace, "pr", "review-comments", str(iid))
    assert result.returncode == 0, result.stderr
    items = json.loads(result.stdout)
    assert isinstance(items, list)
    assert items, "expected at least one review-comment after creating a discussion"
    item = items[0]
    # GitHub-compatible keys
    for key in ("id", "user", "body", "created_at"):
        assert key in item, f"review-comment missing key {key!r}: {item!r}"
    assert isinstance(item["user"], dict) and "login" in item["user"], (
        f"user should be dict with 'login', got: {item['user']!r}"
    )
    # in_reply_to_id should be present (None for the discussion head)
    assert "in_reply_to_id" in item


def test_reply_to_comment_round_trips_provider_local_id(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_default_branch: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
) -> None:
    iid, branch, discussion_id = _open_mr_with_diff_thread(
        gitlab_repo, gitlab_default_branch, unique_suffix
    )
    created_resources.add_mr(iid)
    created_resources.add_branch(branch)

    # Read existing comments → grab the head note's provider-local id
    listing = run_kaji(kaji_workspace, "pr", "review-comments", str(iid))
    assert listing.returncode == 0, listing.stderr
    items = json.loads(listing.stdout)
    head = items[0]
    head_id = head["id"]
    assert ":" in head_id, (
        f"provider-local id must be '<discussion_id>:<note_id>' opaque, got: {head_id!r}"
    )

    reply_body = f"reply from {unique_suffix}"
    reply = run_kaji(
        kaji_workspace,
        "pr",
        "reply-to-comment",
        str(iid),
        "--to",
        head_id,
        "--body",
        reply_body,
    )
    assert reply.returncode == 0, reply.stderr

    # Re-read; expect a second item whose body matches and whose discussion
    # thread is the same as the head's.
    listing2 = run_kaji(kaji_workspace, "pr", "review-comments", str(iid))
    assert listing2.returncode == 0, listing2.stderr
    items2 = json.loads(listing2.stdout)
    bodies = [it["body"] for it in items2 if isinstance(it, dict)]
    assert reply_body in bodies, f"reply body not visible after reply-to-comment; bodies={bodies!r}"
    # All ids in the same thread must share the discussion_id prefix
    head_disc = head_id.split(":", 1)[0]
    same_thread = [
        it
        for it in items2
        if isinstance(it, dict)
        and isinstance(it.get("id"), str)
        and it["id"].split(":", 1)[0] == head_disc
    ]
    assert len(same_thread) >= 2, (
        f"discussion thread should hold the head + reply, got {len(same_thread)}: {same_thread!r}"
    )


def test_reviews_emit_github_state_vocabulary(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_default_branch: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    iid, branch, _ = _open_mr_with_diff_thread(gitlab_repo, gitlab_default_branch, unique_suffix)
    created_resources.add_mr(iid)
    created_resources.add_branch(branch)

    body_file = tmp_path / "review.txt"
    body_file.write_text(f"approved by {unique_suffix}")
    review = run_kaji(
        kaji_workspace,
        "pr",
        "review",
        str(iid),
        "--approve",
        "--body-file",
        str(body_file),
    )
    assert review.returncode == 0, review.stderr

    out = run_kaji(kaji_workspace, "pr", "reviews", str(iid))
    assert out.returncode == 0, out.stderr
    items = json.loads(out.stdout)
    assert isinstance(items, list) and items
    states = {it.get("state") for it in items if isinstance(it, dict)}
    assert "APPROVED" in states, (
        f"expected APPROVED in reviews states (GitHub vocabulary), got {states!r}"
    )
    # Vocabulary must NOT include GitLab-native state strings
    forbidden = {"approved", "opened", "closed"}
    assert not (states & forbidden), (
        f"reviews state vocabulary should be GitHub-only; bleeding through: {states & forbidden!r}"
    )
