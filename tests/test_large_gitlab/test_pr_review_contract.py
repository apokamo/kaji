"""``kaji pr review`` / ``kaji pr merge`` contract tests against real GitLab.

Pins the kaji-pr-mr-bridge.md decisions:

- ``review --approve --body[-file]`` posts a note AND records an approval.
  Both must be observable independently; ordering is a best-effort
  secondary check (GitLab API timestamps are second-precision so back-to-back
  note + approve can land on the same timestamp).
- ``review --request-changes`` on an MR the user has not approved is a
  no-op for revoke (note posted, exit 0, approvals unchanged).
- ``merge --squash`` / ``merge --rebase`` are rejected before any glab
  invocation; the MR remains unchanged on disk.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


def _open_test_mr(
    repo: str,
    base: str,
    suffix: str,
) -> tuple[int, str]:
    """Helper: create branch + commit + MR, return (mr_iid, branch)."""
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
        pytest.fail(f"branch create failed: {rc.stderr.strip()}")
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
            "commit_message=kaji-e2e contract test commit",
            "-f",
            "actions[][action]=create",
            "-f",
            f"actions[][file_path]=kaji-e2e/{suffix}.txt",
            "-f",
            f"actions[][content]=contract test {suffix}\n",
            f"projects/{encoded}/repository/commits",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc2.returncode != 0:
        pytest.fail(f"commit failed: {rc2.stderr.strip()}")
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
            f"title=pr-review-contract {suffix}",
            "-f",
            "description=created by test_pr_review_contract.py",
            f"projects/{encoded}/merge_requests",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc3.returncode != 0:
        pytest.fail(f"mr create failed: {rc3.stderr.strip()}")
    payload = json.loads(rc3.stdout)
    iid = payload.get("iid")
    if not isinstance(iid, int):
        pytest.fail(f"glab api mr create did not return int iid: {payload!r}")
    return iid, branch


def test_review_approve_with_body_posts_note_and_records_approval(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_repo_encoded: str,
    gitlab_default_branch: str,
    gitlab_self_username: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
    run_glab_api,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    iid, branch = _open_test_mr(gitlab_repo, gitlab_default_branch, unique_suffix)
    created_resources.add_mr(iid)
    created_resources.add_branch(branch)

    body = f"approval body marker {unique_suffix}"
    body_file = tmp_path / "review_body.txt"
    body_file.write_text(body)

    review = run_kaji(
        kaji_workspace,
        "pr",
        "review",
        str(iid),
        "--approve",
        "--body-file",
        str(body_file),
    )
    assert review.returncode == 0, (
        f"review --approve failed (rc={review.returncode}):\n"
        f"stdout: {review.stdout}\nstderr: {review.stderr}"
    )

    # Independent observation 1: note exists
    notes_proc = run_glab_api(
        f"projects/{gitlab_repo_encoded}/merge_requests/{iid}/notes",
        "--paginate",
    )
    assert notes_proc.returncode == 0, notes_proc.stderr
    notes = json.loads(notes_proc.stdout)
    matching = [n for n in notes if isinstance(n, dict) and body in (n.get("body") or "")]
    assert matching, (
        f"expected a note containing {body!r} after approve --body-file; "
        f"got {[n.get('body') for n in notes]!r}"
    )

    # Independent observation 2: approval recorded for self
    approvals_proc = run_glab_api(
        f"projects/{gitlab_repo_encoded}/merge_requests/{iid}/approvals",
    )
    assert approvals_proc.returncode == 0, approvals_proc.stderr
    approvals = json.loads(approvals_proc.stdout)
    approved_users = {
        entry.get("user", {}).get("username")
        for entry in approvals.get("approved_by", [])
        if isinstance(entry, dict)
    }
    assert gitlab_self_username in approved_users, (
        f"expected self ({gitlab_self_username}) in approved_by, got {approved_users!r}"
    )


def test_review_request_changes_is_noop_for_revoke_when_not_approved(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_repo_encoded: str,
    gitlab_default_branch: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
    run_glab_api,  # type: ignore[no-untyped-def]
) -> None:
    iid, branch = _open_test_mr(gitlab_repo, gitlab_default_branch, unique_suffix)
    created_resources.add_mr(iid)
    created_resources.add_branch(branch)

    body = f"request-changes note {unique_suffix}"
    review = run_kaji(
        kaji_workspace,
        "pr",
        "review",
        str(iid),
        "--request-changes",
        "--body",
        body,
    )
    assert review.returncode == 0, (
        "request-changes on a non-approved MR must exit 0 (note posted, "
        f"revoke skipped); got rc={review.returncode}\n"
        f"stdout: {review.stdout}\nstderr: {review.stderr}"
    )

    # Note must be present
    notes_proc = run_glab_api(
        f"projects/{gitlab_repo_encoded}/merge_requests/{iid}/notes",
        "--paginate",
    )
    assert notes_proc.returncode == 0
    notes = json.loads(notes_proc.stdout)
    assert any(body in (n.get("body") or "") for n in notes if isinstance(n, dict))

    # Approvals must remain empty (no one approved this MR)
    approvals_proc = run_glab_api(
        f"projects/{gitlab_repo_encoded}/merge_requests/{iid}/approvals",
    )
    assert approvals_proc.returncode == 0
    approvals = json.loads(approvals_proc.stdout)
    assert approvals.get("approved_by") in (None, []), (
        f"expected approved_by empty after request-changes on non-approved MR, "
        f"got {approvals.get('approved_by')!r}"
    )


@pytest.mark.parametrize("flag", ["--squash", "--rebase"])
def test_merge_squash_and_rebase_are_rejected(
    kaji_workspace: Path,
    gitlab_repo_encoded: str,
    gitlab_default_branch: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
    run_glab_api,  # type: ignore[no-untyped-def]
    gitlab_repo: str,
    flag: str,
) -> None:
    iid, branch = _open_test_mr(
        gitlab_repo, gitlab_default_branch, f"{unique_suffix}-{flag.lstrip('-')}"
    )
    created_resources.add_mr(iid)
    created_resources.add_branch(branch)

    result = run_kaji(
        kaji_workspace,
        "pr",
        "merge",
        str(iid),
        flag,
    )
    assert result.returncode != 0, (
        f"merge {flag} should be rejected, but exit was 0:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Should be EXIT_INVALID_INPUT (2), not a runtime error
    assert result.returncode == 2, (
        f"expected exit 2 (invalid input), got {result.returncode}; stderr={result.stderr!r}"
    )
    assert flag in result.stderr or "rejects" in result.stderr.lower(), (
        f"stderr should mention rejection of {flag}; got: {result.stderr!r}"
    )

    # MR state untouched
    view = run_glab_api(f"projects/{gitlab_repo_encoded}/merge_requests/{iid}")
    assert view.returncode == 0
    payload = json.loads(view.stdout)
    assert payload.get("state") == "opened", (
        f"MR state changed despite reject: {payload.get('state')!r}"
    )
